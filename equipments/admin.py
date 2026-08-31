from django.contrib import admin

from .models import Equipment


@admin.register(Equipment)
class EquipmentAdmin(admin.ModelAdmin):
    list_display = (
        'id',
        'item_name',
        'equipment_code',
        'year_of_make',
        'status',
        'estimate_cost',
        'created_by',
        'created_at',
    )
    list_filter = ('status', 'year_of_make')
    search_fields = (
        'item_name',
        'make_and_model',
        'equipment_code',
        'remark',
    )
    ordering = ('-created_at',)
    list_per_page = 50
    readonly_fields = ('created_at', 'updated_at', 'created_by', 'updated_by')
