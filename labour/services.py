"""Labour domain services — list/create/update/delete business logic."""

from __future__ import annotations

import logging
import math
from typing import Any

from django.db import IntegrityError, transaction
from django.db.models import Q, QuerySet
from rest_framework.exceptions import NotFound, ValidationError

from .constants import (
    DEFAULT_PAGE_SIZE,
    GENDERS,
    LABOUR_TYPES,
    MAX_PAGE_SIZE,
    SORTABLE_FIELDS,
)
from .models import Labour

logger = logging.getLogger(__name__)


def get_labour_masters() -> dict:
    """Dropdown values for Add Labour form."""
    return {
        'labour_types': [{'id': value, 'name': value} for value in LABOUR_TYPES],
        'genders': [{'id': value, 'name': value} for value in GENDERS],
    }


def get_labour_or_404(labour_id: int) -> Labour:
    try:
        return Labour.objects.get(pk=labour_id)
    except Labour.DoesNotExist as exc:
        raise NotFound('Labour not found.') from exc


def _apply_filters(queryset: QuerySet, filters: dict) -> QuerySet:
    mapping = {
        'first_name': 'first_name__icontains',
        'last_name': 'last_name__icontains',
        'crew_id': 'crew_id__iexact',
        'labour_type': 'labour_type__iexact',
        'gender': 'gender__iexact',
        'contact_number': 'contact_number__icontains',
    }
    for key, lookup in mapping.items():
        value = filters.get(key)
        if value is not None and str(value).strip() != '':
            queryset = queryset.filter(**{lookup: str(value).strip()})
    return queryset


def _apply_search(queryset: QuerySet, search: str) -> QuerySet:
    text = (search or '').strip()
    if not text:
        return queryset
    return queryset.filter(
        Q(first_name__icontains=text)
        | Q(last_name__icontains=text)
        | Q(crew_id__icontains=text)
        | Q(contact_number__icontains=text)
        | Q(address__icontains=text)
        | Q(labour_type__icontains=text)
    )


def _apply_sort(queryset: QuerySet, sort_by: str, sort_order: str) -> QuerySet:
    field = (sort_by or 'created_at').strip()
    if field not in SORTABLE_FIELDS:
        field = 'created_at'
    prefix = '' if (sort_order or 'desc').lower() == 'asc' else '-'
    return queryset.order_by(f'{prefix}{field}', '-id')


def list_labours(*, query: dict) -> dict[str, Any]:
    """Paginated, filtered, searchable labour list (DB-level)."""
    page = int(query.get('page') or 1)
    page_size = int(query.get('page_size') or DEFAULT_PAGE_SIZE)
    page_size = min(max(page_size, 1), MAX_PAGE_SIZE)
    page = max(page, 1)

    queryset = Labour.objects.all()
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
                'A labour with the same crew_id, first_name, last_name, '
                'and date_of_birth already exists.'
            ]
        }
    )


@transaction.atomic
def create_labour(*, validated: dict, username: str) -> Labour:
    try:
        return Labour.objects.create(
            first_name=validated['first_name'],
            last_name=validated['last_name'],
            date_of_birth=validated['date_of_birth'],
            labour_type=validated['labour_type'],
            crew_id=validated['crew_id'],
            gender=validated['gender'],
            contact_number=validated['contact_number'],
            address=validated['address'],
            created_by=username,
            updated_by=username,
        )
    except IntegrityError:
        logger.info(
            'Duplicate labour create blocked for crew_id=%s by user=%s',
            validated.get('crew_id'),
            username,
        )
        raise _duplicate_error() from None


@transaction.atomic
def update_labour(
    *,
    labour: Labour,
    validated: dict,
    username: str,
    partial: bool,
) -> Labour:
    fields = [
        'first_name',
        'last_name',
        'date_of_birth',
        'labour_type',
        'crew_id',
        'gender',
        'contact_number',
        'address',
    ]
    if partial:
        for key in fields:
            if key in validated:
                setattr(labour, key, validated[key])
    else:
        for key in fields:
            setattr(labour, key, validated[key])

    labour.updated_by = username
    try:
        labour.save()
    except IntegrityError:
        logger.info(
            'Duplicate labour update blocked for id=%s by user=%s',
            labour.id,
            username,
        )
        raise _duplicate_error() from None
    return labour


@transaction.atomic
def delete_labour(*, labour: Labour) -> None:
    labour.delete()
