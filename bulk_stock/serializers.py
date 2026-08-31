"""Bulk Stock serializers."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from rest_framework import serializers

from materials.product_validation import validate_material_ids_in_products

from .constants import QUANTITY_DECIMAL_PLACES
from .models import BulkStockTransaction, BulkStockTransactionItem


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


class BulkStockProductWriteSerializer(serializers.Serializer):
    id = serializers.IntegerField(required=False, allow_null=True)
    material_id = serializers.IntegerField(required=True)
    quantity = serializers.DecimalField(
        max_digits=18,
        decimal_places=QUANTITY_DECIMAL_PLACES,
        required=True,
    )

    def validate_quantity(self, value):
        return _parse_positive_decimal(value, 'quantity')


class BulkStockWriteSerializer(serializers.Serializer):
    products = BulkStockProductWriteSerializer(many=True, required=True)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.partial:
            for field in self.fields.values():
                field.required = False

    def validate_products(self, value):
        if value is None:
            raise serializers.ValidationError('products is required.')
        if not isinstance(value, list):
            raise serializers.ValidationError('products must be a list.')
        if len(value) == 0:
            raise serializers.ValidationError('At least one product is required.')

        seen: set[int] = set()
        for item in value:
            material_id = item['material_id']
            if material_id in seen:
                raise serializers.ValidationError(
                    'Duplicate material entries in the same transaction are not allowed.',
                    code='duplicate_material',
                )
            seen.add(material_id)
        validate_material_ids_in_products(value)
        return value


class BulkStockProductReadSerializer(serializers.ModelSerializer):
    material_id = serializers.IntegerField(source='material.id', read_only=True)
    material_name = serializers.CharField(source='material.material_name', read_only=True)
    quantity = serializers.DecimalField(
        max_digits=18,
        decimal_places=QUANTITY_DECIMAL_PLACES,
        coerce_to_string=True,
    )

    class Meta:
        model = BulkStockTransactionItem
        fields = ['id', 'material_id', 'material_name', 'quantity']
        read_only_fields = fields


class BulkStockReadSerializer(serializers.ModelSerializer):
    products = BulkStockProductReadSerializer(source='items', many=True, read_only=True)

    class Meta:
        model = BulkStockTransaction
        fields = [
            'id',
            'transaction_type',
            'products',
            'created_by',
            'updated_by',
            'created_at',
            'updated_at',
        ]
        read_only_fields = fields


class BulkStockListQuerySerializer(serializers.Serializer):
    page = serializers.IntegerField(required=False, min_value=1, default=1)
    page_size = serializers.IntegerField(required=False, min_value=1, default=20)
    search = serializers.CharField(required=False, allow_blank=True, default='')
    sort_by = serializers.CharField(required=False, allow_blank=True, default='created_at')
    sort_order = serializers.ChoiceField(
        choices=['asc', 'desc'],
        required=False,
        default='desc',
    )
    material_id = serializers.IntegerField(required=False)
