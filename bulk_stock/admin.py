"""Bulk Stock admin."""

from django.contrib import admin

from .models import (
    BulkStockTransaction,
    BulkStockTransactionItem,
    MaterialStock,
    MaterialStockMovement,
)


class BulkStockTransactionItemInline(admin.TabularInline):
    model = BulkStockTransactionItem
    extra = 0
    readonly_fields = ('created_at', 'updated_at')


@admin.register(BulkStockTransaction)
class BulkStockTransactionAdmin(admin.ModelAdmin):
    list_display = ('id', 'transaction_type', 'created_by', 'created_at')
    list_filter = ('transaction_type',)
    search_fields = ('created_by', 'items__material__material_name')
    inlines = [BulkStockTransactionItemInline]


@admin.register(MaterialStock)
class MaterialStockAdmin(admin.ModelAdmin):
    list_display = ('material', 'quantity', 'updated_at')
    search_fields = ('material__material_name',)


@admin.register(MaterialStockMovement)
class MaterialStockMovementAdmin(admin.ModelAdmin):
    list_display = (
        'id',
        'material',
        'transaction_type',
        'quantity_delta',
        'balance_after',
        'created_by',
        'created_at',
    )
    list_filter = ('transaction_type',)
    search_fields = ('material__material_name', 'created_by')
