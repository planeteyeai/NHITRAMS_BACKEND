from django.db import models


class Projects(models.Model):
    project_id = models.CharField(max_length=100, primary_key=True)
    project_name = models.CharField(max_length=255)
    district = models.CharField(max_length=100, null=True, blank=True)

    class Meta:
        db_table = 'projects'
        managed = False

    def __str__(self):
        return self.project_name


class ProjectInfo(models.Model):
    month_val = models.DateField()
    project = models.OneToOneField(Projects, on_delete=models.CASCADE, primary_key=True, related_name='project_info')

    class Meta:
        db_table = 'project_info'
        managed = False

    def __str__(self):
        return f"Project Info {self.project_id}"


class RoadSegments(models.Model):
    segment_id = models.CharField(max_length=100, primary_key=True)
    chainage_start = models.FloatField()
    chainage_end = models.FloatField()
    direction = models.CharField(max_length=100)
    carriage_type = models.CharField(max_length=100)

    class Meta:
        db_table = 'road_segments'
        managed = False

    def __str__(self):
        return f"Segment {self.segment_id}"


class Pavement(models.Model):
    pavement_id = models.IntegerField(primary_key=True)  # Assuming pavement_id is Integer; adjust if BigInteger or other
    sum_chainage = models.FloatField()
    date_of_survey = models.DateField()
    road_id = models.CharField(max_length=100)
    latitude = models.FloatField()
    longitude = models.FloatField()
    aadt = models.IntegerField()
    pavement_condition_score_pcs = models.CharField(max_length=20)
    iri_index = models.CharField(max_length=20)
    rainfall_mm = models.FloatField()
    drainage_condition = models.CharField(max_length=255)
    type_of_last_maintenance = models.CharField(max_length=255)
    last_maintenance_date = models.DateField(null=True, blank=True)
    carriageway_width = models.CharField(max_length=50, null=True, blank=True)
    temperature_c = models.CharField(max_length=20, null=True, blank=True)
    international_roughness_index_iri = models.CharField(max_length=20, null=True, blank=True)
    roughness_index_status = models.CharField(max_length=50, null=True, blank=True)
    commercial_vehicle_damage_cvd_class = models.CharField(max_length=50, null=True, blank=True)
    project = models.ForeignKey(Projects, on_delete=models.CASCADE, db_column='project_id')
    segment = models.ForeignKey(RoadSegments, on_delete=models.CASCADE, db_column='segment_id')

    class Meta:
        db_table = 'pavement'
        managed = False  # Since table already exists

    def __str__(self):
        return f"Pavement {self.pavement_id} at {self.sum_chainage}"