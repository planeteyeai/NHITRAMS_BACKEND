from decimal import Decimal

from django.db import models

from .constants import (
    EQUIPMENT_CODE_MAX_LENGTH,
    ESTIMATE_COST_DECIMAL_PLACES,
    ESTIMATE_COST_MAX_DIGITS,
    ITEM_NAME_MAX_LENGTH,
    MAKE_AND_MODEL_MAX_LENGTH,
    REMARK_MAX_LENGTH,
    STATUS_MAX_LENGTH,
)


class Equipment(models.Model):
    """Equipment record for Equipment Management (Add Equipment form)."""

    id = models.BigAutoField(primary_key=True)
    item_name = models.CharField(max_length=ITEM_NAME_MAX_LENGTH, db_index=True)
    year_of_make = models.PositiveIntegerField(db_index=True)
    make_and_model = models.CharField(max_length=MAKE_AND_MODEL_MAX_LENGTH, db_index=True)
    equipment_code = models.CharField(
        max_length=EQUIPMENT_CODE_MAX_LENGTH,
        unique=True,
        db_index=True,
    )
    estimate_cost = models.DecimalField(
        max_digits=ESTIMATE_COST_MAX_DIGITS,
        decimal_places=ESTIMATE_COST_DECIMAL_PLACES,
    )
    status = models.CharField(max_length=STATUS_MAX_LENGTH, db_index=True)
    remark = models.CharField(max_length=REMARK_MAX_LENGTH)

    created_by = models.CharField(max_length=150, blank=True, default='', db_index=True)
    updated_by = models.CharField(max_length=150, blank=True, default='')

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'equipments'
        ordering = ['-created_at', '-id']
        indexes = [
            models.Index(fields=['status', 'year_of_make'], name='equip_status_year_idx'),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(estimate_cost__gte=Decimal('0')),
                name='equipments_estimate_cost_gte_zero',
            ),
        ]

    def __str__(self):
        return f'{self.item_name} ({self.equipment_code})'
