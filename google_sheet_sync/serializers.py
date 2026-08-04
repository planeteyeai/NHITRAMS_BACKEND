"""Serializers for Google Sheets distress row sync."""

from __future__ import annotations

import uuid
from typing import Any

from rest_framework import serializers


# Numeric fields: blank → None; non-empty must be coercible to float.
NUMERIC_FIELDS = (
    'latitude',
    'longitude',
    'chainage_start',
    'chainage_end',
    'length',
    'area',
    'width',
    'depth',
)

# String / metric columns: trim; blank → None (except required fields below).
STRING_FIELDS = (
    'sync_id',
    'status',
    'project_name',
    'distress_type',
    'direction',
    'total_distress',
    'oblique_crack',
    'pothole',
    'edge_break',
    'patching',
    'bleeding',
    'hotspots',
    'alligator_crack',
    'roughness',
    'repair',
    'block_crack',
    'rutting',
    'longitudinal_crack',
    'raveling',
    'date',
    'carriage_type',
    'single_discrete_crack',
    'multiple_cracks',
    'joint_crack',
    'joint_seal_defects',
    'punchout',
    'slippage',
    'heaves',
    'simple_crack',
    'transverse_crack',
    'lane',
    'hairline_crack',
    'hungry_surface',
    'settlement',
    'shoving',
    'stripping',
    'faulting_stepping_in_cracks',
    'popout_small_hole',
    'single_longitudinal_crack',
    'blowup_or_bulking',
    'polished_surface_glazing',
    'depression',
    'image_url',
)

# Keep blank as "" so required validators fire with clear messages.
REQUIRED_STRING_FIELDS = frozenset({'project_name', 'distress_type'})


def _is_blank(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str) and value.strip() == '':
        return True
    return False


class GoogleSheetSyncSerializer(serializers.Serializer):
    """Validate one Google Sheets row payload before upsert."""

    sync_id = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    status = serializers.CharField(required=False, allow_blank=True, allow_null=True)

    latitude = serializers.FloatField(required=False, allow_null=True)
    longitude = serializers.FloatField(required=False, allow_null=True)
    chainage_start = serializers.FloatField(required=False, allow_null=True)
    chainage_end = serializers.FloatField(required=False, allow_null=True)

    project_name = serializers.CharField(required=True, allow_blank=False, max_length=255)
    distress_type = serializers.CharField(required=True, allow_blank=False, max_length=255)

    direction = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    total_distress = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    oblique_crack = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    pothole = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    edge_break = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    patching = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    bleeding = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    hotspots = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    alligator_crack = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    roughness = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    repair = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    block_crack = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    rutting = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    longitudinal_crack = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    raveling = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    date = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    length = serializers.FloatField(required=False, allow_null=True)
    area = serializers.FloatField(required=False, allow_null=True)
    carriage_type = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    single_discrete_crack = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    multiple_cracks = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    joint_crack = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    joint_seal_defects = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    punchout = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    slippage = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    heaves = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    simple_crack = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    transverse_crack = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    width = serializers.FloatField(required=False, allow_null=True)
    depth = serializers.FloatField(required=False, allow_null=True)
    lane = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    hairline_crack = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    hungry_surface = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    settlement = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    shoving = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    stripping = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    faulting_stepping_in_cracks = serializers.CharField(
        required=False, allow_blank=True, allow_null=True
    )
    popout_small_hole = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    single_longitudinal_crack = serializers.CharField(
        required=False, allow_blank=True, allow_null=True
    )
    blowup_or_bulking = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    polished_surface_glazing = serializers.CharField(
        required=False, allow_blank=True, allow_null=True
    )
    depression = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    image_url = serializers.CharField(required=False, allow_blank=True, allow_null=True, max_length=2048)

    def to_internal_value(self, data):
        """Trim strings and convert blank values to None before field validation."""
        if not isinstance(data, dict):
            return super().to_internal_value(data)

        cleaned: dict[str, Any] = {}
        for key, value in data.items():
            if key in STRING_FIELDS:
                if _is_blank(value):
                    # Required fields stay as '' so CharField(required) fails clearly.
                    cleaned[key] = '' if key in REQUIRED_STRING_FIELDS else None
                elif isinstance(value, str):
                    cleaned[key] = value.strip()
                else:
                    cleaned[key] = value
            elif key in NUMERIC_FIELDS:
                if _is_blank(value):
                    cleaned[key] = None
                else:
                    cleaned[key] = value
            else:
                cleaned[key] = value

        return super().to_internal_value(cleaned)

    def validate_latitude(self, value):
        if value is None:
            return value
        if not -90 <= value <= 90:
            raise serializers.ValidationError('Latitude must be between -90 and 90.')
        return value

    def validate_longitude(self, value):
        if value is None:
            return value
        if not -180 <= value <= 180:
            raise serializers.ValidationError('Longitude must be between -180 and 180.')
        return value

    def validate_project_name(self, value):
        if value is None or str(value).strip() == '':
            raise serializers.ValidationError('Project Name is required.')
        return str(value).strip()

    def validate_distress_type(self, value):
        if value is None or str(value).strip() == '':
            raise serializers.ValidationError('Distress Type is required.')
        return str(value).strip()

    def validate_sync_id(self, value):
        """Empty sync_id means create; non-empty must be a valid UUID."""
        if value is None or value == '':
            return None
        try:
            return str(uuid.UUID(str(value)))
        except (ValueError, AttributeError, TypeError) as exc:
            raise serializers.ValidationError('sync_id must be a valid UUID.') from exc
