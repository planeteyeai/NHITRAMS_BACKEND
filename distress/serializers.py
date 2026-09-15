from rest_framework import serializers

from .models import DistressReport
from .services.distress_predict_filter_response import (
    DEFAULT_DISTRESS_PREDICT_FILTER_PAGE_SIZE,
    MAX_DISTRESS_PREDICT_FILTER_PAGE_SIZE,
)


class DistressReportWriteSerializer(serializers.ModelSerializer):
    """Accepts the exact PascalCase payload used by the legacy Excel API.

    Username is intentionally excluded — it is set from the JWT in the view.
    Carriage_Type is required and limited to Rigid / Flexible (dropdown).
    """

    class Meta:
        model = DistressReport
        fields = [
            'Latitude',
            'Longitude',
            'Chainage_Start',
            'Chainage_End',
            'Project_Name',
            'Distress_Type',
            'Direction',
            'Date',
            'Length',
            'Carriage_Type',
            'Width',
            'Depth',
            'Lane',
            'No_of_Distress',
            'Image_URL',
        ]
        extra_kwargs = {
            'Latitude': {'required': True},
            'Longitude': {'required': True},
            'Chainage_Start': {'required': True},
            'Chainage_End': {'required': True},
            'Project_Name': {'required': True, 'allow_blank': False},
            'Distress_Type': {'required': True, 'allow_blank': False},
            'Direction': {'required': True, 'allow_blank': False},
            'Date': {'required': True, 'allow_blank': False},
            'Length': {'required': False},
            'Carriage_Type': {
                'required': True,
                'allow_blank': False,
                'help_text': 'Carriage type dropdown: Rigid or Flexible.',
            },
            'Width': {'required': False},
            'Depth': {'required': False},
            'Lane': {'required': False, 'allow_blank': True},
            'No_of_Distress': {'required': False},
            'Image_URL': {'required': False, 'allow_blank': True},
        }

    def to_internal_value(self, data):
        # Accept snake_case alias from newer FE clients.
        if hasattr(data, 'keys'):
            mutable = {}
            if hasattr(data, 'copy'):
                mutable = data.copy()
                if not isinstance(mutable, dict):
                    mutable = dict(mutable)
            else:
                mutable = dict(data)
            if 'Carriage_Type' not in mutable and 'carriage_type' in mutable:
                mutable['Carriage_Type'] = mutable.get('carriage_type')
            data = mutable
        return super().to_internal_value(data)

    def validate_Carriage_Type(self, value):
        text = str(value or '').strip()
        if not text:
            raise serializers.ValidationError('Carriage_Type is required.')
        key = text.casefold()
        if key not in {'rigid', 'flexible'}:
            raise serializers.ValidationError(
                'Carriage_Type must be Rigid or Flexible.'
            )
        return key.capitalize()

    def validate(self, data):
        instance = getattr(self, 'instance', None)
        chainage_start = data.get(
            'Chainage_Start',
            getattr(instance, 'Chainage_Start', None) if instance is not None else None,
        )
        chainage_end = data.get(
            'Chainage_End',
            getattr(instance, 'Chainage_End', None) if instance is not None else None,
        )
        if (
            chainage_start is not None
            and chainage_end is not None
            and chainage_start > chainage_end
        ):
            raise serializers.ValidationError(
                {'Chainage_End': 'Chainage_End must be greater than or equal to Chainage_Start.'}
            )
        return data


class DistressReportReadSerializer(serializers.ModelSerializer):
    """Full report representation preserving PascalCase field names."""

    class Meta:
        model = DistressReport
        fields = [
            'id',
            'username',
            'Latitude',
            'Longitude',
            'Chainage_Start',
            'Chainage_End',
            'Project_Name',
            'Distress_Type',
            'Direction',
            'Date',
            'Length',
            'Carriage_Type',
            'Width',
            'Depth',
            'Lane',
            'No_of_Distress',
            'Image_URL',
            'created_at',
            'updated_at',
        ]
        read_only_fields = fields


class DistressFilterSerializer(serializers.Serializer):
    """Request body for distress_report_filter / distress_predict_filter."""

    chainage_start = serializers.FloatField(required=False, min_value=0)
    chainage_end = serializers.FloatField(required=False, min_value=0)
    date = serializers.DateField(
        required=False,
        format='%Y-%m-%d',
        input_formats=['%Y-%m-%d'],
    )
    direction = serializers.ListField(
        child=serializers.CharField(max_length=100),
        required=False,
        help_text='Direction values, or ["All"] to skip direction filter.',
    )
    project_name = serializers.ListField(
        child=serializers.CharField(max_length=100),
        required=False,
        help_text='Project names, or ["All"] to skip project filter.',
    )
    distress_type = serializers.ListField(
        child=serializers.CharField(max_length=100),
        required=False,
        help_text='Distress types, or ["All"] to skip type filter.',
    )
    carriage_type = serializers.ListField(
        child=serializers.CharField(max_length=100),
        required=False,
        help_text='Carriage types: Rigid, Flexible, or ["All"] to skip.',
    )
    page = serializers.IntegerField(required=False, default=1, min_value=1)
    page_size = serializers.IntegerField(
        required=False,
        default=DEFAULT_DISTRESS_PREDICT_FILTER_PAGE_SIZE,
        min_value=1,
        max_value=MAX_DISTRESS_PREDICT_FILTER_PAGE_SIZE,
    )

    def validate_carriage_type(self, value):
        allowed = {'all', 'rigid', 'flexible'}
        normalized = []
        for raw in value or []:
            text = str(raw or '').strip()
            if not text:
                continue
            key = text.casefold()
            if key not in allowed:
                raise serializers.ValidationError(
                    'carriage_type values must be Rigid, Flexible, or All.'
                )
            if key == 'all':
                normalized.append('All')
            else:
                normalized.append(key.capitalize())
        return normalized

    def validate(self, data):
        if 'chainage_start' in data and 'chainage_end' in data:
            if data['chainage_start'] > data['chainage_end']:
                raise serializers.ValidationError(
                    'chainage_start must be less than or equal to chainage_end'
                )
        return data

class DistressReportSerializer(serializers.Serializer):
    project_name = serializers.CharField(max_length=100)
    segment_id = serializers.IntegerField()
    chainage_start = serializers.FloatField()
    chainage_end = serializers.FloatField()
    direction = serializers.CharField(max_length=100)
    distress_type = serializers.CharField(max_length=100)
    total_count = serializers.IntegerField()
    latitude = serializers.FloatField()
    longitude = serializers.FloatField()

class DistressSummarySerializer(serializers.Serializer):
    # For the summary endpoint, it's a dict of distress_type: count
    # But since it's dynamic, perhaps use a DictField
    pass  # We'll handle response differently for summary

class ChainageSummarySerializer(serializers.Serializer):
    dataset = serializers.ChoiceField(choices=[
        'distress_predicted', 'distress_predicted2', 'distress_reported', 'distress_reported2',
        'inventory', 'inventory2', 'pis', 'pis2', 'tis', 'tis2', 'pms', 'pms2',
        'rwfis', 'rwfis2', 'ais', 'ais2', 'bms'
    ], required=True)
    project_name = serializers.CharField(required=False, allow_blank=True)