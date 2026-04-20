from django.urls import path
from .views import PisReportView

urlpatterns = [
    path('pis_filter/', PisReportView.as_view(), name='pis-filter'),
]