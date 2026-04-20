from rest_framework import serializers

class InventoryFilterSerializer(serializers.Serializer):
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
    asset_type = serializers.ListField(
        child=serializers.CharField(max_length=100),
        required=True  # As per requirements
    )

    def validate(self, data):
        if 'chainage_start' in data and 'chainage_end' in data:
            if data['chainage_start'] > data['chainage_end']:
                raise serializers.ValidationError("chainage_start must be less than or equal to chainage_end")
        if not data.get('asset_type'):
            raise serializers.ValidationError("asset_type is required")
        return data

class InventoryResponseSerializer(serializers.Serializer):
    # Dynamic fields will be added in the view
    pass