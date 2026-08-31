"""Bulk Stock Management API views."""

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
from .constants import TRANSACTION_TYPE_INWARD, TRANSACTION_TYPE_OUTWARD
from .permissions import IsJWTAuthenticated
from .serializers import (
    BulkStockListQuerySerializer,
    BulkStockReadSerializer,
    BulkStockWriteSerializer,
)
from . import services
from .services import InsufficientStockError

logger = logging.getLogger(__name__)


def _jwt_username(request) -> str:
    return request.user.username


def _validation_error_response(errors, *, code: str = 'VALIDATION_ERROR', message: str | None = None):
    return Response(
        {
            'success': False,
            'error': {
                'code': code,
                'message': message or 'Invalid bulk stock data.',
                'details': errors,
            },
            'errors': errors,
        },
        status=status.HTTP_400_BAD_REQUEST,
    )


def _serializer_error_response(errors):
    if 'Duplicate material entries' in str(errors):
        return _validation_error_response(
            errors,
            code='DUPLICATE_MATERIAL',
            message='Duplicate material entries in the same transaction are not allowed.',
        )
    return _validation_error_response(errors)


def _map_validation_error(exc: ValidationError):
    raw_code = getattr(exc, 'code', None)
    detail_text = str(exc.detail)

    if raw_code == 'insufficient_stock' or 'Insufficient stock' in detail_text:
        products = exc.detail.get('products', []) if isinstance(exc.detail, dict) else []
        message = products[0] if products else 'Insufficient stock.'
        return _validation_error_response(exc.detail, code='INSUFFICIENT_STOCK', message=message)

    if raw_code == 'duplicate_material' or 'Duplicate material entries' in detail_text:
        return _validation_error_response(
            exc.detail,
            code='DUPLICATE_MATERIAL',
            message='Duplicate material entries in the same transaction are not allowed.',
        )

    if hasattr(exc, 'get_codes'):
        try:
            codes = exc.get_codes()
            flattened = str(codes)
            if 'insufficient_stock' in flattened:
                return _map_validation_error(
                    ValidationError(exc.detail, code='insufficient_stock')
                )
            if 'duplicate_material' in flattened:
                return _map_validation_error(
                    ValidationError(exc.detail, code='duplicate_material')
                )
        except Exception:
            pass

    return _validation_error_response(exc.detail)


class _BulkStockBaseView(APIView):
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsJWTAuthenticated]
    parser_classes = [JSONParser, FormParser]
    transaction_type: str = ''
    resource_label: str = ''

    def _load(self, transaction_id: int):
        try:
            return services.get_transaction_or_404(
                transaction_id=transaction_id,
                transaction_type=self.transaction_type,
            )
        except NotFound:
            return None

    def _not_found(self):
        return Response(
            {
                'success': False,
                'error': {
                    'code': 'TRANSACTION_NOT_FOUND',
                    'message': f'{self.resource_label} not found.',
                    'details': {},
                },
                'message': f'{self.resource_label} not found.',
            },
            status=status.HTTP_404_NOT_FOUND,
        )


class BulkInwardListCreateView(_BulkStockBaseView):
    transaction_type = TRANSACTION_TYPE_INWARD
    resource_label = 'Bulk inward transaction'

    @swagger_auto_schema(
        operation_description='List bulk inward transactions.',
        manual_parameters=[
            openapi.Parameter('page', openapi.IN_QUERY, type=openapi.TYPE_INTEGER),
            openapi.Parameter('page_size', openapi.IN_QUERY, type=openapi.TYPE_INTEGER),
            openapi.Parameter('search', openapi.IN_QUERY, type=openapi.TYPE_STRING),
            openapi.Parameter('sort_by', openapi.IN_QUERY, type=openapi.TYPE_STRING),
            openapi.Parameter('sort_order', openapi.IN_QUERY, type=openapi.TYPE_STRING),
            openapi.Parameter('material_id', openapi.IN_QUERY, type=openapi.TYPE_INTEGER),
        ],
        responses={200: openapi.Response('Paginated list'), 401: 'Unauthorized'},
        security=[{'Bearer': []}],
        tags=['Bulk Inward Management'],
    )
    def get(self, request):
        query_serializer = BulkStockListQuerySerializer(data=request.query_params)
        if not query_serializer.is_valid():
            return _validation_error_response(query_serializer.errors)

        result = services.list_transactions(
            transaction_type=self.transaction_type,
            query=query_serializer.validated_data,
        )
        return Response(
            {
                'success': True,
                'data': BulkStockReadSerializer(result['rows'], many=True).data,
                'pagination': result['pagination'],
            },
            status=status.HTTP_200_OK,
        )

    @swagger_auto_schema(
        operation_description='Create a bulk inward transaction.',
        request_body=BulkStockWriteSerializer,
        responses={201: 'Created', 400: 'Validation error', 401: 'Unauthorized'},
        security=[{'Bearer': []}],
        tags=['Bulk Inward Management'],
    )
    def post(self, request):
        return self._create(request)


