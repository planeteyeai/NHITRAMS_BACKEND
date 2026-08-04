"""Routine maintenance domain services (master data + report create/update)."""

from __future__ import annotations

from datetime import datetime, timezone

from django.db import transaction
from django.utils.dateparse import parse_datetime
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError

from inventory.models import (
    RoutineMaintenanceCategory,
    RoutineMaintenanceReport,
    SeverityMaster,
    SideMaster,
)
from inventory.serializers import resolve_dpr_uploaded_images
from inventory.services.project_lookup import ProjectDetailsNotFound, get_project_details
from inventory.utils import upload_dpr_images


def get_routine_maintenance_master() -> dict:
    """Return active dropdown masters for the frontend."""
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
            # Accept "YYYY-MM-DD HH:MM:SS"
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


@transaction.atomic
def create_routine_maintenance_report(*, username: str, validated: dict, image_files) -> RoutineMaintenanceReport:
    """Persist a routine maintenance report for the authenticated username."""
    try:
        project = get_project_details(username)
    except ProjectDetailsNotFound:
        raise
    except ValueError as exc:
        raise ValidationError({'detail': str(exc)}) from exc

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
    """Apply a partial/full update. Immutable identity/project fields are never changed."""
    try:
        new_urls = upload_dpr_images(image_files) if image_files else []
    except ValidationError:
        raise
    except RuntimeError as exc:
        raise RuntimeError(str(exc)) from exc

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

    report.uploaded_image_urls = resolve_dpr_uploaded_images(
        existing=report.uploaded_image_urls,
        new_urls=new_urls,
        keep_images=validated['keep_images'] if 'keep_images' in validated else None,
        remove_images=validated['remove_images'] if 'remove_images' in validated else None,
        clear_images=bool(validated.get('clear_images')),
        partial=partial,
        has_new_files=bool(image_files),
    )

    # username / project_* / created_at / id are never updated from the client
    report.save()
    return report


def list_routine_maintenance_reports(username: str):
    return (
        RoutineMaintenanceReport.objects.filter(username=username)
        .select_related('category', 'subcategory', 'side', 'severity')
        .order_by('-created_at')
    )
