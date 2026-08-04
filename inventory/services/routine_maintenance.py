"""Routine maintenance domain services (master data + report create/update)."""

from __future__ import annotations

import re
from datetime import datetime, timezone

from django.db import transaction
from django.db.models import Max, Min
from django.utils.dateparse import parse_datetime
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError

from inventory.models import (
    Project,
    RoadSegment,
    RoutineMaintenanceCategory,
    RoutineMaintenanceReport,
    SeverityMaster,
    SideMaster,
)
from inventory.utils import upload_dpr_images


def _format_chainage(value) -> str:
    if value is None:
        return ''
    try:
        return f'{float(value):.3f}'
    except (TypeError, ValueError):
        return str(value)


def _format_length_km(value) -> str:
    if value is None:
        return ''
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if number == int(number):
        return f'{int(number)} km'
    return f'{number} km'


def _normalize_project_key(name: str) -> str:
    """Match key ignoring case, whitespace, hyphen spacing, and Kaljar/Kaljhar."""
    text = ' '.join(str(name or '').split()).casefold()
    # Known spelling variants for the same place.
    text = text.replace('kaljar', 'kaljhar')
    # "Kaljar -1" and "Kaljhar-1" are the same project.
    text = re.sub(r'\s*-\s*', '-', text)
    return text


def _preferred_display_name(names: list[str]) -> str:
    """Pick a stable display label from duplicate name variants."""
    if not names:
        return ''
    scored = []
    for raw in names:
        cleaned = ' '.join(str(raw).split())
        if not cleaned:
            continue
        score = 0
        if cleaned == cleaned.title():
            score += 3
        elif cleaned[:1].isupper() and any(c.islower() for c in cleaned):
            score += 2
        elif cleaned[:1].isupper():
            score += 1
        # Prefer the canonical Kaljhar spelling over Kaljar.
        folded = cleaned.casefold()
        if 'kaljhar' in folded:
            score += 5
        if re.search(r'\S-\S', cleaned):
            score += 1  # prefer "Kaljhar-1" over "Kaljhar -1"
        scored.append((score, len(cleaned), cleaned))
    if not scored:
        return ' '.join(str(names[0]).split())
    scored.sort(key=lambda item: (item[0], item[1], item[2].casefold()), reverse=True)
    return scored[0][2]


def project_payload_from_model(project: Project, *, start=None, end=None, display_name: str | None = None) -> dict:
    """Build the API project object from a Project row (+ optional chainage bounds)."""
    if start is None or end is None:
        bounds = RoadSegment.objects.filter(project_id=project.project_id).aggregate(
            start=Min('chainage_start'),
            end=Max('chainage_end'),
        )
        start = bounds['start'] if start is None else start
        end = bounds['end'] if end is None else end

    return {
        'id': project.project_id,
        'project_name': display_name or project.project_name,
        'start_chainage': _format_chainage(start),
        'end_chainage': _format_chainage(end),
        'total_project_length': _format_length_km(project.length_km),
    }


def list_inventory_projects() -> list[dict]:
    """Return deduplicated canonical project names for dropdowns."""
    groups: dict[str, list[str]] = {}
    for project in Project.objects.all().only('project_name'):
        key = _normalize_project_key(project.project_name)
        if not key:
            continue
        groups.setdefault(key, []).append(project.project_name)

    payload = [
        {'project_name': _preferred_display_name(names)}
        for names in groups.values()
    ]
    payload.sort(key=lambda row: row['project_name'].casefold())
    return payload


def resolve_project_details(project_name) -> dict:
    """Resolve project snapshot by name (case/whitespace insensitive)."""
    if project_name is None or str(project_name).strip() == '':
        raise ValidationError({'project_name': ['This field is required.']})

    key = _normalize_project_key(project_name)
    if not key:
        raise ValidationError({'project_name': ['Invalid project selected.']})

    matches = [
        project
        for project in Project.objects.all()
        if _normalize_project_key(project.project_name) == key
    ]
    if not matches:
        raise ValidationError({'project_name': ['Invalid project selected.']})

    # Prefer the most complete row among duplicates.
    matches.sort(key=lambda p: (-(p.length_km or 0), p.project_id))
    project = matches[0]
    display = _preferred_display_name([m.project_name for m in matches])
    return project_payload_from_model(project, display_name=display)


