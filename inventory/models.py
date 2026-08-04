from django.db import models

class AssetType(models.Model):
    asset_type_id = models.AutoField(primary_key=True)
    asset_type_name = models.CharField(max_length=100)

    class Meta:
        managed = False
        db_table = 'asset_types'

    def __str__(self):
        return self.asset_type_name

class Project(models.Model):
    project_id = models.AutoField(primary_key=True)
    project_name = models.CharField(max_length=255)
    state = models.CharField(max_length=100)
    district = models.CharField(max_length=100)
    block_taluka = models.CharField(max_length=100)
    road_code = models.CharField(max_length=255)
    originating = models.CharField(max_length=255)
    terminating = models.CharField(max_length=255)
    length_km = models.FloatField()

    class Meta:
        managed = False
        db_table = 'projects'

    def __str__(self):
        return self.project_name

class SubAssetType(models.Model):
    sub_asset_type_id = models.AutoField(primary_key=True)
    sub_asset_type_name = models.CharField(max_length=100)

    class Meta:
        managed = False
        db_table = 'sub_asset_types'

    def __str__(self):
        return self.sub_asset_type_name

class RoadSegment(models.Model):
    segment_id = models.AutoField(primary_key=True)
    project = models.ForeignKey(Project, on_delete=models.CASCADE, db_column="project_id")
    chainage_start = models.FloatField()
    chainage_end = models.FloatField()
    direction = models.CharField(max_length=100)
    lane = models.CharField(max_length=10)
    carriage_type = models.CharField(max_length=50)

    class Meta:
        managed = False
        db_table = 'road_segments'

    def __str__(self):
        return f"Segment {self.segment_id}"

class Inventory(models.Model):
    inventory_id = models.AutoField(primary_key=True)
    segment = models.ForeignKey(RoadSegment, on_delete=models.CASCADE)
    project = models.ForeignKey(Project, on_delete=models.CASCADE)
    asset_type = models.ForeignKey(AssetType, on_delete=models.CASCADE)
    sub_asset_type = models.ForeignKey(SubAssetType, on_delete=models.CASCADE, null=True, blank=True)
    survey_date = models.DateField()
    latitude = models.FloatField()
    longitude = models.FloatField()
    count_val = models.FloatField()
    source_file = models.TextField(null=True, blank=True)
    # Excel sheet column names (exact headers)
    image_url = models.TextField(null=True, blank=True, db_column='Image URL')
    source = models.TextField(null=True, blank=True, db_column='Source')

    class Meta:
        managed = False
        db_table = 'inventory'

    def __str__(self):
        return f"Inventory {self.inventory_id}"


class InventoryWide(models.Model):
    """Denormalized inventory rows matching the Excel sheet layout exactly."""

    inventory_id = models.BigIntegerField(primary_key=True)
    project_name = models.TextField(null=True, blank=True, db_column='Project Name')
    chainage_start = models.FloatField(null=True, blank=True, db_column='Chainage start')
    chainage_end = models.FloatField(null=True, blank=True, db_column='Chainage end')
    direction = models.TextField(null=True, blank=True, db_column='Direction')
    asset_type = models.TextField(null=True, blank=True, db_column='Asset type')
    trees = models.FloatField(default=0, db_column='Trees')
    culvert = models.FloatField(default=0, db_column='Culvert')
    street_lights = models.FloatField(default=0, db_column='Street Lights')
    bridges = models.FloatField(default=0, db_column='Bridges')
    traffic_signals = models.FloatField(default=0, db_column='Traffic Signals')
    bus_stop = models.FloatField(default=0, db_column='Bus Stop')
    truck_layby = models.FloatField(default=0, db_column='Truck LayBy')
    toll_plaza = models.FloatField(default=0, db_column='Toll Plaza')
    adjacent_road = models.FloatField(default=0, db_column='Adjacent Road')
    toilet_blocks = models.FloatField(default=0, db_column='Toilet Blocks')
    rest_area = models.FloatField(default=0, db_column='Rest Area')
    rcc_drain = models.FloatField(default=0, db_column='RCC Drain')
    fuel_station = models.FloatField(default=0, db_column='Fuel Station')
    emergency_call_box = models.FloatField(default=0, db_column='Emergency Call Box')
    tunnels = models.FloatField(default=0, db_column='Tunnels')
    footpath = models.FloatField(default=0, db_column='Footpath')
    junction = models.FloatField(default=0, db_column='Junction')
    sign_boards = models.FloatField(default=0, db_column='Sign Boards')
    solar_blinker = models.FloatField(default=0, db_column='Solar Blinker')
    median_plants = models.FloatField(default=0, db_column='Median Plants')
    service_road = models.FloatField(default=0, db_column='Service Road')
    km_stones = models.FloatField(default=0, db_column='KM Stones')
    crash_barrier = models.FloatField(default=0, db_column='Crash Barrier')
    median_opening = models.FloatField(default=0, db_column='Median Opening')
    latitude = models.FloatField(null=True, blank=True, db_column='Latitude')
    longitude = models.FloatField(null=True, blank=True, db_column='Longitude')
    survey_date = models.DateField(null=True, blank=True, db_column='Date')
    sub_asset_type = models.TextField(null=True, blank=True, db_column='Sub Asset Type')
    carriage_type = models.TextField(null=True, blank=True, db_column='Carriage Type')
    lane = models.TextField(null=True, blank=True, db_column='Lane')
    kerb = models.FloatField(default=0, db_column='Kerb')
    guard_post = models.FloatField(default=0, db_column='Guard Post')
    delineators = models.FloatField(default=0, db_column='Delineators')
    pedestrian_guard_rail = models.FloatField(default=0, db_column='Pedestrian Guard Rail')
    slip_roads = models.FloatField(default=0, db_column='Slip Roads')
    bus_bays = models.FloatField(default=0, db_column='Bus Bays')
    image_url = models.TextField(null=True, blank=True, db_column='Image URL')
    source = models.TextField(null=True, blank=True, db_column='Source')

    class Meta:
        managed = False
        db_table = 'inventory_wide'

    def __str__(self):
        return f"InventoryWide {self.inventory_id}"


