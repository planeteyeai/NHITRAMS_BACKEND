from django.contrib import admin
from django.utils.html import format_html
from django.db.models import Sum, Count, Avg
from django.contrib.admin import SimpleListFilter
from .models import Distress, DistressType, RoadSegment, DistressReport


# ─────────────────────────────────────────────
# Custom Filters
# ─────────────────────────────────────────────

class SeverityFilter(SimpleListFilter):
    title = 'Severity (Count)'
    parameter_name = 'severity'

    def lookups(self, request, model_admin):
        return [
            ('low', 'Low (1–5)'),
            ('medium', 'Medium (6–20)'),
            ('high', 'High (21+)'),
        ]

    def queryset(self, request, queryset):
        if self.value() == 'low':
            return queryset.filter(count_val__gte=1, count_val__lte=5)
        if self.value() == 'medium':
            return queryset.filter(count_val__gte=6, count_val__lte=20)
        if self.value() == 'high':
            return queryset.filter(count_val__gte=21)
        return queryset


# ─────────────────────────────────────────────
# DistressType Admin
# ─────────────────────────────────────────────

@admin.register(DistressType)
class DistressTypeAdmin(admin.ModelAdmin):
    list_display = ('distress_type_id', 'distress_type_name', 'distress_count')
    search_fields = ('distress_type_name',)
    ordering = ('distress_type_name',)

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.annotate(_distress_count=Count('distress'))

    def distress_count(self, obj):
        count = getattr(obj, '_distress_count', 0)
        return format_html('<span style="font-weight:bold;color:#1a7ab5;">{}</span>', count)
    distress_count.short_description = 'Total Records'
    distress_count.admin_order_field = '_distress_count'


# ─────────────────────────────────────────────
# RoadSegment Admin (Distress)
# ─────────────────────────────────────────────

@admin.register(RoadSegment)
class RoadSegmentAdmin(admin.ModelAdmin):
    list_display = (
        'segment_id', 'chainage_start', 'chainage_end',
        'direction', 'carriage_type', 'lane', 'segment_length_km'
    )
    search_fields = ('direction', 'carriage_type', 'lane')
    list_filter = ('direction', 'carriage_type', 'lane')
    ordering = ('chainage_start',)
    list_per_page = 50

    def segment_length_km(self, obj):
        length = (obj.chainage_end or 0) - (obj.chainage_start or 0)
        # Pre-format the float — format_html does NOT support :.3f format specs
        return format_html(
            '<span style="color:#27ae60;">{} km</span>',
            '{:.3f}'.format(length)
        )
    segment_length_km.short_description = 'Length (km)'


# ─────────────────────────────────────────────
# Distress Admin
# ─────────────────────────────────────────────

@admin.register(Distress)
class DistressAdmin(admin.ModelAdmin):
    list_display = (
        'distress_id',
        'project_name',
        'distress_type_name',
        'distress_date',
        'count_badge',
        'length_val',
        'area_val',
        'width_val',
        'depth_val',
        'segment_range',
        'coordinates',
    )
    list_filter = (
        'distress_type',
        'project',
        SeverityFilter,
        'distress_date',
        'segment__direction',
        'segment__carriage_type',
    )
    search_fields = (
        'project__project_name',
        'distress_type__distress_type_name',
        'segment__direction',
    )
    date_hierarchy = 'distress_date'
    ordering = ('-distress_date', 'project__project_name')
    list_per_page = 50
    list_select_related = ('project', 'distress_type', 'segment')
    readonly_fields = (
        'distress_id', 'project', 'segment', 'distress_type',
        'distress_date', 'latitude', 'longitude',
        'count_val', 'length_val', 'area_val', 'width_val', 'depth_val',
    )

    fieldsets = (
        ('Identification', {
            'fields': ('distress_id', 'project', 'distress_type', 'distress_date'),
        }),
        ('Segment Info', {
            'fields': ('segment',),
        }),
        ('Measurements', {
            'fields': ('count_val', 'length_val', 'area_val', 'width_val', 'depth_val'),
            'classes': ('collapse',),
        }),
        ('Location', {
            'fields': ('latitude', 'longitude'),
        }),
    )

    def project_name(self, obj):
        return obj.project.project_name if obj.project else '—'
    project_name.short_description = 'Project'
    project_name.admin_order_field = 'project__project_name'

    def distress_type_name(self, obj):
        return obj.distress_type.distress_type_name if obj.distress_type else '—'
    distress_type_name.short_description = 'Distress Type'
    distress_type_name.admin_order_field = 'distress_type__distress_type_name'

    def count_badge(self, obj):
        val = obj.count_val or 0
        if val >= 21:
            color = '#e74c3c'
        elif val >= 6:
            color = '#e67e22'
        else:
            color = '#27ae60'
        return format_html(
            '<span style="background:{};color:#fff;padding:2px 8px;border-radius:12px;font-weight:bold;">{}</span>',
            color, val
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
    segment_range.short_description = 'Chainage Range'

    def coordinates(self, obj):
        return format_html(
            '<a href="https://maps.google.com/?q={},{}" target="_blank" '
            'style="color:#2980b9;text-decoration:none;">📍 Map</a>',
            obj.latitude or 0, obj.longitude or 0
        )
    coordinates.short_description = 'Location'


# ─────────────────────────────────────────────
# Admin site customization
# ─────────────────────────────────────────────

admin.site.site_header = "NHIT RAMS Administration"
admin.site.site_title = "NHIT RAMS"
admin.site.index_title = "Road Asset Management System"


@admin.register(DistressReport)
class DistressReportAdmin(admin.ModelAdmin):
    list_display = (
        'id',
        'username',
        'Project_Name',
        'Distress_Type',
        'Direction',
        'Date',
        'No_of_Distress',
        'created_at',
    )
    list_filter = ('Distress_Type', 'Direction', 'Project_Name')
    search_fields = ('username', 'Project_Name', 'Distress_Type', 'Image_URL')
    readonly_fields = ('created_at', 'updated_at')
    ordering = ('-created_at',)
