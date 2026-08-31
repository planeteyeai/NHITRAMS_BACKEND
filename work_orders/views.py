"""Work Order Management API views."""

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
    WorkOrderListQuerySerializer,
    WorkOrderReadSerializer,
    WorkOrderWriteSerializer,
)
from . import services

logger = logging.getLogger(__name__)


def _jwt_username(request) -> str:
    return request.user.username


def _validation_error_response(errors, *, conflict: bool = False, code: str | None = None, message: str | None = None):
    if conflict:
        http_status = status.HTTP_409_CONFLICT
        error_code = code or 'DUPLICATE_WORK_ORDER'
        error_message = message or 'Work Order ID already exists.'
    else:
        http_status = status.HTTP_400_BAD_REQUEST
        error_code = code or 'VALIDATION_ERROR'
        error_message = message or 'Invalid work order data.'

    return Response(
        {
            'success': False,
            'error': {
                'code': error_code,
                'message': error_message,
                'details': errors,
            },
            'errors': errors,
        },
        status=http_status,
    )


def _map_service_error(exc: ValidationError):
    raw_code = getattr(exc, 'code', None)
    detail_text = str(exc.detail)

    if raw_code == 'duplicate' or 'Work Order ID already exists' in detail_text:
        return _validation_error_response(
            exc.detail,
            conflict=True,
            code='DUPLICATE_WORK_ORDER',
            message='Work Order ID already exists.',
        )

    if raw_code == 'invalid_chainage_range' or 'Chainage km To must be' in detail_text:
        return _validation_error_response(
            exc.detail,
            code='INVALID_CHAINAGE_RANGE',
            message='Chainage km To must be greater than or equal to Chainage km From.',
        )

    if raw_code == 'other_type_required' or 'Other Type is required' in detail_text:
        return _validation_error_response(
            exc.detail,
            code='OTHER_TYPE_REQUIRED',
            message='Other Type is required when Type of Work is Other.',
        )

    return _validation_error_response(exc.detail)


def _is_duplicate_error(exc: ValidationError) -> bool:
    if getattr(exc, 'code', None) == 'duplicate':
        return True
    detail = exc.detail
    if isinstance(detail, dict):
        return 'work_order_id' in detail
    return False


