from django.contrib import admin
from django.utils.html import format_html
from django.db.models import Sum, Count
from django.contrib.admin import SimpleListFilter
from .models import (
    InventoryWide,
    AssetType,
    SubAssetType,
    Project,
    RoadSegment,
    InventoryReport,
    InventoryCategory,
    InventorySubcategory,
    UserProjectAssignment,
    DPRReport,
    RoutineMaintenanceCategory,
    RoutineMaintenanceSubcategory,
    SideMaster,
    SeverityMaster,
    RoutineMaintenanceReport,
    ChainageReferencePoint,
)


# ─────────────────────────────────────────────
# CANONICAL PROJECT ADMIN
# Registered here because inventory.Project has
# the richest schema (state, district, road_code,
# originating, terminating, length_km).
# All other apps (distress, pavement, roadside)
# map to the same `projects` DB table and are
# NOT registered separately.
# ─────────────────────────────────────────────

@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = (
        'project_id',
        'project_name',
        'state',
        'district',
        'block_taluka',
        'road_code',
        'length_badge',
        'originating',
        'terminating',
        'asset_count',
        'inventory_total',
    )
    search_fields = ('project_name', 'state', 'district', 'road_code', 'originating', 'terminating')
    list_filter = ('state', 'district')
    ordering = ('project_name',)
    list_per_page = 50
    readonly_fields = (
        'project_id', 'project_name', 'state', 'district', 'block_taluka',
        'road_code', 'originating', 'terminating', 'length_km',
    )

    fieldsets = (
        ('Project Identity', {
            'fields': ('project_id', 'project_name', 'road_code'),
        }),
        ('Location', {
            'fields': ('state', 'district', 'block_taluka'),
        }),
        ('Route', {
            'fields': ('originating', 'terminating', 'length_km'),
        }),
    )

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(
            _asset_count=Count('inventory'),
            _total_count=Sum('inventory__count_val'),
        )

    def length_badge(self, obj):
        val = obj.length_km or 0
        return format_html(
            '<span style="background:#eaf4fb;color:#2980b9;padding:2px 8px;border-radius:10px;font-weight:bold;">{} km</span>',
            '{:.2f}'.format(val)
        )
    length_badge.short_description = 'Length'
    length_badge.admin_order_field = 'length_km'

    def asset_count(self, obj):
        val = getattr(obj, '_asset_count', 0)
        return format_html('<b style="color:#8e44ad;">{}</b>', val)
    asset_count.short_description = 'Asset Records'
    asset_count.admin_order_field = '_asset_count'

    def inventory_total(self, obj):
        val = getattr(obj, '_total_count', 0) or 0
        return format_html('<span style="color:#27ae60;">{}</span>', int(val))
    inventory_total.short_description = 'Total Assets'
    inventory_total.admin_order_field = '_total_count'


# ─────────────────────────────────────────────
# AssetType Admin
# ─────────────────────────────────────────────

@admin.register(AssetType)
class AssetTypeAdmin(admin.ModelAdmin):
    list_display = ('asset_type_id', 'asset_type_name', 'inventory_count', 'total_count')
    search_fields = ('asset_type_name',)
    ordering = ('asset_type_name',)

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(
            _inv_count=Count('inventory'),
            _total_count=Sum('inventory__count_val'),
        )

    def inventory_count(self, obj):
        return getattr(obj, '_inv_count', 0)
    inventory_count.short_description = 'Records'
    inventory_count.admin_order_field = '_inv_count'

    def total_count(self, obj):
        val = getattr(obj, '_total_count', 0) or 0
        return format_html('<b style="color:#2980b9;">{}</b>', int(val))
    total_count.short_description = 'Total Count'
    total_count.admin_order_field = '_total_count'


# ─────────────────────────────────────────────
# SubAssetType Admin
# ─────────────────────────────────────────────

@admin.register(SubAssetType)
class SubAssetTypeAdmin(admin.ModelAdmin):
    list_display = ('sub_asset_type_id', 'sub_asset_type_name')
    search_fields = ('sub_asset_type_name',)
    ordering = ('sub_asset_type_name',)


# ─────────────────────────────────────────────
# RoadSegment Admin
# ─────────────────────────────────────────────

