"""JWT authentication for inventory report endpoints.

Re-exports the shared AI Identifier JWT authenticator so inventory views
can import from a local module as expected by the project structure.
"""

from authentication.authentication import JWTAuthentication  # noqa: F401

__all__ = ['JWTAuthentication']
