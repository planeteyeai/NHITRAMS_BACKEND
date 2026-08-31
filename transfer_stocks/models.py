from decimal import Decimal

from django.db import models

from materials.models import Material
from supplier.models import Supplier

from .constants import (
    BATCH_NUMBER_MAX_LENGTH,
    LOCATION_MAX_LENGTH,
    QUANTITY_DECIMAL_PLACES,
    QUANTITY_MAX_DIGITS,
    RATE_DECIMAL_PLACES,
    RATE_MAX_DIGITS,
    REMARK_MAX_LENGTH,
)


class Batch(models.Model):
    """Batch/lot master for Transfer Stock batch dropdown."""

    id = models.BigAutoField(primary_key=True)
    batch_number = models.CharField(
        max_length=BATCH_NUMBER_MAX_LENGTH,
        unique=True,
        db_index=True,
    )
    is_active = models.BooleanField(default=True, db_index=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'batches'
        ordering = ['batch_number', 'id']

    def __str__(self):
        return self.batch_number


class TransferStock(models.Model):
    """Transfer Stock header (one record, many product lines)."""

    id = models.BigAutoField(primary_key=True)
    supplier = models.ForeignKey(
        Supplier,
        on_delete=models.PROTECT,
        related_name='transfer_stocks',
        db_index=True,
    )
    date = models.DateField(db_index=True)
    location = models.CharField(max_length=LOCATION_MAX_LENGTH, db_index=True)
    remark = models.CharField(max_length=REMARK_MAX_LENGTH, blank=True, default='')

    created_by = models.CharField(max_length=150, blank=True, default='', db_index=True)
    updated_by = models.CharField(max_length=150, blank=True, default='')

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'transfer_stocks'
        ordering = ['-date', '-id']
        indexes = [
            models.Index(fields=['date', 'supplier'], name='ts_date_supplier_idx'),
        ]

    def __str__(self):
        return f'Transfer #{self.id} — {self.supplier.company_name} ({self.date})'


class TransferStockItem(models.Model):
    """Product line on a Transfer Stock record."""

    id = models.BigAutoField(primary_key=True)
    transfer_stock = models.ForeignKey(
        TransferStock,
        on_delete=models.CASCADE,
        related_name='items',
        db_index=True,
    )
    material = models.ForeignKey(
        Material,
        on_delete=models.PROTECT,
        related_name='transfer_stock_items',
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
    batch = models.ForeignKey(
        Batch,
        on_delete=models.PROTECT,
        related_name='transfer_stock_items',
        db_index=True,
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'transfer_stock_items'
        ordering = ['id']
        indexes = [
            models.Index(
                fields=['transfer_stock', 'material', 'batch'],
                name='ts_item_ts_mat_batch_idx',
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['transfer_stock', 'material', 'batch'],
                name='transfer_stock_items_unique_ts_material_batch',
            ),
            models.CheckConstraint(
                condition=models.Q(quantity__gt=Decimal('0')),
                name='transfer_stock_items_quantity_gt_zero',
            ),
            models.CheckConstraint(
                condition=models.Q(rate__gte=Decimal('0')),
                name='transfer_stock_items_rate_gte_zero',
            ),
        ]

    def __str__(self):
        return (
            f'{self.material.material_name} x {self.quantity} '
            f'@ {self.rate} ({self.batch.batch_number})'
        )
