from django.urls import path
from .views import InventoryFilterView, HealthCheckView, ReadinessCheckView, PisReportView, TrafficFilterView

urlpatterns = [
    path('inventory_filter/', InventoryFilterView.as_view(), name='inventory-filter'),
    path('health/', HealthCheckView.as_view(), name='health-check'),
    path('ready/', ReadinessCheckView.as_view(), name='readiness-check'),
    path('pis_filter/', PisReportView.as_view(), name='pis-filter'),
    path('traffic_filter/', TrafficFilterView.as_view(), name='traffic-filter'),
]