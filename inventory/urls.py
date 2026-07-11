from django.urls import path
from .views import (
    InventoryFilterView,
    HealthCheckView,
    ReadinessCheckView,
    AppendInventoryExcelView,
    InventoryReportHistoryView,
    InventoryReportDetailView,
)

urlpatterns = [
    path('inventory_filter/', InventoryFilterView.as_view(), name='inventory-filter'),
    path('health/', HealthCheckView.as_view(), name='health-check'),
    path('ready/', ReadinessCheckView.as_view(), name='readiness-check'),

    # PostgreSQL-backed replacement for Excel storage (URL kept for frontend compatibility)
    path(
        'append_inventory_excel/',
        AppendInventoryExcelView.as_view(),
        name='append-inventory-excel',
    ),
    path('inventory/history/', InventoryReportHistoryView.as_view(), name='inventory-report-history'),
    path('inventory/<int:report_id>/', InventoryReportDetailView.as_view(), name='inventory-report-detail'),
]
