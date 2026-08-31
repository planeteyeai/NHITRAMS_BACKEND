"""Purchase Invoice serializers."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from rest_framework import serializers

from materials.product_validation import validate_material_ids_in_products
from supplier.models import Supplier

from .constants import (
    BATCH_NUMBER_MAX_LENGTH,
    INVOICE_NUMBER_MAX_LENGTH,
    QUANTITY_DECIMAL_PLACES,
    RATE_DECIMAL_PLACES,
)
from .models import PurchaseInvoice, PurchaseInvoiceItem


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


class PurchaseInvoiceProductWriteSerializer(serializers.Serializer):
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
    batch_number = serializers.CharField(
        max_length=BATCH_NUMBER_MAX_LENGTH,
        required=True,
        allow_blank=False,
        trim_whitespace=True,
    )

    def validate_quantity(self, value):
        return _parse_positive_decimal(value, 'quantity')

    def validate_rate(self, value):
        return _parse_non_negative_decimal(value, 'rate')

    def validate_batch_number(self, value):
        return _required_text(value, 'batch_number', max_length=BATCH_NUMBER_MAX_LENGTH)


class PurchaseInvoiceWriteSerializer(serializers.Serializer):
    supplier_id = serializers.IntegerField(required=True)
    invoice_date = serializers.DateField(required=True)
    invoice_number = serializers.CharField(
        max_length=INVOICE_NUMBER_MAX_LENGTH,
        required=True,
        allow_blank=False,
        trim_whitespace=True,
    )
    products = PurchaseInvoiceProductWriteSerializer(many=True, required=True)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.partial:
            for field in self.fields.values():
                field.required = False

    def validate_supplier_id(self, value):
        if not Supplier.objects.filter(pk=value).exists():
            raise serializers.ValidationError('Supplier not found.')
        return value

    def validate_invoice_number(self, value):
        return _required_text(value, 'invoice_number', max_length=INVOICE_NUMBER_MAX_LENGTH)

    def validate_products(self, value):
        if value is None:
            raise serializers.ValidationError('products is required.')
        if not isinstance(value, list):
            raise serializers.ValidationError('products must be a list.')
        if len(value) == 0:
            raise serializers.ValidationError('At least one product is required.')
        validate_material_ids_in_products(value)
        return value


class PurchaseInvoiceProductReadSerializer(serializers.ModelSerializer):
    material_id = serializers.IntegerField(source='material.id', read_only=True)
    material_name = serializers.CharField(source='material.material_name', read_only=True)
    quantity = serializers.DecimalField(
        max_digits=18,
        decimal_places=QUANTITY_DECIMAL_PLACES,
        coerce_to_string=True,
    )
    rate = serializers.DecimalField(
        max_digits=18,
        decimal_places=RATE_DECIMAL_PLACES,
        coerce_to_string=True,
    )

    class Meta:
        model = PurchaseInvoiceItem
        fields = [
            'id',
            'material_id',
            'material_name',
            'quantity',
            'rate',
            'batch_number',
        ]
        read_only_fields = fields


class PurchaseInvoiceReadSerializer(serializers.ModelSerializer):
    supplier_id = serializers.IntegerField(source='supplier.id', read_only=True)
    supplier_name = serializers.CharField(source='supplier.company_name', read_only=True)
    products = PurchaseInvoiceProductReadSerializer(source='items', many=True, read_only=True)

    class Meta:
        model = PurchaseInvoice
        fields = [
            'id',
            'supplier_id',
            'supplier_name',
            'invoice_date',
            'invoice_number',
            'products',
            'created_by',
            'updated_by',
            'created_at',
            'updated_at',
        ]
        read_only_fields = fields


class PurchaseInvoiceListQuerySerializer(serializers.Serializer):
    page = serializers.IntegerField(required=False, min_value=1, default=1)
    page_size = serializers.IntegerField(required=False, min_value=1, default=20)
    search = serializers.CharField(required=False, allow_blank=True, default='')
    sort_by = serializers.CharField(
        required=False, allow_blank=True, default='invoice_date'
    )
    sort_order = serializers.ChoiceField(
        choices=['asc', 'desc'],
        required=False,
        default='desc',
    )
    supplier_id = serializers.IntegerField(required=False)
    invoice_date = serializers.DateField(required=False)
    material_id = serializers.IntegerField(required=False)
    invoice_number = serializers.CharField(required=False, allow_blank=True)
