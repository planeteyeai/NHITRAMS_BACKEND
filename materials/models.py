from django.db import models

from .constants import (
    MATERIAL_DESCRIPTION_MAX_LENGTH,
    MATERIAL_NAME_MAX_LENGTH,
    MATERIAL_TYPE_MAX_LENGTH,
    MATERIAL_UNIT_MAX_LENGTH,
    OTHER_TYPE_MAX_LENGTH,
)


class Material(models.Model):
    """Material record for Material Management (Add Material form)."""

    id = models.BigAutoField(primary_key=True)
    material_name = models.CharField(max_length=MATERIAL_NAME_MAX_LENGTH, db_index=True)
    material_unit = models.CharField(max_length=MATERIAL_UNIT_MAX_LENGTH, db_index=True)
    material_type = models.CharField(max_length=MATERIAL_TYPE_MAX_LENGTH, db_index=True)
    other_type = models.CharField(
        max_length=OTHER_TYPE_MAX_LENGTH,
        blank=True,
        default='',
    )
    material_description = models.CharField(
        max_length=MATERIAL_DESCRIPTION_MAX_LENGTH,
        blank=True,
        default='',
    )

    created_by = models.CharField(max_length=150, blank=True, default='', db_index=True)
    updated_by = models.CharField(max_length=150, blank=True, default='')

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'materials'
        ordering = ['-created_at', '-id']
        indexes = [
            models.Index(
                fields=['material_name', 'material_unit', 'material_type'],
                name='materials_name_unit_type_idx',
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=[
                    'material_name',
                    'material_unit',
                    'material_type',
                    'other_type',
                ],
                name='materials_unique_name_unit_type_other',
            ),
        ]

    def __str__(self):
        label = self.other_type if self.material_type == 'Other' else self.material_type
        return f'{self.material_name} ({self.material_unit}, {label})'
