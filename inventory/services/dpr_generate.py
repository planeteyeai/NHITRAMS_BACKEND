"""Asset Change Detection and Monitoring DPR — read-only generation service."""

from __future__ import annotations

from datetime import date, datetime
from typing import Iterable

from django.db.models import Sum
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from inventory.models import (
    Inventory,
    InventoryReport,
    Project,
    RoutineMaintenanceReport,
)
from inventory.services.routine_maintenance import (
    _normalize_project_key,
    resolve_project_details,
)

# InventorySubcategory (casefold) → normalized Inventory.asset_type keys.
# Master stock lives on legacy `inventory` rows (count_val), not RAMS APK masters.
_SUBCATEGORY_TO_ASSET_KEYS: dict[str, set[str]] = {
    'kilometer stones': {'km_stones'},
    'hectometer stones': {'km_stones'},
    'boundary stones': {'km_stones'},
    'delineators': {'delineators'},
    'guard posts': {'guard_post'},
    'kerb': {'kerb'},
    'road studs': set(),
    'chevron boards': {'sign_boards', 'sign_board', 'sing_boards'},
    'hazard markers': {'sign_boards', 'sign_board'},
    'object markers': {'sign_boards', 'sign_board'},
    'median markers': {'sign_boards', 'sign_board'},
    'regulatory signs': {'sign_boards', 'sign_board', 'sing_boards'},
    'warning signs': {'sign_boards', 'sign_board', 'sing_boards'},
    'informatory signs': {'sign_boards', 'sign_board', 'sing_boards'},
    'gantry signs': {'sign_boards', 'sign_board', 'sing_boards'},
    'cantilever signs': {'sign_boards', 'sign_board', 'sing_boards'},
    'variable message signs (vms)': {'sign_boards', 'sign_board', 'sing_boards'},
    'w-beam crash barrier': {'crash_barrier'},
    'thrie-beam barrier': {'crash_barrier'},
    'wire rope barrier': {'crash_barrier'},
    'concrete barrier': {'crash_barrier'},
    'pedestrian guard rail': {'pedestrian_guard_rail'},
    'anti-glare screen': set(),
    'rumble strips': set(),
    'speed breakers': set(),
    'high mast lights': {'street_lights', 'street_light'},
    'single arm light': {'street_lights', 'street_light'},
    'double arm light': {'street_lights', 'street_light'},
    'lighting poles': {'street_lights', 'street_light'},
    'transformers': set(),
    'dg sets': set(),
    'solar lights': {'street_lights', 'street_light', 'solar_blinker', 'solar_blinkers'},
    'solar blinker': {'solar_blinker', 'solar_blinkers'},
    'rcc drain line drain': {'rcc_drain'},
    'rcc drain cover drain': {'rcc_drain'},
    'median drains': {'rcc_drain'},
    'chutes': {'rcc_drain'},
    'earthen drain': {'rcc_drain'},
    'avenue plantation': {'trees'},
    'median plantation': {'trees', 'median_plants'},
    'noise barriers': set(),
    'rainwater harvesting structures': set(),
    'landscaping': {'trees', 'median_plants'},
    'bus bays': {'bus_stop', 'bus_bays'},
    'bus shelters': {'bus_stop'},
    'major bridges': {'bridges'},
    'minor bridges': {'bridges'},
    'rob': {'bridges'},
    'rub': {'bridges'},
    'flyovers': {'bridges'},
    'vehicular underpasses (vup)': {'bridges'},
    'pedestrian underpasses (pup)': {'bridges'},
    'elephant underpass (eup)': {'bridges'},
    'cattle underpass (cup)': {'bridges'},
    'box culverts': {'culvert'},
    'pipe culverts': {'culvert'},
    'slab culverts': {'culvert'},
    'causeways': {'culvert'},
    'retaining walls': set(),
    'breast walls': set(),
    're walls': set(),
    'traffic signals': {'traffic_signal', 'traffic_signals'},
    'atms equipment': set(),
    'cctv cameras': set(),
    'speed detection cameras': set(),
    'vehicle detection sensors': set(),
    'emergency call boxes': {'emergency_call_box'},
    'emergency call box (ecb)': {'emergency_call_box'},
    'weigh-in-motion (wim)': set(),
    'weigh-in-motion (wim) system': set(),
    'toll booths': {'toll_plaza'},
    'etc lanes': {'toll_plaza'},
    'control room': {'toll_plaza'},
    'ups room': {'toll_plaza'},
    'dg set': {'toll_plaza'},
    'administrative building': {'toll_plaza'},
    'ambulance': {'toll_plaza'},
    'crane': {'toll_plaza'},
    'recovery vehicle': {'toll_plaza'},
    'patrol vehicle': {'toll_plaza'},
    'fuel stations': {'fuel_station'},
    'ev charging stations': {'fuel_station'},
    'restaurants': set(),
    'food courts': set(),
    'public toilets': {'toilet_blocks'},
    'drinking water': set(),
    'parking area': set(),
    'truck lay bays': {'truck_layby'},
    'rest areas': {'rest_area'},
    'first aid centre': set(),
    'major junction': {'junction'},
    'minor junction': {'junction'},
    'median openings': {'median_opening'},
    'slope pitching': set(),
    'toe wall': set(),
    'retaining wall': set(),
    'gabion wall': set(),
}

