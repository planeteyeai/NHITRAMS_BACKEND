from rest_framework import serializers

class TrafficFilterRequestSerializer(serializers.Serializer):
    chainage_start = serializers.FloatField()
    chainage_end = serializers.FloatField()
    date = serializers.DateField(format='%Y-%m-%d', input_formats=['%Y-%m-%d'])
    direction = serializers.ListField(child=serializers.CharField())
    project_name = serializers.ListField(child=serializers.CharField())