from django.db import models

from .constants import (
    ADDRESS_MAX_LENGTH,
    CITY_MAX_LENGTH,
    COMPANY_NAME_MAX_LENGTH,
    CONTACT_PERSON_MAX_LENGTH,
    EMAIL_MAX_LENGTH,
    MOBILE_NUMBER_MAX_LENGTH,
    STATE_MAX_LENGTH,
)


class Supplier(models.Model):
    """Supplier record for Supplier Management (Add Supplier form)."""

    id = models.BigAutoField(primary_key=True)
    company_name = models.CharField(max_length=COMPANY_NAME_MAX_LENGTH, db_index=True)
    contact_person = models.CharField(max_length=CONTACT_PERSON_MAX_LENGTH)
    mobile_number = models.CharField(max_length=MOBILE_NUMBER_MAX_LENGTH, db_index=True)
    email = models.EmailField(max_length=EMAIL_MAX_LENGTH, db_index=True)
    state = models.CharField(max_length=STATE_MAX_LENGTH, db_index=True)
    city = models.CharField(max_length=CITY_MAX_LENGTH, db_index=True)
    address = models.CharField(max_length=ADDRESS_MAX_LENGTH)

    created_by = models.CharField(max_length=150, blank=True, default='', db_index=True)
    updated_by = models.CharField(max_length=150, blank=True, default='')

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'suppliers'
        ordering = ['-created_at', '-id']
        indexes = [
            models.Index(
                fields=['company_name', 'email'],
                name='suppliers_company_email_idx',
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['company_name', 'email'],
                name='suppliers_unique_company_email',
            ),
        ]

    def __str__(self):
        return f'{self.company_name} ({self.email})'
