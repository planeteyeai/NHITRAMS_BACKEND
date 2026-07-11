"""JWT token creation and validation utilities.

This module issues and verifies tokens for the AI Identifier only.
It does not integrate with Django's User model or the PHP auth system.
"""

from datetime import datetime, timedelta, timezone

import jwt
from django.conf import settings


ALGORITHM = 'HS256'
TOKEN_LIFETIME_SECONDS = 86400  # 24 hours


def get_jwt_secret():
    return getattr(settings, 'JWT_SECRET_KEY', None) or settings.SECRET_KEY


def create_access_token(username: str) -> str:
    """Create a signed JWT containing only the NHIT RAMS username."""
    now = datetime.now(timezone.utc)
    payload = {
        'username': username,
        'iat': now,
        'exp': now + timedelta(seconds=TOKEN_LIFETIME_SECONDS),
    }
    return jwt.encode(payload, get_jwt_secret(), algorithm=ALGORITHM)


def decode_access_token(token: str) -> dict:
    """Decode and validate a JWT. Raises jwt exceptions on failure."""
    return jwt.decode(token, get_jwt_secret(), algorithms=[ALGORITHM])
