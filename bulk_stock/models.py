from decimal import Decimal

from django.db import models

from materials.models import Material

from .constants import (
    QUANTITY_DECIMAL_PLACES,
    QUANTITY_MAX_DIGITS,
    TRANSACTION_TYPE_INWARD,
    TRANSACTION_TYPE_OUTWARD,
)


class MaterialStock(models.Model):
    """Current warehouse quantity per material."""

    material = models.OneToOneField(
        Material,
        on_delete=models.PROTECT,
        related_name='stock',
        primary_key=True,
    )
    quantity = models.DecimalField(
        max_digits=QUANTITY_MAX_DIGITS,
        decimal_places=QUANTITY_DECIMAL_PLACES,
        default=Decimal('0'),
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'material_stocks'
        constraints = [
            models.CheckConstraint(
                condition=models.Q(quantity__gte=Decimal('0')),
                name='material_stocks_quantity_gte_zero',
            ),
        ]

    def __str__(self):
        return f'{self.material.material_name}: {self.quantity}'


class BulkStockTransaction(models.Model):
    """Bulk inward or outward stock transaction header."""

    TRANSACTION_TYPES = (
        (TRANSACTION_TYPE_INWARD, 'Inward'),
        (TRANSACTION_TYPE_OUTWARD, 'Outward'),
    )

    id = models.BigAutoField(primary_key=True)
    transaction_type = models.CharField(
        max_length=10,
        choices=TRANSACTION_TYPES,
        db_index=True,
    )

    created_by = models.CharField(max_length=150, blank=True, default='', db_index=True)
    updated_by = models.CharField(max_length=150, blank=True, default='')

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'bulk_stock_transactions'
        ordering = ['-created_at', '-id']
        indexes = [
            models.Index(
                fields=['transaction_type', 'created_at'],
                name='bulk_stock_tx_type_created_idx',
            ),
        ]

    def __str__(self):
        return f'{self.transaction_type} #{self.id}'


class BulkStockTransactionItem(models.Model):
    """Product line on a bulk stock transaction."""

    id = models.BigAutoField(primary_key=True)
    transaction = models.ForeignKey(
        BulkStockTransaction,
        on_delete=models.CASCADE,
        related_name='items',
        db_index=True,
    )
    material = models.ForeignKey(
        Material,
        on_delete=models.PROTECT,
        related_name='bulk_stock_items',
        db_index=True,
    )
    quantity = models.DecimalField(
        max_digits=QUANTITY_MAX_DIGITS,
        decimal_places=QUANTITY_DECIMAL_PLACES,
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'bulk_stock_transaction_items'
        ordering = ['id']
        indexes = [
            models.Index(
                fields=['transaction', 'material'],
                name='bulk_stock_item_tx_mat_idx',
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['transaction', 'material'],
                name='bulk_stock_items_unique_tx_material',
            ),
            models.CheckConstraint(
                condition=models.Q(quantity__gt=Decimal('0')),
                name='bulk_stock_items_quantity_gt_zero',
            ),
        ]

    def __str__(self):
        return f'{self.material.material_name} x {self.quantity}'


class MaterialStockMovement(models.Model):
    """Ledger entry for every material stock change."""

    id = models.BigAutoField(primary_key=True)
    material = models.ForeignKey(
        Material,
        on_delete=models.PROTECT,
        related_name='stock_movements',
        db_index=True,
    )
    transaction = models.ForeignKey(
        BulkStockTransaction,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='stock_movements',
        db_index=True,
    )
    transaction_type = models.CharField(max_length=10, db_index=True)
    quantity_delta = models.DecimalField(
        max_digits=QUANTITY_MAX_DIGITS,
        decimal_places=QUANTITY_DECIMAL_PLACES,
    )
    balance_after = models.DecimalField(
        max_digits=QUANTITY_MAX_DIGITS,
        decimal_places=QUANTITY_DECIMAL_PLACES,
    )
    created_by = models.CharField(max_length=150, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = 'material_stock_movements'
        ordering = ['-created_at', '-id']
