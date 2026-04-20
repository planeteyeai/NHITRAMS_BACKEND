from django.urls import path
from .views import TrafficFilterView

urlpatterns = [
    path('traffic_filter/', TrafficFilterView.as_view(), name='traffic-filter'),
]