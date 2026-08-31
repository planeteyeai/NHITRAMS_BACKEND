from django.contrib import admin

from .models import Batch, TransferStock, TransferStockItem


@admin.register(Batch)
class BatchAdmin(admin.ModelAdmin):
    list_display = ('id', 'batch_number', 'is_active', 'created_at')
    search_fields = ('batch_number',)
    list_filter = ('is_active',)


class TransferStockItemInline(admin.TabularInline):
    model = TransferStockItem
    extra = 0
    readonly_fields = ('created_at', 'updated_at')
    autocomplete_fields = ('material', 'batch')


@admin.register(TransferStock)
class TransferStockAdmin(admin.ModelAdmin):
    list_display = ('id', 'supplier', 'date', 'location', 'created_by', 'created_at')
    list_filter = ('date',)
    search_fields = ('location', 'remark', 'supplier__company_name')
    inlines = [TransferStockItemInline]
    readonly_fields = ('created_at', 'updated_at', 'created_by', 'updated_by')
    autocomplete_fields = ('supplier',)


@admin.register(TransferStockItem)
class TransferStockItemAdmin(admin.ModelAdmin):
    list_display = (
        'id',
        'transfer_stock',
        'material',
        'quantity',
        'rate',
        'batch',
    )
    autocomplete_fields = ('transfer_stock', 'material', 'batch')
