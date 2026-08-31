from django.db import models


class Projects(models.Model):
    project_id = models.BigIntegerField(primary_key=True, db_column='project_id')
    project_name = models.TextField()
    state = models.TextField(null=True, blank=True)
    district = models.TextField(null=True, blank=True)
    block_taluka = models.TextField(null=True, blank=True)
    road_code = models.TextField(null=True, blank=True)

    class Meta:
        db_table = 'projects'
        managed = False

    def __str__(self):
        return self.project_name


class ProjectInfo(models.Model):
    month_val = models.DateField()
    project = models.OneToOneField(
        Projects,
        on_delete=models.CASCADE,
        primary_key=True,
        related_name='project_info',
        db_column='project_id',
    )

    class Meta:
        db_table = 'project_info'
        managed = False

    def __str__(self):
        return f'Project Info {self.project_id}'


class RoadSegments(models.Model):
    segment_id = models.BigIntegerField(primary_key=True, db_column='segment_id')
    chainage_start = models.FloatField()
    chainage_end = models.FloatField()
    direction = models.TextField()
    carriage_type = models.TextField()

    class Meta:
        db_table = 'road_segments'
        managed = False

    def __str__(self):
        return f'Segment {self.segment_id}'


class Pavement(models.Model):
    pavement_id = models.BigIntegerField(primary_key=True, db_column='pavement_id')
    sum_chainage = models.FloatField()
    date_of_survey = models.DateField()
    date_val = models.DateField(null=True, blank=True)
    road_id = models.TextField(null=True, blank=True)
    latitude = models.FloatField()
    longitude = models.FloatField()
    aadt = models.FloatField()
    pavement_condition_score_pcs = models.TextField(null=True, blank=True)
    iri_index = models.TextField(null=True, blank=True)
    rainfall_mm = models.FloatField(null=True, blank=True)
    drainage_condition = models.TextField(null=True, blank=True)
    type_of_last_maintenance = models.TextField(null=True, blank=True)
    last_maintenance_date = models.DateField(null=True, blank=True)
    carriageway_width = models.TextField(null=True, blank=True)
    temperature_c = models.TextField(null=True, blank=True)
    international_roughness_index_iri = models.TextField(null=True, blank=True)
    roughness_index_status = models.TextField(null=True, blank=True)
    commercial_vehicle_damage_cvd_class = models.TextField(null=True, blank=True)
    source_file = models.TextField(null=True, blank=True)
    project = models.ForeignKey(Projects, on_delete=models.CASCADE, db_column='project_id')
    segment = models.ForeignKey(RoadSegments, on_delete=models.CASCADE, db_column='segment_id')

    class Meta:
        db_table = 'pavement'
        managed = False

    def __str__(self):
        return f'Pavement {self.pavement_id} at {self.sum_chainage}'
