from django.db import models

class DistressType(models.Model):
    distress_type_id = models.AutoField(primary_key=True)
    distress_type_name = models.CharField(max_length=100)

    class Meta:
        managed = False
        db_table = 'distress_types'

    def __str__(self):
        return self.distress_type_name

class Project(models.Model):
    project_id = models.AutoField(primary_key=True)
    project_name = models.CharField(max_length=100)

    class Meta:
        managed = False
        db_table = 'projects'

    def __str__(self):
        return self.project_name

class RoadSegment(models.Model):
    segment_id = models.AutoField(primary_key=True)
    chainage_start = models.FloatField()
    chainage_end = models.FloatField()
    direction = models.CharField(max_length=100)
    carriage_type = models.CharField(max_length=100, null=True, blank=True)
    lane = models.CharField(max_length=100, null=True, blank=True)

    class Meta:
        managed = False
        db_table = 'road_segments'

    def __str__(self):
        return f"Segment {self.segment_id}: {self.chainage_start}-{self.chainage_end} {self.direction}"

class Distress(models.Model):
    distress_id = models.AutoField(primary_key=True)
    segment = models.ForeignKey(RoadSegment, on_delete=models.CASCADE)
    project = models.ForeignKey(Project, on_delete=models.CASCADE)
    distress_type = models.ForeignKey(DistressType, on_delete=models.CASCADE)
    distress_date = models.DateField()
    latitude = models.FloatField()
    longitude = models.FloatField()
    count_val = models.IntegerField()
    length_val = models.FloatField()
    area_val = models.FloatField()
    width_val = models.FloatField()
    depth_val = models.FloatField()

    class Meta:
        managed = False
        db_table = 'distress'

    def __str__(self):
        return f"Distress {self.distress_id} - {self.distress_type.distress_type_name}"
