"""Purchase Invoice domain services."""

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
from .models import PurchaseInvoice, PurchaseInvoiceItem

logger = logging.getLogger(__name__)

_ITEM_PREFETCH = Prefetch(
    'items',
    queryset=PurchaseInvoiceItem.objects.select_related('material').order_by('id'),
)


def _base_queryset() -> QuerySet:
    return PurchaseInvoice.objects.select_related('supplier').prefetch_related(
        _ITEM_PREFETCH
    )


def get_purchase_invoice_or_404(purchase_invoice_id: int) -> PurchaseInvoice:
    try:
        return _base_queryset().get(pk=purchase_invoice_id)
    except PurchaseInvoice.DoesNotExist as exc:
        raise NotFound('Purchase invoice not found.') from exc


def _apply_filters(queryset: QuerySet, filters: dict) -> QuerySet:
    supplier_id = filters.get('supplier_id')
    if supplier_id is not None:
        queryset = queryset.filter(supplier_id=supplier_id)

    invoice_date = filters.get('invoice_date')
    if invoice_date is not None:
        queryset = queryset.filter(invoice_date=invoice_date)

    material_id = filters.get('material_id')
    if material_id is not None:
        queryset = queryset.filter(items__material_id=material_id).distinct()

    invoice_number = filters.get('invoice_number')
    if invoice_number is not None and str(invoice_number).strip() != '':
        queryset = queryset.filter(
            invoice_number__iexact=str(invoice_number).strip()
        )

    return queryset


def _apply_search(queryset: QuerySet, search: str) -> QuerySet:
    text = (search or '').strip()
    if not text:
        return queryset
    return queryset.filter(
        Q(invoice_number__icontains=text)
        | Q(supplier__company_name__icontains=text)
        | Q(items__material__material_name__icontains=text)
        | Q(items__batch_number__icontains=text)
    ).distinct()


def _apply_sort(queryset: QuerySet, sort_by: str, sort_order: str) -> QuerySet:
    field = (sort_by or 'invoice_date').strip()
    if field not in SORTABLE_FIELDS:
        field = 'invoice_date'
    prefix = '' if (sort_order or 'desc').lower() == 'asc' else '-'
    return queryset.order_by(f'{prefix}{field}', '-id')


