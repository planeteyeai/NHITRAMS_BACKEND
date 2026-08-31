"""Equipment domain services — list/create/update/delete business logic."""

from __future__ import annotations

import logging
import math
from typing import Any

from django.db import IntegrityError, transaction
from django.db.models import Q, QuerySet
from rest_framework.exceptions import NotFound, ValidationError

from .constants import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, SORTABLE_FIELDS
from .models import Equipment

logger = logging.getLogger(__name__)


def get_equipment_or_404(equipment_id: int) -> Equipment:
    try:
        return Equipment.objects.get(pk=equipment_id)
    except Equipment.DoesNotExist as exc:
        raise NotFound('Equipment not found.') from exc


def _apply_filters(queryset: QuerySet, filters: dict) -> QuerySet:
    status_value = filters.get('status')
    if status_value is not None and str(status_value).strip() != '':
        queryset = queryset.filter(status__iexact=str(status_value).strip())

    year_of_make = filters.get('year_of_make')
    if year_of_make is not None:
        queryset = queryset.filter(year_of_make=year_of_make)

    equipment_code = filters.get('equipment_code')
    if equipment_code is not None and str(equipment_code).strip() != '':
        queryset = queryset.filter(equipment_code__iexact=str(equipment_code).strip())

    return queryset


def _apply_search(queryset: QuerySet, search: str) -> QuerySet:
    text = (search or '').strip()
    if not text:
        return queryset
    return queryset.filter(
        Q(item_name__icontains=text)
        | Q(make_and_model__icontains=text)
        | Q(equipment_code__icontains=text)
        | Q(status__icontains=text)
        | Q(remark__icontains=text)
    )


def _apply_sort(queryset: QuerySet, sort_by: str, sort_order: str) -> QuerySet:
    field = (sort_by or 'created_at').strip()
    if field not in SORTABLE_FIELDS:
        field = 'created_at'
    prefix = '' if (sort_order or 'desc').lower() == 'asc' else '-'
    return queryset.order_by(f'{prefix}{field}', '-id')


def list_equipments(*, query: dict) -> dict[str, Any]:
    page = int(query.get('page') or 1)
    page_size = int(query.get('page_size') or DEFAULT_PAGE_SIZE)
    page_size = min(max(page_size, 1), MAX_PAGE_SIZE)
    page = max(page, 1)

    queryset = Equipment.objects.all()
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


def _duplicate_error(equipment_code: str | None = None) -> ValidationError:
    code = equipment_code or ''
    return ValidationError(
        {
            'equipment_code': [
                f'Equipment code already exists: {code}'.strip(': ')
                if code
                else 'Equipment code already exists.'
            ]
        },
        code='duplicate',
    )


@transaction.atomic
def create_equipment(*, validated: dict, username: str) -> Equipment:
    try:
        return Equipment.objects.create(
            item_name=validated['item_name'],
            year_of_make=validated['year_of_make'],
            make_and_model=validated['make_and_model'],
            equipment_code=validated['equipment_code'],
            estimate_cost=validated['estimate_cost'],
            status=validated['status'],
            remark=validated['remark'],
            created_by=username,
            updated_by=username,
        )
    except IntegrityError:
        logger.info(
            'Duplicate equipment create blocked for code=%s by user=%s',
            validated.get('equipment_code'),
            username,
        )
        raise _duplicate_error(validated.get('equipment_code')) from None


@transaction.atomic
def update_equipment(
    *,
    equipment: Equipment,
    validated: dict,
    username: str,
    partial: bool,
) -> Equipment:
    fields = [
        'item_name',
        'year_of_make',
        'make_and_model',
        'equipment_code',
        'estimate_cost',
        'status',
        'remark',
    ]
    if partial:
        for key in fields:
            if key in validated:
                setattr(equipment, key, validated[key])
    else:
        for key in fields:
            setattr(equipment, key, validated[key])

    equipment.updated_by = username
    try:
        equipment.save()
    except IntegrityError:
        logger.info(
            'Duplicate equipment update blocked for id=%s code=%s by user=%s',
            equipment.id,
            validated.get('equipment_code', equipment.equipment_code),
            username,
        )
        raise _duplicate_error(
            validated.get('equipment_code', equipment.equipment_code)
        ) from None
    return equipment


@transaction.atomic
def delete_equipment(*, equipment: Equipment) -> None:
    equipment.delete()
