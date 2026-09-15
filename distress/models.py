from django.db import models
import uuid

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
    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        db_column='project_id',
        null=True,
        blank=True,
    )
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
    count_val = models.FloatField()
    length_val = models.FloatField()
    area_val = models.FloatField()
    width_val = models.FloatField()
    depth_val = models.FloatField()
    # Provenance / Google Sheet upsert key (columns exist on production table;
    # sync_id is added via google_sheet_sync migration if missing).
    distress_source = models.TextField(null=True, blank=True)
    source_file = models.TextField(null=True, blank=True)
    source_sheet = models.TextField(null=True, blank=True)
    sync_id = models.UUIDField(null=True, blank=True, unique=True)

    class Meta:
        managed = False
        db_table = 'distress'

    def __str__(self):
        return f"Distress {self.distress_id} - {self.distress_type.distress_type_name}"


class PredictedDistress(models.Model):
    """Denormalized AI prediction rows used by POST /distress_predict_filter/.

    Source of truth table on Neon: ``predicted_distress`` (~1.18M rows).
    Response still exposes ``distress_id`` (= ``predicted_distress_id``) for FE/repair.
    """

    predicted_distress_id = models.BigAutoField(primary_key=True)
    latitude = models.DecimalField(max_digits=12, decimal_places=7, null=True, blank=True)
    longitude = models.DecimalField(max_digits=12, decimal_places=7, null=True, blank=True)
    project_name = models.CharField(max_length=255, null=True, blank=True)
    chainage_start = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)
    chainage_end = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)
    total_distress = models.IntegerField(null=True, blank=True)
    distress_type = models.CharField(max_length=255, null=True, blank=True)
    pothole = models.IntegerField(null=True, blank=True)
    alligator_crack = models.IntegerField(null=True, blank=True)
    block_crack_oblique_crack = models.IntegerField(null=True, blank=True)
    edge_break = models.IntegerField(null=True, blank=True)
    patchwork = models.IntegerField(null=True, blank=True)
    bleeding = models.IntegerField(null=True, blank=True)
    hotspots = models.IntegerField(null=True, blank=True)
    rutting = models.IntegerField(null=True, blank=True)
    raveling = models.IntegerField(null=True, blank=True)
    transverse_crack = models.IntegerField(null=True, blank=True)
    rough_spot = models.IntegerField(null=True, blank=True)
    direction = models.CharField(max_length=100, null=True, blank=True)
    lane = models.CharField(max_length=100, null=True, blank=True)
    distress_date = models.DateField(null=True, blank=True)
    carriage_type = models.CharField(max_length=100, null=True, blank=True)
    hairline_crack = models.IntegerField(null=True, blank=True)
    longitudinal_crack = models.IntegerField(null=True, blank=True)
    created_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        managed = False
        db_table = 'predicted_distress'

    def __str__(self):
        return f'PredictedDistress {self.predicted_distress_id} - {self.distress_type}'


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


class DistressPdfExportJob(models.Model):
    """Background job for large multi-report distress PDF exports.

    Progress is derived from completed_reports + failed_reports over total_reports.
    Store output_s3_key only — generate fresh presigned URLs on status reads.
    """

    STATUS_QUEUED = 'queued'
    STATUS_PROCESSING = 'processing'
    STATUS_COMPLETED = 'completed'
    STATUS_COMPLETED_WITH_ERRORS = 'completed_with_errors'
    STATUS_FAILED = 'failed'
    STATUS_CHOICES = [
        (STATUS_QUEUED, 'Queued'),
        (STATUS_PROCESSING, 'Processing'),
        (STATUS_COMPLETED, 'Completed'),
        (STATUS_COMPLETED_WITH_ERRORS, 'Completed with errors'),
        (STATUS_FAILED, 'Failed'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    username = models.CharField(max_length=150, db_index=True)
    status = models.CharField(
        max_length=32,
        choices=STATUS_CHOICES,
        default=STATUS_QUEUED,
        db_index=True,
    )
    report_ids = models.JSONField(default=list)
    total_reports = models.PositiveIntegerField(default=0)
    completed_reports = models.PositiveIntegerField(default=0)
    failed_reports = models.PositiveIntegerField(default=0)
    output_s3_key = models.CharField(max_length=1024, blank=True, default='')
    error_message = models.TextField(blank=True, default='')
    failure_details = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'distress_pdf_export_jobs'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['username', '-created_at'], name='dpej_user_created_idx'),
            models.Index(fields=['status'], name='dpej_status_idx'),
        ]

    def __str__(self):
        return f'DistressPdfExportJob {self.id} ({self.status})'

    @property
    def progress(self) -> int:
        total = int(self.total_reports or 0)
        if total <= 0:
            return 0
        done = int(self.completed_reports or 0) + int(self.failed_reports or 0)
        return int(round((done / total) * 100))


class DistressRepairLog(models.Model):
    """Append-only audit log for resolving AI-predicted distresses.

    Location / type attributes are never duplicated here — always read from
    the linked ``distress`` prediction row via ``distress_id``.
    Multiple logs per ``distress_id`` are allowed (partial repairs).
    """

    STATUS_REPAIRED = 'REPAIRED'
    STATUS_NOT_PRESENT = 'NOT_PRESENT'
    STATUS_CHOICES = [
        (STATUS_REPAIRED, 'Repaired'),
        (STATUS_NOT_PRESENT, 'Not Present'),
    ]

    id = models.BigAutoField(primary_key=True)
    distress_id = models.BigIntegerField(db_index=True)
    status = models.CharField(max_length=32, choices=STATUS_CHOICES, db_index=True)
    quantity_resolved = models.FloatField(default=1.0)
    updated_by = models.CharField(max_length=150, db_index=True)
    updated_at = models.DateTimeField(auto_now_add=True, db_index=True)
    remarks = models.TextField(blank=True, default='')

    class Meta:
        db_table = 'distress_repair_logs'
        ordering = ['-updated_at']
        indexes = [
            models.Index(fields=['distress_id', '-updated_at'], name='drl_distress_updated_idx'),
        ]

    def __str__(self):
        return (
            f'DistressRepairLog {self.id} ({self.status}) — '
            f'distress_id={self.distress_id} qty={self.quantity_resolved}'
        )
