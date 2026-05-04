from django.contrib import admin
from django.utils.html import format_html
from .models import ProjectInfo


# ─────────────────────────────────────────────
# PIS (Project Information System) Admin
# ─────────────────────────────────────────────

class ProgressFilter(admin.SimpleListFilter):
    title = 'Overall Progress'
    parameter_name = 'progress'

    def lookups(self, request, model_admin):
        # overall_progress is stored as double precision in DB — cannot use __exact=''
        # Only exclude NULLs; cast to str for display purposes
        values = ProjectInfo.objects.exclude(
            overall_progress__isnull=True
        ).values_list('overall_progress', flat=True).distinct()
        return [(v, str(v)) for v in sorted(v for v in set(values) if v is not None)]

    def queryset(self, request, queryset):
        if self.value():
            return queryset.filter(overall_progress=self.value())
        return queryset


@admin.register(ProjectInfo)
class ProjectInfoAdmin(admin.ModelAdmin):
    list_display = (
        'info_id',
        'project_id',
        'month_val',
        'overall_progress_badge',
        'kilometer',
        'length',
        'carriage_width',
        'aadt',
        'structures_summary',
        'toll_plaza_location',
        'coordinates',
        'date_navigation',
    )
    list_filter = (
        'month_val',
        ProgressFilter,
    )
    search_fields = (
        'toll_plaza_location',
        'overall_progress',
    )
    date_hierarchy = 'month_val'
    ordering = ('-month_val',)
    list_per_page = 30
    readonly_fields = (
        'info_id', 'project_id', 'month_val', 'overall_progress',
        'longitude', 'latitude', 'kilometer', 'length', 'toll_plaza_location',
        'previous_date', 'next_date', 'carriage_width',
        'mjb', 'mnb', 'flyover', 'aadt', 'pup', 'vup', 'rob',
    )

    fieldsets = (
        ('Project Reference', {
            'fields': ('info_id', 'project_id', 'month_val', 'overall_progress'),
        }),
        ('Road Info', {
            'fields': ('kilometer', 'length', 'carriage_width', 'toll_plaza_location'),
        }),
        ('Survey Dates', {
            'fields': ('previous_date', 'next_date'),
        }),
        ('Structures', {
            'fields': ('mjb', 'mnb', 'flyover', 'aadt', 'pup', 'vup', 'rob'),
            'classes': ('collapse',),
        }),
        ('Location', {
            'fields': ('latitude', 'longitude'),
        }),
    )

    def overall_progress_badge(self, obj):
        raw = obj.overall_progress
        if raw is None:
            return '—'
        # If stored as float (e.g. 0.85 = 85%), display as percentage
        try:
            pct = float(raw)
            val = '{:.1f}%'.format(pct * 100) if pct <= 1 else '{:.1f}%'.format(pct)
            color = '#27ae60' if pct >= 0.9 else ('#e67e22' if pct >= 0.6 else '#e74c3c')
        except (TypeError, ValueError):
            val = str(raw)
            val_lower = val.lower()
            color = ('#27ae60' if 'complet' in val_lower
                     else '#e67e22' if ('progress' in val_lower or 'ongoing' in val_lower)
                     else '#7f8c8d')
        return format_html(
            '<span style="background:{};color:#fff;padding:2px 8px;border-radius:10px;font-size:12px;">{}</span>',
            color, val
        )
    overall_progress_badge.short_description = 'Progress'
    overall_progress_badge.admin_order_field = 'overall_progress'

    def structures_summary(self, obj):
        total = (obj.mjb or 0) + (obj.mnb or 0) + (obj.flyover or 0) + (obj.rob or 0) + (obj.pup or 0) + (obj.vup or 0)
        return format_html(
            '<span title="MJB:{} | MNB:{} | Flyover:{} | ROB:{} | PUP:{} | VUP:{}" '
            'style="cursor:help;color:#8e44ad;font-weight:bold;">🏗 {}</span>',
            obj.mjb, obj.mnb, obj.flyover, obj.rob, obj.pup, obj.vup, total
        )
    structures_summary.short_description = 'Structures (hover)'

    def coordinates(self, obj):
        return format_html(
            '<a href="https://maps.google.com/?q={},{}" target="_blank" style="color:#2980b9;">📍 Map</a>',
            obj.latitude or 0, obj.longitude or 0
        )
    coordinates.short_description = 'Location'

    def date_navigation(self, obj):
        prev = obj.previous_date or '—'
        nxt = obj.next_date or '—'
        return format_html(
            '<small style="color:#7f8c8d;">← {} | {} →</small>',
            prev, nxt
        )
    date_navigation.short_description = 'Prev / Next Date'
