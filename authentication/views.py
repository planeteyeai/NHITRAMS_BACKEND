from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView
from drf_yasg.utils import swagger_auto_schema
from drf_yasg import openapi

from .jwt_utils import TOKEN_LIFETIME_SECONDS, create_access_token
from .serializers import LoginSerializer


class LoginView(APIView):
    """Issue a JWT for the AI Identifier after a successful PHP login.

    This endpoint does NOT validate passwords. It only receives the username
    that the legacy NHIT RAMS application has already authenticated.
    """

    authentication_classes = []
    permission_classes = [AllowAny]

    @swagger_auto_schema(
        operation_description=(
            'Generate a JWT for the AI Identifier. '
            'Call this after a successful PHP login. '
            'Send the real NHIT RAMS username (e.g. ram.patil), not the numeric user id. '
            'Optional aliases: user_name, login_username, name. user_id is ignored for JWT.'
        ),
        request_body=LoginSerializer,
        responses={
            200: openapi.Response(
                description='JWT issued successfully',
                examples={
                    'application/json': {
                        'success': True,
                        'token': '<jwt_token>',
                        'expires_in': 86400,
                    }
                },
            ),
            400: 'Bad Request — username missing, blank, or numeric user id only',
        },
        tags=['AI Authentication'],
    )
    def post(self, request):
        serializer = LoginSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {'success': False, 'errors': serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )

        username = serializer.validated_data['username']
        token = create_access_token(username)

        return Response(
            {
                'success': True,
                'token': token,
                'expires_in': TOKEN_LIFETIME_SECONDS,
                'username': username,
            },
            status=status.HTTP_200_OK,
        )
