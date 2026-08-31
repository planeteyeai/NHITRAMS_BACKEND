from django.contrib import admin

from .models import Supplier


@admin.register(Supplier)
class SupplierAdmin(admin.ModelAdmin):
    list_display = (
        'id',
        'company_name',
        'contact_person',
        'mobile_number',
        'email',
        'state',
        'city',
        'created_by',
        'created_at',
    )
    list_filter = ('state', 'city')
    search_fields = (
        'company_name',
        'contact_person',
        'email',
        'mobile_number',
        'address',
    )
    ordering = ('-created_at',)
    list_per_page = 50
    readonly_fields = ('created_at', 'updated_at', 'created_by', 'updated_by')
