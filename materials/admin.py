from django.contrib import admin

from .models import Material


@admin.register(Material)
class MaterialAdmin(admin.ModelAdmin):
    list_display = (
        'id',
        'material_name',
        'material_unit',
        'material_type',
        'other_type',
        'created_by',
        'created_at',
    )
    list_filter = ('material_unit', 'material_type')
    search_fields = (
        'material_name',
        'other_type',
        'material_description',
    )
    ordering = ('-created_at',)
    list_per_page = 50
    readonly_fields = ('created_at', 'updated_at', 'created_by', 'updated_by')
