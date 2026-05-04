from django.contrib import admin
from django.utils.html import format_html
from .models import RoadsideFeatures, RoadSegments, ProjectInfo
# NOTE: Projects (roadside) maps to the same `projects` DB table as inventory.Project.
# The canonical Project admin is registered in inventory/admin.py — do NOT register here.


# ─────────────────────────────────────────────
# RoadSegments Admin (Roadside)
# ─────────────────────────────────────────────

@admin.register(RoadSegments)
class RoadsideRoadSegmentsAdmin(admin.ModelAdmin):
    list_display = (
        'segment_id', 'chainage_start', 'chainage_end',
        'direction', 'carriage_type', 'lane', 'segment_length'
    )
    search_fields = ('segment_id', 'direction', 'carriage_type')
    list_filter = ('direction', 'carriage_type', 'lane')
    ordering = ('chainage_start',)
    list_per_page = 50

    def segment_length(self, obj):
        length = (obj.chainage_end or 0) - (obj.chainage_start or 0)
        return format_html(
            '<span style="color:#27ae60;">{} km</span>',
            '{:.3f}'.format(length)
        )
    segment_length.short_description = 'Length'


# ─────────────────────────────────────────────
# ProjectInfo Admin (Roadside)
# ─────────────────────────────────────────────

@admin.register(ProjectInfo)
class RoadsideProjectInfoAdmin(admin.ModelAdmin):
    list_display = ('project', 'month_val')
    search_fields = ('project__project_name',)
    date_hierarchy = 'month_val'
    ordering = ('-month_val',)
    list_select_related = ('project',)
    readonly_fields = ('project', 'month_val')


# ─────────────────────────────────────────────
# Condition / Hazard Color Maps
# ─────────────────────────────────────────────

CONDITION_COLORS = {
    'Good':    '#27ae60',
    'Fair':    '#f1c40f',
    'Poor':    '#e74c3c',
    'Damaged': '#8e44ad',
    'Missing': '#c0392b',
}

HAZARD_COLORS = {
    'Y':   '#e74c3c',
    'N':   '#27ae60',
    'Yes': '#e74c3c',
    'No':  '#27ae60',
}


# ─────────────────────────────────────────────
# Custom Filters
# ─────────────────────────────────────────────

class FeatureDescFilter(admin.SimpleListFilter):
    title = 'Feature Type'
    parameter_name = 'feature_desc'

    def lookups(self, request, model_admin):
        vals = RoadsideFeatures.objects.values_list(
            'feature_desc', flat=True
        ).distinct()
        return [(v, v) for v in sorted(v for v in set(vals) if v is not None)]

    def queryset(self, request, queryset):
        if self.value():
            return queryset.filter(feature_desc=self.value())
        return queryset


class TerrainFilter(admin.SimpleListFilter):
    title = 'Terrain'
    parameter_name = 'terrain'

    def lookups(self, request, model_admin):
        vals = RoadsideFeatures.objects.values_list('terrain', flat=True).distinct()
        return [(v, v) for v in sorted(v for v in set(vals) if v is not None)]

    def queryset(self, request, queryset):
        if self.value():
            return queryset.filter(terrain=self.value())
        return queryset


# ─────────────────────────────────────────────
# RoadsideFeatures Admin
# ─────────────────────────────────────────────

@admin.register(RoadsideFeatures)
class RoadsideFeaturesAdmin(admin.ModelAdmin):
    list_display = (
        'feature_id',
        'project_name_display',
        'feature_desc_display',
        'material_type',
        'condition_badge',
        'hazard_badge',
        'land_use',
        'terrain',
        'survey_date',
        'segment_range',
        'cross_section_location',
        'coordinates',
    )
    list_filter = (
        'survey_date',
        FeatureDescFilter,
        TerrainFilter,
        'feature_condition',
        'safety_hazard',
        'land_use',
        'project',
        'segment__direction',
    )
    search_fields = (
        'project__project_name',
        'feature_desc',
        'material_type',
        'remarks',
        'cross_section_location',
    )
    date_hierarchy = 'survey_date'
    ordering = ('-survey_date', 'project__project_name')
    list_per_page = 50
    list_select_related = ('project', 'segment')
    readonly_fields = (
        'feature_id', 'survey_date', 'feature_desc', 'material_type',
        'feature_condition', 'safety_hazard', 'land_use', 'terrain',
        'latitude', 'longitude', 'altitude', 'remarks',
        'length_val', 'cross_section_location', 'offset_from_center',
        'segment', 'project',
    )

    fieldsets = (
        ('Feature Identity', {
            'fields': ('feature_id', 'project', 'segment', 'survey_date'),
        }),
        ('Feature Details', {
            'fields': ('feature_desc', 'material_type', 'feature_condition', 'safety_hazard'),
        }),
        ('Environment', {
            'fields': ('land_use', 'terrain'),
        }),
        ('Geometry', {
            'fields': ('length_val', 'cross_section_location', 'offset_from_center', 'remarks'),
            'classes': ('collapse',),
        }),
        ('Location', {
            'fields': ('latitude', 'longitude', 'altitude'),
        }),
    )

    def project_name_display(self, obj):
        return obj.project.project_name if obj.project else '—'
    project_name_display.short_description = 'Project'
    project_name_display.admin_order_field = 'project__project_name'

    def feature_desc_display(self, obj):
        return format_html(
            '<span style="background:#eaf4fb;color:#2980b9;padding:2px 6px;border-radius:6px;">{}</span>',
            obj.feature_desc or '—'
        )
    feature_desc_display.short_description = 'Feature'
    feature_desc_display.admin_order_field = 'feature_desc'

    def condition_badge(self, obj):
        val = obj.feature_condition or '—'
        color = CONDITION_COLORS.get(val, '#7f8c8d')
        return format_html(
            '<span style="background:{};color:#fff;padding:2px 8px;'
            'border-radius:10px;font-size:12px;">{}</span>',
            color, val
        )
    condition_badge.short_description = 'Condition'
    condition_badge.admin_order_field = 'feature_condition'

    def hazard_badge(self, obj):
        val = obj.safety_hazard or '—'
        color = HAZARD_COLORS.get(val, '#7f8c8d')
        icon = '⚠️' if val in ('Y', 'Yes') else ('✅' if val in ('N', 'No') else 'ℹ️')
        return format_html('<span style="color:{};">{} {}</span>', color, icon, val)
    hazard_badge.short_description = 'Safety Hazard'
    hazard_badge.admin_order_field = 'safety_hazard'

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
            '<a href="https://maps.google.com/?q={},{}" target="_blank" '
            'style="color:#2980b9;">📍 Map</a>',
            obj.latitude or 0, obj.longitude or 0
        )
    coordinates.short_description = 'Location'