class BulkOutwardListCreateView(_BulkStockBaseView):
    transaction_type = TRANSACTION_TYPE_OUTWARD
    resource_label = 'Bulk outward transaction'

    @swagger_auto_schema(
        operation_description='List bulk outward transactions.',
        manual_parameters=[
            openapi.Parameter('page', openapi.IN_QUERY, type=openapi.TYPE_INTEGER),
            openapi.Parameter('page_size', openapi.IN_QUERY, type=openapi.TYPE_INTEGER),
            openapi.Parameter('search', openapi.IN_QUERY, type=openapi.TYPE_STRING),
            openapi.Parameter('sort_by', openapi.IN_QUERY, type=openapi.TYPE_STRING),
            openapi.Parameter('sort_order', openapi.IN_QUERY, type=openapi.TYPE_STRING),
            openapi.Parameter('material_id', openapi.IN_QUERY, type=openapi.TYPE_INTEGER),
        ],
        responses={200: openapi.Response('Paginated list'), 401: 'Unauthorized'},
        security=[{'Bearer': []}],
        tags=['Bulk Outward Management'],
    )
    def get(self, request):
        query_serializer = BulkStockListQuerySerializer(data=request.query_params)
        if not query_serializer.is_valid():
            return _validation_error_response(query_serializer.errors)

        result = services.list_transactions(
            transaction_type=self.transaction_type,
            query=query_serializer.validated_data,
        )
        return Response(
            {
                'success': True,
                'data': BulkStockReadSerializer(result['rows'], many=True).data,
                'pagination': result['pagination'],
            },
            status=status.HTTP_200_OK,
        )

    @swagger_auto_schema(
        operation_description='Create a bulk outward transaction.',
        request_body=BulkStockWriteSerializer,
        responses={201: 'Created', 400: 'Validation error', 401: 'Unauthorized'},
        security=[{'Bearer': []}],
        tags=['Bulk Outward Management'],
    )
    def post(self, request):
        return self._create(request)


