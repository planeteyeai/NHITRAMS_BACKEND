"""Supplier Management API views."""

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
    SupplierListQuerySerializer,
    SupplierReadSerializer,
    SupplierWriteSerializer,
)
from . import services
from nhit.caching import CACHE_NS_SUPPLIER_MASTERS, cached_get_response

logger = logging.getLogger(__name__)


def _jwt_username(request) -> str:
    return request.user.username


def _validation_error_response(errors, *, conflict: bool = False):
    http_status = (
        status.HTTP_409_CONFLICT if conflict else status.HTTP_400_BAD_REQUEST
    )
    code = 'DUPLICATE_SUPPLIER' if conflict else 'VALIDATION_ERROR'
    return Response(
        {
            'success': False,
            'error': {
                'code': code,
                'message': (
                    'A duplicate supplier record already exists.'
                    if conflict
                    else 'Invalid supplier data'
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


class SupplierMasterView(APIView):
    """GET /api/suppliers/masters/ — state dropdown from existing projects table."""

    authentication_classes = [JWTAuthentication]
    permission_classes = [IsJWTAuthenticated]

    @swagger_auto_schema(
        operation_description=(
            'Return state dropdown values derived from distinct project states. '
            'City remains a free-text field on the supplier form.'
        ),
        responses={200: openapi.Response('Master data'), 401: 'Unauthorized'},
        security=[{'Bearer': []}],
        tags=['Supplier Management'],
    )
    def get(self, request):
        return cached_get_response(
            namespace=CACHE_NS_SUPPLIER_MASTERS,
            payload={},
            builder=lambda: {'success': True, 'data': services.get_supplier_masters()},
        )


class SupplierListCreateView(APIView):
    """GET /api/suppliers/  |  POST /api/suppliers/"""

    authentication_classes = [JWTAuthentication]
    permission_classes = [IsJWTAuthenticated]
    parser_classes = [JSONParser, FormParser]

    @swagger_auto_schema(
        operation_description=(
            'List suppliers with pagination, filtering, search, and sorting. '
            'Query: page, page_size, search, sort_by, sort_order, '
            'state, city, company_name, contact_person, email, mobile_number.'
        ),
        manual_parameters=[
            openapi.Parameter('page', openapi.IN_QUERY, type=openapi.TYPE_INTEGER),
            openapi.Parameter('page_size', openapi.IN_QUERY, type=openapi.TYPE_INTEGER),
            openapi.Parameter('search', openapi.IN_QUERY, type=openapi.TYPE_STRING),
            openapi.Parameter('sort_by', openapi.IN_QUERY, type=openapi.TYPE_STRING),
            openapi.Parameter('sort_order', openapi.IN_QUERY, type=openapi.TYPE_STRING),
            openapi.Parameter('state', openapi.IN_QUERY, type=openapi.TYPE_STRING),
            openapi.Parameter('city', openapi.IN_QUERY, type=openapi.TYPE_STRING),
            openapi.Parameter('company_name', openapi.IN_QUERY, type=openapi.TYPE_STRING),
            openapi.Parameter(
                'contact_person', openapi.IN_QUERY, type=openapi.TYPE_STRING
            ),
            openapi.Parameter('email', openapi.IN_QUERY, type=openapi.TYPE_STRING),
            openapi.Parameter(
                'mobile_number', openapi.IN_QUERY, type=openapi.TYPE_STRING
            ),
        ],
        responses={
            200: openapi.Response('Paginated list'),
            400: 'Bad query',
            401: 'Unauthorized',
        },
        security=[{'Bearer': []}],
        tags=['Supplier Management'],
    )
    def get(self, request):
        query_serializer = SupplierListQuerySerializer(data=request.query_params)
        if not query_serializer.is_valid():
            return _validation_error_response(query_serializer.errors)

        result = services.list_suppliers(query=query_serializer.validated_data)
        return Response(
            {
                'success': True,
                'data': SupplierReadSerializer(result['rows'], many=True).data,
                'pagination': result['pagination'],
            },
            status=status.HTTP_200_OK,
        )

    @swagger_auto_schema(
        operation_description=(
            'Create a supplier record. All form fields are required. '
            'Duplicate company_name + email returns 409.'
        ),
        request_body=SupplierWriteSerializer,
        responses={
            201: openapi.Response('Created'),
            400: 'Validation error',
            401: 'Unauthorized',
            409: 'Duplicate supplier',
        },
        security=[{'Bearer': []}],
        tags=['Supplier Management'],
    )
    def post(self, request):
        serializer = SupplierWriteSerializer(data=request.data)
        if not serializer.is_valid():
            return _validation_error_response(serializer.errors)

        try:
            supplier = services.create_supplier(
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
                'Supplier create failed for user=%s', _jwt_username(request)
            )
            return Response(
                {
                    'success': False,
                    'error': {
                        'code': 'SERVER_ERROR',
                        'message': 'Failed to create supplier.',
                        'details': {},
                    },
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        return Response(
            {
                'success': True,
                'message': 'Supplier created successfully.',
                'data': SupplierReadSerializer(supplier).data,
            },
            status=status.HTTP_201_CREATED,
        )


class SupplierDetailView(APIView):
    """GET / PUT / PATCH / DELETE /api/suppliers/{id}/"""

    authentication_classes = [JWTAuthentication]
    permission_classes = [IsJWTAuthenticated]
    parser_classes = [JSONParser, FormParser]

    def _load(self, supplier_id: int):
        try:
            return services.get_supplier_or_404(supplier_id)
        except NotFound:
            return None

    @swagger_auto_schema(
        operation_description='Retrieve a single supplier by id.',
        responses={
            200: SupplierReadSerializer,
            401: 'Unauthorized',
            404: 'Not Found',
        },
        security=[{'Bearer': []}],
        tags=['Supplier Management'],
    )
    def get(self, request, supplier_id):
        supplier = self._load(supplier_id)
        if supplier is None:
            return Response(
                {
                    'success': False,
                    'error': {
                        'code': 'NOT_FOUND',
                        'message': 'Supplier not found.',
                        'details': {},
                    },
                    'message': 'Supplier not found.',
                },
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(
            {'success': True, 'data': SupplierReadSerializer(supplier).data},
            status=status.HTTP_200_OK,
        )

    @swagger_auto_schema(
        operation_description='Full replacement update of a supplier record.',
        request_body=SupplierWriteSerializer,
        responses={
            200: openapi.Response('Updated'),
            400: 'Validation error',
            401: 'Unauthorized',
            404: 'Not Found',
            409: 'Duplicate supplier',
        },
        security=[{'Bearer': []}],
        tags=['Supplier Management'],
    )
    def put(self, request, supplier_id):
        return self._update(request, supplier_id, partial=False)

    @swagger_auto_schema(
        operation_description='Partial update — send only fields to change.',
        request_body=SupplierWriteSerializer,
        responses={
            200: openapi.Response('Updated'),
            400: 'Validation error',
            401: 'Unauthorized',
            404: 'Not Found',
            409: 'Duplicate supplier',
        },
        security=[{'Bearer': []}],
        tags=['Supplier Management'],
    )
    def patch(self, request, supplier_id):
        return self._update(request, supplier_id, partial=True)

    def _update(self, request, supplier_id, *, partial: bool):
        supplier = self._load(supplier_id)
        if supplier is None:
            return Response(
                {
                    'success': False,
                    'error': {
                        'code': 'NOT_FOUND',
                        'message': 'Supplier not found.',
                        'details': {},
                    },
                    'message': 'Supplier not found.',
                },
                status=status.HTTP_404_NOT_FOUND,
            )

        serializer = SupplierWriteSerializer(
            data=request.data,
            partial=partial,
        )
        if not serializer.is_valid():
            return _validation_error_response(serializer.errors)

        try:
            supplier = services.update_supplier(
                supplier=supplier,
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
                'Supplier update failed for id=%s user=%s',
                supplier_id,
                _jwt_username(request),
            )
            return Response(
                {
                    'success': False,
                    'error': {
                        'code': 'SERVER_ERROR',
                        'message': 'Failed to update supplier.',
                        'details': {},
                    },
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        return Response(
            {
                'success': True,
                'message': 'Supplier updated successfully.',
                'data': SupplierReadSerializer(supplier).data,
            },
            status=status.HTTP_200_OK,
        )

    @swagger_auto_schema(
        operation_description='Hard-delete a supplier record.',
        responses={
            204: 'Deleted',
            401: 'Unauthorized',
            404: 'Not Found',
        },
        security=[{'Bearer': []}],
        tags=['Supplier Management'],
    )
    def delete(self, request, supplier_id):
        supplier = self._load(supplier_id)
        if supplier is None:
            return Response(
                {
                    'success': False,
                    'error': {
                        'code': 'NOT_FOUND',
                        'message': 'Supplier not found.',
                        'details': {},
                    },
                    'message': 'Supplier not found.',
                },
                status=status.HTTP_404_NOT_FOUND,
            )
        services.delete_supplier(supplier=supplier)
        return Response(status=status.HTTP_204_NO_CONTENT)
