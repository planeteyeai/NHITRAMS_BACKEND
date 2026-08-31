"""Transfer Stock serializers — validation and nested product payloads."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from rest_framework import serializers

from materials.product_validation import validate_material_ids_in_products
from supplier.models import Supplier

from .constants import (
    LOCATION_MAX_LENGTH,
    QUANTITY_DECIMAL_PLACES,
    RATE_DECIMAL_PLACES,
    REMARK_MAX_LENGTH,
)
from .models import Batch, TransferStock, TransferStockItem


def _required_text(value, field_name: str, *, max_length: int) -> str:
    text = str(value or '').strip()
    if not text:
        raise serializers.ValidationError(f'{field_name} is required.')
    if len(text) > max_length:
        raise serializers.ValidationError(
            f'{field_name} must be at most {max_length} characters.'
        )
    return text


def _parse_positive_decimal(value, field_name: str) -> Decimal:
    if value is None or value == '':
        raise serializers.ValidationError(f'{field_name} is required.')
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise serializers.ValidationError(f'{field_name} must be a valid number.') from exc
    if amount <= 0:
        raise serializers.ValidationError(f'{field_name} must be greater than zero.')
    return amount.quantize(Decimal('1.' + '0' * QUANTITY_DECIMAL_PLACES))


def _parse_non_negative_decimal(value, field_name: str) -> Decimal:
    if value is None or value == '':
        raise serializers.ValidationError(f'{field_name} is required.')
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise serializers.ValidationError(f'{field_name} must be a valid number.') from exc
    if amount < 0:
        raise serializers.ValidationError(f'{field_name} must be greater than or equal to zero.')
    return amount.quantize(Decimal('1.' + '0' * RATE_DECIMAL_PLACES))


class TransferStockProductWriteSerializer(serializers.Serializer):
    id = serializers.IntegerField(required=False, allow_null=True)
    material_id = serializers.IntegerField(required=True)
    quantity = serializers.DecimalField(
        max_digits=18,
        decimal_places=QUANTITY_DECIMAL_PLACES,
        required=True,
    )
    rate = serializers.DecimalField(
        max_digits=18,
        decimal_places=RATE_DECIMAL_PLACES,
        required=True,
    )
    batch_id = serializers.IntegerField(required=True)

    def validate_quantity(self, value):
        return _parse_positive_decimal(value, 'quantity')

    def validate_rate(self, value):
        return _parse_non_negative_decimal(value, 'rate')


class TransferStockWriteSerializer(serializers.Serializer):
    supplier_id = serializers.IntegerField(required=True)
    date = serializers.DateField(required=True)
    location = serializers.CharField(
        max_length=LOCATION_MAX_LENGTH,
        required=True,
        allow_blank=False,
        trim_whitespace=True,
    )
    remark = serializers.CharField(
        max_length=REMARK_MAX_LENGTH,
        required=False,
        allow_blank=True,
        allow_null=True,
        trim_whitespace=True,
    )
    products = TransferStockProductWriteSerializer(many=True, required=True)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.partial:
            for field in self.fields.values():
                field.required = False

    def validate_supplier_id(self, value):
        if not Supplier.objects.filter(pk=value).exists():
            raise serializers.ValidationError('Supplier not found.')
        return value

    def validate_location(self, value):
        return _required_text(value, 'location', max_length=LOCATION_MAX_LENGTH)

    def validate_remark(self, value):
        if value is None:
            return ''
        return str(value).strip()

    def validate_products(self, value):
        if value is None:
            raise serializers.ValidationError('products is required.')
        if not isinstance(value, list):
            raise serializers.ValidationError('products must be a list.')
        if len(value) == 0:
            raise serializers.ValidationError('At least one product is required.')

        validate_material_ids_in_products(value)

        batch_ids = {item['batch_id'] for item in value}
        active_batch_ids = set(
            Batch.objects.filter(pk__in=batch_ids, is_active=True).values_list('pk', flat=True)
        )
        product_errors = [{} for _ in value]
        for index, item in enumerate(value):
            if item['batch_id'] not in active_batch_ids:
                product_errors[index] = {'batch_id': ['Batch not found.']}
        if any(product_errors):
            raise serializers.ValidationError(product_errors)

        return value


class TransferStockProductReadSerializer(serializers.ModelSerializer):
    material_id = serializers.IntegerField(source='material.id', read_only=True)
    material_name = serializers.CharField(source='material.material_name', read_only=True)
    batch_id = serializers.IntegerField(source='batch.id', read_only=True)
    batch_number = serializers.CharField(source='batch.batch_number', read_only=True)

    class Meta:
        model = TransferStockItem
        fields = [
            'id',
            'material_id',
            'material_name',
            'quantity',
            'rate',
            'batch_id',
            'batch_number',
        ]
        read_only_fields = fields


class TransferStockReadSerializer(serializers.ModelSerializer):
    supplier_id = serializers.IntegerField(source='supplier.id', read_only=True)
    supplier_name = serializers.CharField(source='supplier.company_name', read_only=True)
    products = TransferStockProductReadSerializer(source='items', many=True, read_only=True)

    class Meta:
        model = TransferStock
        fields = [
            'id',
            'supplier_id',
            'supplier_name',
            'date',
            'location',
            'remark',
            'products',
            'created_by',
            'updated_by',
            'created_at',
            'updated_at',
        ]
        read_only_fields = fields


class TransferStockListQuerySerializer(serializers.Serializer):
    page = serializers.IntegerField(required=False, min_value=1, default=1)
    page_size = serializers.IntegerField(required=False, min_value=1, default=20)
    search = serializers.CharField(required=False, allow_blank=True, default='')
    sort_by = serializers.CharField(required=False, allow_blank=True, default='date')
    sort_order = serializers.ChoiceField(
        choices=['asc', 'desc'],
        required=False,
        default='desc',
    )
    supplier_id = serializers.IntegerField(required=False)
    date = serializers.DateField(required=False)
    material_id = serializers.IntegerField(required=False)
    batch_id = serializers.IntegerField(required=False)
    location = serializers.CharField(required=False, allow_blank=True)
