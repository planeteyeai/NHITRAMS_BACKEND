from django.urls import path

from .views import LabourDetailView, LabourListCreateView, LabourMasterView

urlpatterns = [
    path('api/labours/masters/', LabourMasterView.as_view(), name='labour-masters'),
    path('api/labours/masters', LabourMasterView.as_view(), name='labour-masters-noslash'),
    path('api/labours/', LabourListCreateView.as_view(), name='labour-list-create'),
    path('api/labours', LabourListCreateView.as_view(), name='labour-list-create-noslash'),
    path(
        'api/labours/<int:labour_id>/',
        LabourDetailView.as_view(),
        name='labour-detail',
    ),
    path(
        'api/labours/<int:labour_id>',
        LabourDetailView.as_view(),
        name='labour-detail-noslash',
    ),
]
