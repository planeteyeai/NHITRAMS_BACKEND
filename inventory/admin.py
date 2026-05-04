from django.contrib import admin
from django.utils.html import format_html
from django.db.models import Sum, Count
from django.contrib.admin import SimpleListFilter
from .models import Inventory, AssetType, SubAssetType, Project, RoadSegment


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
# Inventory Admin
# ─────────────────────────────────────────────

class AssetTypeListFilter(SimpleListFilter):
    title = 'Asset Category'
    parameter_name = 'asset_cat'

    def lookups(self, request, model_admin):
        return AssetType.objects.values_list('asset_type_name', 'asset_type_name')

    def queryset(self, request, queryset):
        if self.value():
            return queryset.filter(asset_type__asset_type_name=self.value())
        return queryset


@admin.register(Inventory)
class InventoryAdmin(admin.ModelAdmin):
    list_display = (
        'inventory_id',
        'project_name_display',
        'asset_type_display',
        'sub_asset_display',
        'survey_date',
        'count_badge',
        'segment_range',
        'coordinates',
    )
    list_filter = (
        'asset_type',
        'survey_date',
        'segment__direction',
        AssetTypeListFilter,
        'project',
    )
    search_fields = (
        'project__project_name',
        'asset_type__asset_type_name',
        'sub_asset_type__sub_asset_type_name',
    )
    date_hierarchy = 'survey_date'
    ordering = ('-survey_date', 'project__project_name')
    list_per_page = 50
    list_select_related = ('project', 'asset_type', 'sub_asset_type', 'segment')
    readonly_fields = (
        'inventory_id', 'segment', 'project', 'asset_type', 'sub_asset_type',
        'survey_date', 'latitude', 'longitude', 'count_val',
    )

    fieldsets = (
        ('Identification', {
            'fields': ('inventory_id', 'project', 'asset_type', 'sub_asset_type', 'survey_date'),
        }),
        ('Segment', {
            'fields': ('segment',),
        }),
        ('Count & Location', {
            'fields': ('count_val', 'latitude', 'longitude'),
        }),
    )

    def project_name_display(self, obj):
        return obj.project.project_name if obj.project else '—'
    project_name_display.short_description = 'Project'
    project_name_display.admin_order_field = 'project__project_name'

    def asset_type_display(self, obj):
        name = obj.asset_type.asset_type_name if obj.asset_type else '—'
        return format_html(
            '<span style="background:#eaf4fb;color:#2980b9;padding:2px 6px;border-radius:6px;">{}</span>',
            name
        )
    asset_type_display.short_description = 'Asset Type'
    asset_type_display.admin_order_field = 'asset_type__asset_type_name'

    def sub_asset_display(self, obj):
        return obj.sub_asset_type.sub_asset_type_name if obj.sub_asset_type else '—'
    sub_asset_display.short_description = 'Sub-Asset'

    def count_badge(self, obj):
        val = obj.count_val or 0
        return format_html(
            '<span style="background:#27ae60;color:#fff;padding:2px 10px;border-radius:12px;font-weight:bold;">{}</span>',
            int(val)
        )
    count_badge.short_description = 'Count'
    count_badge.admin_order_field = 'count_val'

    def segment_range(self, obj):
        if obj.segment:
            return format_html(
                '<small>{} → {} ({})</small>',
                obj.segment.chainage_start,
                obj.segment.chainage_end,
                obj.segment.direction,
            )
        return '—'
    segment_range.short_description = 'Chainage'

    def coordinates(self, obj):
        return format_html(
            '<a href="https://maps.google.com/?q={},{}" target="_blank" style="color:#2980b9;">📍 Map</a>',
            obj.latitude or 0, obj.longitude or 0
        )
    coordinates.short_description = 'Location'
