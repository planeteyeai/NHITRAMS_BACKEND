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


class DistressReport(models.Model):
    """User-submitted distress report formerly stored in Excel.

    Field names intentionally match the legacy frontend JSON contract
    (PascalCase). Username is always set from the JWT, never the body.
    """

    id = models.BigAutoField(primary_key=True)
    username = models.CharField(max_length=150, db_index=True)
    Latitude = models.FloatField()
    Longitude = models.FloatField()
    Chainage_Start = models.FloatField()
    Chainage_End = models.FloatField()
    Project_Name = models.CharField(max_length=255)
    Distress_Type = models.CharField(max_length=255)
    Direction = models.CharField(max_length=100)
    Date = models.CharField(max_length=50)
    Length = models.FloatField(default=0)
    Carriage_Type = models.CharField(max_length=100, blank=True, default='')
    Width = models.FloatField(default=0)
    Depth = models.FloatField(default=0)
    Lane = models.CharField(max_length=100, blank=True, default='')
    No_of_Distress = models.IntegerField(default=1)
    Image_URL = models.CharField(max_length=1024, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'distress_reports'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['username', '-created_at'], name='distress_rpt_user_created_idx'),
        ]

    def __str__(self):
        return f'DistressReport {self.id} ({self.username}) — {self.Distress_Type}'
