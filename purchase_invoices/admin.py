from django.contrib import admin

from .models import PurchaseInvoice, PurchaseInvoiceItem


class PurchaseInvoiceItemInline(admin.TabularInline):
    model = PurchaseInvoiceItem
    extra = 0
    readonly_fields = ('created_at', 'updated_at')
    autocomplete_fields = ('material',)


@admin.register(PurchaseInvoice)
class PurchaseInvoiceAdmin(admin.ModelAdmin):
    list_display = (
        'id',
        'invoice_number',
        'supplier',
        'invoice_date',
        'created_by',
        'created_at',
    )
    list_filter = ('invoice_date',)
    search_fields = ('invoice_number', 'supplier__company_name')
    inlines = [PurchaseInvoiceItemInline]
    readonly_fields = ('created_at', 'updated_at', 'created_by', 'updated_by')
    autocomplete_fields = ('supplier',)


@admin.register(PurchaseInvoiceItem)
class PurchaseInvoiceItemAdmin(admin.ModelAdmin):
    list_display = (
        'id',
        'purchase_invoice',
        'material',
        'quantity',
        'rate',
        'batch_number',
    )
    autocomplete_fields = ('purchase_invoice', 'material')
