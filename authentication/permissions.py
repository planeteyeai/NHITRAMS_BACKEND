"""Permissions for JWT-authenticated AI Identifier endpoints."""

from rest_framework.permissions import BasePermission

from .constants import USER_MANAGER_ROLES


class IsJWTAuthenticated(BasePermission):
    """Allow access only to requests authenticated via JWTAuthentication."""

    message = 'Authentication credentials were not provided or are invalid.'

    def has_permission(self, request, view):
        return bool(
            request.user
            and getattr(request.user, 'is_authenticated', False)
            and getattr(request.user, 'username', None)
        )


class IsUserManager(BasePermission):
    """Allow user-management access for Admin/SuperAdmin profiles."""

    message = 'You do not have permission to manage users.'

    def has_permission(self, request, view):
        if not IsJWTAuthenticated().has_permission(request, view):
            return False

        from .user_services import get_actor_profile

        profile = get_actor_profile(request.user.username)
        if profile is None or not profile.user.is_active:
            return False
        if profile.role not in USER_MANAGER_ROLES:
            return False

        request.actor_profile = profile
        return True
