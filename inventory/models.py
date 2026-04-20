from django.db import models

class AssetType(models.Model):
    asset_type_id = models.AutoField(primary_key=True)
    asset_type_name = models.CharField(max_length=100)

    class Meta:
        managed = False
        db_table = 'asset_types'

    def __str__(self):
        return self.asset_type_name

class Project(models.Model):
    project_id = models.AutoField(primary_key=True)
    project_name = models.CharField(max_length=255)
    state = models.CharField(max_length=100)
    district = models.CharField(max_length=100)
    block_taluka = models.CharField(max_length=100)
    road_code = models.CharField(max_length=255)
    originating = models.CharField(max_length=255)
    terminating = models.CharField(max_length=255)
    length_km = models.FloatField()

    class Meta:
        managed = False
        db_table = 'projects'

    def __str__(self):
        return self.project_name

class SubAssetType(models.Model):
    sub_asset_type_id = models.AutoField(primary_key=True)
    sub_asset_type_name = models.CharField(max_length=100)

    class Meta:
        managed = False
        db_table = 'sub_asset_types'

    def __str__(self):
        return self.sub_asset_type_name

class RoadSegment(models.Model):
    segment_id = models.AutoField(primary_key=True)
    project = models.ForeignKey(Project, on_delete=models.CASCADE, db_column="project_id")
    chainage_start = models.FloatField()
    chainage_end = models.FloatField()
    direction = models.CharField(max_length=100)
    lane = models.CharField(max_length=10)
    carriage_type = models.CharField(max_length=50)

    class Meta:
        managed = False
        db_table = 'road_segments'

    def __str__(self):
        return f"Segment {self.segment_id}"

class Inventory(models.Model):
    inventory_id = models.AutoField(primary_key=True)
    segment = models.ForeignKey(RoadSegment, on_delete=models.CASCADE)
    project = models.ForeignKey(Project, on_delete=models.CASCADE)
    asset_type = models.ForeignKey(AssetType, on_delete=models.CASCADE)
    sub_asset_type = models.ForeignKey(SubAssetType, on_delete=models.CASCADE, null=True, blank=True)
    survey_date = models.DateField()
    latitude = models.FloatField()
    longitude = models.FloatField()
    count_val = models.FloatField()

    class Meta:
        managed = False
        db_table = 'inventory'

    def __str__(self):
        return f"Inventory {self.inventory_id}"

class ProjectInfo(models.Model):
    info_id = models.AutoField(primary_key=True)
    project_id = models.IntegerField(unique=True)
    month_val = models.DateField()
    overall_progress = models.CharField(max_length=255)
    longitude = models.FloatField()
    latitude = models.FloatField()
    kilometer = models.FloatField()
    length = models.FloatField(db_column="length_val")
    toll_plaza_location = models.CharField(max_length=255)
    previous_date = models.CharField(max_length=10, null=True, blank=True)
    next_date = models.CharField(max_length=10, null=True, blank=True)
    carriage_width = models.FloatField()
    mjb = models.IntegerField()
    mnb = models.IntegerField()
    flyover = models.IntegerField()
    aadt = models.IntegerField()
    pup = models.IntegerField()
    vup = models.IntegerField()
    rob = models.IntegerField()

    class Meta:
        managed = False
        db_table = "project_info"

    def __str__(self):
        return f"ProjectInfo {self.info_id}"

class Traffic(models.Model):
    traffic_id = models.AutoField(primary_key=True, db_column='traffic_id')
    project = models.ForeignKey(
        Project,
        db_column="project_id",
        on_delete=models.DO_NOTHING
    )
    segment = models.ForeignKey(
        RoadSegment,
        db_column="segment_id",
        on_delete=models.DO_NOTHING
    )
    survey_date = models.DateField(db_column='survey_date')
    year_val = models.CharField(max_length=10, db_column='year_val')
    length_val = models.FloatField(db_column='length_val')
    three_wheeler_auto = models.IntegerField(db_column='three_wheeler')
    car_jeep_van_taxi = models.IntegerField(db_column='car_jeep_taxi')
    standard_bus = models.IntegerField(db_column='standard_bus')
    lcv = models.IntegerField(db_column='lcv')
    two_axle_trucks = models.IntegerField(db_column='two_axle_trucks')
    three_axle_trucks = models.IntegerField(db_column='three_axle_trucks')
    mav = models.IntegerField(db_column='mav')
    osv = models.IntegerField(db_column='osv')
    tractor = models.IntegerField(db_column='tractor')
    others = models.IntegerField(db_column='others')
    aadt_vehicles = models.IntegerField(db_column='aadt_vehicles')
    aadt_pcu = models.IntegerField(db_column='aadt_pcu')
    cvd_vehicles = models.IntegerField(db_column='cvd_vehicles')
    latitude = models.FloatField(db_column='latitude')
    longitude = models.FloatField(db_column='longitude')
    toll_plaza = models.CharField(max_length=255, db_column='toll_plaza')
    source_file = models.CharField(max_length=255, db_column='source_file')

    class Meta:
        managed = False
        db_table = "traffic"

    def __str__(self):
        return f"Traffic {self.id}"
