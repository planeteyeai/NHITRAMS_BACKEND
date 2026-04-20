from django.urls import path
from .views import InventoryFilterView, HealthCheckView, ReadinessCheckView

urlpatterns = [
    path('inventory_filter/', InventoryFilterView.as_view(), name='inventory-filter'),
    path('health/', HealthCheckView.as_view(), name='health-check'),
    path('ready/', ReadinessCheckView.as_view(), name='readiness-check'),
]