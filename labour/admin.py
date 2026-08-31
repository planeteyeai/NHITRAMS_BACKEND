from django.contrib import admin

from .models import Labour


@admin.register(Labour)
class LabourAdmin(admin.ModelAdmin):
    list_display = (
        'id',
        'first_name',
        'last_name',
        'crew_id',
        'labour_type',
        'gender',
        'contact_number',
        'date_of_birth',
        'created_by',
        'created_at',
    )
    list_filter = ('labour_type', 'gender', 'crew_id')
    search_fields = (
        'first_name',
        'last_name',
        'crew_id',
        'contact_number',
        'address',
    )
    ordering = ('-created_at',)
    list_per_page = 50
    readonly_fields = ('created_at', 'updated_at', 'created_by', 'updated_by')
