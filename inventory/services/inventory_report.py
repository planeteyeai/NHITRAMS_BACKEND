"""Inventory report domain services (master data + create/update)."""

from __future__ import annotations

from datetime import datetime, timezone

from django.db import transaction
from django.utils.dateparse import parse_datetime
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError

from inventory.models import (
    InventoryCategory,
    InventoryReport,
    InventorySubcategory,
    SeverityMaster,
    SideMaster,
)
from inventory.services.routine_maintenance import (
    list_inventory_projects,
    resolve_project_details,
)


def get_inventory_master() -> dict:
    """Return active inventory dropdown masters for the frontend."""
    categories = (
        InventoryCategory.objects.filter(is_active=True)
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
        'inventory_categories': category_payload,
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


def parse_inventory_inspection_datetime(raw) -> datetime:
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
                '%Y-%m-%dT%H:%M:%SZ',
                '%d-%m-%Y %H:%M:%S',
                '%d/%m/%Y %H:%M:%S',
                '%Y-%m-%d',
            ):
                try:
                    value = datetime.strptime(text.replace('Z', ''), fmt.replace('Z', ''))
                    break
                except ValueError:
                    continue
        if value is None:
            raise ValidationError({'inspection_datetime': 'Invalid datetime format.'})

    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value


def get_owned_inventory_report(username: str, report_id: int) -> InventoryReport:
    try:
        report = (
            InventoryReport.objects.select_related(
                'inventory_category',
                'inventory_subcategory',
                'side',
                'severity',
            ).get(pk=report_id)
        )
    except InventoryReport.DoesNotExist as exc:
        raise NotFound('Report not found.') from exc

    if report.username != username:
        raise PermissionDenied('You do not have permission to access this report.')

    return report


def _legacy_chainage_float(value) -> float:
    if value is None or value == '':
        return 0.0
    try:
        return float(str(value).replace('km', '').strip().split()[0])
    except (TypeError, ValueError, IndexError):
        return 0.0


def _sync_legacy_columns(report: InventoryReport) -> None:
    """Keep PascalCase columns populated for PDF / older consumers."""
    report.Project_Name = report.project_name or report.Project_Name
    report.Latitude = report.latitude if report.latitude is not None else report.Latitude
    report.Longitude = report.longitude if report.longitude is not None else report.Longitude
    report.chainage = report.chainage or ''
    report.Chainage_start = _legacy_chainage_float(report.chainage) or (
        report.Chainage_start or 0
    )
    report.Chainage_end = report.Chainage_start
    if report.inventory_category_id:
        report.Asset_type = report.inventory_category.name
    if report.inventory_subcategory_id:
        report.Sub_Asset_Type = report.inventory_subcategory.name
    if report.side_id:
        report.Lane = report.side.name
        report.Direction = report.side.name
    report.No_of_inventories = int(report.nos or 0)
    if report.inspection_datetime:
        report.Date = report.inspection_datetime.isoformat()


def _apply_project_snapshot(report: InventoryReport, project: dict) -> None:
    report.project_id = project.get('id')
    report.project_name = project['project_name']
    report.start_chainage = project['start_chainage']
    report.end_chainage = project['end_chainage']
    report.total_project_length = project['total_project_length']
    report.Project_Name = project['project_name']


@transaction.atomic
def create_inventory_report(*, username: str, validated: dict) -> InventoryReport:
    project = validated.get('project') or resolve_project_details(validated.get('project_name'))

    inspection_datetime = validated.get('inspection_datetime')
    if inspection_datetime is None:
        inspection_datetime = datetime.now(timezone.utc)
    elif isinstance(inspection_datetime, str):
        inspection_datetime = parse_inventory_inspection_datetime(inspection_datetime)

    report = InventoryReport(
        username=username,
        inventory_category=validated['inventory_category'],
        inventory_subcategory=validated['inventory_subcategory'],
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
        Image_URL=validated.get('Image_URL') or '',
        Video_URL=validated.get('Video_URL') or '',
        Source=validated.get('Source') or 'AI Identifier',
    )
    _apply_project_snapshot(report, project)
    _sync_legacy_columns(report)
    report.save()
    return report


@transaction.atomic
def update_inventory_report(
    *,
    report: InventoryReport,
    validated: dict,
    partial: bool = True,
) -> InventoryReport:
    # Project snapshot is immutable after create.
    if 'inventory_category' in validated:
        report.inventory_category = validated['inventory_category']
    if 'inventory_subcategory' in validated:
        report.inventory_subcategory = validated['inventory_subcategory']
    if 'side' in validated:
        report.side = validated['side']
    if 'severity' in validated:
        report.severity = validated['severity']

    for field in (
        'latitude',
        'longitude',
        'chainage',
        'description_of_work',
        'nos',
        'length',
        'width',
        'depth',
        'total_quantity',
        'Image_URL',
        'Video_URL',
    ):
        if field in validated:
            setattr(report, field, validated[field])

    if 'inspection_datetime' in validated:
        value = validated['inspection_datetime']
        if isinstance(value, str):
            value = parse_inventory_inspection_datetime(value)
        report.inspection_datetime = value

    _sync_legacy_columns(report)
    report.save()
    return report


def list_inventory_reports(username: str):
    return (
        InventoryReport.objects.filter(username=username)
        .select_related(
            'inventory_category',
            'inventory_subcategory',
            'side',
            'severity',
        )
        .order_by('-created_at')
    )
