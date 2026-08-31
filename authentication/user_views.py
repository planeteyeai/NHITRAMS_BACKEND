"""User Management API views."""

from __future__ import annotations

import logging

from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema
from rest_framework import status
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.parsers import FormParser, JSONParser
from rest_framework.response import Response
from rest_framework.views import APIView

from .authentication import JWTAuthentication
from .permissions import IsUserManager
from .user_serializers import (
    UserListQuerySerializer,
    UserReadSerializer,
    UserWriteSerializer,
)
from . import user_services

logger = logging.getLogger(__name__)


def _jwt_username(request) -> str:
    return request.user.username


def _validation_error_response(
    errors,
    *,
    code: str = 'VALIDATION_ERROR',
    message: str | None = None,
    http_status=status.HTTP_400_BAD_REQUEST,
):
    return Response(
        {
            'success': False,
            'error': {
                'code': code,
                'message': message or 'Invalid user data.',
                'details': errors,
            },
            'errors': errors,
        },
        status=http_status,
    )


def _map_service_error(exc: ValidationError):
    raw_code = getattr(exc, 'code', None)
    detail_text = str(exc.detail)

    if raw_code == 'duplicate_username' or 'Username already exists' in detail_text:
        return _validation_error_response(
            exc.detail,
            code='DUPLICATE_USERNAME',
            message='Username already exists.',
            http_status=status.HTTP_409_CONFLICT,
        )
    if raw_code == 'duplicate_email' or 'Email already exists' in detail_text:
        return _validation_error_response(
            exc.detail,
            code='DUPLICATE_EMAIL',
            message='Email already exists.',
            http_status=status.HTTP_409_CONFLICT,
        )
    if raw_code == 'invalid_role_access' or 'Access type is not valid' in detail_text:
        return _validation_error_response(
            exc.detail,
            code='INVALID_ROLE_ACCESS',
            message='Access type is not valid for the selected user role.',
        )
    if raw_code == 'invalid_role' or 'Invalid user role' in detail_text:
        return _validation_error_response(
            exc.detail,
            code='INVALID_ROLE',
            message='Invalid user role.',
        )
    return _validation_error_response(exc.detail)


def _actor_profile(request):
    return getattr(request, 'actor_profile', None)


