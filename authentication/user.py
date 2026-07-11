"""Lightweight authenticated principal derived from a JWT.

No database user record is created or looked up.
"""


class JWTUser:
    """Represents an authenticated AI Identifier user identified by username."""

    is_authenticated = True
    is_anonymous = False

    def __init__(self, username: str):
        self.username = username
        self.pk = username

    def __str__(self):
        return self.username

    def __eq__(self, other):
        if not isinstance(other, JWTUser):
            return NotImplemented
        return self.username == other.username

    def __hash__(self):
        return hash(self.username)
