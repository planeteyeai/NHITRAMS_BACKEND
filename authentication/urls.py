from django.urls import path

from .views import LoginView
from .user_views import UserDetailView, UserListCreateView

urlpatterns = [
    path('api/auth/login', LoginView.as_view(), name='ai-auth-login'),
    path('api/auth/login/', LoginView.as_view(), name='ai-auth-login-slash'),
    path('api/users/', UserListCreateView.as_view(), name='user-list-create'),
    path('api/users', UserListCreateView.as_view()),
    path('api/users/<int:user_id>/', UserDetailView.as_view(), name='user-detail'),
    path('api/users/<int:user_id>', UserDetailView.as_view()),
]
