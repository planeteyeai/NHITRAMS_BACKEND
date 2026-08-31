"""Material serializers — validation here; persistence in services."""

from __future__ import annotations

from rest_framework import serializers

from .constants import (
    MATERIAL_DESCRIPTION_MAX_LENGTH,
    MATERIAL_NAME_MAX_LENGTH,
    MATERIAL_TYPE_MAX_LENGTH,
    MATERIAL_UNIT_MAX_LENGTH,
    OTHER_TYPE_MAX_LENGTH,
    is_other_material_type,
)
from .models import Material


def _required_text(value, field_name: str, *, max_length: int) -> str:
    text = str(value or '').strip()
    if not text:
        raise serializers.ValidationError(f'{field_name} is required.')
    if len(text) > max_length:
        raise serializers.ValidationError(
            f'{field_name} must be at most {max_length} characters.'
        )
    return text


class MaterialWriteSerializer(serializers.Serializer):
    """Create / full update / partial update payload for Material."""

    material_name = serializers.CharField(
        max_length=MATERIAL_NAME_MAX_LENGTH,
        required=True,
        allow_blank=False,
        trim_whitespace=True,
    )
    material_unit = serializers.CharField(
        max_length=MATERIAL_UNIT_MAX_LENGTH,
        required=True,
        allow_blank=False,
        trim_whitespace=True,
    )
    material_type = serializers.CharField(
        max_length=MATERIAL_TYPE_MAX_LENGTH,
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
    material_description = serializers.CharField(
        max_length=MATERIAL_DESCRIPTION_MAX_LENGTH,
        required=False,
        allow_blank=True,
        allow_null=True,
        trim_whitespace=True,
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.partial:
            for field in self.fields.values():
                field.required = False

    def validate_material_name(self, value):
        return _required_text(value, 'material_name', max_length=MATERIAL_NAME_MAX_LENGTH)

    def validate_material_unit(self, value):
        return _required_text(value, 'material_unit', max_length=MATERIAL_UNIT_MAX_LENGTH)

    def validate_material_type(self, value):
        return _required_text(value, 'material_type', max_length=MATERIAL_TYPE_MAX_LENGTH)

    def validate_other_type(self, value):
        if value is None:
            return ''
        return str(value).strip()

    def validate_material_description(self, value):
        if value is None:
            return ''
        return str(value).strip()


class MaterialReadSerializer(serializers.ModelSerializer):
    other_type = serializers.SerializerMethodField()

    class Meta:
        model = Material
        fields = [
            'id',
            'material_name',
            'material_unit',
            'material_type',
            'other_type',
            'material_description',
            'created_by',
            'updated_by',
            'created_at',
            'updated_at',
        ]
        read_only_fields = fields

    def get_other_type(self, obj):
        if is_other_material_type(obj.material_type):
            return obj.other_type or None
        return None


class MaterialListQuerySerializer(serializers.Serializer):
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
    material_unit = serializers.CharField(required=False, allow_blank=True)
    material_type = serializers.CharField(required=False, allow_blank=True)
