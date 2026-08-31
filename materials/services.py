"""Material domain services — list/create/update/delete business logic."""

from __future__ import annotations

import logging
import math
from typing import Any

from django.db import IntegrityError, transaction
from django.db.models import Q, QuerySet
from rest_framework.exceptions import NotFound, ValidationError

from .constants import (
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    SORTABLE_FIELDS,
    is_other_material_type,
)
from .models import Material

logger = logging.getLogger(__name__)


def get_material_or_404(material_id: int) -> Material:
    try:
        return Material.objects.get(pk=material_id)
    except Material.DoesNotExist as exc:
        raise NotFound('Material not found.') from exc


def _apply_filters(queryset: QuerySet, filters: dict) -> QuerySet:
    unit = filters.get('material_unit')
    if unit is not None and str(unit).strip() != '':
        queryset = queryset.filter(material_unit__iexact=str(unit).strip())

    material_type = filters.get('material_type')
    if material_type is not None and str(material_type).strip() != '':
        queryset = queryset.filter(material_type__iexact=str(material_type).strip())

    return queryset


def _apply_search(queryset: QuerySet, search: str) -> QuerySet:
    text = (search or '').strip()
    if not text:
        return queryset
    return queryset.filter(
        Q(material_name__icontains=text)
        | Q(material_type__icontains=text)
        | Q(other_type__icontains=text)
        | Q(material_unit__icontains=text)
        | Q(material_description__icontains=text)
    )


def _apply_sort(queryset: QuerySet, sort_by: str, sort_order: str) -> QuerySet:
    field = (sort_by or 'created_at').strip()
    if field not in SORTABLE_FIELDS:
        field = 'created_at'
    prefix = '' if (sort_order or 'desc').lower() == 'asc' else '-'
    return queryset.order_by(f'{prefix}{field}', '-id')


def list_materials(*, query: dict) -> dict[str, Any]:
    page = int(query.get('page') or 1)
    page_size = int(query.get('page_size') or DEFAULT_PAGE_SIZE)
    page_size = min(max(page_size, 1), MAX_PAGE_SIZE)
    page = max(page, 1)

    queryset = Material.objects.all()
    queryset = _apply_filters(queryset, query)
    queryset = _apply_search(queryset, query.get('search') or '')
    queryset = _apply_sort(
        queryset,
        query.get('sort_by') or 'created_at',
        query.get('sort_order') or 'desc',
    )

    total = queryset.count()
    total_pages = math.ceil(total / page_size) if total else 0
    if total_pages and page > total_pages:
        page = total_pages

    offset = (page - 1) * page_size
    rows = list(queryset[offset : offset + page_size])

    return {
        'rows': rows,
        'pagination': {
            'page': page,
            'page_size': page_size,
            'total': total,
            'total_pages': total_pages,
        },
    }


def _duplicate_error() -> ValidationError:
    return ValidationError(
        {
            'non_field_errors': [
                'A material with the same material_name, material_unit, '
                'material_type, and other_type already exists.'
            ]
        }
    )


def _resolve_effective_fields(
    *,
    material: Material | None,
    validated: dict,
    partial: bool,
) -> dict:
    """Merge PATCH/PUT payload with existing row and enforce Other-type rules."""
    if material is None:
        effective = dict(validated)
    elif partial:
        effective = {
            'material_name': validated.get('material_name', material.material_name),
            'material_unit': validated.get('material_unit', material.material_unit),
            'material_type': validated.get('material_type', material.material_type),
            'other_type': (
                validated['other_type']
                if 'other_type' in validated
                else material.other_type
            ),
            'material_description': (
                validated['material_description']
                if 'material_description' in validated
                else material.material_description
            ),
        }
    else:
        effective = dict(validated)

    material_type = effective['material_type']
    if is_other_material_type(material_type):
        other_type = str(effective.get('other_type') or '').strip()
        if not other_type:
            raise ValidationError(
                {
                    'other_type': [
                        'Other Type is required when Material Type is Other.'
                    ]
                }
            )
        effective['other_type'] = other_type
    else:
        effective['other_type'] = ''

    effective['material_description'] = str(
        effective.get('material_description') or ''
    ).strip()
    return effective


@transaction.atomic
def create_material(*, validated: dict, username: str) -> Material:
    payload = _resolve_effective_fields(material=None, validated=validated, partial=False)
    try:
        return Material.objects.create(
            material_name=payload['material_name'],
            material_unit=payload['material_unit'],
            material_type=payload['material_type'],
            other_type=payload['other_type'],
            material_description=payload['material_description'],
            created_by=username,
            updated_by=username,
        )
    except IntegrityError:
        logger.info(
            'Duplicate material create blocked for name=%s unit=%s type=%s by user=%s',
            payload.get('material_name'),
            payload.get('material_unit'),
            payload.get('material_type'),
            username,
        )
        raise _duplicate_error() from None


@transaction.atomic
def update_material(
    *,
    material: Material,
    validated: dict,
    username: str,
    partial: bool,
) -> Material:
    payload = _resolve_effective_fields(
        material=material,
        validated=validated,
        partial=partial,
    )

    for key in (
        'material_name',
        'material_unit',
        'material_type',
        'other_type',
        'material_description',
    ):
        setattr(material, key, payload[key])

    material.updated_by = username
    try:
        material.save()
    except IntegrityError:
        logger.info(
            'Duplicate material update blocked for id=%s by user=%s',
            material.id,
            username,
        )
        raise _duplicate_error() from None
    return material


@transaction.atomic
def delete_material(*, material: Material) -> None:
    material.delete()
