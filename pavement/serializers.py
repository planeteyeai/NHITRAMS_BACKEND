from rest_framework import serializers


class PmsFilterRequestSerializer(serializers.Serializer):
    chainage_start = serializers.FloatField(required=True)
    chainage_end = serializers.FloatField(required=True)
    date = serializers.DateField(required=True)
    direction = serializers.ListField(
        child=serializers.CharField(),
        allow_empty=False,
        required=True,
    )
    project_name = serializers.ListField(
        child=serializers.CharField(),
        allow_empty=False,
        required=True,
    )
