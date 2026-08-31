"""Work Order admin."""

from django.contrib import admin

from .models import WorkOrder


@admin.register(WorkOrder)
class WorkOrderAdmin(admin.ModelAdmin):
    list_display = (
        'id',
        'work_order_id',
        'type_of_work',
        'other_type',
        'chainage_km_from',
        'chainage_km_to',
        'created_by',
        'created_at',
    )
    list_filter = ('type_of_work',)
    search_fields = ('work_order_id', 'type_of_work', 'other_type', 'created_by')
    ordering = ('-created_at', '-id')
