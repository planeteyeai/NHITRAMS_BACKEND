from django.urls import path
from .views import RoadsideFeaturesView

urlpatterns = [
    path('rwfis_filter/', RoadsideFeaturesView.as_view(), name='roadside-features'),
]