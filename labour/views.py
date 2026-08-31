"""Labour Management API views."""

from __future__ import annotations

import logging

from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema
from rest_framework import status
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.parsers import FormParser, JSONParser
from rest_framework.response import Response
from rest_framework.views import APIView

from .authentication import JWTAuthentication
from .permissions import IsJWTAuthenticated
from .serializers import (
    LabourListQuerySerializer,
    LabourReadSerializer,
    LabourWriteSerializer,
)
from . import services
from nhit.caching import CACHE_NS_LABOUR_MASTERS, cached_get_response

logger = logging.getLogger(__name__)


def _jwt_username(request) -> str:
    return request.user.username


def _validation_error_response(errors, *, conflict: bool = False):
    http_status = (
        status.HTTP_409_CONFLICT if conflict else status.HTTP_400_BAD_REQUEST
    )
    code = 'DUPLICATE_LABOUR' if conflict else 'VALIDATION_ERROR'
    return Response(
        {
            'success': False,
            'error': {
                'code': code,
                'message': (
                    'A duplicate labour record already exists.'
                    if conflict
                    else 'Invalid labour data'
                ),
                'details': errors,
            },
            # Project-compatible alias used by inventory/RM clients.
            'errors': errors,
        },
        status=http_status,
    )


def _is_duplicate_error(exc: ValidationError) -> bool:
    detail = exc.detail
    if isinstance(detail, dict):
        messages = detail.get('non_field_errors') or []
        return any('already exists' in str(m).lower() for m in messages)
    return False


class LabourMasterView(APIView):
    """GET /api/labours/masters/ — dropdown values for Add Labour."""

    authentication_classes = [JWTAuthentication]
    permission_classes = [IsJWTAuthenticated]

    @swagger_auto_schema(
        operation_description='Return labour_type and gender dropdown values.',
        responses={200: openapi.Response('Master data'), 401: 'Unauthorized'},
        security=[{'Bearer': []}],
        tags=['Labour Management'],
    )
    def get(self, request):
        return cached_get_response(
            namespace=CACHE_NS_LABOUR_MASTERS,
            payload={},
            builder=lambda: {'success': True, 'data': services.get_labour_masters()},
        )


