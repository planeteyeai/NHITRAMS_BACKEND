"""Supplier serializers — validation here; persistence in services."""

from __future__ import annotations

import re

from rest_framework import serializers

from .constants import (
    ADDRESS_MAX_LENGTH,
    CITY_MAX_LENGTH,
    COMPANY_NAME_MAX_LENGTH,
    CONTACT_PERSON_MAX_LENGTH,
    EMAIL_MAX_LENGTH,
    MOBILE_NUMBER_MAX_LENGTH,
    MOBILE_NUMBER_PATTERN,
    STATE_MAX_LENGTH,
)
from .models import Supplier


def _required_text(value, field_name: str, *, max_length: int | None = None) -> str:
    text = str(value or '').strip()
    if not text:
        raise serializers.ValidationError(f'{field_name} is required.')
    if max_length and len(text) > max_length:
        raise serializers.ValidationError(
            f'{field_name} must be at most {max_length} characters.'
        )
    return text


class SupplierWriteSerializer(serializers.Serializer):
    """Create / full update / partial update payload for Supplier."""

    company_name = serializers.CharField(
        max_length=COMPANY_NAME_MAX_LENGTH,
        required=True,
        allow_blank=False,
        trim_whitespace=True,
    )
    contact_person = serializers.CharField(
        max_length=CONTACT_PERSON_MAX_LENGTH,
        required=True,
        allow_blank=False,
        trim_whitespace=True,
    )
    mobile_number = serializers.CharField(
        max_length=MOBILE_NUMBER_MAX_LENGTH,
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
    state = serializers.CharField(
        max_length=STATE_MAX_LENGTH,
        required=True,
        allow_blank=False,
        trim_whitespace=True,
    )
    city = serializers.CharField(
        max_length=CITY_MAX_LENGTH,
        required=True,
        allow_blank=False,
        trim_whitespace=True,
    )
    address = serializers.CharField(
        max_length=ADDRESS_MAX_LENGTH,
        required=True,
        allow_blank=False,
        trim_whitespace=True,
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.partial:
            for field in self.fields.values():
                field.required = False

    def validate_company_name(self, value):
        return _required_text(value, 'company_name', max_length=COMPANY_NAME_MAX_LENGTH)

    def validate_contact_person(self, value):
        return _required_text(
            value, 'contact_person', max_length=CONTACT_PERSON_MAX_LENGTH
        )

    def validate_mobile_number(self, value):
        text = re.sub(r'\s+', '', str(value or '').strip())
        if text.startswith('+91') and len(text) > 3:
            text = text[3:]
        elif text.startswith('91') and len(text) == 12:
            text = text[2:]
        if not re.fullmatch(MOBILE_NUMBER_PATTERN, text):
            raise serializers.ValidationError(
                'Enter a valid 10-digit Indian mobile number (starting with 6–9).'
            )
        return text

    def validate_email(self, value):
        text = str(value or '').strip().lower()
        if not text:
            raise serializers.ValidationError('Email is required.')
        return text

    def validate_state(self, value):
        return _required_text(value, 'state', max_length=STATE_MAX_LENGTH)

    def validate_city(self, value):
        return _required_text(value, 'city', max_length=CITY_MAX_LENGTH)

    def validate_address(self, value):
        return _required_text(value, 'address', max_length=ADDRESS_MAX_LENGTH)


class SupplierReadSerializer(serializers.ModelSerializer):
    class Meta:
        model = Supplier
        fields = [
            'id',
            'company_name',
            'contact_person',
            'mobile_number',
            'email',
            'state',
            'city',
            'address',
            'created_by',
            'updated_by',
            'created_at',
            'updated_at',
        ]
        read_only_fields = fields


class SupplierListQuerySerializer(serializers.Serializer):
    """Query params for list endpoint — validated to prevent unsafe construction."""

    page = serializers.IntegerField(required=False, min_value=1, default=1)
    page_size = serializers.IntegerField(required=False, min_value=1, default=20)
    search = serializers.CharField(required=False, allow_blank=True, default='')
    sort_by = serializers.CharField(
        required=False, allow_blank=True, default='created_at'
    )
    sort_order = serializers.ChoiceField(
        choices=['asc', 'desc'],
        required=False,
        default='desc',
    )
    state = serializers.CharField(required=False, allow_blank=True)
    city = serializers.CharField(required=False, allow_blank=True)
    company_name = serializers.CharField(required=False, allow_blank=True)
    contact_person = serializers.CharField(required=False, allow_blank=True)
    email = serializers.CharField(required=False, allow_blank=True)
    mobile_number = serializers.CharField(required=False, allow_blank=True)