@admin.register(RoadSegment)
class RoadSegmentAdmin(admin.ModelAdmin):
    list_display = (
        'segment_id', 'project', 'chainage_start', 'chainage_end',
        'direction', 'lane', 'carriage_type', 'segment_length',
    )
    search_fields = ('project__project_name', 'direction', 'lane', 'carriage_type')
    list_filter = ('direction', 'carriage_type', 'lane', 'project')
    ordering = ('project', 'chainage_start')
    list_select_related = ('project',)
    list_per_page = 50
    readonly_fields = (
        'segment_id', 'project', 'chainage_start', 'chainage_end',
        'direction', 'lane', 'carriage_type',
    )

    def segment_length(self, obj):
        length = (obj.chainage_end or 0) - (obj.chainage_start or 0)
        return format_html(
            '<span style="color:#27ae60;">{} km</span>',
            '{:.3f}'.format(length)
        )
    segment_length.short_description = 'Length'


# ─────────────────────────────────────────────
# Inventory Wide Admin (inventory_wide table)
# ─────────────────────────────────────────────

_INVENTORY_WIDE_COUNT_FIELDS = (
    'trees',
    'culvert',
    'street_lights',
    'bridges',
    'traffic_signals',
    'bus_stop',
    'truck_layby',
    'toll_plaza',
    'adjacent_road',
    'toilet_blocks',
    'rest_area',
    'rcc_drain',
    'fuel_station',
    'emergency_call_box',
    'tunnels',
    'footpath',
    'junction',
    'sign_boards',
    'solar_blinker',
    'median_plants',
    'service_road',
    'km_stones',
    'crash_barrier',
    'median_opening',
    'kerb',
    'guard_post',
    'delineators',
    'pedestrian_guard_rail',
    'slip_roads',
    'bus_bays',
)


class InventoryWideAssetTypeFilter(SimpleListFilter):
    title = 'asset type'
    parameter_name = 'asset_type'

    def lookups(self, request, model_admin):
        values = (
            InventoryWide.objects.exclude(asset_type__isnull=True)
            .exclude(asset_type='')
            .values_list('asset_type', flat=True)
            .distinct()
            .order_by('asset_type')
        )
        return [(v, v) for v in values]

    def queryset(self, request, queryset):
        if self.value():
            return queryset.filter(asset_type=self.value())
        return queryset


@admin.register(InventoryWide)
class InventoryWideAdmin(admin.ModelAdmin):
    list_display = (
        'inventory_id',
        'project_name',
        'asset_type_display',
        'sub_asset_type',
        'survey_date',
        'count_badge',
        'chainage_range',
        'direction',
        'coordinates',
    )
    list_filter = (
        InventoryWideAssetTypeFilter,
        'survey_date',
        'direction',
        'carriage_type',
        'lane',
        'project_name',
    )
    search_fields = (
        'project_name',
        'asset_type',
        'sub_asset_type',
        'direction',
    )
    date_hierarchy = 'survey_date'
    ordering = ('-survey_date', 'project_name')
    list_per_page = 50
    readonly_fields = (
        'inventory_id',
        'project_name',
        'chainage_start',
        'chainage_end',
        'direction',
        'asset_type',
        'sub_asset_type',
        'survey_date',
        'latitude',
        'longitude',
        'carriage_type',
        'lane',
        'image_url',
        'source',
        *_INVENTORY_WIDE_COUNT_FIELDS,
    )

    fieldsets = (
        ('Identification', {
            'fields': (
                'inventory_id',
                'project_name',
                'asset_type',
                'sub_asset_type',
                'survey_date',
            ),
        }),
        ('Location', {
            'fields': (
                'chainage_start',
                'chainage_end',
                'direction',
                'carriage_type',
                'lane',
                'latitude',
                'longitude',
            ),
        }),
        ('Counts', {
            'classes': ('collapse',),
            'fields': _INVENTORY_WIDE_COUNT_FIELDS,
        }),
        ('Media / Source', {
            'fields': ('image_url', 'source'),
        }),
    )

    def asset_type_display(self, obj):
        name = obj.asset_type or '—'
        return format_html(
            '<span style="background:#eaf4fb;color:#2980b9;padding:2px 6px;border-radius:6px;">{}</span>',
            name,
        )
    asset_type_display.short_description = 'Asset Type'
    asset_type_display.admin_order_field = 'asset_type'

    def count_badge(self, obj):
        key = (obj.asset_type or '').strip().lower().replace(' ', '_')
        val = 0
        if key in _INVENTORY_WIDE_COUNT_FIELDS:
            val = getattr(obj, key, 0) or 0
        else:
            for field in _INVENTORY_WIDE_COUNT_FIELDS:
                candidate = getattr(obj, field, 0) or 0
                if candidate:
                    val = candidate
                    break
        return format_html(
            '<span style="background:#27ae60;color:#fff;padding:2px 10px;border-radius:12px;font-weight:bold;">{}</span>',
            int(val),
        )
    count_badge.short_description = 'Count'

    def chainage_range(self, obj):
        return format_html(
            '<small>{} → {} ({})</small>',
            obj.chainage_start if obj.chainage_start is not None else '—',
            obj.chainage_end if obj.chainage_end is not None else '—',
            obj.direction or '—',
        )
    chainage_range.short_description = 'Chainage'

    def coordinates(self, obj):
        return format_html(
            '<a href="https://maps.google.com/?q={},{}" target="_blank" style="color:#2980b9;">📍 Map</a>',
            obj.latitude or 0,
            obj.longitude or 0,
        )
    coordinates.short_description = 'Location'


