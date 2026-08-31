"""Purchase Invoice Management API views."""

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
    PurchaseInvoiceListQuerySerializer,
    PurchaseInvoiceReadSerializer,
    PurchaseInvoiceWriteSerializer,
)
from . import services

logger = logging.getLogger(__name__)


def _jwt_username(request) -> str:
    return request.user.username


def _validation_error_response(errors, *, conflict: bool = False):
    http_status = (
        status.HTTP_409_CONFLICT if conflict else status.HTTP_400_BAD_REQUEST
    )
    if conflict and isinstance(errors, dict) and 'invoice_number' in errors:
        code = 'DUPLICATE_PURCHASE_INVOICE'
        message = 'A purchase invoice with this supplier and invoice number already exists.'
    elif conflict:
        code = 'DUPLICATE_PURCHASE_INVOICE_ITEM'
        message = (
            'Duplicate product entries with the same material and batch number '
            'are not allowed.'
        )
    else:
        code = 'VALIDATION_ERROR'
        message = 'Invalid purchase invoice data'
    return Response(
        {
            'success': False,
            'error': {
                'code': code,
                'message': message,
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
        return 'invoice_number' in detail or 'products' in detail
    return False


class PurchaseInvoiceListCreateView(APIView):
    """GET /api/purchase-invoices/  |  POST /api/purchase-invoices/"""

    authentication_classes = [JWTAuthentication]
    permission_classes = [IsJWTAuthenticated]
    parser_classes = [JSONParser, FormParser]

    @swagger_auto_schema(
        operation_description=(
            'List purchase invoices with pagination, search, filtering, and sorting.'
        ),
        manual_parameters=[
            openapi.Parameter('page', openapi.IN_QUERY, type=openapi.TYPE_INTEGER),
            openapi.Parameter('page_size', openapi.IN_QUERY, type=openapi.TYPE_INTEGER),
            openapi.Parameter('search', openapi.IN_QUERY, type=openapi.TYPE_STRING),
            openapi.Parameter('sort_by', openapi.IN_QUERY, type=openapi.TYPE_STRING),
            openapi.Parameter('sort_order', openapi.IN_QUERY, type=openapi.TYPE_STRING),
            openapi.Parameter('supplier_id', openapi.IN_QUERY, type=openapi.TYPE_INTEGER),
            openapi.Parameter('invoice_date', openapi.IN_QUERY, type=openapi.TYPE_STRING),
            openapi.Parameter('material_id', openapi.IN_QUERY, type=openapi.TYPE_INTEGER),
            openapi.Parameter('invoice_number', openapi.IN_QUERY, type=openapi.TYPE_STRING),
        ],
        responses={200: openapi.Response('Paginated list'), 401: 'Unauthorized'},
        security=[{'Bearer': []}],
        tags=['Purchase Invoice Management'],
    )
    def get(self, request):
        query_serializer = PurchaseInvoiceListQuerySerializer(data=request.query_params)
        if not query_serializer.is_valid():
            return _validation_error_response(query_serializer.errors)

        result = services.list_purchase_invoices(query=query_serializer.validated_data)
        return Response(
            {
                'success': True,
                'data': PurchaseInvoiceReadSerializer(result['rows'], many=True).data,
                'pagination': result['pagination'],
            },
            status=status.HTTP_200_OK,
        )

    @swagger_auto_schema(
        operation_description=(
            'Create a purchase invoice with one or more product lines in a single '
            'transaction. Supplier and Material come from existing master modules.'
        ),
        request_body=PurchaseInvoiceWriteSerializer,
        responses={
            201: openapi.Response('Created'),
            400: 'Validation error',
            401: 'Unauthorized',
            409: 'Duplicate invoice or product line',
        },
        security=[{'Bearer': []}],
        tags=['Purchase Invoice Management'],
    )
    def post(self, request):
        serializer = PurchaseInvoiceWriteSerializer(data=request.data)
        if not serializer.is_valid():
            return _validation_error_response(serializer.errors)

        try:
            invoice = services.create_purchase_invoice(
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
                'Purchase invoice create failed for user=%s', _jwt_username(request)
            )
            return Response(
                {
                    'success': False,
                    'error': {
                        'code': 'SERVER_ERROR',
                        'message': 'Failed to create purchase invoice.',
                        'details': {},
                    },
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        return Response(
            {
                'success': True,
                'message': 'Purchase invoice created successfully.',
                'data': PurchaseInvoiceReadSerializer(invoice).data,
            },
            status=status.HTTP_201_CREATED,
        )


class PurchaseInvoiceDetailView(APIView):
    """GET / PUT / PATCH / DELETE /api/purchase-invoices/{id}/"""

    authentication_classes = [JWTAuthentication]
    permission_classes = [IsJWTAuthenticated]
    parser_classes = [JSONParser, FormParser]

    def _load(self, purchase_invoice_id: int):
        try:
            return services.get_purchase_invoice_or_404(purchase_invoice_id)
        except NotFound:
            return None

    @swagger_auto_schema(
        operation_description='Retrieve a purchase invoice with all product lines.',
        responses={200: PurchaseInvoiceReadSerializer, 401: 'Unauthorized', 404: 'Not Found'},
        security=[{'Bearer': []}],
        tags=['Purchase Invoice Management'],
    )
    def get(self, request, purchase_invoice_id):
        invoice = self._load(purchase_invoice_id)
        if invoice is None:
            return Response(
                {
                    'success': False,
                    'error': {
                        'code': 'NOT_FOUND',
                        'message': 'Purchase invoice not found.',
                        'details': {},
                    },
                    'message': 'Purchase invoice not found.',
                },
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(
            {'success': True, 'data': PurchaseInvoiceReadSerializer(invoice).data},
            status=status.HTTP_200_OK,
        )

    @swagger_auto_schema(
        operation_description='Full replacement update; products list replaces all lines.',
        request_body=PurchaseInvoiceWriteSerializer,
        responses={
            200: openapi.Response('Updated'),
            400: 'Validation error',
            401: 'Unauthorized',
            404: 'Not Found',
            409: 'Duplicate',
        },
        security=[{'Bearer': []}],
        tags=['Purchase Invoice Management'],
    )
    def put(self, request, purchase_invoice_id):
        return self._update(request, purchase_invoice_id, partial=False)

    @swagger_auto_schema(
        operation_description=(
            'Partial update. When products is omitted, existing lines are unchanged. '
            'When products is provided, lines with id update, without id create, '
            'and omitted existing lines are kept.'
        ),
        request_body=PurchaseInvoiceWriteSerializer,
        responses={
            200: openapi.Response('Updated'),
            400: 'Validation error',
            401: 'Unauthorized',
            404: 'Not Found',
            409: 'Duplicate',
        },
        security=[{'Bearer': []}],
        tags=['Purchase Invoice Management'],
    )
    def patch(self, request, purchase_invoice_id):
        return self._update(request, purchase_invoice_id, partial=True)

    def _update(self, request, purchase_invoice_id, *, partial: bool):
        invoice = self._load(purchase_invoice_id)
        if invoice is None:
            return Response(
                {
                    'success': False,
                    'error': {
                        'code': 'NOT_FOUND',
                        'message': 'Purchase invoice not found.',
                        'details': {},
                    },
                    'message': 'Purchase invoice not found.',
                },
                status=status.HTTP_404_NOT_FOUND,
            )

        serializer = PurchaseInvoiceWriteSerializer(
            data=request.data,
            partial=partial,
        )
        if not serializer.is_valid():
            return _validation_error_response(serializer.errors)

        try:
            invoice = services.update_purchase_invoice(
                purchase_invoice=invoice,
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
                'Purchase invoice update failed for id=%s user=%s',
                purchase_invoice_id,
                _jwt_username(request),
            )
            return Response(
                {
                    'success': False,
                    'error': {
                        'code': 'SERVER_ERROR',
                        'message': 'Failed to update purchase invoice.',
                        'details': {},
                    },
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        return Response(
            {
                'success': True,
                'message': 'Purchase invoice updated successfully.',
                'data': PurchaseInvoiceReadSerializer(invoice).data,
            },
            status=status.HTTP_200_OK,
        )

    @swagger_auto_schema(
        operation_description='Hard-delete a purchase invoice and its product lines.',
        responses={204: 'Deleted', 401: 'Unauthorized', 404: 'Not Found'},
        security=[{'Bearer': []}],
        tags=['Purchase Invoice Management'],
    )
    def delete(self, request, purchase_invoice_id):
        invoice = self._load(purchase_invoice_id)
        if invoice is None:
            return Response(
                {
                    'success': False,
                    'error': {
                        'code': 'NOT_FOUND',
                        'message': 'Purchase invoice not found.',
                        'details': {},
                    },
                    'message': 'Purchase invoice not found.',
                },
                status=status.HTTP_404_NOT_FOUND,
            )
        services.delete_purchase_invoice(purchase_invoice=invoice)
        return Response(status=status.HTTP_204_NO_CONTENT)
