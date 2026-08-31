from django.conf import settings
from django.db import models

from .constants import (
    ACCESS_TYPE_MAX_LENGTH,
    PHONE_NUMBER_MAX_LENGTH,
    ROLE_MAX_LENGTH,
)


class UserProfile(models.Model):
    """Extended NHIT user attributes linked to Django auth User."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='profile',
        primary_key=True,
    )
    phone_number = models.CharField(max_length=PHONE_NUMBER_MAX_LENGTH, db_index=True)
    role = models.CharField(max_length=ROLE_MAX_LENGTH, db_index=True)
    access_type = models.CharField(max_length=ACCESS_TYPE_MAX_LENGTH, db_index=True)

    created_by = models.CharField(max_length=150, blank=True, default='', db_index=True)
    updated_by = models.CharField(max_length=150, blank=True, default='')

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'user_profiles'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['role', 'access_type'], name='user_profile_role_access_idx'),
        ]

    def __str__(self):
        return f'{self.user.username} ({self.role})'
