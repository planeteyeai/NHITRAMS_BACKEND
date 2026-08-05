"""Serializers for RAMS APK Inventory Report APIs."""

from __future__ import annotations

from rest_framework import serializers

from inventory.models import (
    InventoryCategory,
    InventoryReport,
    InventorySubcategory,
    SeverityMaster,
    SideMaster,
)
from inventory.services.inventory_report import parse_inventory_inspection_datetime
from inventory.services.routine_maintenance import resolve_project_details

INV_SERVER_OWNED_FIELDS = frozenset({
    'username',
    'project_id',
    'start_chainage',
    'end_chainage',
    'total_project_length',
    'report_id',
    'created_at',
    'updated_at',
})


def _non_negative_float(value) -> float:
    if value is None or value == '':
        return 0.0
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise serializers.ValidationError('Must be a number.') from exc
    if number < 0:
        raise serializers.ValidationError('Must be greater than or equal to 0.')
    return number


class InventoryReportWriteSerializer(serializers.Serializer):
    """Create/update payload for the RAMS APK inventory flow."""

    project_name = serializers.CharField(required=False, allow_blank=False, max_length=255)
    inventory_category_id = serializers.IntegerField(required=False)
    inventory_subcategory_id = serializers.IntegerField(required=False)
    side_id = serializers.IntegerField(required=False)
    severity_id = serializers.IntegerField(required=False)

    description_of_work = serializers.CharField(required=False, allow_blank=True, default='')
    nos = serializers.FloatField(required=False, default=0)
    length = serializers.FloatField(required=False, default=0)
    width = serializers.FloatField(required=False, default=0)
    depth = serializers.FloatField(required=False, default=0)
    total_quantity = serializers.FloatField(required=False, default=0)

    latitude = serializers.FloatField(required=False)
    longitude = serializers.FloatField(required=False)
    chainage = serializers.CharField(max_length=100, required=False)
    inspection_datetime = serializers.CharField(required=False, allow_blank=True, allow_null=True)

    Image_URL = serializers.CharField(required=False, allow_blank=True, default='')
    Video_URL = serializers.CharField(required=False, allow_blank=True, default='')
    Source = serializers.CharField(required=False, allow_blank=True, default='')

    def to_internal_value(self, data):
        if hasattr(data, 'keys'):
            incoming = {str(key).lower() for key in data.keys()}
        else:
            incoming = set()
        blocked = incoming & {field.lower() for field in INV_SERVER_OWNED_FIELDS}
        if blocked:
            raise serializers.ValidationError(
                {
                    field: 'This field is resolved on the server and must not be sent.'
                    for field in sorted(blocked)
                }
            )

        mutable = {}
        if hasattr(data, 'keys'):
            key_map = {str(k).lower(): k for k in data.keys()}
        else:
            key_map = {}

        def _get(*names):
            for name in names:
                if name in data:
                    return data.get(name)
                lower = name.lower()
                if lower in key_map:
                    return data.get(key_map[lower])
            return None

        field_sources = {
            'project_name': ('project_name',),
            'inventory_category_id': ('inventory_category_id', 'category_id'),
            'inventory_subcategory_id': ('inventory_subcategory_id', 'subcategory_id'),
            'side_id': ('side_id',),
            'severity_id': ('severity_id',),
            'description_of_work': ('description_of_work',),
            'nos': ('nos',),
            'length': ('length',),
            'width': ('width',),
            'depth': ('depth',),
            'total_quantity': ('total_quantity',),
            'latitude': ('latitude', 'Latitude'),
            'longitude': ('longitude', 'Longitude'),
            'chainage': ('chainage',),
            'inspection_datetime': (
                'inspection_datetime',
                'date_time',
                'Date_Time',
                'datetime',
                'Date',
            ),
            'Image_URL': ('Image_URL', 'image_url'),
            'Video_URL': ('Video_URL', 'video_url'),
            'Source': ('Source', 'source'),
        }
        for target, sources in field_sources.items():
            value = _get(*sources)
            if value is not None and value != '':
                mutable[target] = value

        return super().to_internal_value(mutable)

    def validate_nos(self, value):
        return _non_negative_float(value)

    def validate_length(self, value):
        return _non_negative_float(value)

    def validate_width(self, value):
        return _non_negative_float(value)

    def validate_depth(self, value):
        return _non_negative_float(value)

    def validate_total_quantity(self, value):
        return _non_negative_float(value)

    def _resolve_category_pair(self, attrs):
        instance = getattr(self, 'instance', None)
        updating_category = 'inventory_category_id' in attrs
        updating_subcategory = 'inventory_subcategory_id' in attrs

        if not updating_category and not updating_subcategory:
            if self.partial:
                return None, None
            raise serializers.ValidationError(
                {
                    'inventory_category_id': 'This field is required.',
                    'inventory_subcategory_id': 'This field is required.',
                }
            )

        category_id = attrs.get('inventory_category_id')
        subcategory_id = attrs.get('inventory_subcategory_id')

        if self.partial and instance is not None:
            if not updating_category:
                category_id = instance.inventory_category_id
            if not updating_subcategory:
                subcategory_id = instance.inventory_subcategory_id

        try:
            category = InventoryCategory.objects.get(pk=category_id, is_active=True)
        except InventoryCategory.DoesNotExist as exc:
            raise serializers.ValidationError(
                {'inventory_category_id': 'Invalid or inactive inventory_category_id.'}
            ) from exc

        try:
            subcategory = InventorySubcategory.objects.get(
                pk=subcategory_id, is_active=True
            )
        except InventorySubcategory.DoesNotExist as exc:
            raise serializers.ValidationError(
                {'inventory_subcategory_id': 'Invalid or inactive inventory_subcategory_id.'}
            ) from exc

        if subcategory.category_id != category.id:
            raise serializers.ValidationError(
                {
                    'inventory_subcategory_id': (
                        'inventory_subcategory_id does not belong to the selected category.'
                    )
                }
            )

        return category, subcategory

    def validate(self, attrs):
        # Project is required on create only; immutable on update.
        if self.instance is None:
            if 'project_name' not in attrs:
                raise serializers.ValidationError(
                    {'project_name': ['This field is required.']}
                )
            attrs['project'] = resolve_project_details(attrs['project_name'])
        else:
            attrs.pop('project_name', None)
            attrs.pop('project', None)

        category, subcategory = self._resolve_category_pair(attrs)
        if category is not None:
            attrs['inventory_category'] = category
            attrs['inventory_subcategory'] = subcategory

        if 'side_id' in attrs:
            try:
                attrs['side'] = SideMaster.objects.get(pk=attrs['side_id'], is_active=True)
            except SideMaster.DoesNotExist as exc:
                raise serializers.ValidationError(
                    {'side_id': 'Invalid or inactive side_id.'}
                ) from exc
        elif not self.partial:
            raise serializers.ValidationError({'side_id': 'This field is required.'})

        if 'severity_id' in attrs:
            try:
                attrs['severity'] = SeverityMaster.objects.get(
                    pk=attrs['severity_id'], is_active=True
                )
            except SeverityMaster.DoesNotExist as exc:
                raise serializers.ValidationError(
                    {'severity_id': 'Invalid or inactive severity_id.'}
                ) from exc
        elif not self.partial:
            raise serializers.ValidationError({'severity_id': 'This field is required.'})

        if 'latitude' in attrs:
            if attrs['latitude'] < -90 or attrs['latitude'] > 90:
                raise serializers.ValidationError(
                    {'latitude': 'Latitude must be between -90 and 90.'}
                )
        elif not self.partial:
            raise serializers.ValidationError({'latitude': 'This field is required.'})

        if 'longitude' in attrs:
            if attrs['longitude'] < -180 or attrs['longitude'] > 180:
                raise serializers.ValidationError(
                    {'longitude': 'Longitude must be between -180 and 180.'}
                )
        elif not self.partial:
            raise serializers.ValidationError({'longitude': 'This field is required.'})

        if 'chainage' not in attrs and not self.partial:
            raise serializers.ValidationError({'chainage': 'This field is required.'})

        if 'inspection_datetime' in attrs:
            attrs['inspection_datetime'] = parse_inventory_inspection_datetime(
                attrs.get('inspection_datetime')
            )
        elif not self.partial:
            attrs['inspection_datetime'] = parse_inventory_inspection_datetime(None)

        return attrs


