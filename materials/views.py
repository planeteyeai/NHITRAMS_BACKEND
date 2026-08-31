"""Material Management API views."""

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
    MaterialListQuerySerializer,
    MaterialReadSerializer,
    MaterialWriteSerializer,
)
from . import services
from nhit.caching import CACHE_NS_MATERIALS_LIST, cached_get_response
from nhit.caching.constants import CACHE_TTL_LIST

logger = logging.getLogger(__name__)


def _jwt_username(request) -> str:
    return request.user.username


def _validation_error_response(errors, *, conflict: bool = False):
    http_status = (
        status.HTTP_409_CONFLICT if conflict else status.HTTP_400_BAD_REQUEST
    )
    code = 'DUPLICATE_MATERIAL' if conflict else 'VALIDATION_ERROR'
    return Response(
        {
            'success': False,
            'error': {
                'code': code,
                'message': (
                    'A duplicate material record already exists.'
                    if conflict
                    else 'Invalid material data'
                ),
                'details': errors,
            },
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


class MaterialListCreateView(APIView):
    """GET /api/materials/  |  POST /api/materials/"""

    authentication_classes = [JWTAuthentication]
    permission_classes = [IsJWTAuthenticated]
    parser_classes = [JSONParser, FormParser]

    @swagger_auto_schema(
        operation_description=(
            'List materials with pagination, filtering, search, and sorting. '
            'Query: page, page_size, search, sort_by, sort_order, '
            'material_unit, material_type.'
        ),
        manual_parameters=[
            openapi.Parameter('page', openapi.IN_QUERY, type=openapi.TYPE_INTEGER),
            openapi.Parameter('page_size', openapi.IN_QUERY, type=openapi.TYPE_INTEGER),
            openapi.Parameter('search', openapi.IN_QUERY, type=openapi.TYPE_STRING),
            openapi.Parameter('sort_by', openapi.IN_QUERY, type=openapi.TYPE_STRING),
            openapi.Parameter('sort_order', openapi.IN_QUERY, type=openapi.TYPE_STRING),
            openapi.Parameter('material_unit', openapi.IN_QUERY, type=openapi.TYPE_STRING),
            openapi.Parameter('material_type', openapi.IN_QUERY, type=openapi.TYPE_STRING),
        ],
        responses={
            200: openapi.Response('Paginated list'),
            400: 'Bad query',
            401: 'Unauthorized',
        },
        security=[{'Bearer': []}],
        tags=['Material Management'],
    )
    def get(self, request):
        query_serializer = MaterialListQuerySerializer(data=request.query_params)
        if not query_serializer.is_valid():
            return _validation_error_response(query_serializer.errors)

        validated_query = query_serializer.validated_data

        def build_response():
            result = services.list_materials(query=validated_query)
            return {
                'success': True,
                'data': MaterialReadSerializer(result['rows'], many=True).data,
                'pagination': result['pagination'],
            }

        return cached_get_response(
            namespace=CACHE_NS_MATERIALS_LIST,
            payload=validated_query,
            timeout=CACHE_TTL_LIST,
            builder=build_response,
        )

    @swagger_auto_schema(
        operation_description=(
            'Create a material record. material_name, material_unit, and '
            'material_type are required and accepted as sent by the frontend. '
            'When material_type is Other, other_type is required. '
            'Duplicate name+unit+type+other_type returns 409.'
        ),
        request_body=MaterialWriteSerializer,
        responses={
            201: openapi.Response('Created'),
            400: 'Validation error',
            401: 'Unauthorized',
            409: 'Duplicate material',
        },
        security=[{'Bearer': []}],
        tags=['Material Management'],
    )
    def post(self, request):
        serializer = MaterialWriteSerializer(data=request.data)
        if not serializer.is_valid():
            return _validation_error_response(serializer.errors)

        try:
            material = services.create_material(
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
                'Material create failed for user=%s', _jwt_username(request)
            )
            return Response(
                {
                    'success': False,
                    'error': {
                        'code': 'SERVER_ERROR',
                        'message': 'Failed to create material.',
                        'details': {},
                    },
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        return Response(
            {
                'success': True,
                'message': 'Material created successfully.',
                'data': MaterialReadSerializer(material).data,
            },
            status=status.HTTP_201_CREATED,
        )


class MaterialDetailView(APIView):
    """GET / PUT / PATCH / DELETE /api/materials/{id}/"""

    authentication_classes = [JWTAuthentication]
    permission_classes = [IsJWTAuthenticated]
    parser_classes = [JSONParser, FormParser]

    def _load(self, material_id: int):
        try:
            return services.get_material_or_404(material_id)
        except NotFound:
            return None

    @swagger_auto_schema(
        operation_description='Retrieve a single material by id.',
        responses={
            200: MaterialReadSerializer,
            401: 'Unauthorized',
            404: 'Not Found',
        },
        security=[{'Bearer': []}],
        tags=['Material Management'],
    )
    def get(self, request, material_id):
        material = self._load(material_id)
        if material is None:
            return Response(
                {
                    'success': False,
                    'error': {
                        'code': 'NOT_FOUND',
                        'message': 'Material not found.',
                        'details': {},
                    },
                    'message': 'Material not found.',
                },
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(
            {'success': True, 'data': MaterialReadSerializer(material).data},
            status=status.HTTP_200_OK,
        )

    @swagger_auto_schema(
        operation_description='Full replacement update of a material record.',
        request_body=MaterialWriteSerializer,
        responses={
            200: openapi.Response('Updated'),
            400: 'Validation error',
            401: 'Unauthorized',
            404: 'Not Found',
            409: 'Duplicate material',
        },
        security=[{'Bearer': []}],
        tags=['Material Management'],
    )
    def put(self, request, material_id):
        return self._update(request, material_id, partial=False)

    @swagger_auto_schema(
        operation_description='Partial update — send only fields to change.',
        request_body=MaterialWriteSerializer,
        responses={
            200: openapi.Response('Updated'),
            400: 'Validation error',
            401: 'Unauthorized',
            404: 'Not Found',
            409: 'Duplicate material',
        },
        security=[{'Bearer': []}],
        tags=['Material Management'],
    )
    def patch(self, request, material_id):
        return self._update(request, material_id, partial=True)

    def _update(self, request, material_id, *, partial: bool):
        material = self._load(material_id)
        if material is None:
            return Response(
                {
                    'success': False,
                    'error': {
                        'code': 'NOT_FOUND',
                        'message': 'Material not found.',
                        'details': {},
                    },
                    'message': 'Material not found.',
                },
                status=status.HTTP_404_NOT_FOUND,
            )

        serializer = MaterialWriteSerializer(
            data=request.data,
            partial=partial,
        )
        if not serializer.is_valid():
            return _validation_error_response(serializer.errors)

        try:
            material = services.update_material(
                material=material,
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
                'Material update failed for id=%s user=%s',
                material_id,
                _jwt_username(request),
            )
            return Response(
                {
                    'success': False,
                    'error': {
                        'code': 'SERVER_ERROR',
                        'message': 'Failed to update material.',
                        'details': {},
                    },
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        return Response(
            {
                'success': True,
                'message': 'Material updated successfully.',
                'data': MaterialReadSerializer(material).data,
            },
            status=status.HTTP_200_OK,
        )

    @swagger_auto_schema(
        operation_description='Hard-delete a material record.',
        responses={
            204: 'Deleted',
            401: 'Unauthorized',
            404: 'Not Found',
        },
        security=[{'Bearer': []}],
        tags=['Material Management'],
    )
    def delete(self, request, material_id):
        material = self._load(material_id)
        if material is None:
            return Response(
                {
                    'success': False,
                    'error': {
                        'code': 'NOT_FOUND',
                        'message': 'Material not found.',
                        'details': {},
                    },
                    'message': 'Material not found.',
                },
                status=status.HTTP_404_NOT_FOUND,
            )
        services.delete_material(material=material)
        return Response(status=status.HTTP_204_NO_CONTENT)
