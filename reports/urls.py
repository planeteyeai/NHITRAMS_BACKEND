from django.urls import path

from .views import ReportCreateView, ReportDetailView, ReportHistoryView

urlpatterns = [
    path('api/reports', ReportCreateView.as_view(), name='ai-report-create'),
    path('api/reports/', ReportCreateView.as_view(), name='ai-report-create-slash'),
    path('api/reports/history', ReportHistoryView.as_view(), name='ai-report-history'),
    path('api/reports/history/', ReportHistoryView.as_view(), name='ai-report-history-slash'),
    path('api/reports/<int:report_id>', ReportDetailView.as_view(), name='ai-report-detail'),
    path('api/reports/<int:report_id>/', ReportDetailView.as_view(), name='ai-report-detail-slash'),
]
