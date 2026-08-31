from django.urls import path
from .views import AisFilterView

urlpatterns = [
    path('ais_filter/', AisFilterView.as_view(), name='ais-filter'),
]
