from django.db import models

from .constants import (
    ADDRESS_MAX_LENGTH,
    CONTACT_NUMBER_MAX_LENGTH,
    CREW_ID_MAX_LENGTH,
    FIRST_NAME_MAX_LENGTH,
    GENDER_MAX_LENGTH,
    LABOUR_TYPE_MAX_LENGTH,
    LAST_NAME_MAX_LENGTH,
)


class Labour(models.Model):
    """Labour worker record for Labour Management (Add Labour form)."""

    id = models.BigAutoField(primary_key=True)
    first_name = models.CharField(max_length=FIRST_NAME_MAX_LENGTH)
    last_name = models.CharField(max_length=LAST_NAME_MAX_LENGTH)
    date_of_birth = models.DateField()
    labour_type = models.CharField(max_length=LABOUR_TYPE_MAX_LENGTH, db_index=True)
    crew_id = models.CharField(max_length=CREW_ID_MAX_LENGTH, db_index=True)
    gender = models.CharField(max_length=GENDER_MAX_LENGTH, db_index=True)
    contact_number = models.CharField(max_length=CONTACT_NUMBER_MAX_LENGTH, db_index=True)
    address = models.CharField(max_length=ADDRESS_MAX_LENGTH)

    # Audit — JWT username of creator / last updater (not used for ownership ACL).
    created_by = models.CharField(max_length=150, blank=True, default='', db_index=True)
    updated_by = models.CharField(max_length=150, blank=True, default='')

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'labours'
        ordering = ['-created_at', '-id']
        indexes = [
            models.Index(fields=['first_name'], name='labours_first_name_idx'),
            models.Index(fields=['last_name'], name='labours_last_name_idx'),
            models.Index(
                fields=['crew_id', 'first_name', 'last_name', 'date_of_birth'],
                name='labours_dup_lookup_idx',
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['crew_id', 'first_name', 'last_name', 'date_of_birth'],
                name='labours_unique_crew_name_dob',
            ),
        ]

    def __str__(self):
        return f'{self.first_name} {self.last_name} ({self.crew_id})'
