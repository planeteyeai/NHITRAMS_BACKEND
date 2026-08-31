"""Work Order domain services."""

from __future__ import annotations

import logging
import math
from decimal import Decimal
from typing import Any

from django.db import IntegrityError, transaction
from django.db.models import Q, QuerySet
from rest_framework.exceptions import NotFound, ValidationError

from .constants import (
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    SORTABLE_FIELDS,
    is_other_type_of_work,
)
from .models import WorkOrder

logger = logging.getLogger(__name__)


def get_work_order_or_404(work_order_pk: int) -> WorkOrder:
    try:
        return WorkOrder.objects.get(pk=work_order_pk)
    except WorkOrder.DoesNotExist as exc:
        raise NotFound('Work order not found.') from exc


def _duplicate_error(work_order_id: str | None = None) -> ValidationError:
    return ValidationError(
        {
            'work_order_id': [
                f'Work Order ID already exists: {work_order_id}'.strip(': ')
                if work_order_id
                else 'Work Order ID already exists.'
            ]
        },
        code='duplicate',
    )


def _chainage_range_error() -> ValidationError:
    return ValidationError(
        {
            'chainage_km_to': [
                'Chainage km To must be greater than or equal to Chainage km From.'
            ]
        },
        code='invalid_chainage_range',
    )


def _other_type_required_error() -> ValidationError:
    return ValidationError(
        {
            'other_type': [
                'Other Type is required when Type of Work is Other.'
            ]
        },
        code='other_type_required',
    )


def _resolve_effective_fields(
    *,
    work_order: WorkOrder | None,
    validated: dict,
    partial: bool,
) -> dict:
    if work_order is None:
        effective = dict(validated)
    elif partial:
        effective = {
            'work_order_id': validated.get('work_order_id', work_order.work_order_id),
            'type_of_work': validated.get('type_of_work', work_order.type_of_work),
            'other_type': (
                validated['other_type']
                if 'other_type' in validated
                else work_order.other_type
            ),
            'chainage_km_from': validated.get(
                'chainage_km_from', work_order.chainage_km_from
            ),
            'chainage_km_to': validated.get(
                'chainage_km_to', work_order.chainage_km_to
            ),
        }
    else:
        effective = dict(validated)

    type_of_work = effective['type_of_work']
    if is_other_type_of_work(type_of_work):
        other_type = str(effective.get('other_type') or '').strip()
        if not other_type:
            raise _other_type_required_error()
        effective['other_type'] = other_type
    else:
        effective['other_type'] = ''

    chainage_from = effective['chainage_km_from']
    chainage_to = effective['chainage_km_to']
    if chainage_to < chainage_from:
        raise _chainage_range_error()

    return effective


def _apply_filters(queryset: QuerySet, filters: dict) -> QuerySet:
    type_of_work = filters.get('type_of_work')
    if type_of_work is not None and str(type_of_work).strip() != '':
        queryset = queryset.filter(type_of_work__iexact=str(type_of_work).strip())

    work_order_id = filters.get('work_order_id')
    if work_order_id is not None and str(work_order_id).strip() != '':
        queryset = queryset.filter(work_order_id__iexact=str(work_order_id).strip())

    chainage_from = filters.get('chainage_from')
    if chainage_from is not None:
        queryset = queryset.filter(chainage_km_to__gte=chainage_from)

    chainage_to = filters.get('chainage_to')
    if chainage_to is not None:
        queryset = queryset.filter(chainage_km_from__lte=chainage_to)

    return queryset


def _apply_search(queryset: QuerySet, search: str) -> QuerySet:
    text = (search or '').strip()
    if not text:
        return queryset
    return queryset.filter(
        Q(work_order_id__icontains=text)
        | Q(type_of_work__icontains=text)
        | Q(other_type__icontains=text)
    )


def _apply_sort(queryset: QuerySet, sort_by: str, sort_order: str) -> QuerySet:
    field = (sort_by or 'created_at').strip()
    if field not in SORTABLE_FIELDS:
        field = 'created_at'
    prefix = '' if (sort_order or 'desc').lower() == 'asc' else '-'
    return queryset.order_by(f'{prefix}{field}', '-id')


def list_work_orders(*, query: dict) -> dict[str, Any]:
    page = int(query.get('page') or 1)
    page_size = int(query.get('page_size') or DEFAULT_PAGE_SIZE)
    page_size = min(max(page_size, 1), MAX_PAGE_SIZE)
    page = max(page, 1)

    queryset = WorkOrder.objects.all()
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


@transaction.atomic
def create_work_order(*, validated: dict, username: str) -> WorkOrder:
    payload = _resolve_effective_fields(
        work_order=None,
        validated=validated,
        partial=False,
    )
    try:
        return WorkOrder.objects.create(
            work_order_id=payload['work_order_id'],
            type_of_work=payload['type_of_work'],
            other_type=payload['other_type'],
            chainage_km_from=payload['chainage_km_from'],
            chainage_km_to=payload['chainage_km_to'],
            created_by=username,
            updated_by=username,
        )
    except IntegrityError:
        logger.info(
            'Duplicate work order create blocked for work_order_id=%s by user=%s',
            payload.get('work_order_id'),
            username,
        )
        raise _duplicate_error(payload.get('work_order_id')) from None


@transaction.atomic
def update_work_order(
    *,
    work_order: WorkOrder,
    validated: dict,
    username: str,
    partial: bool,
) -> WorkOrder:
    payload = _resolve_effective_fields(
        work_order=work_order,
        validated=validated,
        partial=partial,
    )

    for key in (
        'work_order_id',
        'type_of_work',
        'other_type',
        'chainage_km_from',
        'chainage_km_to',
    ):
        setattr(work_order, key, payload[key])

    work_order.updated_by = username
    try:
        work_order.save()
    except IntegrityError:
        logger.info(
            'Duplicate work order update blocked for id=%s work_order_id=%s by user=%s',
            work_order.id,
            payload.get('work_order_id'),
            username,
        )
        raise _duplicate_error(payload.get('work_order_id')) from None
    return work_order


@transaction.atomic
def delete_work_order(*, work_order: WorkOrder) -> None:
    work_order.delete()