def list_purchase_invoices(*, query: dict) -> dict[str, Any]:
    page = int(query.get('page') or 1)
    page_size = int(query.get('page_size') or DEFAULT_PAGE_SIZE)
    page_size = min(max(page_size, 1), MAX_PAGE_SIZE)
    page = max(page, 1)

    queryset = _base_queryset()
    queryset = _apply_filters(queryset, query)
    queryset = _apply_search(queryset, query.get('search') or '')
    queryset = _apply_sort(
        queryset,
        query.get('sort_by') or 'invoice_date',
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


def _duplicate_invoice_error(invoice_number: str | None = None) -> ValidationError:
    message = 'A purchase invoice with this supplier and invoice number already exists.'
    if invoice_number:
        return ValidationError(
            {
                'invoice_number': [
                    f'Invoice number already exists for this supplier: {invoice_number}'
                ]
            },
            code='duplicate',
        )
    return ValidationError({'invoice_number': [message]}, code='duplicate')


def _duplicate_products_error() -> ValidationError:
    return ValidationError(
        {
            'products': [
                'Duplicate product entries with the same material and batch number '
                'are not allowed.'
            ]
        },
        code='duplicate',
    )


def _validate_product_pairs(products: list[dict]) -> None:
    seen: set[tuple[int, str]] = set()
    for item in products:
        key = (item['material_id'], item['batch_number'])
        if key in seen:
            raise _duplicate_products_error()
        seen.add(key)


def _resolve_header_fields(
    *,
    purchase_invoice: PurchaseInvoice | None,
    validated: dict,
    partial: bool,
) -> dict:
    if purchase_invoice is None:
        return {
            'supplier_id': validated['supplier_id'],
            'invoice_date': validated['invoice_date'],
            'invoice_number': validated['invoice_number'],
        }
    if partial:
        return {
            'supplier_id': validated.get('supplier_id', purchase_invoice.supplier_id),
            'invoice_date': validated.get('invoice_date', purchase_invoice.invoice_date),
            'invoice_number': validated.get(
                'invoice_number', purchase_invoice.invoice_number
            ),
        }
    return {
        'supplier_id': validated['supplier_id'],
        'invoice_date': validated['invoice_date'],
        'invoice_number': validated['invoice_number'],
    }


def _sync_products(
    *,
    purchase_invoice: PurchaseInvoice,
    products: list[dict],
    replace_all: bool,
) -> None:
    _validate_product_pairs(products)

    if replace_all:
        payload_ids = {item['id'] for item in products if item.get('id')}
        purchase_invoice.items.exclude(id__in=payload_ids).delete()

    existing = {item.id: item for item in purchase_invoice.items.all()}
    keep_ids: set[int] = set()

    material_ids = {payload['material_id'] for payload in products}
    material_map = {
        material.id: material
        for material in Material.objects.filter(pk__in=material_ids)
    }

    for payload in products:
        item_id = payload.get('id')
        material_id = payload['material_id']
        try:
            material = material_map[material_id]
        except KeyError as exc:
            raise Material.DoesNotExist(
                'Material matching query does not exist.'
            ) from exc

        if item_id:
            item = existing.get(item_id)
            if item is None or item.purchase_invoice_id != purchase_invoice.id:
                raise ValidationError(
                    {'products': [f'Product item with id {item_id} was not found.']}
                )
            item.material = material
            item.quantity = payload['quantity']
            item.rate = payload['rate']
            item.batch_number = payload['batch_number']
            try:
                item.save()
            except IntegrityError as exc:
                raise _duplicate_products_error() from exc
            keep_ids.add(item.id)
            continue

        try:
            created = PurchaseInvoiceItem.objects.create(
                purchase_invoice=purchase_invoice,
                material=material,
                quantity=payload['quantity'],
                rate=payload['rate'],
                batch_number=payload['batch_number'],
            )
        except IntegrityError as exc:
            raise _duplicate_products_error() from exc
        keep_ids.add(created.id)

    if replace_all:
        purchase_invoice.items.exclude(id__in=keep_ids).delete()


@transaction.atomic
def create_purchase_invoice(*, validated: dict, username: str) -> PurchaseInvoice:
    products = validated['products']
    _validate_product_pairs(products)

    try:
        purchase_invoice = PurchaseInvoice.objects.create(
            supplier_id=validated['supplier_id'],
            invoice_date=validated['invoice_date'],
            invoice_number=validated['invoice_number'],
            created_by=username,
            updated_by=username,
        )
    except IntegrityError:
        logger.info(
            'Duplicate purchase invoice create blocked for supplier_id=%s invoice=%s by user=%s',
            validated.get('supplier_id'),
            validated.get('invoice_number'),
            username,
        )
        raise _duplicate_invoice_error(validated.get('invoice_number')) from None

    try:
        PurchaseInvoiceItem.objects.bulk_create(
            [
                PurchaseInvoiceItem(
                    purchase_invoice=purchase_invoice,
                    material_id=payload['material_id'],
                    quantity=payload['quantity'],
                    rate=payload['rate'],
                    batch_number=payload['batch_number'],
                )
                for payload in products
            ]
        )
    except IntegrityError as exc:
        logger.info(
            'Purchase invoice item create rolled back for invoice=%s by user=%s',
            validated.get('invoice_number'),
            username,
        )
        raise _duplicate_products_error() from exc

    return get_purchase_invoice_or_404(purchase_invoice.id)


@transaction.atomic
def update_purchase_invoice(
    *,
    purchase_invoice: PurchaseInvoice,
    validated: dict,
    username: str,
    partial: bool,
) -> PurchaseInvoice:
    header = _resolve_header_fields(
        purchase_invoice=purchase_invoice,
        validated=validated,
        partial=partial,
    )

    if not Supplier.objects.filter(pk=header['supplier_id']).exists():
        raise ValidationError({'supplier_id': ['Supplier not found.']})

    purchase_invoice.supplier_id = header['supplier_id']
    purchase_invoice.invoice_date = header['invoice_date']
    purchase_invoice.invoice_number = header['invoice_number']
    purchase_invoice.updated_by = username

    try:
        purchase_invoice.save()
    except IntegrityError:
        logger.info(
            'Duplicate purchase invoice update blocked for id=%s by user=%s',
            purchase_invoice.id,
            username,
        )
        raise _duplicate_invoice_error(header['invoice_number']) from None

    if 'products' in validated:
        replace_all = not partial
        _sync_products(
            purchase_invoice=purchase_invoice,
            products=validated['products'],
            replace_all=replace_all,
        )

    return get_purchase_invoice_or_404(purchase_invoice.id)


@transaction.atomic
def delete_purchase_invoice(*, purchase_invoice: PurchaseInvoice) -> None:
    purchase_invoice.delete()