_CATEGORY_TO_ASSET_KEYS: dict[str, set[str]] = {
    'traffic signs': {'sign_boards', 'sign_board', 'sing_boards'},
    'electrical assets': {
        'street_lights',
        'street_light',
        'solar_blinker',
        'solar_blinkers',
    },
    'safety assets': {'crash_barrier', 'pedestrian_guard_rail', 'kerb'},
    'rcc drain line drain': {'rcc_drain'},
    'road furniture': {'km_stones', 'delineators', 'guard_post'},
    'plantation': {'trees', 'median_plants'},
    'bus and public transport facilities': {'bus_stop', 'bus_bays'},
    'structures': {'culvert', 'bridges'},
    'wayside amenities': {
        'fuel_station',
        'toilet_blocks',
        'truck_layby',
        'rest_area',
        'bus_stop',
    },
    'traffic management facilities': {
        'traffic_signal',
        'traffic_signals',
        'emergency_call_box',
    },
    'toll plaza facilities': {'toll_plaza'},
    'junctions': {'junction', 'median_opening'},
    'advanced traffic management system': {'emergency_call_box'},
    'pavement markings': set(),
    'slope protection': set(),
}


def calculate_unit(nos=0, length=0, width=0, depth=0) -> str:
    """Derive unit from dimension fields (never client-supplied)."""
    nos_v = float(nos or 0)
    length_v = float(length or 0)
    width_v = float(width or 0)
    depth_v = float(depth or 0)

    if nos_v > 0:
        return 'Nos'
    if length_v > 0 and width_v == 0 and depth_v == 0:
        return 'RMT'
    if length_v > 0 and width_v > 0 and depth_v == 0:
        return 'm²'
    if length_v > 0 and width_v > 0 and depth_v > 0:
        return 'm³'
    return ''


def generate_photo_references(photo_count: int, *, start_at: int = 1) -> list[str]:
    """Return Evidence Available labels for N photos starting at start_at."""
    if photo_count <= 0:
        return []
    return [
        f'Refer Image No. {index}'
        for index in range(start_at, start_at + photo_count)
    ]


def _normalize_asset_key(name: str) -> str:
    text = ' '.join(str(name or '').split()).casefold()
    text = text.replace('-', ' ').replace('/', ' ')
    text = '_'.join(text.split())
    return text


def _matching_project_names(project_name: str) -> list[str]:
    key = _normalize_project_key(project_name)
    names = {str(project_name).strip()} if str(project_name).strip() else set()
    for project in Project.objects.all().only('project_name'):
        if _normalize_project_key(project.project_name) == key:
            names.add(project.project_name)
    return list(names)


def _matching_project_ids(project_name: str) -> list[int]:
    key = _normalize_project_key(project_name)
    return [
        project.project_id
        for project in Project.objects.all().only('project_id', 'project_name')
        if _normalize_project_key(project.project_name) == key
    ]


def _asset_keys_for_subcategory(subcategory_name: str, category_name: str | None) -> set[str]:
    sub_key = str(subcategory_name or '').strip().casefold()
    keys = set(_SUBCATEGORY_TO_ASSET_KEYS.get(sub_key, set()))
    if keys:
        return keys
    cat_key = str(category_name or '').strip().casefold()
    return set(_CATEGORY_TO_ASSET_KEYS.get(cat_key, set()))