def get_routine_maintenance_master() -> dict:
    """Return active dropdown masters for the frontend (includes projects)."""
    categories = (
        RoutineMaintenanceCategory.objects.filter(is_active=True)
        .prefetch_related('subcategories')
        .order_by('display_order', 'name')
    )

    category_payload = []
    for category in categories:
        subcategories = [
            {'id': sub.id, 'name': sub.name}
            for sub in sorted(
                (s for s in category.subcategories.all() if s.is_active),
                key=lambda s: (s.display_order, s.name),
            )
        ]
        category_payload.append(
            {
                'id': category.id,
                'name': category.name,
                'subcategories': subcategories,
            }
        )

    return {
        'projects': list_inventory_projects(),
        'categories': category_payload,
        'side_master': [
            {'id': side.id, 'name': side.name}
            for side in SideMaster.objects.filter(is_active=True).order_by(
                'display_order', 'name'
            )
        ],
        'severity_master': [
            {'id': row.id, 'name': row.name}
            for row in SeverityMaster.objects.filter(is_active=True).order_by(
                'display_order', 'name'
            )
        ],
    }


def _parse_inspection_datetime(raw) -> datetime:
    if raw is None or raw == '':
        return datetime.now(timezone.utc)

    if isinstance(raw, datetime):
        value = raw
    else:
        text = str(raw).strip()
        value = parse_datetime(text)
        if value is None:
            for fmt in (
                '%Y-%m-%d %H:%M:%S',
                '%Y-%m-%dT%H:%M:%S',
                '%d-%m-%Y %H:%M:%S',
                '%d/%m/%Y %H:%M:%S',
                '%Y-%m-%d',
            ):
                try:
                    value = datetime.strptime(text, fmt)
                    break
                except ValueError:
                    continue
        if value is None:
            raise ValidationError(
                {'inspection_datetime': 'Invalid datetime format.'}
            )

    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value


def collect_rm_images(request) -> list:
    files = []
    for key in (
        'images',
        'images[]',
        'image',
        'evidence',
        'evidence[]',
        'new_images',
        'new_images[]',
    ):
        files.extend(request.FILES.getlist(key))
    seen = set()
    unique = []
    for f in files:
        marker = id(f)
        if marker not in seen:
            seen.add(marker)
            unique.append(f)
    return unique


def get_owned_routine_maintenance_report(username: str, report_id: int) -> RoutineMaintenanceReport:
    """Fetch a report and enforce ownership. Raises NotFound / PermissionDenied."""
    try:
        report = (
            RoutineMaintenanceReport.objects.select_related(
                'category', 'subcategory', 'side', 'severity'
            ).get(pk=report_id)
        )
    except RoutineMaintenanceReport.DoesNotExist as exc:
        raise NotFound('Routine maintenance report not found.') from exc

    if report.username != username:
        raise PermissionDenied('You are not authorized to update this report.')

    return report


def _apply_project_snapshot(report: RoutineMaintenanceReport, project: dict) -> None:
    report.project_id = project.get('id')
    report.project_name = project['project_name']
    report.start_chainage = project['start_chainage']
    report.end_chainage = project['end_chainage']
    report.total_project_length = project['total_project_length']


