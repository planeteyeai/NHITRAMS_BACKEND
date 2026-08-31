"""Work Order URL routes."""

from django.urls import path

from .views import WorkOrderDetailView, WorkOrderListCreateView

urlpatterns = [
    path('api/work-orders/', WorkOrderListCreateView.as_view(), name='work-order-list-create'),
    path('api/work-orders', WorkOrderListCreateView.as_view()),
    path(
        'api/work-orders/<int:work_order_pk>/',
        WorkOrderDetailView.as_view(),
        name='work-order-detail',
    ),
    path('api/work-orders/<int:work_order_pk>', WorkOrderDetailView.as_view()),
]
