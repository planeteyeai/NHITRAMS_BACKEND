from rest_framework import serializers

from .models import InventoryReport


class InventoryReportWriteSerializer(serializers.ModelSerializer):
    """Accepts the exact payload used by the legacy Excel API.

    Username is intentionally excluded — it is set from the JWT in the view.
    """

    class Meta:
        model = InventoryReport
        fields = [
            'Project_Name',
            'Chainage_start',
            'Chainage_end',
            'Direction',
            'Asset_type',
            'Latitude',
            'Longitude',
            'Date',
            'Sub_Asset_Type',
            'Carriage_Type',
            'Lane',
            'No_of_inventories',
            'Image_URL',
            'Video_URL',
        ]
        extra_kwargs = {
            'Project_Name': {'required': True, 'allow_blank': False},
            'Chainage_start': {'required': True},
            'Chainage_end': {'required': True},
            'Direction': {'required': True, 'allow_blank': False},
            'Asset_type': {'required': True, 'allow_blank': False},
            'Latitude': {'required': True},
            'Longitude': {'required': True},
            'Date': {'required': True, 'allow_blank': False},
            'Sub_Asset_Type': {'required': False, 'allow_blank': True},
            'Carriage_Type': {'required': False, 'allow_blank': True},
            'Lane': {'required': False, 'allow_blank': True},
            'No_of_inventories': {'required': False},
            'Image_URL': {'required': False, 'allow_blank': True},
            'Video_URL': {'required': False, 'allow_blank': True},
        }

    def validate(self, data):
        chainage_start = data.get('Chainage_start')
        chainage_end = data.get('Chainage_end')
        if (
            chainage_start is not None
            and chainage_end is not None
            and chainage_start > chainage_end
        ):
            raise serializers.ValidationError(
                {'Chainage_end': 'Chainage_end must be greater than or equal to Chainage_start.'}
            )
        return data


class InventoryReportReadSerializer(serializers.ModelSerializer):
    """Full report representation preserving legacy field names."""

    class Meta:
        model = InventoryReport
        fields = [
            'id',
            'username',
            'Project_Name',
            'Chainage_start',
            'Chainage_end',
            'Direction',
            'Asset_type',
            'Latitude',
            'Longitude',
            'Date',
            'Sub_Asset_Type',
            'Carriage_Type',
            'Lane',
            'No_of_inventories',
            'Image_URL',
            'Video_URL',
            'created_at',
            'updated_at',
        ]
        read_only_fields = fields


class InventoryFilterSerializer(serializers.Serializer):
    chainage_start = serializers.FloatField()
    chainage_end = serializers.FloatField()
    date = serializers.DateField(format='%Y-%m-%d', input_formats=['%Y-%m-%d'])
    direction = serializers.ListField(
        child=serializers.CharField(max_length=100)
    )
    project_name = serializers.ListField(
        child=serializers.CharField(max_length=100)
    )
    asset_type = serializers.ListField(
        child=serializers.CharField(max_length=100)
    )

    def validate(self, data):
        if data['chainage_start'] > data['chainage_end']:
            raise serializers.ValidationError("chainage_start must be less than or equal to chainage_end")
        return data

class InventoryResponseSerializer(serializers.Serializer):
    project_name = serializers.CharField()
    chainage_start = serializers.FloatField()
    chainage_end = serializers.FloatField()
    direction = serializers.CharField()
    asset_type = serializers.CharField()
    trees = serializers.FloatField()
    culvert = serializers.FloatField()
    street_lights = serializers.FloatField()
    bridges = serializers.FloatField()
    traffic_signals = serializers.FloatField()
    bus_stop = serializers.FloatField()
    truck_layby = serializers.FloatField()
    toll_plaza = serializers.FloatField()
    adjacent_road = serializers.FloatField()
    toilet_blocks = serializers.FloatField()
    rest_area = serializers.FloatField()
    rcc_drain = serializers.FloatField()
    fuel_station = serializers.FloatField()
    emergency_call_box = serializers.FloatField()
    tunnels = serializers.FloatField()
    footpath = serializers.FloatField()
    junction = serializers.FloatField()
    sign_boards = serializers.FloatField()
    solar_blinker = serializers.FloatField()
    median_plants = serializers.FloatField()
    service_road = serializers.FloatField()
    km_stones = serializers.FloatField()
    crash_barrier = serializers.FloatField()
    median_opening = serializers.CharField()
    latitude = serializers.FloatField()
    longitude = serializers.FloatField()
    date = serializers.DateField()
    sub_asset_type = serializers.CharField(allow_null=True)
    carriage_type = serializers.CharField()
    lane = serializers.CharField()