def _master_quantity_for_subcategory(
    project_ids: list[int],
    subcategory_name: str,
    category_name: str | None,
) -> float:
    if not project_ids:
        return 0.0
    wanted = _asset_keys_for_subcategory(subcategory_name, category_name)
    if not wanted:
        return 0.0

    total = 0.0
    rows = (
        Inventory.objects.filter(project_id__in=project_ids)
        .values('asset_type__asset_type_name')
        .annotate(qty=Sum('count_val'))
    )
    for row in rows:
        if _normalize_asset_key(row['asset_type__asset_type_name']) in wanted:
            total += float(row['qty'] or 0)
    return total


def build_overall_summary(inventory_reports: Iterable[InventoryReport], project_name: str) -> list[dict]:
    """Build overall summary rows for subcategories that have at least one report."""
    project_ids = _matching_project_ids(project_name)
    damaged_by_sub: dict[str, dict] = {}

    for report in inventory_reports:
        sub = report.inventory_subcategory
        if sub is None:
            # Legacy fallback
            label = (report.Sub_Asset_Type or report.Asset_type or '').strip()
            if not label:
                continue
            category_name = ''
            sub_id = None
            sub_name = label
        else:
            label = sub.name
            category_name = sub.category.name if sub.category_id else ''
            sub_id = sub.id
            sub_name = sub.name

        bucket = damaged_by_sub.setdefault(
            label,
            {
                'asset_type': sub_name if sub is not None else label,
                'subcategory_id': sub_id,
                'category_name': category_name,
                'damaged_quantity': 0.0,
            },
        )
        bucket['damaged_quantity'] += float(report.total_quantity or 0)

    summary = []
    for label in sorted(damaged_by_sub.keys(), key=lambda s: s.casefold()):
        bucket = damaged_by_sub[label]
        total_quantity = _master_quantity_for_subcategory(
            project_ids,
            bucket['asset_type'],
            bucket.get('category_name'),
        )
        damaged_quantity = bucket['damaged_quantity']
        summary.append(
            {
                'asset_type': bucket['asset_type'],
                'total_quantity': total_quantity,
                'damaged_quantity': damaged_quantity,
                'available_quantity': total_quantity - damaged_quantity,
            }
        )
    return summary


def _split_media_urls(raw) -> list[str]:
    if raw is None or raw == '':
        return []
    if isinstance(raw, (list, tuple)):
        values = [str(item).strip() for item in raw]
    else:
        text = str(raw).strip()
        for sep in (';', '|', '\n', ','):
            if sep in text:
                values = [part.strip() for part in text.split(sep)]
                break
        else:
            values = [text]
    return [url for url in values if url]


def _chainage_sort_key(value) -> float:
    text = str(value or '').strip()
    if not text:
        return float('inf')
    if '+' in text:
        parts = text.split('+', 1)
        try:
            return float(parts[0]) + (float(parts[1]) / 1000.0)
        except (TypeError, ValueError):
            return float('inf')
    try:
        return float(text)
    except (TypeError, ValueError):
        return float('inf')


def _format_datetime(value) -> str:
    if value is None:
        return ''
    if timezone.is_naive(value):
        value = timezone.make_aware(value, timezone.utc)
    local = timezone.localtime(value)
    return local.strftime('%d-%m-%Y %I:%M %p')


def _format_date(value) -> str:
    if value is None:
        return ''
    if isinstance(value, datetime):
        return _format_datetime(value)
    if isinstance(value, date):
        return value.strftime('%d-%m-%Y')
    return str(value)


def _parse_report_date(raw, field_name: str = 'assessment_date') -> date:
    if isinstance(raw, date) and not isinstance(raw, datetime):
        return raw
    if isinstance(raw, datetime):
        return raw.date()
    text = str(raw or '').strip()
    for fmt in ('%Y-%m-%d', '%d-%m-%Y', '%d/%m/%Y'):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise ValidationError({field_name: ['Invalid date format. Use YYYY-MM-DD.']})


def _parse_assessment_date(raw) -> date:
    return _parse_report_date(raw, 'assessment_date')


