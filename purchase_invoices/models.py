from decimal import Decimal

from django.db import models

from materials.models import Material
from supplier.models import Supplier

from .constants import (
    BATCH_NUMBER_MAX_LENGTH,
    INVOICE_NUMBER_MAX_LENGTH,
    QUANTITY_DECIMAL_PLACES,
    QUANTITY_MAX_DIGITS,
    RATE_DECIMAL_PLACES,
    RATE_MAX_DIGITS,
)


class PurchaseInvoice(models.Model):
    """Purchase invoice header (one record, many product lines)."""

    id = models.BigAutoField(primary_key=True)
    supplier = models.ForeignKey(
        Supplier,
        on_delete=models.PROTECT,
        related_name='purchase_invoices',
        db_index=True,
    )
    invoice_date = models.DateField(db_index=True)
    invoice_number = models.CharField(max_length=INVOICE_NUMBER_MAX_LENGTH, db_index=True)

    created_by = models.CharField(max_length=150, blank=True, default='', db_index=True)
    updated_by = models.CharField(max_length=150, blank=True, default='')

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'purchase_invoices'
        ordering = ['-invoice_date', '-id']
        indexes = [
            models.Index(
                fields=['supplier', 'invoice_date'],
                name='pi_supplier_date_idx',
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['supplier', 'invoice_number'],
                name='purchase_invoices_unique_supplier_invoice',
            ),
        ]

    def __str__(self):
        return f'{self.invoice_number} — {self.supplier.company_name}'


class PurchaseInvoiceItem(models.Model):
    """Product line on a purchase invoice."""

    id = models.BigAutoField(primary_key=True)
    purchase_invoice = models.ForeignKey(
        PurchaseInvoice,
        on_delete=models.CASCADE,
        related_name='items',
        db_index=True,
    )
    material = models.ForeignKey(
        Material,
        on_delete=models.PROTECT,
        related_name='purchase_invoice_items',
        db_index=True,
    )
    quantity = models.DecimalField(
        max_digits=QUANTITY_MAX_DIGITS,
        decimal_places=QUANTITY_DECIMAL_PLACES,
    )
    rate = models.DecimalField(
        max_digits=RATE_MAX_DIGITS,
        decimal_places=RATE_DECIMAL_PLACES,
    )
    batch_number = models.CharField(max_length=BATCH_NUMBER_MAX_LENGTH, db_index=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'purchase_invoice_items'
        ordering = ['id']
        indexes = [
            models.Index(
                fields=['purchase_invoice', 'material', 'batch_number'],
                name='pi_item_inv_mat_batch_idx',
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['purchase_invoice', 'material', 'batch_number'],
                name='pi_items_unique_inv_mat_batch',
            ),
            models.CheckConstraint(
                condition=models.Q(quantity__gt=Decimal('0')),
                name='pi_items_quantity_gt_zero',
            ),
            models.CheckConstraint(
                condition=models.Q(rate__gte=Decimal('0')),
                name='pi_items_rate_gte_zero',
            ),
        ]

    def __str__(self):
        return (
            f'{self.material.material_name} x {self.quantity} '
            f'@ {self.rate} ({self.batch_number})'
        )
