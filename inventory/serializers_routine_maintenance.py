"""Serializers for Routine Maintenance APIs."""

from __future__ import annotations

from datetime import datetime, timezone

from django.utils.dateparse import parse_datetime
from rest_framework import serializers

from .models import (
    RoutineMaintenanceCategory,
    RoutineMaintenanceReport,
    RoutineMaintenanceSubcategory,
    SeverityMaster,
    SideMaster,
)
from .serializers import _extract_retain_image_urls, _parse_url_list
from .services.routine_maintenance import resolve_project_details

RM_SERVER_OWNED_FIELDS = frozenset({
    'username',
    'project_id',
    'start_chainage',
    'end_chainage',
    'total_project_length',
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


def _parse_inspection_datetime(raw):
    if raw is None or raw == '':
        return datetime.now(timezone.utc)
    if isinstance(raw, datetime):
        value = raw
    else:
        text = str(raw).strip()
        value = parse_datetime(text)
        if value is None:
            for fmt in (
                '%Y-%m-%d %H:%M:%S',
                '%Y-%m-%dT%H:%M:%S',
                '%Y-%m-%dT%H:%M:%SZ',
                '%d-%m-%Y %H:%M:%S',
                '%d/%m/%Y %H:%M:%S',
                '%Y-%m-%d',
            ):
                try:
                    value = datetime.strptime(text.replace('Z', ''), fmt.replace('Z', ''))
                    break
                except ValueError:
                    continue
        if value is None:
            raise serializers.ValidationError('Invalid datetime format.')
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value


class RoutineMaintenanceReportWriteSerializer(serializers.Serializer):
    """Multipart create/update payload. Identity/chainage snapshot fields are rejected."""

    project_name = serializers.CharField(required=False, allow_blank=False, max_length=255)
    category_id = serializers.IntegerField(required=False)
    subcategory_id = serializers.IntegerField(required=False)
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

    # Multipart often sends datetimes as plain strings — parse in validate().
    inspection_datetime = serializers.CharField(required=False, allow_blank=True, allow_null=True)

    status = serializers.ChoiceField(
        choices=RoutineMaintenanceReport.STATUS_CHOICES,
        required=False,
        default=RoutineMaintenanceReport.STATUS_SUBMITTED,
    )

    # Image management (PATCH/PUT); ignored on create except unused
    keep_images = serializers.JSONField(required=False, allow_null=True)
    remove_images = serializers.JSONField(required=False, allow_null=True)
    clear_images = serializers.BooleanField(required=False, default=False)

    def to_internal_value(self, data):
        if hasattr(data, 'keys'):
            incoming = {str(key).lower() for key in data.keys()}
        else:
            incoming = set()
        blocked = incoming & {field.lower() for field in RM_SERVER_OWNED_FIELDS}
        if blocked:
            raise serializers.ValidationError(
                {
                    field: 'This field is resolved on the server and must not be sent.'
                    for field in sorted(blocked)
                }
            )

        # Normalize Latitude/Longitude aliases from the frontend contract.
        mutable = {}
        if hasattr(data, 'keys'):
            key_map = {str(k).lower(): k for k in data.keys()}
            keys_present = set(data.keys())
        else:
            key_map = {}
            keys_present = set()

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
            'category_id': ('category_id',),
            'subcategory_id': ('subcategory_id',),
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
            'inspection_datetime': ('inspection_datetime', 'date_time', 'Date_Time', 'datetime'),
            'status': ('status',),
            'clear_images': ('clear_images',),
        }
        for target, sources in field_sources.items():
            value = _get(*sources)
            if value is not None and value != '':
                mutable[target] = value

        if 'remove_images' in keys_present or 'remove_images' in key_map:
            mutable['remove_images'] = _get('remove_images')

        retain_present, retain_value = _extract_retain_image_urls(data)
        if retain_present:
            mutable['keep_images'] = retain_value

        if 'keep_images' in mutable:
            mutable['keep_images'] = _parse_url_list(mutable['keep_images'], 'keep_images')
        if 'remove_images' in mutable:
            mutable['remove_images'] = _parse_url_list(mutable['remove_images'], 'remove_images')

        if 'clear_images' in mutable:
            raw_clear = mutable['clear_images']
            if isinstance(raw_clear, str):
                mutable['clear_images'] = raw_clear.strip().lower() in {
                    '1', 'true', 'yes', 'on',
                }

        return super().to_internal_value(mutable)

    def validate_latitude(self, value):
        if value is None:
            return value
        if value < -90 or value > 90:
            raise serializers.ValidationError('Latitude must be between -90 and 90.')
        return value

    def validate_longitude(self, value):
        if value is None:
            return value
        if value < -180 or value > 180:
            raise serializers.ValidationError('Longitude must be between -180 and 180.')
        return value

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
        """Resolve category/subcategory for create or partial update."""
        instance = getattr(self, 'instance', None)
        updating_category = 'category_id' in attrs
        updating_subcategory = 'subcategory_id' in attrs

        if not updating_category and not updating_subcategory:
            if self.partial:
                return None, None
            raise serializers.ValidationError(
                {
                    'category_id': 'This field is required.',
                    'subcategory_id': 'This field is required.',
                }
            )

        category_id = attrs.get('category_id')
        subcategory_id = attrs.get('subcategory_id')

        if self.partial and instance is not None:
            if not updating_category:
                category_id = instance.category_id
            if not updating_subcategory:
                subcategory_id = instance.subcategory_id

        try:
            category = RoutineMaintenanceCategory.objects.get(pk=category_id, is_active=True)
        except RoutineMaintenanceCategory.DoesNotExist as exc:
            raise serializers.ValidationError(
                {'category_id': 'Invalid or inactive category_id.'}
            ) from exc

        try:
            subcategory = RoutineMaintenanceSubcategory.objects.get(
                pk=subcategory_id, is_active=True
            )
        except RoutineMaintenanceSubcategory.DoesNotExist as exc:
            raise serializers.ValidationError(
                {'subcategory_id': 'Invalid or inactive subcategory_id.'}
            ) from exc

        if subcategory.category_id != category.id:
            raise serializers.ValidationError(
                {'subcategory_id': 'subcategory_id does not belong to the selected category.'}
            )

        return category, subcategory

    def validate(self, attrs):
        if 'project_name' in attrs:
            attrs['project'] = resolve_project_details(attrs['project_name'])
        elif not self.partial:
            raise serializers.ValidationError({'project_name': ['This field is required.']})

        category, subcategory = self._resolve_category_pair(attrs)
        if category is not None:
            attrs['category'] = category
            attrs['subcategory'] = subcategory

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
            attrs['inspection_datetime'] = _parse_inspection_datetime(
                attrs.get('inspection_datetime')
            )
        elif not self.partial:
            attrs['inspection_datetime'] = _parse_inspection_datetime(None)

        return attrs


class RoutineMaintenanceReportReadSerializer(serializers.ModelSerializer):
    report_id = serializers.IntegerField(source='id', read_only=True)
    project = serializers.SerializerMethodField()
    category = serializers.SerializerMethodField()
    subcategory = serializers.SerializerMethodField()
    side = serializers.SerializerMethodField()
    severity = serializers.SerializerMethodField()

    class Meta:
        model = RoutineMaintenanceReport
        fields = [
            'report_id',
            'username',
            'project',
            'category',
            'subcategory',
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
            'uploaded_image_urls',
            'status',
            'created_at',
            'updated_at',
        ]
        read_only_fields = fields

    def get_project(self, obj):
        return {
            'project_name': obj.project_name,
            'start_chainage': obj.start_chainage,
            'end_chainage': obj.end_chainage,
            'total_project_length': obj.total_project_length,
        }

    def get_category(self, obj):
        return {'id': obj.category_id, 'name': obj.category.name}

    def get_subcategory(self, obj):
        return {'id': obj.subcategory_id, 'name': obj.subcategory.name}

    def get_side(self, obj):
        return {'id': obj.side_id, 'name': obj.side.name}

    def get_severity(self, obj):
        return {'id': obj.severity_id, 'name': obj.severity.name}
