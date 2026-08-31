from django.urls import path

from .views import TransferStockDetailView, TransferStockListCreateView

urlpatterns = [
    path(
        'api/transfer-stocks/',
        TransferStockListCreateView.as_view(),
        name='transfer-stock-list-create',
    ),
    path(
        'api/transfer-stocks',
        TransferStockListCreateView.as_view(),
        name='transfer-stock-list-create-noslash',
    ),
    path(
        'api/transfer-stocks/<int:transfer_stock_id>/',
        TransferStockDetailView.as_view(),
        name='transfer-stock-detail',
    ),
    path(
        'api/transfer-stocks/<int:transfer_stock_id>',
        TransferStockDetailView.as_view(),
        name='transfer-stock-detail-noslash',
    ),
]
