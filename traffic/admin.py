from django.contrib import admin
from django.utils.html import format_html
from django.db.models import Count
from .models import Traffic
from inventory.models import Project, RoadSegment


# ─────────────────────────────────────────────
# Traffic Admin
# ─────────────────────────────────────────────

class AADTRangeFilter(admin.SimpleListFilter):
    title = 'AADT Range (Vehicles)'
    parameter_name = 'aadt_range'

    def lookups(self, request, model_admin):
        return [
            ('low',    'Low  (< 1,000)'),
            ('medium', 'Medium (1,000–5,000)'),
            ('high',   'High (5,000+)'),
        ]

    def queryset(self, request, queryset):
        if self.value() == 'low':
            return queryset.filter(aadt_vehicles__lt=1000)
        if self.value() == 'medium':
            return queryset.filter(aadt_vehicles__gte=1000, aadt_vehicles__lte=5000)
        if self.value() == 'high':
            return queryset.filter(aadt_vehicles__gt=5000)
        return queryset


@admin.register(Traffic)
class TrafficAdmin(admin.ModelAdmin):
    list_display = (
        'traffic_id',
        'project_name_display',
        'survey_date',
        'year_val',
        'segment_range',
        'aadt_badge',
        'aadt_pcu',
        'cvd_vehicles',
        'vehicle_breakdown',
        'toll_plaza',
        'coordinates',
    )
    list_filter = (
        'survey_date',
        'year_val',
        AADTRangeFilter,
        'project',
        'segment__direction',
    )
    search_fields = (
        'project__project_name',
        'toll_plaza',
        'year_val',
    )
    date_hierarchy = 'survey_date'
    ordering = ('-survey_date', 'project__project_name')
    list_per_page = 50
    list_select_related = ('project', 'segment')
    readonly_fields = (
        'traffic_id', 'project', 'segment',
        'survey_date', 'year_val', 'length_val',
        'three_wheeler_auto', 'car_jeep_van_taxi', 'standard_bus', 'lcv',
        'two_axle_trucks', 'three_axle_trucks', 'mav', 'osv', 'tractor', 'others',
        'aadt_vehicles', 'aadt_pcu', 'cvd_vehicles',
        'latitude', 'longitude', 'toll_plaza', 'source_file',
    )

    fieldsets = (
        ('Survey Info', {
            'fields': ('traffic_id', 'project', 'segment', 'survey_date', 'year_val', 'length_val', 'toll_plaza'),
        }),
        ('Vehicle Counts', {
            'fields': (
                'three_wheeler_auto', 'car_jeep_van_taxi', 'standard_bus', 'lcv',
                'two_axle_trucks', 'three_axle_trucks', 'mav', 'osv', 'tractor', 'others',
            ),
            'classes': ('collapse',),
        }),
        ('AADT & CVD', {
            'fields': ('aadt_vehicles', 'aadt_pcu', 'cvd_vehicles'),
        }),
        ('Location', {
            'fields': ('latitude', 'longitude', 'source_file'),
        }),
    )

    def project_name_display(self, obj):
        return obj.project.project_name if obj.project else '—'
    project_name_display.short_description = 'Project'
    project_name_display.admin_order_field = 'project__project_name'

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

    def aadt_badge(self, obj):
        val = obj.aadt_vehicles or 0
        if val >= 5000:
            color = '#e74c3c'
        elif val >= 1000:
            color = '#e67e22'
        else:
            color = '#27ae60'
        return format_html(
            '<span style="background:{};color:#fff;padding:2px 8px;border-radius:12px;font-weight:bold;">{}</span>',
            color, '{:,}'.format(val)
        )
    aadt_badge.short_description = 'AADT (Veh)'
    aadt_badge.admin_order_field = 'aadt_vehicles'

    def vehicle_breakdown(self, obj):
        total = (
            (obj.three_wheeler_auto or 0) +
            (obj.car_jeep_van_taxi or 0) +
            (obj.standard_bus or 0) +
            (obj.lcv or 0) +
            (obj.two_axle_trucks or 0) +
            (obj.three_axle_trucks or 0) +
            (obj.mav or 0) +
            (obj.osv or 0) +
            (obj.tractor or 0) +
            (obj.others or 0)
        )
        return format_html(
            '<span title="3W:{} | Car:{} | Bus:{} | LCV:{} | 2-Axle:{} | 3-Axle:{} | MAV:{} | OSV:{} | Tractor:{} | Others:{}"'
            ' style="cursor:help;color:#8e44ad;">📊 {}</span>',
            obj.three_wheeler_auto, obj.car_jeep_van_taxi, obj.standard_bus, obj.lcv,
            obj.two_axle_trucks, obj.three_axle_trucks, obj.mav, obj.osv, obj.tractor, obj.others,
            total
        )
    vehicle_breakdown.short_description = 'Total Vehicles (hover)'

    def coordinates(self, obj):
        return format_html(
            '<a href="https://maps.google.com/?q={},{}" target="_blank" style="color:#2980b9;">📍 Map</a>',
            obj.latitude or 0, obj.longitude or 0
        )
    coordinates.short_description = 'Location'
