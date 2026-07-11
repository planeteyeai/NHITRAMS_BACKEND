"""JWT authentication for distress report endpoints.

Re-exports the shared AI Identifier JWT authenticator so distress views
can import from a local module as expected by the project structure.
"""

from authentication.authentication import JWTAuthentication  # noqa: F401

__all__ = ['JWTAuthentication']
