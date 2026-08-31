"""Transfer Stock domain services."""

from __future__ import annotations

import logging
import math
from typing import Any

from django.db import IntegrityError, transaction
from django.db.models import Prefetch, Q, QuerySet
from rest_framework.exceptions import NotFound, ValidationError

from materials.models import Material
from supplier.models import Supplier

from .constants import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, SORTABLE_FIELDS
from .models import Batch, TransferStock, TransferStockItem

logger = logging.getLogger(__name__)

_ITEM_PREFETCH = Prefetch(
    'items',
    queryset=TransferStockItem.objects.select_related('material', 'batch').order_by('id'),
)


def _base_queryset() -> QuerySet:
    return TransferStock.objects.select_related('supplier').prefetch_related(_ITEM_PREFETCH)


def get_transfer_stock_or_404(transfer_stock_id: int) -> TransferStock:
    try:
        return _base_queryset().get(pk=transfer_stock_id)
    except TransferStock.DoesNotExist as exc:
        raise NotFound('Transfer stock not found.') from exc


def _apply_filters(queryset: QuerySet, filters: dict) -> QuerySet:
    supplier_id = filters.get('supplier_id')
    if supplier_id is not None:
        queryset = queryset.filter(supplier_id=supplier_id)

    date_value = filters.get('date')
    if date_value is not None:
        queryset = queryset.filter(date=date_value)

    location = filters.get('location')
    if location is not None and str(location).strip() != '':
        queryset = queryset.filter(location__icontains=str(location).strip())

    material_id = filters.get('material_id')
    if material_id is not None:
        queryset = queryset.filter(items__material_id=material_id).distinct()

    batch_id = filters.get('batch_id')
    if batch_id is not None:
        queryset = queryset.filter(items__batch_id=batch_id).distinct()

    return queryset


def _apply_search(queryset: QuerySet, search: str) -> QuerySet:
    text = (search or '').strip()
    if not text:
        return queryset
    return queryset.filter(
        Q(location__icontains=text)
        | Q(remark__icontains=text)
        | Q(supplier__company_name__icontains=text)
        | Q(items__material__material_name__icontains=text)
    ).distinct()


def _apply_sort(queryset: QuerySet, sort_by: str, sort_order: str) -> QuerySet:
    field = (sort_by or 'date').strip()
    if field not in SORTABLE_FIELDS:
        field = 'date'
    prefix = '' if (sort_order or 'desc').lower() == 'asc' else '-'
    return queryset.order_by(f'{prefix}{field}', '-id')


