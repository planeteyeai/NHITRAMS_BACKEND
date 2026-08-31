"""Transfer Stock Management API views."""

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
    TransferStockListQuerySerializer,
    TransferStockReadSerializer,
    TransferStockWriteSerializer,
)
from . import services

logger = logging.getLogger(__name__)


def _jwt_username(request) -> str:
    return request.user.username


def _validation_error_response(errors, *, conflict: bool = False):
    http_status = (
        status.HTTP_409_CONFLICT if conflict else status.HTTP_400_BAD_REQUEST
    )
    code = 'DUPLICATE_TRANSFER_STOCK_ITEM' if conflict else 'VALIDATION_ERROR'
    return Response(
        {
            'success': False,
            'error': {
                'code': code,
                'message': (
                    'Duplicate product entries with the same material and batch '
                    'are not allowed.'
                    if conflict
                    else 'Invalid transfer stock data'
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
        messages = detail.get('products') or detail.get('non_field_errors') or []
        return any('duplicate' in str(m).lower() for m in messages)
    return False


class TransferStockListCreateView(APIView):
    """GET /api/transfer-stocks/  |  POST /api/transfer-stocks/"""

    authentication_classes = [JWTAuthentication]
    permission_classes = [IsJWTAuthenticated]
    parser_classes = [JSONParser, FormParser]

    @swagger_auto_schema(
        operation_description=(
            'List transfer stocks with pagination, search, filtering, and sorting. '
            'Query: page, page_size, search, sort_by, sort_order, supplier_id, date, '
            'material_id, batch_id, location.'
        ),
        manual_parameters=[
            openapi.Parameter('page', openapi.IN_QUERY, type=openapi.TYPE_INTEGER),
            openapi.Parameter('page_size', openapi.IN_QUERY, type=openapi.TYPE_INTEGER),
            openapi.Parameter('search', openapi.IN_QUERY, type=openapi.TYPE_STRING),
            openapi.Parameter('sort_by', openapi.IN_QUERY, type=openapi.TYPE_STRING),
            openapi.Parameter('sort_order', openapi.IN_QUERY, type=openapi.TYPE_STRING),
            openapi.Parameter('supplier_id', openapi.IN_QUERY, type=openapi.TYPE_INTEGER),
            openapi.Parameter('date', openapi.IN_QUERY, type=openapi.TYPE_STRING),
            openapi.Parameter('material_id', openapi.IN_QUERY, type=openapi.TYPE_INTEGER),
            openapi.Parameter('batch_id', openapi.IN_QUERY, type=openapi.TYPE_INTEGER),
            openapi.Parameter('location', openapi.IN_QUERY, type=openapi.TYPE_STRING),
        ],
        responses={
            200: openapi.Response('Paginated list'),
            400: 'Bad query',
            401: 'Unauthorized',
        },
        security=[{'Bearer': []}],
        tags=['Transfer Stock Management'],
    )
    def get(self, request):
        query_serializer = TransferStockListQuerySerializer(data=request.query_params)
        if not query_serializer.is_valid():
            return _validation_error_response(query_serializer.errors)

        result = services.list_transfer_stocks(query=query_serializer.validated_data)
        return Response(
            {
                'success': True,
                'data': TransferStockReadSerializer(result['rows'], many=True).data,
                'pagination': result['pagination'],
            },
            status=status.HTTP_200_OK,
        )

    @swagger_auto_schema(
        operation_description=(
            'Create a transfer stock with one or more product lines in a single '
            'transaction. Product name comes from Material via material_id.'
        ),
        request_body=TransferStockWriteSerializer,
        responses={
            201: openapi.Response('Created'),
            400: 'Validation error',
            401: 'Unauthorized',
            409: 'Duplicate product line',
        },
        security=[{'Bearer': []}],
        tags=['Transfer Stock Management'],
    )
    def post(self, request):
        serializer = TransferStockWriteSerializer(data=request.data)
        if not serializer.is_valid():
            return _validation_error_response(serializer.errors)

        try:
            transfer_stock = services.create_transfer_stock(
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
                'Transfer stock create failed for user=%s', _jwt_username(request)
            )
            return Response(
                {
                    'success': False,
                    'error': {
                        'code': 'SERVER_ERROR',
                        'message': 'Failed to create transfer stock.',
                        'details': {},
                    },
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        return Response(
            {
                'success': True,
                'message': 'Transfer stock created successfully.',
                'data': TransferStockReadSerializer(transfer_stock).data,
            },
            status=status.HTTP_201_CREATED,
        )


class TransferStockDetailView(APIView):
    """GET / PUT / PATCH / DELETE /api/transfer-stocks/{id}/"""

    authentication_classes = [JWTAuthentication]
    permission_classes = [IsJWTAuthenticated]
    parser_classes = [JSONParser, FormParser]

    def _load(self, transfer_stock_id: int):
        try:
            return services.get_transfer_stock_or_404(transfer_stock_id)
        except NotFound:
            return None

    @swagger_auto_schema(
        operation_description='Retrieve a transfer stock with all product lines.',
        responses={
            200: TransferStockReadSerializer,
            401: 'Unauthorized',
            404: 'Not Found',
        },
        security=[{'Bearer': []}],
        tags=['Transfer Stock Management'],
    )
    def get(self, request, transfer_stock_id):
        transfer_stock = self._load(transfer_stock_id)
        if transfer_stock is None:
            return Response(
                {
                    'success': False,
                    'error': {
                        'code': 'NOT_FOUND',
                        'message': 'Transfer stock not found.',
                        'details': {},
                    },
                    'message': 'Transfer stock not found.',
                },
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(
            {
                'success': True,
                'data': TransferStockReadSerializer(transfer_stock).data,
            },
            status=status.HTTP_200_OK,
        )

    @swagger_auto_schema(
        operation_description=(
            'Full replacement update. The products array replaces all existing '
            'product lines; omitted lines are removed.'
        ),
        request_body=TransferStockWriteSerializer,
        responses={
            200: openapi.Response('Updated'),
            400: 'Validation error',
            401: 'Unauthorized',
            404: 'Not Found',
            409: 'Duplicate product line',
        },
        security=[{'Bearer': []}],
        tags=['Transfer Stock Management'],
    )
    def put(self, request, transfer_stock_id):
        return self._update(request, transfer_stock_id, partial=False)

    @swagger_auto_schema(
        operation_description=(
            'Partial update. When products is omitted, existing lines are unchanged. '
            'When products is provided, lines with id are updated, lines without id '
            'are created, and omitted existing lines are kept.'
        ),
        request_body=TransferStockWriteSerializer,
        responses={
            200: openapi.Response('Updated'),
            400: 'Validation error',
            401: 'Unauthorized',
            404: 'Not Found',
            409: 'Duplicate product line',
        },
        security=[{'Bearer': []}],
        tags=['Transfer Stock Management'],
    )
    def patch(self, request, transfer_stock_id):
        return self._update(request, transfer_stock_id, partial=True)

    def _update(self, request, transfer_stock_id, *, partial: bool):
        transfer_stock = self._load(transfer_stock_id)
        if transfer_stock is None:
            return Response(
                {
                    'success': False,
                    'error': {
                        'code': 'NOT_FOUND',
                        'message': 'Transfer stock not found.',
                        'details': {},
                    },
                    'message': 'Transfer stock not found.',
                },
                status=status.HTTP_404_NOT_FOUND,
            )

        serializer = TransferStockWriteSerializer(
            data=request.data,
            partial=partial,
        )
        if not serializer.is_valid():
            return _validation_error_response(serializer.errors)

        try:
            transfer_stock = services.update_transfer_stock(
                transfer_stock=transfer_stock,
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
                'Transfer stock update failed for id=%s user=%s',
                transfer_stock_id,
                _jwt_username(request),
            )
            return Response(
                {
                    'success': False,
                    'error': {
                        'code': 'SERVER_ERROR',
                        'message': 'Failed to update transfer stock.',
                        'details': {},
                    },
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        return Response(
            {
                'success': True,
                'message': 'Transfer stock updated successfully.',
                'data': TransferStockReadSerializer(transfer_stock).data,
            },
            status=status.HTTP_200_OK,
        )

    @swagger_auto_schema(
        operation_description='Hard-delete a transfer stock and its product lines.',
        responses={
            204: 'Deleted',
            401: 'Unauthorized',
            404: 'Not Found',
        },
        security=[{'Bearer': []}],
        tags=['Transfer Stock Management'],
    )
    def delete(self, request, transfer_stock_id):
        transfer_stock = self._load(transfer_stock_id)
        if transfer_stock is None:
            return Response(
                {
                    'success': False,
                    'error': {
                        'code': 'NOT_FOUND',
                        'message': 'Transfer stock not found.',
                        'details': {},
                    },
                    'message': 'Transfer stock not found.',
                },
                status=status.HTTP_404_NOT_FOUND,
            )
        services.delete_transfer_stock(transfer_stock=transfer_stock)
        return Response(status=status.HTTP_204_NO_CONTENT)
