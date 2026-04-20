from django.db import models

class Project(models.Model):
    project_id = models.AutoField(primary_key=True)
    project_name = models.CharField(max_length=100)

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

class Segment(models.Model):
    segment_id = models.AutoField(primary_key=True)
    chainage_start = models.FloatField()
    chainage_end = models.FloatField()
    direction = models.CharField(max_length=100)

    class Meta:
        managed = False
        db_table = 'road_segments'

    def __str__(self):
        return f"Segment {self.segment_id}"

class Inventory(models.Model):
    inventory_id = models.AutoField(primary_key=True)
    segment = models.ForeignKey(Segment, on_delete=models.CASCADE)
    project = models.ForeignKey(Project, on_delete=models.CASCADE)
    asset_type_id = models.IntegerField()
    sub_asset_type_id = models.IntegerField()
    survey_date = models.DateField()
    latitude = models.FloatField()
    longitude = models.FloatField()
    count_val = models.IntegerField()

    class Meta:
        managed = False
        db_table = 'inventory'

    def __str__(self):
        return f"Inventory {self.inventory_id}"
