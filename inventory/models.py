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


