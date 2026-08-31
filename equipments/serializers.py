"""Equipment serializers — validation here; persistence in services."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from rest_framework import serializers

from .constants import (
    EQUIPMENT_CODE_MAX_LENGTH,
    ESTIMATE_COST_DECIMAL_PLACES,
    ITEM_NAME_MAX_LENGTH,
    MAKE_AND_MODEL_MAX_LENGTH,
    MIN_YEAR_OF_MAKE,
    REMARK_MAX_LENGTH,
    STATUS_MAX_LENGTH,
)
from .models import Equipment


def _required_text(value, field_name: str, *, max_length: int) -> str:
    text = str(value or '').strip()
    if not text:
        raise serializers.ValidationError(f'{field_name} is required.')
    if len(text) > max_length:
        raise serializers.ValidationError(
            f'{field_name} must be at most {max_length} characters.'
        )
    return text


def _parse_non_negative_cost(value, field_name: str = 'estimate_cost') -> Decimal:
    if value is None or value == '':
        raise serializers.ValidationError(f'{field_name} is required.')
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise serializers.ValidationError(f'{field_name} must be a valid number.') from exc
    if amount < 0:
        raise serializers.ValidationError(
            f'{field_name} must be greater than or equal to zero.'
        )
    return amount.quantize(Decimal('1.' + '0' * ESTIMATE_COST_DECIMAL_PLACES))


def parse_year_of_make(value) -> int:
    if value is None or value == '':
        raise serializers.ValidationError('year_of_make is required.')

    if isinstance(value, int):
        year = value
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            raise serializers.ValidationError('year_of_make is required.')
        if '-' in text:
            try:
                parsed = datetime.strptime(text[:10], '%Y-%m-%d').date()
            except ValueError as exc:
                raise serializers.ValidationError(
                    'year_of_make must be a valid year or date.'
                ) from exc
            year = parsed.year
        else:
            try:
                year = int(text)
            except ValueError as exc:
                raise serializers.ValidationError(
                    'year_of_make must be a valid year.'
                ) from exc
    else:
        try:
            year = int(value)
        except (TypeError, ValueError) as exc:
            raise serializers.ValidationError(
                'year_of_make must be a valid year.'
            ) from exc

    current_year = date.today().year
    if year < MIN_YEAR_OF_MAKE:
        raise serializers.ValidationError(
            f'year_of_make must be {MIN_YEAR_OF_MAKE} or later.'
        )
    if year > current_year:
        raise serializers.ValidationError('year_of_make cannot be in the future.')
    return year


class FlexibleYearField(serializers.Field):
    def to_internal_value(self, data):
        return parse_year_of_make(data)

    def to_representation(self, value):
        return value


class EquipmentWriteSerializer(serializers.Serializer):
    item_name = serializers.CharField(
        max_length=ITEM_NAME_MAX_LENGTH,
        required=True,
        allow_blank=False,
        trim_whitespace=True,
    )
    year_of_make = FlexibleYearField(required=True)
    make_and_model = serializers.CharField(
        max_length=MAKE_AND_MODEL_MAX_LENGTH,
        required=True,
        allow_blank=False,
        trim_whitespace=True,
    )
    equipment_code = serializers.CharField(
        max_length=EQUIPMENT_CODE_MAX_LENGTH,
        required=True,
        allow_blank=False,
        trim_whitespace=True,
    )
    estimate_cost = serializers.DecimalField(
        max_digits=18,
        decimal_places=ESTIMATE_COST_DECIMAL_PLACES,
        required=True,
    )
    status = serializers.CharField(
        max_length=STATUS_MAX_LENGTH,
        required=True,
        allow_blank=False,
        trim_whitespace=True,
    )
    remark = serializers.CharField(
        max_length=REMARK_MAX_LENGTH,
        required=True,
        allow_blank=False,
        trim_whitespace=True,
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.partial:
            for field in self.fields.values():
                field.required = False

    def validate_item_name(self, value):
        return _required_text(value, 'item_name', max_length=ITEM_NAME_MAX_LENGTH)

    def validate_make_and_model(self, value):
        return _required_text(
            value, 'make_and_model', max_length=MAKE_AND_MODEL_MAX_LENGTH
        )

    def validate_equipment_code(self, value):
        return _required_text(
            value, 'equipment_code', max_length=EQUIPMENT_CODE_MAX_LENGTH
        )

    def validate_estimate_cost(self, value):
        return _parse_non_negative_cost(value)

    def validate_status(self, value):
        return _required_text(value, 'status', max_length=STATUS_MAX_LENGTH)

    def validate_remark(self, value):
        return _required_text(value, 'remark', max_length=REMARK_MAX_LENGTH)


class EquipmentReadSerializer(serializers.ModelSerializer):
    estimate_cost = serializers.DecimalField(
        max_digits=18,
        decimal_places=ESTIMATE_COST_DECIMAL_PLACES,
        coerce_to_string=True,
    )

    class Meta:
        model = Equipment
        fields = [
            'id',
            'item_name',
            'year_of_make',
            'make_and_model',
            'equipment_code',
            'estimate_cost',
            'status',
            'remark',
            'created_by',
            'updated_by',
            'created_at',
            'updated_at',
        ]
        read_only_fields = fields


class EquipmentListQuerySerializer(serializers.Serializer):
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
    status = serializers.CharField(required=False, allow_blank=True)
    year_of_make = serializers.IntegerField(required=False)
    equipment_code = serializers.CharField(required=False, allow_blank=True)
