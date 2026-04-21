from django.db import models


class ProjectInfo(models.Model):
    month_val = models.DateField()
    project = models.OneToOneField('Projects', on_delete=models.CASCADE, primary_key=True, related_name='project_info')

    class Meta:
        db_table = 'project_info'
        managed = False

    def __str__(self):
        return f"Project Info {self.project_id}"


class Projects(models.Model):
    project_id = models.CharField(max_length=100, primary_key=True)
    project_name = models.CharField(max_length=255)
    district = models.CharField(max_length=100, null=True, blank=True)
    block_taluka = models.CharField(max_length=100, null=True, blank=True)

    class Meta:
        db_table = 'projects'
        managed = False

    def __str__(self):
        return self.project_name


class RoadSegments(models.Model):
    segment_id = models.CharField(max_length=100, primary_key=True)
    chainage_start = models.FloatField()
    chainage_end = models.FloatField()
    direction = models.CharField(max_length=100)
    carriage_type = models.CharField(max_length=100)
    lane = models.CharField(max_length=100, null=True, blank=True)

    class Meta:
        db_table = 'road_segments'
        managed = False

    def __str__(self):
        return f"Segment {self.segment_id}"


class RoadsideFeatures(models.Model):
    feature_id = models.IntegerField(primary_key=True)
    survey_date = models.DateField()
    feature_desc = models.CharField(max_length=255)
    material_type = models.CharField(max_length=255)
    feature_condition = models.CharField(max_length=10)
    safety_hazard = models.CharField(max_length=255)
    land_use = models.CharField(max_length=255)
    terrain = models.CharField(max_length=255)
    latitude = models.FloatField()
    longitude = models.FloatField()
    altitude = models.FloatField()
    remarks = models.TextField(null=True, blank=True)
    length_val = models.FloatField(null=True, blank=True)
    cross_section_location = models.CharField(max_length=255, null=True, blank=True)
    offset_from_center = models.FloatField(null=True, blank=True)
    segment = models.ForeignKey(RoadSegments, on_delete=models.CASCADE, db_column='segment_id')
    project = models.ForeignKey(Projects, on_delete=models.CASCADE, db_column='project_id')

    class Meta:
        db_table = 'roadside_features'
        managed = False

    def __str__(self):
        return f"Feature {self.feature_id}"