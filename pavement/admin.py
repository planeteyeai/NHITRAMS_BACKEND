from django.contrib import admin
from django.utils.html import format_html
from .models import Pavement, RoadSegments, ProjectInfo
# NOTE: Projects (pavement) maps to the same `projects` DB table as inventory.Project.
# The canonical Project admin is registered in inventory/admin.py — do NOT register here.


# ─────────────────────────────────────────────
# RoadSegments Admin (Pavement)
# ─────────────────────────────────────────────

@admin.register(RoadSegments)
class PavementRoadSegmentsAdmin(admin.ModelAdmin):
    list_display = (
        'segment_id', 'chainage_start', 'chainage_end',
        'direction', 'carriage_type', 'segment_length'
    )
    search_fields = ('segment_id', 'direction', 'carriage_type')
    list_filter = ('direction', 'carriage_type')
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
# ProjectInfo Admin (Pavement)
# ─────────────────────────────────────────────

@admin.register(ProjectInfo)
class PavementProjectInfoAdmin(admin.ModelAdmin):
    list_display = ('project', 'month_val')
    search_fields = ('project__project_name',)
    date_hierarchy = 'month_val'
    ordering = ('-month_val',)
    list_select_related = ('project',)
    readonly_fields = ('project', 'month_val')


# ─────────────────────────────────────────────
# PCS / IRI Color Maps
# ─────────────────────────────────────────────

PCS_COLORS = {
    'Good':      '#27ae60',
    'Fair':      '#f1c40f',
    'Poor':      '#e67e22',
    'Very Poor': '#e74c3c',
    'Failed':    '#8e44ad',
}

IRI_COLORS = {
    'Good':       '#27ae60',
    'Acceptable': '#f1c40f',
    'Alert':      '#e67e22',
    'Severe':     '#e74c3c',
}


# ─────────────────────────────────────────────
# Custom Filters
# ─────────────────────────────────────────────

class PCSFilter(admin.SimpleListFilter):
    title = 'Pavement Condition Score'
    parameter_name = 'pcs'

    def lookups(self, request, model_admin):
        vals = Pavement.objects.values_list(
            'pavement_condition_score_pcs', flat=True
        ).distinct()
        return [(v, v) for v in sorted(v for v in set(vals) if v is not None)]

    def queryset(self, request, queryset):
        if self.value():
            return queryset.filter(pavement_condition_score_pcs=self.value())
        return queryset


class DrainageFilter(admin.SimpleListFilter):
    title = 'Drainage Condition'
    parameter_name = 'drainage'

    def lookups(self, request, model_admin):
        vals = Pavement.objects.values_list(
            'drainage_condition', flat=True
        ).distinct()
        return [(v, v) for v in sorted(v for v in set(vals) if v is not None)]

    def queryset(self, request, queryset):
        if self.value():
            return queryset.filter(drainage_condition=self.value())
        return queryset


# ─────────────────────────────────────────────
# Pavement Admin
# ─────────────────────────────────────────────