@admin.register(InventoryReport)
class InventoryReportAdmin(admin.ModelAdmin):
    list_display = (
        'id',
        'username',
        'Project_Name',
        'Asset_type',
        'Direction',
        'Date',
        'No_of_inventories',
        'created_at',
    )
    list_filter = ('Asset_type', 'Direction', 'Project_Name')
    search_fields = ('username', 'Project_Name', 'Asset_type', 'Image_URL', 'Video_URL')
    readonly_fields = ('created_at', 'updated_at')
    ordering = ('-created_at',)


@admin.register(InventoryCategory)
class InventoryCategoryAdmin(admin.ModelAdmin):
    list_display = ('id', 'name', 'display_order', 'is_active', 'updated_at')
    list_filter = ('is_active',)
    search_fields = ('name',)
    ordering = ('display_order', 'name')


@admin.register(InventorySubcategory)
class InventorySubcategoryAdmin(admin.ModelAdmin):
    list_display = ('id', 'category', 'name', 'display_order', 'is_active', 'updated_at')
    list_filter = ('is_active', 'category')
    search_fields = ('name', 'category__name')
    ordering = ('category__display_order', 'display_order', 'name')


@admin.register(UserProjectAssignment)
class UserProjectAssignmentAdmin(admin.ModelAdmin):
    list_display = (
        'username',
        'project_name',
        'start_chainage',
        'end_chainage',
        'total_project_length',
        'updated_at',
    )
    search_fields = ('username', 'project_name')
    ordering = ('username',)


@admin.register(DPRReport)
class DPRReportAdmin(admin.ModelAdmin):
    list_display = (
        'id',
        'username',
        'project_name',
        'report_date',
        'reporting_engineer',
        'status',
        'created_at',
    )
    list_filter = ('status', 'project_name', 'report_date')
    search_fields = ('username', 'project_name', 'reporting_engineer', 'remarks')
    readonly_fields = ('created_at', 'updated_at', 'uploaded_images')
    ordering = ('-created_at',)


@admin.register(RoutineMaintenanceCategory)
class RoutineMaintenanceCategoryAdmin(admin.ModelAdmin):
    list_display = ('id', 'name', 'display_order', 'is_active', 'updated_at')
    list_filter = ('is_active',)
    search_fields = ('name',)
    ordering = ('display_order', 'name')


@admin.register(RoutineMaintenanceSubcategory)
class RoutineMaintenanceSubcategoryAdmin(admin.ModelAdmin):
    list_display = ('id', 'category', 'name', 'display_order', 'is_active', 'updated_at')
    list_filter = ('is_active', 'category')
    search_fields = ('name', 'category__name')
    ordering = ('category', 'display_order', 'name')


@admin.register(SideMaster)
class SideMasterAdmin(admin.ModelAdmin):
    list_display = ('id', 'name', 'display_order', 'is_active')
    list_filter = ('is_active',)
    ordering = ('display_order', 'name')


@admin.register(SeverityMaster)
class SeverityMasterAdmin(admin.ModelAdmin):
    list_display = ('id', 'name', 'display_order', 'is_active')
    list_filter = ('is_active',)
    ordering = ('display_order', 'name')


@admin.register(RoutineMaintenanceReport)
class RoutineMaintenanceReportAdmin(admin.ModelAdmin):
    list_display = (
        'id',
        'username',
        'project_name',
        'category',
        'subcategory',
        'side',
        'severity',
        'inspection_datetime',
        'status',
        'created_at',
    )
    list_filter = ('status', 'category', 'severity', 'side')
    search_fields = ('username', 'project_name', 'description_of_work', 'chainage')
    readonly_fields = ('created_at', 'updated_at', 'uploaded_image_urls')
    ordering = ('-created_at',)


@admin.register(ChainageReferencePoint)
class ChainageReferencePointAdmin(admin.ModelAdmin):
    list_display = (
        'id',
        'project_name',
        'chainage_km',
        'chainage_meters',
        'latitude',
        'longitude',
    )
    list_filter = ('project_name',)
    search_fields = ('project_name',)
    ordering = ('project_name', 'chainage_meters')
