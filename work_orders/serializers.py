"""Work Order serializers — validation here; persistence in services."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from rest_framework import serializers

from .constants import (
    CHAINAGE_DECIMAL_PLACES,
    OTHER_TYPE_MAX_LENGTH,
    TYPE_OF_WORK_MAX_LENGTH,
    WORK_ORDER_ID_MAX_LENGTH,
)
from .models import WorkOrder


def _required_text(value, field_name: str, *, max_length: int) -> str:
    text = str(value or '').strip()
    if not text:
        raise serializers.ValidationError(f'{field_name} is required.')
    if len(text) > max_length:
        raise serializers.ValidationError(
            f'{field_name} must be at most {max_length} characters.'
        )
    return text


def _parse_non_negative_chainage(value, field_name: str) -> Decimal:
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
    return amount.quantize(Decimal('1.' + '0' * CHAINAGE_DECIMAL_PLACES))


class WorkOrderWriteSerializer(serializers.Serializer):
    work_order_id = serializers.CharField(
        max_length=WORK_ORDER_ID_MAX_LENGTH,
        required=True,
        allow_blank=False,
        trim_whitespace=True,
    )
    type_of_work = serializers.CharField(
        max_length=TYPE_OF_WORK_MAX_LENGTH,
        required=True,
        allow_blank=False,
        trim_whitespace=True,
    )
    other_type = serializers.CharField(
        max_length=OTHER_TYPE_MAX_LENGTH,
        required=False,
        allow_blank=True,
        allow_null=True,
        trim_whitespace=True,
    )
    chainage_km_from = serializers.DecimalField(
        max_digits=12,
        decimal_places=CHAINAGE_DECIMAL_PLACES,
        required=True,
    )
    chainage_km_to = serializers.DecimalField(
        max_digits=12,
        decimal_places=CHAINAGE_DECIMAL_PLACES,
        required=True,
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.partial:
            for field in self.fields.values():
                field.required = False

    def validate_work_order_id(self, value):
        return _required_text(value, 'work_order_id', max_length=WORK_ORDER_ID_MAX_LENGTH)

    def validate_type_of_work(self, value):
        return _required_text(value, 'type_of_work', max_length=TYPE_OF_WORK_MAX_LENGTH)

    def validate_other_type(self, value):
        if value is None:
            return ''
        return str(value).strip()

    def validate_chainage_km_from(self, value):
        return _parse_non_negative_chainage(value, 'chainage_km_from')

    def validate_chainage_km_to(self, value):
        return _parse_non_negative_chainage(value, 'chainage_km_to')


class WorkOrderReadSerializer(serializers.ModelSerializer):
    other_type = serializers.SerializerMethodField()
    chainage_km_from = serializers.DecimalField(
        max_digits=12,
        decimal_places=CHAINAGE_DECIMAL_PLACES,
        coerce_to_string=True,
    )
    chainage_km_to = serializers.DecimalField(
        max_digits=12,
        decimal_places=CHAINAGE_DECIMAL_PLACES,
        coerce_to_string=True,
    )

    class Meta:
        model = WorkOrder
        fields = [
            'id',
            'work_order_id',
            'type_of_work',
            'other_type',
            'chainage_km_from',
            'chainage_km_to',
            'created_by',
            'updated_by',
            'created_at',
            'updated_at',
        ]
        read_only_fields = fields

    def get_other_type(self, obj):
        from .constants import is_other_type_of_work

        if is_other_type_of_work(obj.type_of_work):
            return obj.other_type or None
        return None


class WorkOrderListQuerySerializer(serializers.Serializer):
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
    type_of_work = serializers.CharField(required=False, allow_blank=True)
    work_order_id = serializers.CharField(required=False, allow_blank=True)
    chainage_from = serializers.DecimalField(
        max_digits=12,
        decimal_places=CHAINAGE_DECIMAL_PLACES,
        required=False,
    )
    chainage_to = serializers.DecimalField(
        max_digits=12,
        decimal_places=CHAINAGE_DECIMAL_PLACES,
        required=False,
    )
