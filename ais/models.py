from django.db import models
from inventory.models import Project, RoadSegment


class Accident(models.Model):
    accident_id = models.AutoField(primary_key=True, db_column='accident_id')
    segment = models.ForeignKey(
        RoadSegment,
        db_column='segment_id',
        on_delete=models.DO_NOTHING,
        related_name='ais_accidents',
    )
    project = models.ForeignKey(
        Project,
        db_column='project_id',
        on_delete=models.DO_NOTHING,
        related_name='ais_accidents',
    )
    accident_date = models.DateField(db_column='accident_date')
    latitude = models.FloatField(db_column='latitude')
    longitude = models.FloatField(db_column='longitude')
    non_injured_accident = models.FloatField(db_column='non_injured_accident')
    minor_accident = models.FloatField(db_column='minor_accident')
    major_accident = models.FloatField(db_column='major_accident')
    fatal_accident = models.FloatField(db_column='fatal_accident')
    total_accident = models.FloatField(db_column='total_accident')
    fatalities = models.FloatField(db_column='fatalities')
    minor_injury = models.FloatField(db_column='minor_injury')
    major_injury = models.FloatField(db_column='major_injury')
    fatal_injury = models.FloatField(db_column='fatal_injury')
    total_injury = models.FloatField(db_column='total_injury')
    head_tail = models.FloatField(db_column='head_tail')
    head_on = models.FloatField(db_column='head_on')
    overturning = models.FloatField(db_column='overturning')
    skidding = models.FloatField(db_column='skidding')
    sideswipe = models.FloatField(db_column='sideswipe')
    pedestrian_hit_run = models.FloatField(db_column='pedestrian_hit_run')
    animals_on_road = models.FloatField(db_column='animals_on_road')
    mechanical_fault = models.FloatField(db_column='mechanical_fault')
    vehicle_lost_control = models.FloatField(db_column='vehicle_lost_control')
    overspeed = models.FloatField(db_column='overspeed')
    drunk_drive = models.FloatField(db_column='drunk_drive')
    fault_of_driver = models.FloatField(db_column='fault_of_driver')
    pedestrian_related = models.FloatField(db_column='pedestrian_related')
    grievous_accident = models.FloatField(db_column='grievous_accident')
    grievous_injury = models.FloatField(db_column='grievous_injury')
    nature_of_accident = models.TextField(db_column='nature_of_accident')
    cause_of_accident = models.TextField(db_column='cause_of_accident')
    source_file = models.TextField(db_column='source_file', null=True, blank=True)

    class Meta:
        managed = False
        db_table = 'accidents'

    def __str__(self):
        return f'Accident {self.accident_id}'
