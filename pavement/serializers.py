from rest_framework import serializers
from .models import Pavement


class PavementSerializer(serializers.ModelSerializer):
    project_name = serializers.CharField(source='project.project_name', read_only=True)
    district = serializers.CharField(source='project.district', read_only=True)
    date = serializers.DateField(source='project.project_info.month_val', read_only=True)
    chainage_start = serializers.FloatField(source='segment.chainage_start', read_only=True)
    chainage_end = serializers.FloatField(source='segment.chainage_end', read_only=True)
    direction = serializers.CharField(source='segment.direction', read_only=True)
    carriage_type = serializers.CharField(source='segment.carriage_type', read_only=True)

    class Meta:
        model = Pavement
        fields = '__all__'