class WorkOrderListCreateView(APIView):
    """GET /api/work-orders/  |  POST /api/work-orders/"""

    authentication_classes = [JWTAuthentication]
    permission_classes = [IsJWTAuthenticated]
    parser_classes = [JSONParser, FormParser]

    @swagger_auto_schema(
        operation_description='List work orders with pagination, search, filtering, and sorting.',
        manual_parameters=[
            openapi.Parameter('page', openapi.IN_QUERY, type=openapi.TYPE_INTEGER),
            openapi.Parameter('page_size', openapi.IN_QUERY, type=openapi.TYPE_INTEGER),
            openapi.Parameter('search', openapi.IN_QUERY, type=openapi.TYPE_STRING),
            openapi.Parameter('sort_by', openapi.IN_QUERY, type=openapi.TYPE_STRING),
            openapi.Parameter('sort_order', openapi.IN_QUERY, type=openapi.TYPE_STRING),
            openapi.Parameter('type_of_work', openapi.IN_QUERY, type=openapi.TYPE_STRING),
            openapi.Parameter('work_order_id', openapi.IN_QUERY, type=openapi.TYPE_STRING),
            openapi.Parameter('chainage_from', openapi.IN_QUERY, type=openapi.TYPE_STRING),
            openapi.Parameter('chainage_to', openapi.IN_QUERY, type=openapi.TYPE_STRING),
        ],
        responses={200: openapi.Response('Paginated list'), 401: 'Unauthorized'},
        security=[{'Bearer': []}],
        tags=['Work Order Management'],
    )
    def get(self, request):
        query_serializer = WorkOrderListQuerySerializer(data=request.query_params)
        if not query_serializer.is_valid():
            return _validation_error_response(query_serializer.errors)

        result = services.list_work_orders(query=query_serializer.validated_data)
        return Response(
            {
                'success': True,
                'data': WorkOrderReadSerializer(result['rows'], many=True).data,
                'pagination': result['pagination'],
            },
            status=status.HTTP_200_OK,
        )

    @swagger_auto_schema(
        operation_description='Create a work order.',
        request_body=WorkOrderWriteSerializer,
        responses={
            201: openapi.Response('Created'),
            400: 'Validation error',
            401: 'Unauthorized',
            409: 'Duplicate Work Order ID',
        },
        security=[{'Bearer': []}],
        tags=['Work Order Management'],
    )
    def post(self, request):
        serializer = WorkOrderWriteSerializer(data=request.data)
        if not serializer.is_valid():
            return _validation_error_response(serializer.errors)

        try:
            work_order = services.create_work_order(
                validated=serializer.validated_data,
                username=_jwt_username(request),
            )
        except ValidationError as exc:
            return _map_service_error(exc)
        except Exception:
            logger.exception(
                'Work order create failed for user=%s', _jwt_username(request)
            )
            return Response(
                {
                    'success': False,
                    'error': {
                        'code': 'SERVER_ERROR',
                        'message': 'Failed to create work order.',
                        'details': {},
                    },
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        return Response(
            {
                'success': True,
                'message': 'Work order created successfully.',
                'data': WorkOrderReadSerializer(work_order).data,
            },
            status=status.HTTP_201_CREATED,
        )


class WorkOrderDetailView(APIView):
    """GET / PUT / PATCH / DELETE /api/work-orders/{id}/"""

    authentication_classes = [JWTAuthentication]
    permission_classes = [IsJWTAuthenticated]
    parser_classes = [JSONParser, FormParser]

    def _load(self, work_order_pk: int):
        try:
            return services.get_work_order_or_404(work_order_pk)
        except NotFound:
            return None

    @swagger_auto_schema(
        operation_description='Retrieve a work order by primary key.',
        responses={200: WorkOrderReadSerializer, 401: 'Unauthorized', 404: 'Not Found'},
        security=[{'Bearer': []}],
        tags=['Work Order Management'],
    )
    def get(self, request, work_order_pk):
        work_order = self._load(work_order_pk)
        if work_order is None:
            return Response(
                {
                    'success': False,
                    'error': {
                        'code': 'WORK_ORDER_NOT_FOUND',
                        'message': 'Work order not found.',
                        'details': {},
                    },
                    'message': 'Work order not found.',
                },
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(
            {'success': True, 'data': WorkOrderReadSerializer(work_order).data},
            status=status.HTTP_200_OK,
        )

    @swagger_auto_schema(
        operation_description='Full replacement update for a work order.',
        request_body=WorkOrderWriteSerializer,
        responses={
            200: openapi.Response('Updated'),
            400: 'Validation error',
            401: 'Unauthorized',
            404: 'Not Found',
            409: 'Duplicate Work Order ID',
        },
        security=[{'Bearer': []}],
        tags=['Work Order Management'],
    )
    def put(self, request, work_order_pk):
        return self._update(request, work_order_pk, partial=False)

    @swagger_auto_schema(
        operation_description='Partial update for a work order.',
        request_body=WorkOrderWriteSerializer,
        responses={
            200: openapi.Response('Updated'),
            400: 'Validation error',
            401: 'Unauthorized',
            404: 'Not Found',
            409: 'Duplicate Work Order ID',
        },
        security=[{'Bearer': []}],
        tags=['Work Order Management'],
    )
    def patch(self, request, work_order_pk):
        return self._update(request, work_order_pk, partial=True)

    def _update(self, request, work_order_pk, *, partial: bool):
        work_order = self._load(work_order_pk)
        if work_order is None:
            return Response(
                {
                    'success': False,
                    'error': {
                        'code': 'WORK_ORDER_NOT_FOUND',
                        'message': 'Work order not found.',
                        'details': {},
                    },
                    'message': 'Work order not found.',
                },
                status=status.HTTP_404_NOT_FOUND,
            )

        serializer = WorkOrderWriteSerializer(data=request.data, partial=partial)
        if not serializer.is_valid():
            return _validation_error_response(serializer.errors)

        try:
            work_order = services.update_work_order(
                work_order=work_order,
                validated=serializer.validated_data,
                username=_jwt_username(request),
                partial=partial,
            )
        except ValidationError as exc:
            return _map_service_error(exc)
        except Exception:
            logger.exception(
                'Work order update failed for id=%s user=%s',
                work_order_pk,
                _jwt_username(request),
            )
            return Response(
                {
                    'success': False,
                    'error': {
                        'code': 'SERVER_ERROR',
                        'message': 'Failed to update work order.',
                        'details': {},
                    },
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        return Response(
            {
                'success': True,
                'message': 'Work order updated successfully.',
                'data': WorkOrderReadSerializer(work_order).data,
            },
            status=status.HTTP_200_OK,
        )

    @swagger_auto_schema(
        operation_description='Hard-delete a work order.',
        responses={204: 'Deleted', 401: 'Unauthorized', 404: 'Not Found'},
        security=[{'Bearer': []}],
        tags=['Work Order Management'],
    )
    def delete(self, request, work_order_pk):
        work_order = self._load(work_order_pk)
        if work_order is None:
            return Response(
                {
                    'success': False,
                    'error': {
                        'code': 'WORK_ORDER_NOT_FOUND',
                        'message': 'Work order not found.',
                        'details': {},
                    },
                    'message': 'Work order not found.',
                },
                status=status.HTTP_404_NOT_FOUND,
            )
        services.delete_work_order(work_order=work_order)
        return Response(status=status.HTTP_204_NO_CONTENT)