class InventoryReport(models.Model):
    """User-submitted inventory report formerly stored in Excel.

    Field names intentionally match the legacy frontend JSON contract.
    Username is always set from the JWT, never the body.
    """

    id = models.BigAutoField(primary_key=True)
    username = models.CharField(max_length=150, db_index=True)
    Project_Name = models.CharField(max_length=255)
    Chainage_start = models.FloatField()
    Chainage_end = models.FloatField()
    Direction = models.CharField(max_length=100)
    Asset_type = models.CharField(max_length=255)
    Latitude = models.FloatField()
    Longitude = models.FloatField()
    Date = models.CharField(max_length=50)
    Sub_Asset_Type = models.CharField(max_length=255, blank=True, default='')
    Carriage_Type = models.CharField(max_length=100, blank=True, default='')
    Lane = models.CharField(max_length=100, blank=True, default='')
    No_of_inventories = models.IntegerField(default=0)
    Image_URL = models.CharField(max_length=1024, blank=True, default='')
    Video_URL = models.CharField(max_length=1024, blank=True, default='')
    Source = models.CharField(max_length=255, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'inventory_reports'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['username', '-created_at'], name='inv_rpt_user_created_idx'),
        ]

    def __str__(self):
        return f'InventoryReport {self.id} ({self.username}) — {self.Asset_type}'


class UserProjectAssignment(models.Model):
    """Maps an NHIT RAMS username to their assigned project details.

    Current data source for get_project_details(username). Swap the
    provider in inventory.services.project_lookup without changing views.
    """

    id = models.BigAutoField(primary_key=True)
    username = models.CharField(max_length=150, unique=True, db_index=True)
    project_name = models.CharField(max_length=255)
    start_chainage = models.CharField(max_length=100)
    end_chainage = models.CharField(max_length=100)
    total_project_length = models.CharField(max_length=100)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'user_project_assignments'
        ordering = ['username']

    def __str__(self):
        return f'{self.username} → {self.project_name}'


class DPRReport(models.Model):
    """Daily Progress Report owned by an authenticated NHIT RAMS username.

    Project fields are always resolved on the backend via get_project_details.
    Username is always set from the JWT — never from the request body.
    """

    STATUS_DRAFT = 'draft'
    STATUS_SUBMITTED = 'submitted'
    STATUS_APPROVED = 'approved'
    STATUS_REJECTED = 'rejected'
    STATUS_CHOICES = [
        (STATUS_DRAFT, 'Draft'),
        (STATUS_SUBMITTED, 'Submitted'),
        (STATUS_APPROVED, 'Approved'),
        (STATUS_REJECTED, 'Rejected'),
    ]

    id = models.BigAutoField(primary_key=True)
    username = models.CharField(max_length=150, db_index=True)

    # Auto-fetched from get_project_details(username) — never from the client
    project_name = models.CharField(max_length=255)
    start_chainage = models.CharField(max_length=100)
    end_chainage = models.CharField(max_length=100)
    total_project_length = models.CharField(max_length=100)

    report_date = models.DateField()
    reporting_engineer = models.CharField(max_length=255)
    assessment_date = models.DateField(null=True, blank=True)
    remarks = models.TextField(blank=True, default='')
    asset_details = models.JSONField(default=dict, blank=True)
    overall_summary = models.JSONField(default=dict, blank=True)
    report_data = models.JSONField(default=dict, blank=True)
    uploaded_images = models.JSONField(default=list, blank=True)
    status = models.CharField(
        max_length=32,
        choices=STATUS_CHOICES,
        default=STATUS_SUBMITTED,
        db_index=True,
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'dpr_reports'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['username', '-created_at'], name='dpr_rpt_user_created_idx'),
        ]

    def __str__(self):
        return f'DPRReport {self.id} ({self.username}) — {self.project_name}'