class LabourListCreateView(APIView):
    """GET /api/labours/  |  POST /api/labours/"""

    authentication_classes = [JWTAuthentication]
    permission_classes = [IsJWTAuthenticated]
    parser_classes = [JSONParser, FormParser]

    @swagger_auto_schema(
        operation_description=(
            'List labours with pagination, filtering, search, and sorting. '
            'Query: page, page_size, search, sort_by, sort_order, '
            'first_name, last_name, crew_id, labour_type, gender, contact_number.'
        ),
        manual_parameters=[
            openapi.Parameter('page', openapi.IN_QUERY, type=openapi.TYPE_INTEGER),
            openapi.Parameter('page_size', openapi.IN_QUERY, type=openapi.TYPE_INTEGER),
            openapi.Parameter('search', openapi.IN_QUERY, type=openapi.TYPE_STRING),
            openapi.Parameter('sort_by', openapi.IN_QUERY, type=openapi.TYPE_STRING),
            openapi.Parameter('sort_order', openapi.IN_QUERY, type=openapi.TYPE_STRING),
            openapi.Parameter('first_name', openapi.IN_QUERY, type=openapi.TYPE_STRING),
            openapi.Parameter('last_name', openapi.IN_QUERY, type=openapi.TYPE_STRING),
            openapi.Parameter('crew_id', openapi.IN_QUERY, type=openapi.TYPE_STRING),
            openapi.Parameter('labour_type', openapi.IN_QUERY, type=openapi.TYPE_STRING),
            openapi.Parameter('gender', openapi.IN_QUERY, type=openapi.TYPE_STRING),
            openapi.Parameter(
                'contact_number', openapi.IN_QUERY, type=openapi.TYPE_STRING
            ),
        ],
        responses={200: openapi.Response('Paginated list'), 400: 'Bad query', 401: 'Unauthorized'},
        security=[{'Bearer': []}],
        tags=['Labour Management'],
    )
    def get(self, request):
        query_serializer = LabourListQuerySerializer(data=request.query_params)
        if not query_serializer.is_valid():
            return _validation_error_response(query_serializer.errors)

        result = services.list_labours(query=query_serializer.validated_data)
        return Response(
            {
                'success': True,
                'data': LabourReadSerializer(result['rows'], many=True).data,
                'pagination': result['pagination'],
            },
            status=status.HTTP_200_OK,
        )

    @swagger_auto_schema(
        operation_description=(
            'Create a labour record. All form fields are required. '
            'Duplicate crew_id + first_name + last_name + date_of_birth returns 409.'
        ),
        request_body=LabourWriteSerializer,
        responses={
            201: openapi.Response('Created'),
            400: 'Validation error',
            401: 'Unauthorized',
            409: 'Duplicate labour',
        },
        security=[{'Bearer': []}],
        tags=['Labour Management'],
    )
    def post(self, request):
        serializer = LabourWriteSerializer(data=request.data)
        if not serializer.is_valid():
            return _validation_error_response(serializer.errors)

        try:
            labour = services.create_labour(
                validated=serializer.validated_data,
                username=_jwt_username(request),
            )
        except ValidationError as exc:
            return _validation_error_response(
                exc.detail,
                conflict=_is_duplicate_error(exc),
            )
        except Exception:
            logger.exception(
                'Labour create failed for user=%s', _jwt_username(request)
            )
            return Response(
                {
                    'success': False,
                    'error': {
                        'code': 'SERVER_ERROR',
                        'message': 'Failed to create labour.',
                        'details': {},
                    },
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        return Response(
            {
                'success': True,
                'message': 'Labour created successfully.',
                'data': LabourReadSerializer(labour).data,
            },
            status=status.HTTP_201_CREATED,
        )


class LabourDetailView(APIView):
    """GET / PUT / PATCH / DELETE /api/labours/{id}/"""

    authentication_classes = [JWTAuthentication]
    permission_classes = [IsJWTAuthenticated]
    parser_classes = [JSONParser, FormParser]

    def _load(self, labour_id: int):
        try:
            return services.get_labour_or_404(labour_id)
        except NotFound:
            return None

    @swagger_auto_schema(
        operation_description='Retrieve a single labour by id.',
        responses={200: LabourReadSerializer, 401: 'Unauthorized', 404: 'Not Found'},
        security=[{'Bearer': []}],
        tags=['Labour Management'],
    )
    def get(self, request, labour_id):
        labour = self._load(labour_id)
        if labour is None:
            return Response(
                {
                    'success': False,
                    'error': {
                        'code': 'NOT_FOUND',
                        'message': 'Labour not found.',
                        'details': {},
                    },
                    'message': 'Labour not found.',
                },
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(
            {'success': True, 'data': LabourReadSerializer(labour).data},
            status=status.HTTP_200_OK,
        )

    @swagger_auto_schema(
        operation_description='Full replacement update of a labour record.',
        request_body=LabourWriteSerializer,
        responses={
            200: openapi.Response('Updated'),
            400: 'Validation error',
            401: 'Unauthorized',
            404: 'Not Found',
            409: 'Duplicate labour',
        },
        security=[{'Bearer': []}],
        tags=['Labour Management'],
    )
    def put(self, request, labour_id):
        return self._update(request, labour_id, partial=False)

    @swagger_auto_schema(
        operation_description='Partial update — send only fields to change.',
        request_body=LabourWriteSerializer,
        responses={
            200: openapi.Response('Updated'),
            400: 'Validation error',
            401: 'Unauthorized',
            404: 'Not Found',
            409: 'Duplicate labour',
        },
        security=[{'Bearer': []}],
        tags=['Labour Management'],
    )
    def patch(self, request, labour_id):
        return self._update(request, labour_id, partial=True)

    def _update(self, request, labour_id, *, partial: bool):
        labour = self._load(labour_id)
        if labour is None:
            return Response(
                {
                    'success': False,
                    'error': {
                        'code': 'NOT_FOUND',
                        'message': 'Labour not found.',
                        'details': {},
                    },
                    'message': 'Labour not found.',
                },
                status=status.HTTP_404_NOT_FOUND,
            )

        serializer = LabourWriteSerializer(
            data=request.data,
            partial=partial,
        )
        if not serializer.is_valid():
            return _validation_error_response(serializer.errors)

        try:
            labour = services.update_labour(
                labour=labour,
                validated=serializer.validated_data,
                username=_jwt_username(request),
                partial=partial,
            )
        except ValidationError as exc:
            return _validation_error_response(
                exc.detail,
                conflict=_is_duplicate_error(exc),
            )
        except Exception:
            logger.exception(
                'Labour update failed for id=%s user=%s',
                labour_id,
                _jwt_username(request),
            )
            return Response(
                {
                    'success': False,
                    'error': {
                        'code': 'SERVER_ERROR',
                        'message': 'Failed to update labour.',
                        'details': {},
                    },
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        return Response(
            {
                'success': True,
                'message': 'Labour updated successfully.',
                'data': LabourReadSerializer(labour).data,
            },
            status=status.HTTP_200_OK,
        )

    @swagger_auto_schema(
        operation_description='Hard-delete a labour record (matches project report DELETE style).',
        responses={
            204: 'Deleted',
            401: 'Unauthorized',
            404: 'Not Found',
        },
        security=[{'Bearer': []}],
        tags=['Labour Management'],
    )
    def delete(self, request, labour_id):
        labour = self._load(labour_id)
        if labour is None:
            return Response(
                {
                    'success': False,
                    'error': {
                        'code': 'NOT_FOUND',
                        'message': 'Labour not found.',
                        'details': {},
                    },
                    'message': 'Labour not found.',
                },
                status=status.HTTP_404_NOT_FOUND,
            )
        services.delete_labour(labour=labour)
        return Response(status=status.HTTP_204_NO_CONTENT)
