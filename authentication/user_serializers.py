"""User management serializers."""

from __future__ import annotations

import re

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from .constants import (
    ACCESS_TYPES,
    EMAIL_MAX_LENGTH,
    FIRST_NAME_MAX_LENGTH,
    LAST_NAME_MAX_LENGTH,
    PHONE_NUMBER_MAX_LENGTH,
    PHONE_NUMBER_PATTERN,
    ROLE_MAX_LENGTH,
    USER_ROLES,
    USERNAME_MAX_LENGTH,
)

User = get_user_model()


def _required_text(value, field_name: str, *, max_length: int) -> str:
    text = str(value or '').strip()
    if not text:
        raise serializers.ValidationError(f'{field_name} is required.')
    if len(text) > max_length:
        raise serializers.ValidationError(
            f'{field_name} must be at most {max_length} characters.'
        )
    return text


class UserWriteSerializer(serializers.Serializer):
    username = serializers.CharField(
        max_length=USERNAME_MAX_LENGTH,
        required=True,
        allow_blank=False,
        trim_whitespace=True,
    )
    password = serializers.CharField(
        required=False,
        allow_blank=False,
        write_only=True,
        trim_whitespace=False,
    )
    first_name = serializers.CharField(
        max_length=FIRST_NAME_MAX_LENGTH,
        required=True,
        allow_blank=False,
        trim_whitespace=True,
    )
    last_name = serializers.CharField(
        max_length=LAST_NAME_MAX_LENGTH,
        required=True,
        allow_blank=False,
        trim_whitespace=True,
    )
    email = serializers.EmailField(
        max_length=EMAIL_MAX_LENGTH,
        required=True,
        allow_blank=False,
        trim_whitespace=True,
    )
    phone_number = serializers.CharField(
        max_length=PHONE_NUMBER_MAX_LENGTH,
        required=True,
        allow_blank=False,
        trim_whitespace=True,
    )
    role = serializers.CharField(
        max_length=ROLE_MAX_LENGTH,
        required=True,
        allow_blank=False,
        trim_whitespace=True,
    )
    access_type = serializers.CharField(
        max_length=10,
        required=True,
        allow_blank=False,
        trim_whitespace=True,
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.partial:
            for field in self.fields.values():
                field.required = False
        elif not self.partial and self.context.get('require_password', True):
            self.fields['password'].required = True

    def validate_username(self, value):
        return _required_text(value, 'username', max_length=USERNAME_MAX_LENGTH)

    def validate_first_name(self, value):
        return _required_text(value, 'first_name', max_length=FIRST_NAME_MAX_LENGTH)

    def validate_last_name(self, value):
        return _required_text(value, 'last_name', max_length=LAST_NAME_MAX_LENGTH)

    def validate_email(self, value):
        return _required_text(value, 'email', max_length=EMAIL_MAX_LENGTH).lower()

    def validate_phone_number(self, value):
        text = _required_text(value, 'phone_number', max_length=PHONE_NUMBER_MAX_LENGTH)
        if not re.fullmatch(PHONE_NUMBER_PATTERN, text):
            raise serializers.ValidationError(
                'phone_number must be a valid 10-digit Indian mobile number.'
            )
        return text

    def validate_role(self, value):
        text = _required_text(value, 'role', max_length=ROLE_MAX_LENGTH)
        if text not in USER_ROLES:
            raise serializers.ValidationError('Invalid user role.')
        return text

    def validate_access_type(self, value):
        text = _required_text(value, 'access_type', max_length=10)
        if text not in ACCESS_TYPES:
            raise serializers.ValidationError('Invalid access type.')
        return text

    def validate_password(self, value):
        if value in (None, ''):
            return value
        try:
            validate_password(value)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(list(exc.messages)) from exc
        return value


class UserReadSerializer(serializers.Serializer):
    id = serializers.IntegerField(read_only=True)
    username = serializers.CharField(read_only=True)
    first_name = serializers.CharField(read_only=True)
    last_name = serializers.CharField(read_only=True)
    email = serializers.EmailField(read_only=True)
    phone_number = serializers.CharField(read_only=True)
    role = serializers.CharField(read_only=True)
    access_type = serializers.CharField(read_only=True)
    is_active = serializers.BooleanField(read_only=True)
    created_by = serializers.CharField(read_only=True)
    updated_by = serializers.CharField(read_only=True)
    created_at = serializers.DateTimeField(read_only=True)
    updated_at = serializers.DateTimeField(read_only=True)


class UserListQuerySerializer(serializers.Serializer):
    page = serializers.IntegerField(required=False, min_value=1, default=1)
    page_size = serializers.IntegerField(required=False, min_value=1, default=20)
    search = serializers.CharField(required=False, allow_blank=True, default='')
    sort_by = serializers.CharField(required=False, allow_blank=True, default='created_at')
    sort_order = serializers.ChoiceField(
        choices=['asc', 'desc'],
        required=False,
        default='desc',
    )
    role = serializers.CharField(required=False, allow_blank=True)
    access_type = serializers.CharField(required=False, allow_blank=True)
    is_active = serializers.CharField(required=False, allow_blank=True, allow_null=True)

    def validate_is_active(self, value):
        if value in (None, ''):
            return None
        lowered = str(value).strip().lower()
        if lowered in {'true', '1', 'yes'}:
            return True
        if lowered in {'false', '0', 'no'}:
            return False
        raise serializers.ValidationError('is_active must be true or false.')
