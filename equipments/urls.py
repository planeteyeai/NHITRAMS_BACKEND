from django.urls import path

from .views import EquipmentDetailView, EquipmentListCreateView

urlpatterns = [
    path(
        'api/equipments/',
        EquipmentListCreateView.as_view(),
        name='equipment-list-create',
    ),
    path(
        'api/equipments',
        EquipmentListCreateView.as_view(),
        name='equipment-list-create-noslash',
    ),
    path(
        'api/equipments/<int:equipment_id>/',
        EquipmentDetailView.as_view(),
        name='equipment-detail',
    ),
    path(
        'api/equipments/<int:equipment_id>',
        EquipmentDetailView.as_view(),
        name='equipment-detail-noslash',
    ),
]