class InventoryReportReadSerializer(serializers.ModelSerializer):
    report_id = serializers.IntegerField(source='id', read_only=True)
    project = serializers.SerializerMethodField()
    inventory_category = serializers.SerializerMethodField()
    inventory_subcategory = serializers.SerializerMethodField()
    side = serializers.SerializerMethodField()
    severity = serializers.SerializerMethodField()

    class Meta:
        model = InventoryReport
        fields = [
            'report_id',
            'username',
            'project',
            'inventory_category',
            'inventory_subcategory',
            'side',
            'severity',
            'latitude',
            'longitude',
            'chainage',
            'inspection_datetime',
            'description_of_work',
            'nos',
            'length',
            'width',
            'depth',
            'total_quantity',
            'Image_URL',
            'Video_URL',
            'created_at',
            'updated_at',
        ]
        read_only_fields = fields

    def get_project(self, obj):
        return {
            'project_name': obj.project_name or obj.Project_Name,
            'start_chainage': obj.start_chainage,
            'end_chainage': obj.end_chainage,
            'total_project_length': obj.total_project_length,
        }

    def get_inventory_category(self, obj):
        if not obj.inventory_category_id:
            return None
        return {'id': obj.inventory_category_id, 'name': obj.inventory_category.name}

    def get_inventory_subcategory(self, obj):
        if not obj.inventory_subcategory_id:
            return None
        return {
            'id': obj.inventory_subcategory_id,
            'name': obj.inventory_subcategory.name,
        }

    def get_side(self, obj):
        if not obj.side_id:
            return None
        return {'id': obj.side_id, 'name': obj.side.name}

    def get_severity(self, obj):
        if not obj.severity_id:
            return None
        return {'id': obj.severity_id, 'name': obj.severity.name}
