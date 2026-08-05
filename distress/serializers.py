from rest_framework import serializers

from .models import DistressReport


class DistressReportWriteSerializer(serializers.ModelSerializer):
    """Accepts the exact PascalCase payload used by the legacy Excel API.

    Username is intentionally excluded — it is set from the JWT in the view.
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
            'Carriage_Type': {'required': False, 'allow_blank': True},
            'Width': {'required': False},
            'Depth': {'required': False},
            'Lane': {'required': False, 'allow_blank': True},
            'No_of_Distress': {'required': False},
            'Image_URL': {'required': False, 'allow_blank': True},
        }

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
    chainage_start = serializers.FloatField(required=False, min_value=0)
    chainage_end = serializers.FloatField(required=False, min_value=0)
    date = serializers.DateField(required=False, format='%Y-%m-%d', input_formats=['%Y-%m-%d'])
    direction = serializers.ListField(
        child=serializers.CharField(max_length=100),
        required=False
    )
    project_name = serializers.ListField(
        child=serializers.CharField(max_length=100),
        required=False
    )
    distress_type = serializers.ListField(
        child=serializers.CharField(max_length=100),
        required=False
    )

    def validate(self, data):
        if 'chainage_start' in data and 'chainage_end' in data:
            if data['chainage_start'] > data['chainage_end']:
                raise serializers.ValidationError("chainage_start must be less than or equal to chainage_end")
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