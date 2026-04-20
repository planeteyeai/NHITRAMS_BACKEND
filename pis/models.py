from django.db import models

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