@transaction.atomic
def create_routine_maintenance_report(*, username: str, validated: dict, image_files) -> RoutineMaintenanceReport:
    """Persist a routine maintenance report for the authenticated username."""
    project = validated.get('project')
    if not project:
        project = resolve_project_details(validated.get('project_name'))

    try:
        uploaded_urls = upload_dpr_images(image_files) if image_files else []
    except ValidationError:
        raise
    except RuntimeError as exc:
        raise RuntimeError(str(exc)) from exc

    inspection_datetime = validated.get('inspection_datetime')
    if inspection_datetime is None:
        inspection_datetime = datetime.now(timezone.utc)
    elif isinstance(inspection_datetime, str):
        inspection_datetime = _parse_inspection_datetime(inspection_datetime)

    return RoutineMaintenanceReport.objects.create(
        username=username,
        project_id=project.get('id'),
        project_name=project['project_name'],
        start_chainage=project['start_chainage'],
        end_chainage=project['end_chainage'],
        total_project_length=project['total_project_length'],
        category=validated['category'],
        subcategory=validated['subcategory'],
        side=validated['side'],
        severity=validated['severity'],
        latitude=validated['latitude'],
        longitude=validated['longitude'],
        chainage=validated['chainage'],
        inspection_datetime=inspection_datetime,
        description_of_work=validated.get('description_of_work') or '',
        nos=validated.get('nos') or 0,
        length=validated.get('length') or 0,
        width=validated.get('width') or 0,
        depth=validated.get('depth') or 0,
        total_quantity=validated.get('total_quantity') or 0,
        uploaded_image_urls=uploaded_urls,
        status=validated.get('status') or RoutineMaintenanceReport.STATUS_SUBMITTED,
    )


@transaction.atomic
def update_routine_maintenance_report(
    *,
    report: RoutineMaintenanceReport,
    validated: dict,
    image_files,
    partial: bool = True,
) -> RoutineMaintenanceReport:
    """Apply a partial/full update. Username is never changed from the client."""
    try:
        new_urls = upload_dpr_images(image_files) if image_files else []
    except ValidationError:
        raise
    except RuntimeError as exc:
        raise RuntimeError(str(exc)) from exc

    if 'project' in validated:
        _apply_project_snapshot(report, validated['project'])
    elif 'project_name' in validated:
        _apply_project_snapshot(report, resolve_project_details(validated['project_name']))

    if 'category' in validated:
        report.category = validated['category']
    if 'subcategory' in validated:
        report.subcategory = validated['subcategory']
    if 'side' in validated:
        report.side = validated['side']
    if 'severity' in validated:
        report.severity = validated['severity']
    if 'latitude' in validated:
        report.latitude = validated['latitude']
    if 'longitude' in validated:
        report.longitude = validated['longitude']
    if 'chainage' in validated:
        report.chainage = validated['chainage']
    if 'inspection_datetime' in validated:
        value = validated['inspection_datetime']
        if isinstance(value, str):
            value = _parse_inspection_datetime(value)
        report.inspection_datetime = value
    if 'description_of_work' in validated:
        report.description_of_work = validated['description_of_work'] or ''
    if 'nos' in validated:
        report.nos = validated['nos']
    if 'length' in validated:
        report.length = validated['length']
    if 'width' in validated:
        report.width = validated['width']
    if 'depth' in validated:
        report.depth = validated['depth']
    if 'total_quantity' in validated:
        report.total_quantity = validated['total_quantity']
    if 'status' in validated:
        report.status = validated['status']

    from inventory.serializers import resolve_dpr_uploaded_images

    report.uploaded_image_urls = resolve_dpr_uploaded_images(
        existing=report.uploaded_image_urls,
        new_urls=new_urls,
        keep_images=validated['keep_images'] if 'keep_images' in validated else None,
        remove_images=validated['remove_images'] if 'remove_images' in validated else None,
        clear_images=bool(validated.get('clear_images')),
        partial=partial,
        has_new_files=bool(image_files),
    )

    report.save()
    return report


def list_routine_maintenance_reports(username: str):
    return (
        RoutineMaintenanceReport.objects.filter(username=username)
        .select_related('category', 'subcategory', 'side', 'severity')
        .order_by('-created_at')
    )