def _validate_report_period(from_date: date, to_date: date) -> None:
    if from_date > to_date:
        raise ValidationError(
            {'to_date': ['to_date must be greater than or equal to from_date.']}
        )
    span_days = (to_date - from_date).days + 1
    if span_days > 31:
        raise ValidationError(
            {'to_date': ['Report period cannot exceed 31 days.']}
        )


def _period_bounds(from_date: date, to_date: date) -> tuple[datetime, datetime]:
    """Inclusive datetime bounds for filtering inspection_datetime."""
    start = datetime.combine(from_date, datetime.min.time())
    end = datetime.combine(to_date, datetime.max.time())
    if timezone.is_naive(start):
        start = timezone.make_aware(start, timezone.get_current_timezone())
    if timezone.is_naive(end):
        end = timezone.make_aware(end, timezone.get_current_timezone())
    return start, end


def _inspection_in_period(value, start: datetime, end: datetime) -> bool:
    if value is None:
        return False
    dt = value
    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt, timezone.utc)
    return start <= dt <= end


def _sorted_reports(reports: list) -> list:
    return sorted(
        reports,
        key=lambda r: (
            _chainage_sort_key(getattr(r, 'chainage', '')),
            getattr(r, 'inspection_datetime', None) or datetime.min.replace(tzinfo=timezone.utc),
            getattr(r, 'id', 0),
        ),
    )


def _build_inventory_sections(reports: list[InventoryReport]) -> tuple[list[dict], list[dict]]:
    details = []
    photos = []
    image_counter = 0

    for index, report in enumerate(_sorted_reports(reports), start=1):
        asset_type = ''
        if report.inventory_subcategory_id:
            asset_type = report.inventory_subcategory.name
        else:
            asset_type = (report.Sub_Asset_Type or report.Asset_type or '').strip()

        urls = _split_media_urls(report.Image_URL)
        evidence_labels = generate_photo_references(len(urls), start_at=image_counter + 1)
        for url in urls:
            image_counter += 1
            photos.append(
                {
                    'image_number': image_counter,
                    'image_label': f'Image {image_counter}',
                    'asset_type': asset_type,
                    'chainage': report.chainage or '',
                    'photo_url': url,
                }
            )

        evidence = ', '.join(evidence_labels) if evidence_labels else ''
        details.append(
            {
                'sr_no': index,
                'asset_type': asset_type,
                'chainage': report.chainage or '',
                'latitude': report.latitude,
                'longitude': report.longitude,
                'side': report.side.name if report.side_id else '',
                'date_and_time': _format_datetime(report.inspection_datetime),
                'description': report.description_of_work or '',
                'evidence_available': evidence,
                'nos': report.nos,
                'length': report.length,
                'width': report.width,
                'depth': report.depth,
                'total_quantity': report.total_quantity,
                'unit': calculate_unit(report.nos, report.length, report.width, report.depth),
                'severity': report.severity.name if report.severity_id else '',
            }
        )

    return details, photos


def _build_rm_sections(reports: list[RoutineMaintenanceReport]) -> tuple[list[dict], list[dict]]:
    details = []
    photos = []
    image_counter = 0

    for index, report in enumerate(_sorted_reports(reports), start=1):
        maintenance_type = ''
        if report.subcategory_id:
            maintenance_type = report.subcategory.name
        urls = _split_media_urls(report.uploaded_image_urls)
        evidence_labels = generate_photo_references(len(urls), start_at=image_counter + 1)
        for url in urls:
            image_counter += 1
            photos.append(
                {
                    'image_number': image_counter,
                    'image_label': f'Image {image_counter}',
                    'maintenance_type': maintenance_type,
                    'chainage': report.chainage or '',
                    'photo_url': url,
                }
            )

        evidence = ', '.join(evidence_labels) if evidence_labels else ''
        details.append(
            {
                'sr_no': index,
                'maintenance_type': maintenance_type,
                'chainage': report.chainage or '',
                'latitude': report.latitude,
                'longitude': report.longitude,
                'side': report.side.name if report.side_id else '',
                'date_and_time': _format_datetime(report.inspection_datetime),
                'description': report.description_of_work or '',
                'evidence_available': evidence,
                'nos': report.nos,
                'length': report.length,
                'width': report.width,
                'depth': report.depth,
                'total_quantity': report.total_quantity,
                'unit': calculate_unit(report.nos, report.length, report.width, report.depth),
                'severity': report.severity.name if report.severity_id else '',
            }
        )

    return details, photos


