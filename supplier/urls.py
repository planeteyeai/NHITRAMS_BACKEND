from django.urls import path

from .views import SupplierDetailView, SupplierListCreateView, SupplierMasterView

urlpatterns = [
    path(
        'api/suppliers/masters/',
        SupplierMasterView.as_view(),
        name='supplier-masters',
    ),
    path(
        'api/suppliers/masters',
        SupplierMasterView.as_view(),
        name='supplier-masters-noslash',
    ),
    path(
        'api/suppliers/',
        SupplierListCreateView.as_view(),
        name='supplier-list-create',
    ),
    path(
        'api/suppliers',
        SupplierListCreateView.as_view(),
        name='supplier-list-create-noslash',
    ),
    path(
        'api/suppliers/<int:supplier_id>/',
        SupplierDetailView.as_view(),
        name='supplier-detail',
    ),
    path(
        'api/suppliers/<int:supplier_id>',
        SupplierDetailView.as_view(),
        name='supplier-detail-noslash',
    ),
]
