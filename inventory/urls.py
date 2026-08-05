from django.urls import path
from .views import (
    InventoryFilterView,
    HealthCheckView,
    ReadinessCheckView,
    AppendInventoryExcelView,
    InventoryReportHistoryView,
    InventoryReportDetailView,
    InventoryPDFExportView,
    InventoryMasterView,
    DPRReportCreateView,
    DPRReportDetailView,
    DPRReportHistoryView,
    AutoFetchChainageView,
)
from .views_routine_maintenance import (
    InventoryProjectsListView,
    RoutineMaintenanceMasterView,
    RoutineMaintenanceReportCreateView,
    RoutineMaintenanceReportDetailView,
    RoutineMaintenanceHistoryView,
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
    path('inventory/export/pdf/', InventoryPDFExportView.as_view(), name='inventory-export-pdf'),
    path('inventory/<int:report_id>/', InventoryReportDetailView.as_view(), name='inventory-report-detail'),

    # Inventory master dropdowns (JWT-protected)
    path('api/inventory/master', InventoryMasterView.as_view(), name='inventory-master'),
    path('api/inventory/master/', InventoryMasterView.as_view(), name='inventory-master-slash'),

    # Daily Progress Report (JWT-protected)
    path('api/inventory/dpr/report', DPRReportCreateView.as_view(), name='inventory-dpr-report'),
    path('api/inventory/dpr/report/', DPRReportCreateView.as_view(), name='inventory-dpr-report-slash'),
    path(
        'api/inventory/dpr/report/<int:report_id>',
        DPRReportDetailView.as_view(),
        name='inventory-dpr-report-detail',
    ),
    path(
        'api/inventory/dpr/report/<int:report_id>/',
        DPRReportDetailView.as_view(),
        name='inventory-dpr-report-detail-slash',
    ),
    path('api/inventory/dpr/history', DPRReportHistoryView.as_view(), name='inventory-dpr-history'),
    path('api/inventory/dpr/history/', DPRReportHistoryView.as_view(), name='inventory-dpr-history-slash'),

    # Inventory projects (JWT-protected dropdown source)
    path('api/inventory/projects', InventoryProjectsListView.as_view(), name='inventory-projects'),
    path('api/inventory/projects/', InventoryProjectsListView.as_view(), name='inventory-projects-slash'),

    # Routine Maintenance (JWT-protected)
    path(
        'api/inventory/routine-maintenance/master',
        RoutineMaintenanceMasterView.as_view(),
        name='inventory-rm-master',
    ),
    path(
        'api/inventory/routine-maintenance/master/',
        RoutineMaintenanceMasterView.as_view(),
        name='inventory-rm-master-slash',
    ),
    path(
        'api/inventory/routine-maintenance/report',
        RoutineMaintenanceReportCreateView.as_view(),
        name='inventory-rm-report',
    ),
    path(
        'api/inventory/routine-maintenance/report/',
        RoutineMaintenanceReportCreateView.as_view(),
        name='inventory-rm-report-slash',
    ),
    path(
        'api/inventory/routine-maintenance/report/<int:report_id>',
        RoutineMaintenanceReportDetailView.as_view(),
        name='inventory-rm-report-detail',
    ),
    path(
        'api/inventory/routine-maintenance/report/<int:report_id>/',
        RoutineMaintenanceReportDetailView.as_view(),
        name='inventory-rm-report-detail-slash',
    ),
    path(
        'api/inventory/routine-maintenance/history',
        RoutineMaintenanceHistoryView.as_view(),
        name='inventory-rm-history',
    ),
    path(
        'api/inventory/routine-maintenance/history/',
        RoutineMaintenanceHistoryView.as_view(),
        name='inventory-rm-history-slash',
    ),

    # Auto-fetch chainage from live GPS (JWT-protected)
    path(
        'api/inventory/autofetch-chainage',
        AutoFetchChainageView.as_view(),
        name='inventory-autofetch-chainage',
    ),
    path(
        'api/inventory/autofetch-chainage/',
        AutoFetchChainageView.as_view(),
        name='inventory-autofetch-chainage-slash',
    ),
]
