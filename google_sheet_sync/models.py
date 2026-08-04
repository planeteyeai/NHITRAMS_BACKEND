"""Models for Google Sheets → PostgreSQL distress synchronization."""

import uuid

from django.db import models


class GoogleSheetDistress(models.Model):
    """One row synced from Google Sheets via Apps Script.

    ``sync_id`` is the stable key written back to the sheet so later edits
    update the same database row (upsert).
    """

    sync_id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
        help_text='Stable sync key shared with Google Sheets.',
    )
    status = models.CharField(max_length=100, null=True, blank=True)

    # Resolved via normalized project_name lookup against distress.Project
    project = models.ForeignKey(
        'distress.Project',
        on_delete=models.PROTECT,
        related_name='google_sheet_distress_rows',
        db_constraint=False,  # unmanaged legacy Project table
    )
    project_name_raw = models.CharField(
        max_length=255,
        help_text='Project name as received from the sheet (before normalize).',
    )
    project_name_normalized = models.CharField(
        max_length=255,
        db_index=True,
        help_text='Normalized project name used for lookup.',
    )

    latitude = models.FloatField(null=True, blank=True)
    longitude = models.FloatField(null=True, blank=True)
    chainage_start = models.FloatField(null=True, blank=True)
    chainage_end = models.FloatField(null=True, blank=True)

    distress_type = models.CharField(max_length=255)
    direction = models.CharField(max_length=100, null=True, blank=True)
    date = models.CharField(max_length=50, null=True, blank=True)
    length = models.FloatField(null=True, blank=True)
    area = models.FloatField(null=True, blank=True)
    width = models.FloatField(null=True, blank=True)
    depth = models.FloatField(null=True, blank=True)
    carriage_type = models.CharField(max_length=100, null=True, blank=True)
    lane = models.CharField(max_length=100, null=True, blank=True)
    total_distress = models.CharField(max_length=100, null=True, blank=True)
    image_url = models.CharField(max_length=2048, null=True, blank=True)

    # Per-type metric columns from the sheet (stored as text; sheets often mix types)
    oblique_crack = models.CharField(max_length=100, null=True, blank=True)
    pothole = models.CharField(max_length=100, null=True, blank=True)
    edge_break = models.CharField(max_length=100, null=True, blank=True)
    patching = models.CharField(max_length=100, null=True, blank=True)
    bleeding = models.CharField(max_length=100, null=True, blank=True)
    hotspots = models.CharField(max_length=100, null=True, blank=True)
    alligator_crack = models.CharField(max_length=100, null=True, blank=True)
    roughness = models.CharField(max_length=100, null=True, blank=True)
    repair = models.CharField(max_length=100, null=True, blank=True)
    block_crack = models.CharField(max_length=100, null=True, blank=True)
    rutting = models.CharField(max_length=100, null=True, blank=True)
    longitudinal_crack = models.CharField(max_length=100, null=True, blank=True)
    raveling = models.CharField(max_length=100, null=True, blank=True)
    single_discrete_crack = models.CharField(max_length=100, null=True, blank=True)
    multiple_cracks = models.CharField(max_length=100, null=True, blank=True)
    joint_crack = models.CharField(max_length=100, null=True, blank=True)
    joint_seal_defects = models.CharField(max_length=100, null=True, blank=True)
    punchout = models.CharField(max_length=100, null=True, blank=True)
    slippage = models.CharField(max_length=100, null=True, blank=True)
    heaves = models.CharField(max_length=100, null=True, blank=True)
    simple_crack = models.CharField(max_length=100, null=True, blank=True)
    transverse_crack = models.CharField(max_length=100, null=True, blank=True)
    hairline_crack = models.CharField(max_length=100, null=True, blank=True)
    hungry_surface = models.CharField(max_length=100, null=True, blank=True)
    settlement = models.CharField(max_length=100, null=True, blank=True)
    shoving = models.CharField(max_length=100, null=True, blank=True)
    stripping = models.CharField(max_length=100, null=True, blank=True)
    faulting_stepping_in_cracks = models.CharField(max_length=100, null=True, blank=True)
    popout_small_hole = models.CharField(max_length=100, null=True, blank=True)
    single_longitudinal_crack = models.CharField(max_length=100, null=True, blank=True)
    blowup_or_bulking = models.CharField(max_length=100, null=True, blank=True)
    polished_surface_glazing = models.CharField(max_length=100, null=True, blank=True)
    depression = models.CharField(max_length=100, null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'google_sheet_distress'
        ordering = ['-updated_at']
        indexes = [
            models.Index(
                fields=['project', '-updated_at'],
                name='gsheet_distress_proj_upd_idx',
            ),
        ]

    def __str__(self):
        return f'GoogleSheetDistress {self.sync_id} — {self.project_name_raw}'
