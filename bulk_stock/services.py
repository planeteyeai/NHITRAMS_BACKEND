"""Bulk stock domain services."""

from __future__ import annotations

import logging
import math
from decimal import Decimal
from typing import Any

from django.db import IntegrityError, transaction
from django.db.models import Prefetch, Q, QuerySet
from django.utils import timezone
from rest_framework.exceptions import NotFound, ValidationError

from materials.models import Material

from .constants import (
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    SORTABLE_FIELDS,
    TRANSACTION_TYPE_INWARD,
    TRANSACTION_TYPE_OUTWARD,
)
from .models import (
    BulkStockTransaction,
    BulkStockTransactionItem,
    MaterialStock,
    MaterialStockMovement,
)

logger = logging.getLogger(__name__)

_ITEM_PREFETCH = Prefetch(
    'items',
    queryset=BulkStockTransactionItem.objects.select_related('material').order_by('id'),
)


class InsufficientStockError(ValidationError):
    def __init__(self, material_name: str):
        super().__init__(
            {
                'products': [
                    f'Insufficient stock for {material_name}.'
                ]
            },
            code='insufficient_stock',
        )


def _duplicate_material_error() -> ValidationError:
    return ValidationError(
        {
            'products': [
                'Duplicate material entries in the same transaction are not allowed.'
            ]
        },
        code='duplicate_material',
    )


def _base_queryset(*, transaction_type: str) -> QuerySet:
    return (
        BulkStockTransaction.objects.filter(transaction_type=transaction_type)
        .prefetch_related(_ITEM_PREFETCH)
    )


def get_transaction_or_404(*, transaction_id: int, transaction_type: str) -> BulkStockTransaction:
    try:
        return _base_queryset(transaction_type=transaction_type).get(pk=transaction_id)
    except BulkStockTransaction.DoesNotExist as exc:
        raise NotFound('Bulk stock transaction not found.') from exc


def _validate_product_pairs(products: list[dict]) -> None:
    seen: set[int] = set()
    for item in products:
        material_id = item['material_id']
        if material_id in seen:
            raise _duplicate_material_error()
        seen.add(material_id)


def _signed_delta(*, transaction_type: str, quantity: Decimal) -> Decimal:
    if transaction_type == TRANSACTION_TYPE_INWARD:
        return quantity
    return -quantity


def _lock_stocks(material_ids: list[int]) -> dict[int, MaterialStock]:
    if not material_ids:
        return {}
    sorted_ids = sorted(material_ids)
    stocks: dict[int, MaterialStock] = {
        row.material_id: row
        for row in MaterialStock.objects.select_for_update()
        .select_related('material')
        .filter(material_id__in=sorted_ids)
    }
    missing_ids = [material_id for material_id in sorted_ids if material_id not in stocks]
    if missing_ids:
        try:
            MaterialStock.objects.bulk_create(
                [
                    MaterialStock(material_id=material_id, quantity=Decimal('0'))
                    for material_id in missing_ids
                ]
            )
        except IntegrityError:
            pass
        for row in (
            MaterialStock.objects.select_for_update()
            .select_related('material')
            .filter(material_id__in=missing_ids)
        ):
            stocks[row.material_id] = row
    return {material_id: stocks[material_id] for material_id in sorted_ids}


def _apply_stock_deltas(
    *,
    deltas: dict[int, Decimal],
    transaction_type: str,
    bulk_transaction: BulkStockTransaction | None,
    username: str,
) -> None:
    material_ids = sorted(deltas.keys())
    stocks = _lock_stocks(material_ids)

    for material_id in material_ids:
        delta = deltas[material_id]
        stock = stocks[material_id]
        new_qty = stock.quantity + delta
        if new_qty < 0:
            raise InsufficientStockError(stock.material.material_name)

    for material_id in material_ids:
        delta = deltas[material_id]
        stock = stocks[material_id]
        stock.quantity += delta

    if material_ids:
        now = timezone.now()
        stocks_to_update = [stocks[material_id] for material_id in material_ids]
        for stock in stocks_to_update:
            stock.updated_at = now
        MaterialStock.objects.bulk_update(stocks_to_update, ['quantity', 'updated_at'])

    movement_objects = [
        MaterialStockMovement(
            material_id=material_id,
            transaction=bulk_transaction,
            transaction_type=transaction_type,
            quantity_delta=deltas[material_id],
            balance_after=stocks[material_id].quantity,
            created_by=username,
        )
        for material_id in material_ids
    ]
    if movement_objects:
        MaterialStockMovement.objects.bulk_create(movement_objects)


def _items_to_deltas(
    items: list[BulkStockTransactionItem],
    transaction_type: str,
) -> dict[int, Decimal]:
    deltas: dict[int, Decimal] = {}
    for item in items:
        material_id = item.material_id
        signed = _signed_delta(transaction_type=transaction_type, quantity=item.quantity)
        deltas[material_id] = deltas.get(material_id, Decimal('0')) + signed
    return deltas


def _products_to_deltas(products: list[dict], transaction_type: str) -> dict[int, Decimal]:
    deltas: dict[int, Decimal] = {}
    for item in products:
        material_id = item['material_id']
        qty = Decimal(str(item['quantity']))
        signed = _signed_delta(transaction_type=transaction_type, quantity=qty)
        deltas[material_id] = deltas.get(material_id, Decimal('0')) + signed
    return deltas


def _reverse_transaction_stock(
    bulk_transaction: BulkStockTransaction,
    *,
    username: str,
) -> None:
    items = list(bulk_transaction.items.select_related('material'))
    if not items:
        return
    deltas = _items_to_deltas(items, bulk_transaction.transaction_type)
    reversed_deltas = {mid: -delta for mid, delta in deltas.items()}
    _apply_stock_deltas(
        deltas=reversed_deltas,
        transaction_type=bulk_transaction.transaction_type,
        bulk_transaction=bulk_transaction,
        username=username,
    )


