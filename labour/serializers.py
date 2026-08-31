"""Labour serializers — validation lives here; persistence in services."""

from __future__ import annotations

import re
from datetime import date

from rest_framework import serializers

from .constants import (
    ADDRESS_MAX_LENGTH,
    CONTACT_NUMBER_MAX_LENGTH,
    CONTACT_NUMBER_PATTERN,
    CREW_ID_MAX_LENGTH,
    FIRST_NAME_MAX_LENGTH,
    GENDERS,
    LABOUR_TYPES,
    LAST_NAME_MAX_LENGTH,
    MAX_AGE_YEARS,
    MIN_AGE_YEARS,
)
from .models import Labour


def _normalize_choice(value: str, allowed: tuple[str, ...], field_name: str) -> str:
    text = str(value or '').strip()
    if not text:
        raise serializers.ValidationError(f'{field_name} is required.')
    lookup = {item.casefold(): item for item in allowed}
    canonical = lookup.get(text.casefold())
    if canonical is None:
        raise serializers.ValidationError(
            f'{field_name} must be one of: {", ".join(allowed)}.'
        )
    return canonical


def _age_years(dob: date, today: date | None = None) -> int:
    today = today or date.today()
    years = today.year - dob.year
    if (today.month, today.day) < (dob.month, dob.day):
        years -= 1
    return years


class LabourWriteSerializer(serializers.Serializer):
    """Create / full update / partial update payload for Labour."""

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
    date_of_birth = serializers.DateField(required=True, input_formats=['%Y-%m-%d'])
    labour_type = serializers.CharField(
        required=True, allow_blank=False, trim_whitespace=True
    )
    crew_id = serializers.CharField(
        max_length=CREW_ID_MAX_LENGTH,
        required=True,
        allow_blank=False,
        trim_whitespace=True,
    )
    gender = serializers.CharField(
        required=True, allow_blank=False, trim_whitespace=True
    )
    contact_number = serializers.CharField(
        max_length=CONTACT_NUMBER_MAX_LENGTH,
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

    def validate_first_name(self, value):
        text = str(value or '').strip()
        if not text:
            raise serializers.ValidationError('First name is required.')
        return text

    def validate_last_name(self, value):
        text = str(value or '').strip()
        if not text:
            raise serializers.ValidationError('Last name is required.')
        return text

    def validate_date_of_birth(self, value: date):
        today = date.today()
        if value > today:
            raise serializers.ValidationError('Date of birth cannot be in the future.')
        age = _age_years(value, today)
        if age < MIN_AGE_YEARS:
            raise serializers.ValidationError(
                f'Labour must be at least {MIN_AGE_YEARS} years old.'
            )
        if age > MAX_AGE_YEARS:
            raise serializers.ValidationError(
                f'Date of birth implies an age greater than {MAX_AGE_YEARS} years.'
            )
        return value

    def validate_labour_type(self, value):
        return _normalize_choice(value, LABOUR_TYPES, 'labour_type')

    def validate_crew_id(self, value):
        text = str(value or '').strip()
        if not text:
            raise serializers.ValidationError('Crew ID is required.')
        if not re.fullmatch(r'[A-Za-z0-9_\-]+', text):
            raise serializers.ValidationError(
                'Crew ID may only contain letters, numbers, hyphens, and underscores.'
            )
        return text

    def validate_gender(self, value):
        return _normalize_choice(value, GENDERS, 'gender')

    def validate_contact_number(self, value):
        text = re.sub(r'\s+', '', str(value or '').strip())
        if text.startswith('+91') and len(text) > 3:
            text = text[3:]
        elif text.startswith('91') and len(text) == 12:
            text = text[2:]
        if not re.fullmatch(CONTACT_NUMBER_PATTERN, text):
            raise serializers.ValidationError(
                'Enter a valid 10-digit Indian mobile number (starting with 6–9).'
            )
        return text

    def validate_address(self, value):
        text = str(value or '').strip()
        if not text:
            raise serializers.ValidationError('Address is required.')
        return text


class LabourReadSerializer(serializers.ModelSerializer):
    class Meta:
        model = Labour
        fields = [
            'id',
            'first_name',
            'last_name',
            'date_of_birth',
            'labour_type',
            'crew_id',
            'gender',
            'contact_number',
            'address',
            'created_by',
            'updated_by',
            'created_at',
            'updated_at',
        ]
        read_only_fields = fields


class LabourListQuerySerializer(serializers.Serializer):
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
    first_name = serializers.CharField(required=False, allow_blank=True)
    last_name = serializers.CharField(required=False, allow_blank=True)
    crew_id = serializers.CharField(required=False, allow_blank=True)
    labour_type = serializers.CharField(required=False, allow_blank=True)
    gender = serializers.CharField(required=False, allow_blank=True)
    contact_number = serializers.CharField(required=False, allow_blank=True)
