from django.urls import path

from .views import PurchaseInvoiceDetailView, PurchaseInvoiceListCreateView

urlpatterns = [
    path(
        'api/purchase-invoices/',
        PurchaseInvoiceListCreateView.as_view(),
        name='purchase-invoice-list-create',
    ),
    path(
        'api/purchase-invoices',
        PurchaseInvoiceListCreateView.as_view(),
        name='purchase-invoice-list-create-noslash',
    ),
    path(
        'api/purchase-invoices/<int:purchase_invoice_id>/',
        PurchaseInvoiceDetailView.as_view(),
        name='purchase-invoice-detail',
    ),
    path(
        'api/purchase-invoices/<int:purchase_invoice_id>',
        PurchaseInvoiceDetailView.as_view(),
        name='purchase-invoice-detail-noslash',
    ),
]
