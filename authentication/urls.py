from django.urls import path

from .views import LoginView

urlpatterns = [
    path('api/auth/login', LoginView.as_view(), name='ai-auth-login'),
    path('api/auth/login/', LoginView.as_view(), name='ai-auth-login-slash'),
]