def generate_asset_change_dpr(
    *,
    project_name: str,
    reporting_engineer_name: str,
    assessment_date,
    from_date,
    to_date,
    username: str,
) -> dict:
    """Assemble the full Asset Change Detection and Monitoring DPR payload.

    assessment_date is metadata only. Inventory/RM filtering uses from_date..to_date.
    """
    project = resolve_project_details(project_name)
    assessment = _parse_assessment_date(assessment_date)
    period_from = _parse_report_date(from_date, 'from_date')
    period_to = _parse_report_date(to_date, 'to_date')
    _validate_report_period(period_from, period_to)
    period_start, period_end = _period_bounds(period_from, period_to)

    engineer = str(reporting_engineer_name or '').strip()
    if not engineer:
        raise ValidationError(
            {'reporting_engineer_name': ['This field is required.']}
        )

    name_variants = _matching_project_names(project['project_name'])
    if project_name and project_name not in name_variants:
        name_variants.append(project_name)

    target_key = _normalize_project_key(project['project_name'])

    inventory_reports = [
        report
        for report in InventoryReport.objects.select_related(
            'inventory_category',
            'inventory_subcategory',
            'inventory_subcategory__category',
            'side',
            'severity',
        )
        if (
            _normalize_project_key(report.project_name) == target_key
            or report.project_name in name_variants
        )
        and _inspection_in_period(report.inspection_datetime, period_start, period_end)
    ]

    rm_reports = [
        report
        for report in RoutineMaintenanceReport.objects.select_related(
            'category',
            'subcategory',
            'side',
            'severity',
        )
        if (
            _normalize_project_key(report.project_name) == target_key
            or report.project_name in name_variants
        )
        and _inspection_in_period(report.inspection_datetime, period_start, period_end)
    ]

    overall_summary = build_overall_summary(inventory_reports, project['project_name'])
    removed_damaged_assets, removed_photos = _build_inventory_sections(inventory_reports)
    routine_maintenance, rm_photos = _build_rm_sections(rm_reports)

    generation_dt = timezone.now()
    generation_local = timezone.localtime(generation_dt)

    return {
        'project_details': {
            'project_name': project['project_name'],
            'start_chainage': project['start_chainage'],
            'end_chainage': project['end_chainage'],
            'total_project_length': project['total_project_length'],
            'reporting_engineer_name': engineer,
            'assessment_date': assessment.strftime('%Y-%m-%d'),
            'assessment_date_display': assessment.strftime('%d/%m/%Y'),
            'from_date': period_from.strftime('%Y-%m-%d'),
            'to_date': period_to.strftime('%Y-%m-%d'),
            'from_date_display': period_from.strftime('%d/%m/%Y'),
            'to_date_display': period_to.strftime('%d/%m/%Y'),
            'report_period_display': (
                f'{period_from.strftime("%d/%m/%Y")} - {period_to.strftime("%d/%m/%Y")}'
            ),
            'generation_date': generation_dt.isoformat(),
            'generation_date_display': generation_local.strftime('%d/%m/%Y'),
            'generation_datetime_display': generation_local.strftime('%d/%m/%Y %H:%M IST'),
            'generated_by': username,
        },
        'overall_summary': overall_summary,
        'removed_damaged_assets': removed_damaged_assets,
        'removed_damaged_asset_photos': removed_photos,
        'routine_maintenance': routine_maintenance,
        'routine_maintenance_photos': rm_photos,
    }


class DPRGenerationService:
    """Facade for Asset Change Detection DPR JSON generation (single source of truth)."""

    @staticmethod
    def generate(
        *,
        project_name: str,
        reporting_engineer_name: str,
        assessment_date,
        from_date,
        to_date,
        username: str,
    ) -> dict:
        return generate_asset_change_dpr(
            project_name=project_name,
            reporting_engineer_name=reporting_engineer_name,
            assessment_date=assessment_date,
            from_date=from_date,
            to_date=to_date,
            username=username,
        )
