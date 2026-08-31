"""Equipment Management API views."""

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
    EquipmentListQuerySerializer,
    EquipmentReadSerializer,
    EquipmentWriteSerializer,
)
from . import services

logger = logging.getLogger(__name__)


def _jwt_username(request) -> str:
    return request.user.username


def _validation_error_response(errors, *, conflict: bool = False):
    http_status = (
        status.HTTP_409_CONFLICT if conflict else status.HTTP_400_BAD_REQUEST
    )
    code = 'DUPLICATE_EQUIPMENT' if conflict else 'VALIDATION_ERROR'
    return Response(
        {
            'success': False,
            'error': {
                'code': code,
                'message': (
                    'Equipment code already exists.'
                    if conflict
                    else 'Invalid equipment data'
                ),
                'details': errors,
            },
            'errors': errors,
        },
        status=http_status,
    )


def _is_duplicate_error(exc: ValidationError) -> bool:
    if getattr(exc, 'code', None) == 'duplicate':
        return True
    detail = exc.detail
    if isinstance(detail, dict):
        return 'equipment_code' in detail
    return False


class EquipmentListCreateView(APIView):
    """GET /api/equipments/  |  POST /api/equipments/"""

    authentication_classes = [JWTAuthentication]
    permission_classes = [IsJWTAuthenticated]
    parser_classes = [JSONParser, FormParser]

    @swagger_auto_schema(
        operation_description=(
            'List equipment with pagination, search, filtering, and sorting. '
            'Query: page, page_size, search, sort_by, sort_order, status, '
            'year_of_make, equipment_code.'
        ),
        manual_parameters=[
            openapi.Parameter('page', openapi.IN_QUERY, type=openapi.TYPE_INTEGER),
            openapi.Parameter('page_size', openapi.IN_QUERY, type=openapi.TYPE_INTEGER),
            openapi.Parameter('search', openapi.IN_QUERY, type=openapi.TYPE_STRING),
            openapi.Parameter('sort_by', openapi.IN_QUERY, type=openapi.TYPE_STRING),
            openapi.Parameter('sort_order', openapi.IN_QUERY, type=openapi.TYPE_STRING),
            openapi.Parameter('status', openapi.IN_QUERY, type=openapi.TYPE_STRING),
            openapi.Parameter('year_of_make', openapi.IN_QUERY, type=openapi.TYPE_INTEGER),
            openapi.Parameter('equipment_code', openapi.IN_QUERY, type=openapi.TYPE_STRING),
        ],
        responses={
            200: openapi.Response('Paginated list'),
            400: 'Bad query',
            401: 'Unauthorized',
        },
        security=[{'Bearer': []}],
        tags=['Equipment Management'],
    )
    def get(self, request):
        query_serializer = EquipmentListQuerySerializer(data=request.query_params)
        if not query_serializer.is_valid():
            return _validation_error_response(query_serializer.errors)

        result = services.list_equipments(query=query_serializer.validated_data)
        return Response(
            {
                'success': True,
                'data': EquipmentReadSerializer(result['rows'], many=True).data,
                'pagination': result['pagination'],
            },
            status=status.HTTP_200_OK,
        )

    @swagger_auto_schema(
        operation_description=(
            'Create an equipment record. All form fields are required. '
            'equipment_code must be unique. Status is accepted as sent by the frontend.'
        ),
        request_body=EquipmentWriteSerializer,
        responses={
            201: openapi.Response('Created'),
            400: 'Validation error',
            401: 'Unauthorized',
            409: 'Duplicate equipment code',
        },
        security=[{'Bearer': []}],
        tags=['Equipment Management'],
    )
    def post(self, request):
        serializer = EquipmentWriteSerializer(data=request.data)
        if not serializer.is_valid():
            return _validation_error_response(serializer.errors)

        try:
            equipment = services.create_equipment(
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
                'Equipment create failed for user=%s', _jwt_username(request)
            )
            return Response(
                {
                    'success': False,
                    'error': {
                        'code': 'SERVER_ERROR',
                        'message': 'Failed to create equipment.',
                        'details': {},
                    },
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        return Response(
            {
                'success': True,
                'message': 'Equipment created successfully.',
                'data': EquipmentReadSerializer(equipment).data,
            },
            status=status.HTTP_201_CREATED,
        )


class EquipmentDetailView(APIView):
    """GET / PUT / PATCH / DELETE /api/equipments/{id}/"""

    authentication_classes = [JWTAuthentication]
    permission_classes = [IsJWTAuthenticated]
    parser_classes = [JSONParser, FormParser]

    def _load(self, equipment_id: int):
        try:
            return services.get_equipment_or_404(equipment_id)
        except NotFound:
            return None

    @swagger_auto_schema(
        operation_description='Retrieve a single equipment record by id.',
        responses={
            200: EquipmentReadSerializer,
            401: 'Unauthorized',
            404: 'Not Found',
        },
        security=[{'Bearer': []}],
        tags=['Equipment Management'],
    )
    def get(self, request, equipment_id):
        equipment = self._load(equipment_id)
        if equipment is None:
            return Response(
                {
                    'success': False,
                    'error': {
                        'code': 'NOT_FOUND',
                        'message': 'Equipment not found.',
                        'details': {},
                    },
                    'message': 'Equipment not found.',
                },
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(
            {'success': True, 'data': EquipmentReadSerializer(equipment).data},
            status=status.HTTP_200_OK,
        )

    @swagger_auto_schema(
        operation_description='Full replacement update of an equipment record.',
        request_body=EquipmentWriteSerializer,
        responses={
            200: openapi.Response('Updated'),
            400: 'Validation error',
            401: 'Unauthorized',
            404: 'Not Found',
            409: 'Duplicate equipment code',
        },
        security=[{'Bearer': []}],
        tags=['Equipment Management'],
    )
    def put(self, request, equipment_id):
        return self._update(request, equipment_id, partial=False)

    @swagger_auto_schema(
        operation_description='Partial update — send only fields to change.',
        request_body=EquipmentWriteSerializer,
        responses={
            200: openapi.Response('Updated'),
            400: 'Validation error',
            401: 'Unauthorized',
            404: 'Not Found',
            409: 'Duplicate equipment code',
        },
        security=[{'Bearer': []}],
        tags=['Equipment Management'],
    )
    def patch(self, request, equipment_id):
        return self._update(request, equipment_id, partial=True)

    def _update(self, request, equipment_id, *, partial: bool):
        equipment = self._load(equipment_id)
        if equipment is None:
            return Response(
                {
                    'success': False,
                    'error': {
                        'code': 'NOT_FOUND',
                        'message': 'Equipment not found.',
                        'details': {},
                    },
                    'message': 'Equipment not found.',
                },
                status=status.HTTP_404_NOT_FOUND,
            )

        serializer = EquipmentWriteSerializer(
            data=request.data,
            partial=partial,
        )
        if not serializer.is_valid():
            return _validation_error_response(serializer.errors)

        try:
            equipment = services.update_equipment(
                equipment=equipment,
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
                'Equipment update failed for id=%s user=%s',
                equipment_id,
                _jwt_username(request),
            )
            return Response(
                {
                    'success': False,
                    'error': {
                        'code': 'SERVER_ERROR',
                        'message': 'Failed to update equipment.',
                        'details': {},
                    },
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        return Response(
            {
                'success': True,
                'message': 'Equipment updated successfully.',
                'data': EquipmentReadSerializer(equipment).data,
            },
            status=status.HTTP_200_OK,
        )

    @swagger_auto_schema(
        operation_description='Hard-delete an equipment record.',
        responses={
            204: 'Deleted',
            401: 'Unauthorized',
            404: 'Not Found',
        },
        security=[{'Bearer': []}],
        tags=['Equipment Management'],
    )
    def delete(self, request, equipment_id):
        equipment = self._load(equipment_id)
        if equipment is None:
            return Response(
                {
                    'success': False,
                    'error': {
                        'code': 'NOT_FOUND',
                        'message': 'Equipment not found.',
                        'details': {},
                    },
                    'message': 'Equipment not found.',
                },
                status=status.HTTP_404_NOT_FOUND,
            )
        services.delete_equipment(equipment=equipment)
        return Response(status=status.HTTP_204_NO_CONTENT)
