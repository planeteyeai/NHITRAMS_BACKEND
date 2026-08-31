from decimal import Decimal

from django.db import models

from .constants import (
    CHAINAGE_DECIMAL_PLACES,
    CHAINAGE_MAX_DIGITS,
    OTHER_TYPE_MAX_LENGTH,
    TYPE_OF_WORK_MAX_LENGTH,
    WORK_ORDER_ID_MAX_LENGTH,
)


class WorkOrder(models.Model):
    """Work order record for Work Order Management."""

    id = models.BigAutoField(primary_key=True)
    work_order_id = models.CharField(
        max_length=WORK_ORDER_ID_MAX_LENGTH,
        unique=True,
        db_index=True,
    )
    type_of_work = models.CharField(max_length=TYPE_OF_WORK_MAX_LENGTH, db_index=True)
    other_type = models.CharField(max_length=OTHER_TYPE_MAX_LENGTH, blank=True, default='')
    chainage_km_from = models.DecimalField(
        max_digits=CHAINAGE_MAX_DIGITS,
        decimal_places=CHAINAGE_DECIMAL_PLACES,
        db_index=True,
    )
    chainage_km_to = models.DecimalField(
        max_digits=CHAINAGE_MAX_DIGITS,
        decimal_places=CHAINAGE_DECIMAL_PLACES,
        db_index=True,
    )

    created_by = models.CharField(max_length=150, blank=True, default='', db_index=True)
    updated_by = models.CharField(max_length=150, blank=True, default='')

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'work_orders'
        ordering = ['-created_at', '-id']
        indexes = [
            models.Index(
                fields=['type_of_work', 'created_at'],
                name='wo_type_created_idx',
            ),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(chainage_km_from__gte=Decimal('0')),
                name='wo_chainage_from_gte_zero',
            ),
            models.CheckConstraint(
                condition=models.Q(chainage_km_to__gte=Decimal('0')),
                name='wo_chainage_to_gte_zero',
            ),
            models.CheckConstraint(
                condition=models.Q(chainage_km_to__gte=models.F('chainage_km_from')),
                name='wo_chainage_to_gte_from',
            ),
        ]

    def __str__(self):
        return self.work_order_id
