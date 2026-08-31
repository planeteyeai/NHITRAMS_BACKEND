from django.urls import path

from .views import MaterialDetailView, MaterialListCreateView

urlpatterns = [
    path(
        'api/materials/',
        MaterialListCreateView.as_view(),
        name='material-list-create',
    ),
    path(
        'api/materials',
        MaterialListCreateView.as_view(),
        name='material-list-create-noslash',
    ),
    path(
        'api/materials/<int:material_id>/',
        MaterialDetailView.as_view(),
        name='material-detail',
    ),
    path(
        'api/materials/<int:material_id>',
        MaterialDetailView.as_view(),
        name='material-detail-noslash',
    ),
]
