from django.contrib import admin

from .models import GoogleSheetDistress


@admin.register(GoogleSheetDistress)
class GoogleSheetDistressAdmin(admin.ModelAdmin):
    list_display = (
        'sync_id',
        'project_name_raw',
        'distress_type',
        'latitude',
        'longitude',
        'date',
        'updated_at',
    )
    list_filter = ('distress_type', 'project')
    search_fields = (
        'sync_id',
        'project_name_raw',
        'project_name_normalized',
        'distress_type',
        'image_url',
    )
    readonly_fields = ('sync_id', 'created_at', 'updated_at', 'project_name_normalized')
    ordering = ('-updated_at',)
