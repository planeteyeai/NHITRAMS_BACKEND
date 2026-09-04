from django.urls import path
from .views import (
    DistressReportFilterView,
    DistressReportSummaryView,
    DistressReportFilterNewView,
    DistressFullResponseView,
    DistressPredictFilterView,
    DistressCarriageTypeMasterView,
    project_dates,
    ChainageSummaryView,
    AppendDistressReportedExcelView,
    DistressReportHistoryView,
    DistressReportDetailView,
    DistressPDFExportView,
    DistressPDFAsyncExportView,
    DistressPDFExportJobStatusView,
)
from .views_repair import DistressRepairView

urlpatterns = [
    path('api/distress/repair', DistressRepairView.as_view(), name='distress-repair'),
    path(
        'api/distress/carriage-types',
        DistressCarriageTypeMasterView.as_view(),
        name='distress-carriage-types',
    ),
    path(
        'api/distress/carriage-types/',
        DistressCarriageTypeMasterView.as_view(),
        name='distress-carriage-types-slash',
    ),
    path('api/distress/report/filter/', DistressReportFilterView.as_view(), name='distress-report-filter'),
    path('api/distress/report/summary/', DistressReportSummaryView.as_view(), name='distress-report-summary'),
    path('distress_report_filter/', DistressFullResponseView.as_view(), name='distress-full-response'),
    path('distress_predict_filter/', DistressPredictFilterView.as_view(), name='distress-predict-filter'),
    path('projects-dates/<str:dataset>/', project_dates, name='project-dates'),
    path('chainage-summary', ChainageSummaryView.as_view(), name='chainage-summary'),

    # PostgreSQL-backed replacement for Excel storage (URL kept for frontend compatibility)
    path(
        'append_distressReported_excel/',
        AppendDistressReportedExcelView.as_view(),
        name='append-distress-reported-excel',
    ),
    path('distress/history/', DistressReportHistoryView.as_view(), name='distress-report-history'),
    path('distress/export/pdf/', DistressPDFExportView.as_view(), name='distress-export-pdf'),
    path(
        'distress/export/pdf/async/',
        DistressPDFAsyncExportView.as_view(),
        name='distress-export-pdf-async',
    ),
    path(
        'distress/export/pdf/jobs/<uuid:job_id>/status/',
        DistressPDFExportJobStatusView.as_view(),
        name='distress-export-pdf-job-status',
    ),
    path('distress/<int:report_id>/', DistressReportDetailView.as_view(), name='distress-report-detail'),
]
