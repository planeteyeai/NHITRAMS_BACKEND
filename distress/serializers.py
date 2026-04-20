from rest_framework import serializers

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