def _attach_create_mixin():
    def _create(self, request):
        serializer = BulkStockWriteSerializer(data=request.data)
        if not serializer.is_valid():
            return _serializer_error_response(serializer.errors)

        success_message = (
            'Bulk inward created successfully.'
            if self.transaction_type == TRANSACTION_TYPE_INWARD
            else 'Bulk outward created successfully.'
        )

        try:
            bulk_transaction = services.create_bulk_transaction(
                transaction_type=self.transaction_type,
                validated=serializer.validated_data,
                username=_jwt_username(request),
            )
        except InsufficientStockError as exc:
            return _map_validation_error(exc)
        except ValidationError as exc:
            return _map_validation_error(exc)
        except Exception:
            logger.exception(
                'Bulk stock create failed type=%s user=%s',
                self.transaction_type,
                _jwt_username(request),
            )
            return Response(
                {
                    'success': False,
                    'error': {
                        'code': 'SERVER_ERROR',
                        'message': 'Failed to create bulk stock transaction.',
                        'details': {},
                    },
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        return Response(
            {
                'success': True,
                'message': success_message,
                'data': BulkStockReadSerializer(bulk_transaction).data,
            },
            status=status.HTTP_201_CREATED,
        )

    BulkInwardListCreateView._create = _create
    BulkOutwardListCreateView._create = _create


_attach_create_mixin()


class BulkInwardDetailView(_BulkStockBaseView):
    transaction_type = TRANSACTION_TYPE_INWARD
    resource_label = 'Bulk inward transaction'

    @swagger_auto_schema(
        operation_description='Retrieve a bulk inward transaction.',
        responses={200: BulkStockReadSerializer, 401: 'Unauthorized', 404: 'Not Found'},
        security=[{'Bearer': []}],
        tags=['Bulk Inward Management'],
    )
    def get(self, request, transaction_id):
        bulk_transaction = self._load(transaction_id)
        if bulk_transaction is None:
            return self._not_found()
        return Response(
            {'success': True, 'data': BulkStockReadSerializer(bulk_transaction).data},
            status=status.HTTP_200_OK,
        )

    @swagger_auto_schema(
        operation_description='Full replacement update for bulk inward.',
        request_body=BulkStockWriteSerializer,
        responses={200: 'Updated', 400: 'Validation error', 401: 'Unauthorized', 404: 'Not Found'},
        security=[{'Bearer': []}],
        tags=['Bulk Inward Management'],
    )
    def put(self, request, transaction_id):
        return self._update(request, transaction_id, partial=False)

    @swagger_auto_schema(
        operation_description='Partial update for bulk inward.',
        request_body=BulkStockWriteSerializer,
        responses={200: 'Updated', 400: 'Validation error', 401: 'Unauthorized', 404: 'Not Found'},
        security=[{'Bearer': []}],
        tags=['Bulk Inward Management'],
    )
    def patch(self, request, transaction_id):
        return self._update(request, transaction_id, partial=True)

    @swagger_auto_schema(
        operation_description='Delete bulk inward and reverse stock impact.',
        responses={204: 'Deleted', 401: 'Unauthorized', 404: 'Not Found'},
        security=[{'Bearer': []}],
        tags=['Bulk Inward Management'],
    )
    def delete(self, request, transaction_id):
        return self._delete(request, transaction_id)


class BulkOutwardDetailView(_BulkStockBaseView):
    transaction_type = TRANSACTION_TYPE_OUTWARD
    resource_label = 'Bulk outward transaction'

    @swagger_auto_schema(
        operation_description='Retrieve a bulk outward transaction.',
        responses={200: BulkStockReadSerializer, 401: 'Unauthorized', 404: 'Not Found'},
        security=[{'Bearer': []}],
        tags=['Bulk Outward Management'],
    )
    def get(self, request, transaction_id):
        bulk_transaction = self._load(transaction_id)
        if bulk_transaction is None:
            return self._not_found()
        return Response(
            {'success': True, 'data': BulkStockReadSerializer(bulk_transaction).data},
            status=status.HTTP_200_OK,
        )

    @swagger_auto_schema(
        operation_description='Full replacement update for bulk outward.',
        request_body=BulkStockWriteSerializer,
        responses={200: 'Updated', 400: 'Validation error', 401: 'Unauthorized', 404: 'Not Found'},
        security=[{'Bearer': []}],
        tags=['Bulk Outward Management'],
    )
    def put(self, request, transaction_id):
        return self._update(request, transaction_id, partial=False)

    @swagger_auto_schema(
        operation_description='Partial update for bulk outward.',
        request_body=BulkStockWriteSerializer,
        responses={200: 'Updated', 400: 'Validation error', 401: 'Unauthorized', 404: 'Not Found'},
        security=[{'Bearer': []}],
        tags=['Bulk Outward Management'],
    )
    def patch(self, request, transaction_id):
        return self._update(request, transaction_id, partial=True)

    @swagger_auto_schema(
        operation_description='Delete bulk outward and reverse stock impact.',
        responses={204: 'Deleted', 401: 'Unauthorized', 404: 'Not Found'},
        security=[{'Bearer': []}],
        tags=['Bulk Outward Management'],
    )
    def delete(self, request, transaction_id):
        return self._delete(request, transaction_id)


def _attach_detail_mixin():
    def _update(self, request, transaction_id, *, partial: bool):
        bulk_transaction = self._load(transaction_id)
        if bulk_transaction is None:
            return self._not_found()

        serializer = BulkStockWriteSerializer(data=request.data, partial=partial)
        if not serializer.is_valid():
            return _serializer_error_response(serializer.errors)

        success_message = (
            'Bulk inward updated successfully.'
            if self.transaction_type == TRANSACTION_TYPE_INWARD
            else 'Bulk outward updated successfully.'
        )

        try:
            bulk_transaction = services.update_bulk_transaction(
                bulk_transaction=bulk_transaction,
                validated=serializer.validated_data,
                username=_jwt_username(request),
                partial=partial,
            )
        except InsufficientStockError as exc:
            return _map_validation_error(exc)
        except ValidationError as exc:
            return _map_validation_error(exc)
        except Exception:
            logger.exception(
                'Bulk stock update failed id=%s type=%s user=%s',
                transaction_id,
                self.transaction_type,
                _jwt_username(request),
            )
            return Response(
                {
                    'success': False,
                    'error': {
                        'code': 'SERVER_ERROR',
                        'message': 'Failed to update bulk stock transaction.',
                        'details': {},
                    },
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        return Response(
            {
                'success': True,
                'message': success_message,
                'data': BulkStockReadSerializer(bulk_transaction).data,
            },
            status=status.HTTP_200_OK,
        )

    def _delete(self, request, transaction_id):
        bulk_transaction = self._load(transaction_id)
        if bulk_transaction is None:
            return self._not_found()

        try:
            services.delete_bulk_transaction(
                bulk_transaction=bulk_transaction,
                username=_jwt_username(request),
            )
        except InsufficientStockError as exc:
            return _map_validation_error(exc)
        except ValidationError as exc:
            return _map_validation_error(exc)
        except Exception:
            logger.exception(
                'Bulk stock delete failed id=%s type=%s user=%s',
                transaction_id,
                self.transaction_type,
                _jwt_username(request),
            )
            return Response(
                {
                    'success': False,
                    'error': {
                        'code': 'SERVER_ERROR',
                        'message': 'Failed to delete bulk stock transaction.',
                        'details': {},
                    },
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        return Response(status=status.HTTP_204_NO_CONTENT)

    BulkInwardDetailView._update = _update
    BulkInwardDetailView._delete = _delete
    BulkOutwardDetailView._update = _update
    BulkOutwardDetailView._delete = _delete


_attach_detail_mixin()