# ─────────────────────────────────────────────
# Routine Maintenance (masters + reports)
# ─────────────────────────────────────────────


class RoutineMaintenanceCategory(models.Model):
    """Top-level routine maintenance activity category (DB-driven dropdown)."""

    id = models.BigAutoField(primary_key=True)
    name = models.CharField(max_length=255, unique=True)
    display_order = models.PositiveIntegerField(default=0, db_index=True)
    is_active = models.BooleanField(default=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'routine_maintenance_category'
        ordering = ['display_order', 'name']

    def __str__(self):
        return self.name


class RoutineMaintenanceSubcategory(models.Model):
    """Sub-activity under a routine maintenance category."""

    id = models.BigAutoField(primary_key=True)
    category = models.ForeignKey(
        RoutineMaintenanceCategory,
        on_delete=models.CASCADE,
        related_name='subcategories',
    )
    name = models.CharField(max_length=255)
    display_order = models.PositiveIntegerField(default=0, db_index=True)
    is_active = models.BooleanField(default=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'routine_maintenance_subcategory'
        ordering = ['display_order', 'name']
        constraints = [
            models.UniqueConstraint(
                fields=['category', 'name'],
                name='rm_subcat_unique_name_per_category',
            ),
        ]

    def __str__(self):
        return f'{self.category.name} → {self.name}'


class SideMaster(models.Model):
    """Side / lane dropdown for routine maintenance."""

    id = models.BigAutoField(primary_key=True)
    name = models.CharField(max_length=100, unique=True)
    display_order = models.PositiveIntegerField(default=0, db_index=True)
    is_active = models.BooleanField(default=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'side_master'
        ordering = ['display_order', 'name']

    def __str__(self):
        return self.name


class SeverityMaster(models.Model):
    """Severity dropdown for routine maintenance."""

    id = models.BigAutoField(primary_key=True)
    name = models.CharField(max_length=100, unique=True)
    display_order = models.PositiveIntegerField(default=0, db_index=True)
    is_active = models.BooleanField(default=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'severity_master'
        ordering = ['display_order', 'name']

    def __str__(self):
        return self.name


class RoutineMaintenanceReport(models.Model):
    """Routine maintenance inspection/report owned by a JWT username."""

    STATUS_DRAFT = 'draft'
    STATUS_SUBMITTED = 'submitted'
    STATUS_APPROVED = 'approved'
    STATUS_REJECTED = 'rejected'
    STATUS_CHOICES = [
        (STATUS_DRAFT, 'Draft'),
        (STATUS_SUBMITTED, 'Submitted'),
        (STATUS_APPROVED, 'Approved'),
        (STATUS_REJECTED, 'Rejected'),
    ]

    id = models.BigAutoField(primary_key=True)
    username = models.CharField(max_length=150, db_index=True)

    # Auto-fetched via get_project_details(username)
    project_name = models.CharField(max_length=255)
    start_chainage = models.CharField(max_length=100)
    end_chainage = models.CharField(max_length=100)
    total_project_length = models.CharField(max_length=100)

    category = models.ForeignKey(
        RoutineMaintenanceCategory,
        on_delete=models.PROTECT,
        related_name='reports',
    )
    subcategory = models.ForeignKey(
        RoutineMaintenanceSubcategory,
        on_delete=models.PROTECT,
        related_name='reports',
    )
    side = models.ForeignKey(
        SideMaster,
        on_delete=models.PROTECT,
        related_name='reports',
    )
    severity = models.ForeignKey(
        SeverityMaster,
        on_delete=models.PROTECT,
        related_name='reports',
    )

    latitude = models.FloatField()
    longitude = models.FloatField()
    chainage = models.CharField(max_length=100)
    inspection_datetime = models.DateTimeField()

    description_of_work = models.TextField(blank=True, default='')
    nos = models.FloatField(default=0)
    length = models.FloatField(default=0)
    width = models.FloatField(default=0)
    depth = models.FloatField(default=0)
    total_quantity = models.FloatField(default=0)

    uploaded_image_urls = models.JSONField(default=list, blank=True)
    status = models.CharField(
        max_length=32,
        choices=STATUS_CHOICES,
        default=STATUS_SUBMITTED,
        db_index=True,
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'routine_maintenance_reports'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['username', '-created_at'], name='rm_rpt_user_created_idx'),
        ]

    def __str__(self):
        return f'RMReport {self.id} ({self.username}) — {self.category.name}'

