"""Permissions for work order endpoints."""

from authentication.permissions import IsJWTAuthenticated  # noqa: F401

__all__ = ['IsJWTAuthenticated']
