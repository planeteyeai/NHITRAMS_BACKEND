from django.urls import path
from .views import Inventory2FilterView

urlpatterns = [
    path('inventory2_filter/', Inventory2FilterView.as_view(), name='inventory2-filter'),
]