class UserListCreateView(APIView):
    """GET /api/users/  |  POST /api/users/"""

    authentication_classes = [JWTAuthentication]
    permission_classes = [IsUserManager]
    parser_classes = [JSONParser, FormParser]

    @swagger_auto_schema(
        operation_description='List users with pagination, search, filtering, and sorting.',
        manual_parameters=[
            openapi.Parameter('page', openapi.IN_QUERY, type=openapi.TYPE_INTEGER),
            openapi.Parameter('page_size', openapi.IN_QUERY, type=openapi.TYPE_INTEGER),
            openapi.Parameter('search', openapi.IN_QUERY, type=openapi.TYPE_STRING),
            openapi.Parameter('sort_by', openapi.IN_QUERY, type=openapi.TYPE_STRING),
            openapi.Parameter('sort_order', openapi.IN_QUERY, type=openapi.TYPE_STRING),
            openapi.Parameter('role', openapi.IN_QUERY, type=openapi.TYPE_STRING),
            openapi.Parameter('access_type', openapi.IN_QUERY, type=openapi.TYPE_STRING),
            openapi.Parameter('is_active', openapi.IN_QUERY, type=openapi.TYPE_BOOLEAN),
        ],
        responses={200: 'Paginated list', 401: 'Unauthorized', 403: 'Forbidden'},
        security=[{'Bearer': []}],
        tags=['User Management'],
    )
    def get(self, request):
        query_serializer = UserListQuerySerializer(data=request.query_params)
        if not query_serializer.is_valid():
            return _validation_error_response(query_serializer.errors)

        try:
            result = user_services.list_users(
                query=query_serializer.validated_data,
                actor_username=_jwt_username(request),
                actor_profile=_actor_profile(request),
            )
        except PermissionDenied as exc:
            return _validation_error_response(
                {'detail': [str(exc.detail if hasattr(exc, 'detail') else exc)]},
                code='FORBIDDEN',
                message='You do not have permission to manage users.',
                http_status=status.HTTP_403_FORBIDDEN,
            )

        return Response(
            {
                'success': True,
                'data': [
                    user_services.serialize_user(row) for row in result['rows']
                ],
                'pagination': result['pagination'],
            },
            status=status.HTTP_200_OK,
        )

    @swagger_auto_schema(
        operation_description='Create a user with hashed password and role/access profile.',
        request_body=UserWriteSerializer,
        responses={
            201: 'Created',
            400: 'Validation error',
            401: 'Unauthorized',
            403: 'Forbidden',
            409: 'Duplicate username or email',
        },
        security=[{'Bearer': []}],
        tags=['User Management'],
    )
    def post(self, request):
        serializer = UserWriteSerializer(
            data=request.data,
            context={'require_password': True},
        )
        if not serializer.is_valid():
            return _validation_error_response(serializer.errors)

        try:
            user = user_services.create_user(
                validated=serializer.validated_data,
                actor_username=_jwt_username(request),
                actor_profile=_actor_profile(request),
            )
        except PermissionDenied as exc:
            return _validation_error_response(
                {'detail': [str(exc.detail if hasattr(exc, 'detail') else exc)]},
                code='FORBIDDEN',
                message='You do not have permission to perform this action.',
                http_status=status.HTTP_403_FORBIDDEN,
            )
        except ValidationError as exc:
            return _map_service_error(exc)
        except Exception:
            logger.exception('User create failed for actor=%s', _jwt_username(request))
            return Response(
                {
                    'success': False,
                    'error': {
                        'code': 'SERVER_ERROR',
                        'message': 'Failed to create user.',
                        'details': {},
                    },
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        return Response(
            {
                'success': True,
                'message': 'User created successfully.',
                'data': user_services.serialize_user(user),
            },
            status=status.HTTP_201_CREATED,
        )


class UserDetailView(APIView):
    """GET / PUT / PATCH / DELETE /api/users/{id}/"""

    authentication_classes = [JWTAuthentication]
    permission_classes = [IsUserManager]
    parser_classes = [JSONParser, FormParser]

    def _load(self, user_id: int):
        return user_services.get_user_or_404(user_id)

    @swagger_auto_schema(
        operation_description='Retrieve a user by primary key.',
        responses={200: UserReadSerializer, 401: 'Unauthorized', 403: 'Forbidden', 404: 'Not Found'},
        security=[{'Bearer': []}],
        tags=['User Management'],
    )
    def get(self, request, user_id):
        try:
            user = self._load(user_id)
        except NotFound:
            user = None
        if user is None:
            return Response(
                {
                    'success': False,
                    'error': {
                        'code': 'USER_NOT_FOUND',
                        'message': 'User not found.',
                        'details': {},
                    },
                },
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(
            {'success': True, 'data': user_services.serialize_user(user)},
            status=status.HTTP_200_OK,
        )

    @swagger_auto_schema(
        operation_description='Full replacement update for a user.',
        request_body=UserWriteSerializer,
        responses={
            200: 'Updated',
            400: 'Validation error',
            401: 'Unauthorized',
            403: 'Forbidden',
            404: 'Not Found',
            409: 'Duplicate',
        },
        security=[{'Bearer': []}],
        tags=['User Management'],
    )
    def put(self, request, user_id):
        return self._update(request, user_id, partial=False)

    @swagger_auto_schema(
        operation_description='Partial update for a user.',
        request_body=UserWriteSerializer,
        responses={
            200: 'Updated',
            400: 'Validation error',
            401: 'Unauthorized',
            403: 'Forbidden',
            404: 'Not Found',
            409: 'Duplicate',
        },
        security=[{'Bearer': []}],
        tags=['User Management'],
    )
    def patch(self, request, user_id):
        return self._update(request, user_id, partial=True)

    def _update(self, request, user_id, *, partial: bool):
        try:
            user = self._load(user_id)
        except NotFound:
            user = None
        if user is None:
            return Response(
                {
                    'success': False,
                    'error': {
                        'code': 'USER_NOT_FOUND',
                        'message': 'User not found.',
                        'details': {},
                    },
                },
                status=status.HTTP_404_NOT_FOUND,
            )

        serializer = UserWriteSerializer(
            data=request.data,
            partial=partial,
            context={'require_password': not partial},
        )
        if not serializer.is_valid():
            return _validation_error_response(serializer.errors)

        try:
            user = user_services.update_user(
                user=user,
                validated=serializer.validated_data,
                actor_username=_jwt_username(request),
                partial=partial,
                actor_profile=_actor_profile(request),
            )
        except PermissionDenied as exc:
            return _validation_error_response(
                {'detail': [str(exc.detail if hasattr(exc, 'detail') else exc)]},
                code='FORBIDDEN',
                message='You do not have permission to perform this action.',
                http_status=status.HTTP_403_FORBIDDEN,
            )
        except ValidationError as exc:
            return _map_service_error(exc)
        except Exception:
            logger.exception('User update failed id=%s actor=%s', user_id, _jwt_username(request))
            return Response(
                {
                    'success': False,
                    'error': {
                        'code': 'SERVER_ERROR',
                        'message': 'Failed to update user.',
                        'details': {},
                    },
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        return Response(
            {
                'success': True,
                'message': 'User updated successfully.',
                'data': user_services.serialize_user(user),
            },
            status=status.HTTP_200_OK,
        )

    @swagger_auto_schema(
        operation_description='Deactivate a user (soft delete via is_active=false).',
        responses={204: 'Deleted', 401: 'Unauthorized', 403: 'Forbidden', 404: 'Not Found'},
        security=[{'Bearer': []}],
        tags=['User Management'],
    )
    def delete(self, request, user_id):
        try:
            user = self._load(user_id)
        except NotFound:
            user = None
        if user is None:
            return Response(
                {
                    'success': False,
                    'error': {
                        'code': 'USER_NOT_FOUND',
                        'message': 'User not found.',
                        'details': {},
                    },
                },
                status=status.HTTP_404_NOT_FOUND,
            )

        try:
            user_services.deactivate_user(
                user=user,
                actor_username=_jwt_username(request),
                actor_profile=_actor_profile(request),
            )
        except PermissionDenied as exc:
            return _validation_error_response(
                {'detail': [str(exc.detail if hasattr(exc, 'detail') else exc)]},
                code='FORBIDDEN',
                message='You do not have permission to perform this action.',
                http_status=status.HTTP_403_FORBIDDEN,
            )

        return Response(status=status.HTTP_204_NO_CONTENT)
