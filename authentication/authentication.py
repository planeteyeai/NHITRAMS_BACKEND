"""DRF authentication class that validates Bearer JWTs."""

import jwt
from rest_framework import authentication, exceptions

from .jwt_utils import decode_access_token
from .user import JWTUser


class JWTAuthentication(authentication.BaseAuthentication):
    """Authenticate requests using an Authorization: Bearer <token> header.

    On success, request.user is a JWTUser with the username from the token.
    Username is never taken from the request body or query string.
    """

    keyword = 'Bearer'

    def authenticate(self, request):
        auth_header = authentication.get_authorization_header(request).decode('utf-8')

        if not auth_header:
            return None

        parts = auth_header.split()

        if len(parts) == 0:
            return None

        if parts[0] != self.keyword:
            return None

        if len(parts) != 2:
            raise exceptions.AuthenticationFailed('Invalid Authorization header format. Expected: Bearer <token>')

        token = parts[1]
        return self.authenticate_credentials(token)

    def authenticate_credentials(self, token):
        try:
            payload = decode_access_token(token)
        except jwt.ExpiredSignatureError:
            raise exceptions.AuthenticationFailed('Token has expired.')
        except jwt.InvalidTokenError:
            raise exceptions.AuthenticationFailed('Invalid token.')

        username = payload.get('username')
        if not username or not isinstance(username, str):
            raise exceptions.AuthenticationFailed('Token payload missing username.')

        return (JWTUser(username=username), token)

    def authenticate_header(self, request):
        return self.keyword
