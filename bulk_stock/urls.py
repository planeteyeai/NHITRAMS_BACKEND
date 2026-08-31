"""Bulk Stock URL routes."""

from django.urls import path

from .views import (
    BulkInwardDetailView,
    BulkInwardListCreateView,
    BulkOutwardDetailView,
    BulkOutwardListCreateView,
)

urlpatterns = [
    path('api/bulk-inward/', BulkInwardListCreateView.as_view(), name='bulk-inward-list-create'),
    path('api/bulk-inward', BulkInwardListCreateView.as_view()),
    path(
        'api/bulk-inward/<int:transaction_id>/',
        BulkInwardDetailView.as_view(),
        name='bulk-inward-detail',
    ),
    path('api/bulk-inward/<int:transaction_id>', BulkInwardDetailView.as_view()),
    path('api/bulk-outward/', BulkOutwardListCreateView.as_view(), name='bulk-outward-list-create'),
    path('api/bulk-outward', BulkOutwardListCreateView.as_view()),
    path(
        'api/bulk-outward/<int:transaction_id>/',
        BulkOutwardDetailView.as_view(),
        name='bulk-outward-detail',
    ),
    path('api/bulk-outward/<int:transaction_id>', BulkOutwardDetailView.as_view()),
]
