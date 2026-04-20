from django.db import models
from inventory.models import Project, RoadSegment

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
        return f"Traffic {self.traffic_id}"
