from rest_framework import serializers
from .models import RoadsideFeatures


class RWFISFilterSerializer(serializers.Serializer):
    chainage_start = serializers.FloatField(required=False)
    chainage_end = serializers.FloatField(required=False)
    date = serializers.DateField(required=False)
    direction = serializers.ListField(
        child=serializers.CharField(),
        required=False
    )
    project_name = serializers.ListField(
        child=serializers.CharField(),
        required=False
    )
    page = serializers.IntegerField(required=False, default=1)
    page_size = serializers.IntegerField(required=False, default=100)


class RoadsideFeaturesSerializer(serializers.ModelSerializer):
    # Renamed fields
    feature = serializers.CharField(source='feature_desc', read_only=True)
    safety_hazard_y_n = serializers.CharField(source='safety_hazard', read_only=True)
    length = serializers.FloatField(source='length_val', read_only=True)
    offset_from_center_line = serializers.FloatField(source='offset_from_center', read_only=True)
    # From related
    project_name = serializers.CharField(source='project.project_name', read_only=True)
    district = serializers.CharField(source='project.district', read_only=True)
    block_taluka = serializers.CharField(source='project.block_taluka', read_only=True)
    chainage_start = serializers.FloatField(source='segment.chainage_start', read_only=True)
    chainage_end = serializers.FloatField(source='segment.chainage_end', read_only=True)
    direction = serializers.CharField(source='segment.direction', read_only=True)
    carriage_type = serializers.CharField(source='segment.carriage_type', read_only=True)
    lane = serializers.CharField(source='segment.lane', read_only=True)
    # From project_info
    date = serializers.DateField(source='project.project_info.month_val', read_only=True)

    class Meta:
        model = RoadsideFeatures
        fields = [
            'feature', 'material_type', 'feature_condition', 'safety_hazard_y_n', 'land_use', 'terrain',
            'latitude', 'longitude', 'altitude', 'remarks', 'length', 'cross_section_location', 'offset_from_center_line',
            'chainage_start', 'chainage_end', 'direction', 'carriage_type', 'lane',
            'project_name', 'district', 'block_taluka', 'date'
        ]