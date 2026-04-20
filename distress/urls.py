from django.urls import path
from .views import DistressReportFilterView, DistressReportSummaryView, DistressReportFilterNewView, DistressFullResponseView, DistressPredictFilterView, project_dates, ChainageSummaryView

urlpatterns = [
    path('api/distress/report/filter/', DistressReportFilterView.as_view(), name='distress-report-filter'),
    path('api/distress/report/summary/', DistressReportSummaryView.as_view(), name='distress-report-summary'),
    path('distress_report_filter/', DistressFullResponseView.as_view(), name='distress-full-response'),
    path('distress_predict_filter/', DistressPredictFilterView.as_view(), name='distress-predict-filter'),
    path('projects-dates/<str:dataset>/', project_dates, name='project-dates'),
    path('chainage-summary', ChainageSummaryView.as_view(), name='chainage-summary'),
]