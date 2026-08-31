from rest_framework import serializers


class AisFilterRequestSerializer(serializers.Serializer):
    chainage_start = serializers.FloatField()
    chainage_end = serializers.FloatField()
    date = serializers.DateField(format='%Y-%m-%d', input_formats=['%Y-%m-%d'])
    direction = serializers.ListField(child=serializers.CharField())
    project_name = serializers.ListField(child=serializers.CharField())

    def validate(self, data):
        if data['chainage_start'] > data['chainage_end']:
            raise serializers.ValidationError(
                'chainage_start must be less than or equal to chainage_end'
            )
        return data