def list_transfer_stocks(*, query: dict) -> dict[str, Any]:
    page = int(query.get('page') or 1)
    page_size = int(query.get('page_size') or DEFAULT_PAGE_SIZE)
    page_size = min(max(page_size, 1), MAX_PAGE_SIZE)
    page = max(page, 1)

    queryset = _base_queryset()
    queryset = _apply_filters(queryset, query)
    queryset = _apply_search(queryset, query.get('search') or '')
    queryset = _apply_sort(
        queryset,
        query.get('sort_by') or 'date',
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


def _duplicate_products_error() -> ValidationError:
    return ValidationError(
        {
            'products': [
                'Duplicate product entries with the same material and batch are not allowed.'
            ]
        },
        code='duplicate',
    )


def _validate_product_pairs(products: list[dict]) -> None:
    seen: set[tuple[int, int]] = set()
    for item in products:
        key = (item['material_id'], item['batch_id'])
        if key in seen:
            raise _duplicate_products_error()
        seen.add(key)


def _resolve_header_fields(
    *,
    transfer_stock: TransferStock | None,
    validated: dict,
    partial: bool,
) -> dict:
    if transfer_stock is None:
        return {
            'supplier_id': validated['supplier_id'],
            'date': validated['date'],
            'location': validated['location'],
            'remark': validated.get('remark', ''),
        }
    if partial:
        return {
            'supplier_id': validated.get('supplier_id', transfer_stock.supplier_id),
            'date': validated.get('date', transfer_stock.date),
            'location': validated.get('location', transfer_stock.location),
            'remark': (
                validated['remark'] if 'remark' in validated else transfer_stock.remark
            ),
        }
    return {
        'supplier_id': validated['supplier_id'],
        'date': validated['date'],
        'location': validated['location'],
        'remark': validated.get('remark', ''),
    }


def _sync_products(
    *,
    transfer_stock: TransferStock,
    products: list[dict],
    replace_all: bool,
) -> None:
    _validate_product_pairs(products)

    if replace_all:
        payload_ids = {item['id'] for item in products if item.get('id')}
        transfer_stock.items.exclude(id__in=payload_ids).delete()

    existing = {item.id: item for item in transfer_stock.items.all()}
    keep_ids: set[int] = set()

    material_ids = {payload['material_id'] for payload in products}
    batch_ids = {payload['batch_id'] for payload in products}
    material_map = {
        material.id: material
        for material in Material.objects.filter(pk__in=material_ids)
    }
    batch_map = {batch.id: batch for batch in Batch.objects.filter(pk__in=batch_ids)}

    for payload in products:
        item_id = payload.get('id')
        material_id = payload['material_id']
        batch_id = payload['batch_id']
        try:
            material = material_map[material_id]
        except KeyError as exc:
            raise Material.DoesNotExist(
                'Material matching query does not exist.'
            ) from exc
        try:
            batch = batch_map[batch_id]
        except KeyError as exc:
            raise Batch.DoesNotExist(
                'Batch matching query does not exist.'
            ) from exc

        if item_id:
            item = existing.get(item_id)
            if item is None or item.transfer_stock_id != transfer_stock.id:
                raise ValidationError(
                    {'products': [f'Product item with id {item_id} was not found.']}
                )
            item.material = material
            item.quantity = payload['quantity']
            item.rate = payload['rate']
            item.batch = batch
            try:
                item.save()
            except IntegrityError as exc:
                raise _duplicate_products_error() from exc
            keep_ids.add(item.id)
            continue

        try:
            created = TransferStockItem.objects.create(
                transfer_stock=transfer_stock,
                material=material,
                quantity=payload['quantity'],
                rate=payload['rate'],
                batch=batch,
            )
        except IntegrityError as exc:
            raise _duplicate_products_error() from exc
        keep_ids.add(created.id)

    if replace_all:
        transfer_stock.items.exclude(id__in=keep_ids).delete()


@transaction.atomic
def create_transfer_stock(*, validated: dict, username: str) -> TransferStock:
    products = validated['products']
    _validate_product_pairs(products)

    transfer_stock = TransferStock.objects.create(
        supplier_id=validated['supplier_id'],
        date=validated['date'],
        location=validated['location'],
        remark=validated.get('remark', ''),
        created_by=username,
        updated_by=username,
    )

    try:
        TransferStockItem.objects.bulk_create(
            [
                TransferStockItem(
                    transfer_stock=transfer_stock,
                    material_id=payload['material_id'],
                    quantity=payload['quantity'],
                    rate=payload['rate'],
                    batch_id=payload['batch_id'],
                )
                for payload in products
            ]
        )
    except IntegrityError as exc:
        logger.info(
            'Transfer stock create rolled back for supplier_id=%s by user=%s',
            validated.get('supplier_id'),
            username,
        )
        raise _duplicate_products_error() from exc

    return get_transfer_stock_or_404(transfer_stock.id)


@transaction.atomic
def update_transfer_stock(
    *,
    transfer_stock: TransferStock,
    validated: dict,
    username: str,
    partial: bool,
) -> TransferStock:
    header = _resolve_header_fields(
        transfer_stock=transfer_stock,
        validated=validated,
        partial=partial,
    )

    if not Supplier.objects.filter(pk=header['supplier_id']).exists():
        raise ValidationError({'supplier_id': ['Supplier not found.']})

    transfer_stock.supplier_id = header['supplier_id']
    transfer_stock.date = header['date']
    transfer_stock.location = header['location']
    transfer_stock.remark = header['remark']
    transfer_stock.updated_by = username
    transfer_stock.save()

    if 'products' in validated:
        replace_all = not partial
        _sync_products(
            transfer_stock=transfer_stock,
            products=validated['products'],
            replace_all=replace_all,
        )

    return get_transfer_stock_or_404(transfer_stock.id)


@transaction.atomic
def delete_transfer_stock(*, transfer_stock: TransferStock) -> None:
    transfer_stock.delete()
