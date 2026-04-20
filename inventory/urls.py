from django.urls import path
from .views import InventoryFilterView

urlpatterns = [
    path('inventory_filter/', InventoryFilterView.as_view(), name='inventory-filter'),
]