@admin.register(Pavement)
class PavementAdmin(admin.ModelAdmin):
    list_display = (
        'pavement_id',
        'project_name_display',
        'sum_chainage',
        'date_of_survey',
        'pcs_badge',
        'iri_badge',
        'roughness_status',
        'aadt',
        'drainage_cond',
        'last_maintenance',
        'cvd_class',
        'coordinates',
    )
    list_filter = (
        'date_of_survey',
        PCSFilter,
        DrainageFilter,
        'roughness_index_status',
        'commercial_vehicle_damage_cvd_class',
        'project',
        'segment__direction',
    )
    search_fields = (
        'project__project_name',
        'road_id',
        'drainage_condition',
        'type_of_last_maintenance',
    )
    date_hierarchy = 'date_of_survey'
    ordering = ('-date_of_survey', 'sum_chainage')
    list_per_page = 50
    list_select_related = ('project', 'segment')
    readonly_fields = (
        'pavement_id', 'sum_chainage', 'date_of_survey', 'road_id',
        'latitude', 'longitude', 'aadt', 'pavement_condition_score_pcs',
        'iri_index', 'rainfall_mm', 'drainage_condition',
        'type_of_last_maintenance', 'last_maintenance_date',
        'carriageway_width', 'temperature_c',
        'international_roughness_index_iri', 'roughness_index_status',
        'commercial_vehicle_damage_cvd_class', 'project', 'segment',
    )

    fieldsets = (
        ('Survey Identity', {
            'fields': (
                'pavement_id', 'project', 'segment',
                'road_id', 'date_of_survey', 'sum_chainage'
            ),
        }),
        ('Pavement Condition', {
            'fields': (
                'pavement_condition_score_pcs', 'iri_index',
                'international_roughness_index_iri', 'roughness_index_status',
                'commercial_vehicle_damage_cvd_class',
            ),
        }),
        ('Environmental & Traffic', {
            'fields': (
                'aadt', 'rainfall_mm', 'temperature_c',
                'drainage_condition', 'carriageway_width'
            ),
        }),
        ('Maintenance', {
            'fields': ('type_of_last_maintenance', 'last_maintenance_date'),
            'classes': ('collapse',),
        }),
        ('Location', {
            'fields': ('latitude', 'longitude'),
        }),
    )

    def project_name_display(self, obj):
        return obj.project.project_name if obj.project else '—'
    project_name_display.short_description = 'Project'
    project_name_display.admin_order_field = 'project__project_name'

    def pcs_badge(self, obj):
        val = obj.pavement_condition_score_pcs or '—'
        color = PCS_COLORS.get(val, '#7f8c8d')
        return format_html(
            '<span style="background:{};color:#fff;padding:2px 8px;border-radius:10px;'
            'font-size:12px;font-weight:bold;">{}</span>',
            color, val
        )
    pcs_badge.short_description = 'PCS'
    pcs_badge.admin_order_field = 'pavement_condition_score_pcs'

    def iri_badge(self, obj):
        val = obj.iri_index or '—'
        color = IRI_COLORS.get(val, '#7f8c8d')
        return format_html(
            '<span style="background:{};color:#fff;padding:2px 8px;'
            'border-radius:10px;font-size:12px;">{}</span>',
            color, val
        )
    iri_badge.short_description = 'IRI'
    iri_badge.admin_order_field = 'iri_index'

    def roughness_status(self, obj):
        val = obj.roughness_index_status or '—'
        val_lower = val.lower()
        if 'good' in val_lower:
            icon = '✅'
        elif 'severe' in val_lower:
            icon = '🔴'
        elif 'alert' in val_lower or 'poor' in val_lower:
            icon = '⚠️'
        else:
            icon = 'ℹ️'
        return format_html('{} {}', icon, val)
    roughness_status.short_description = 'Roughness Status'
    roughness_status.admin_order_field = 'roughness_index_status'

    def drainage_cond(self, obj):
        return obj.drainage_condition or '—'
    drainage_cond.short_description = 'Drainage'
    drainage_cond.admin_order_field = 'drainage_condition'

    def last_maintenance(self, obj):
        if obj.last_maintenance_date:
            return format_html(
                '<span style="color:#7f8c8d;">{}</span>',
                obj.last_maintenance_date
            )
        return '—'
    last_maintenance.short_description = 'Last Maintenance'
    last_maintenance.admin_order_field = 'last_maintenance_date'

    def cvd_class(self, obj):
        val = obj.commercial_vehicle_damage_cvd_class or '—'
        return format_html(
            '<code style="background:#f8f9fa;padding:2px 4px;border-radius:3px;">{}</code>',
            val
        )
    cvd_class.short_description = 'CVD Class'
    cvd_class.admin_order_field = 'commercial_vehicle_damage_cvd_class'

    def coordinates(self, obj):
        return format_html(
            '<a href="https://maps.google.com/?q={},{}" target="_blank" '
            'style="color:#2980b9;">📍 Map</a>',
            obj.latitude or 0, obj.longitude or 0
        )
    coordinates.short_description = 'Location'
