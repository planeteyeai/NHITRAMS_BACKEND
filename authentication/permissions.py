"""Permissions for JWT-authenticated AI Identifier endpoints."""

from rest_framework.permissions import BasePermission


class IsJWTAuthenticated(BasePermission):
    """Allow access only to requests authenticated via JWTAuthentication."""

    message = 'Authentication credentials were not provided or are invalid.'

    def has_permission(self, request, view):
        return bool(
            request.user
            and getattr(request.user, 'is_authenticated', False)
            and getattr(request.user, 'username', None)
        )
