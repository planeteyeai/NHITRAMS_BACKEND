from django.urls import path
from .views import PavementDataView

urlpatterns = [
    path('pms_filter/', PavementDataView.as_view(), name='pms-filter'),
]