def _apply_transaction_stock(
    bulk_transaction: BulkStockTransaction,
    *,
    username: str,
) -> None:
    items = list(bulk_transaction.items.select_related('material'))
    if not items:
        return
    deltas = _items_to_deltas(items, bulk_transaction.transaction_type)
    _apply_stock_deltas(
        deltas=deltas,
        transaction_type=bulk_transaction.transaction_type,
        bulk_transaction=bulk_transaction,
        username=username,
    )


def _sync_items(
    *,
    bulk_transaction: BulkStockTransaction,
    products: list[dict],
    replace_all: bool,
) -> None:
    _validate_product_pairs(products)

    if replace_all:
        payload_ids = {item['id'] for item in products if item.get('id')}
        bulk_transaction.items.exclude(id__in=payload_ids).delete()

    existing = {item.id: item for item in bulk_transaction.items.all()}
    keep_ids: set[int] = set()
    material_ids = {payload['material_id'] for payload in products}
    material_map = {
        material.id: material
        for material in Material.objects.filter(pk__in=material_ids)
    }

    for payload in products:
        item_id = payload.get('id')
        material = material_map[payload['material_id']]
        quantity = Decimal(str(payload['quantity']))

        if item_id:
            item = existing.get(item_id)
            if item is None or item.transaction_id != bulk_transaction.id:
                raise ValidationError(
                    {'products': [f'Product item with id {item_id} was not found.']}
                )
            item.material = material
            item.quantity = quantity
            try:
                item.save()
            except IntegrityError as exc:
                raise _duplicate_material_error() from exc
            keep_ids.add(item.id)
            continue

        try:
            created = BulkStockTransactionItem.objects.create(
                transaction=bulk_transaction,
                material=material,
                quantity=quantity,
            )
        except IntegrityError as exc:
            raise _duplicate_material_error() from exc
        keep_ids.add(created.id)

    if replace_all:
        bulk_transaction.items.exclude(id__in=keep_ids).delete()


def _apply_filters(queryset: QuerySet, filters: dict) -> QuerySet:
    material_id = filters.get('material_id')
    if material_id is not None:
        queryset = queryset.filter(items__material_id=material_id).distinct()
    return queryset


def _apply_search(queryset: QuerySet, search: str) -> QuerySet:
    text = (search or '').strip()
    if not text:
        return queryset
    return queryset.filter(
        Q(items__material__material_name__icontains=text)
    ).distinct()


def _apply_sort(queryset: QuerySet, sort_by: str, sort_order: str) -> QuerySet:
    field = (sort_by or 'created_at').strip()
    if field not in SORTABLE_FIELDS:
        field = 'created_at'
    prefix = '' if (sort_order or 'desc').lower() == 'asc' else '-'
    return queryset.order_by(f'{prefix}{field}', '-id')


def list_transactions(*, transaction_type: str, query: dict) -> dict[str, Any]:
    page = int(query.get('page') or 1)
    page_size = int(query.get('page_size') or DEFAULT_PAGE_SIZE)
    page_size = min(max(page_size, 1), MAX_PAGE_SIZE)
    page = max(page, 1)

    queryset = _base_queryset(transaction_type=transaction_type)
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
def create_bulk_transaction(
    *,
    transaction_type: str,
    validated: dict,
    username: str,
) -> BulkStockTransaction:
    products = validated['products']
    _validate_product_pairs(products)

    bulk_transaction = BulkStockTransaction.objects.create(
        transaction_type=transaction_type,
        created_by=username,
        updated_by=username,
    )
    BulkStockTransactionItem.objects.bulk_create(
        [
            BulkStockTransactionItem(
                transaction=bulk_transaction,
                material_id=payload['material_id'],
                quantity=payload['quantity'],
            )
            for payload in products
        ]
    )
    _apply_transaction_stock(bulk_transaction, username=username)
    return get_transaction_or_404(
        transaction_id=bulk_transaction.id,
        transaction_type=transaction_type,
    )


@transaction.atomic
def update_bulk_transaction(
    *,
    bulk_transaction: BulkStockTransaction,
    validated: dict,
    username: str,
    partial: bool,
) -> BulkStockTransaction:
    transaction_type = bulk_transaction.transaction_type

    if 'products' not in validated and partial:
        bulk_transaction.updated_by = username
        bulk_transaction.save(update_fields=['updated_by', 'updated_at'])
        return get_transaction_or_404(
            transaction_id=bulk_transaction.id,
            transaction_type=transaction_type,
        )

    _reverse_transaction_stock(bulk_transaction, username=username)

    replace_all = not partial
    _sync_items(
        bulk_transaction=bulk_transaction,
        products=validated['products'],
        replace_all=replace_all,
    )

    bulk_transaction.updated_by = username
    bulk_transaction.save(update_fields=['updated_by', 'updated_at'])

    _apply_transaction_stock(bulk_transaction, username=username)

    return get_transaction_or_404(
        transaction_id=bulk_transaction.id,
        transaction_type=transaction_type,
    )


@transaction.atomic
def delete_bulk_transaction(*, bulk_transaction: BulkStockTransaction, username: str) -> None:
    _reverse_transaction_stock(bulk_transaction, username=username)
    bulk_transaction.delete()


def get_material_stock(material_id: int) -> Decimal:
    try:
        return MaterialStock.objects.get(material_id=material_id).quantity
    except MaterialStock.DoesNotExist:
        return Decimal('0')
