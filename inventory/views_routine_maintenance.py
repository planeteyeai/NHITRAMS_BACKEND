"""Routine Maintenance API views."""

from __future__ import annotations

import logging

from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from .authentication import JWTAuthentication
from .models import RoutineMaintenanceReport
from .permissions import IsJWTAuthenticated
from .serializers_routine_maintenance import (
    RoutineMaintenanceReportReadSerializer,
    RoutineMaintenanceReportWriteSerializer,
)
from .services.routine_maintenance import (
    collect_rm_images,
    create_routine_maintenance_report,
    get_routine_maintenance_master,
    list_inventory_projects,
    list_routine_maintenance_reports,
    update_routine_maintenance_report,
)

logger = logging.getLogger(__name__)


def _jwt_username(request) -> str:
    return request.user.username


class InventoryProjectsListView(APIView):
    """GET /api/inventory/projects — project dropdown for RM (and related forms)."""

    authentication_classes = [JWTAuthentication]
    permission_classes = [IsJWTAuthenticated]

    @swagger_auto_schema(
        operation_description=(
            'Return deduplicated canonical project names from the projects table '
            'for dropdown selection.'
        ),
        responses={200: openapi.Response('Project list'), 401: 'Unauthorized'},
        security=[{'Bearer': []}],
        tags=['Inventory Projects'],
    )
    def get(self, request):
        return Response(
            {
                'success': True,
                'data': list_inventory_projects(),
            },
            status=status.HTTP_200_OK,
        )


class RoutineMaintenanceMasterView(APIView):
    """GET /api/inventory/routine-maintenance/master"""

    authentication_classes = [JWTAuthentication]
    permission_classes = [IsJWTAuthenticated]

    @swagger_auto_schema(
        operation_description=(
            'Return active routine-maintenance dropdown masters, including projects.'
        ),
        responses={200: openapi.Response('Master data'), 401: 'Unauthorized'},
        security=[{'Bearer': []}],
        tags=['Routine Maintenance'],
    )
    def get(self, request):
        return Response(
            {
                'success': True,
                'data': get_routine_maintenance_master(),
            },
            status=status.HTTP_200_OK,
        )


class RoutineMaintenanceReportCreateView(APIView):
    """POST /api/inventory/routine-maintenance/report"""

    authentication_classes = [JWTAuthentication]
    permission_classes = [IsJWTAuthenticated]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    @swagger_auto_schema(
        operation_description=(
            'Create a routine maintenance report. '
            'Send project_name; project details are resolved case-insensitively '
            'from the projects table. Username is taken from the JWT.'
        ),
        responses={
            201: openapi.Response('Created'),
            400: 'Validation error',
            401: 'Unauthorized',
            500: 'Upload/persistence failure',
        },
        security=[{'Bearer': []}],
        tags=['Routine Maintenance'],
    )
    def post(self, request):
        username = _jwt_username(request)
        serializer = RoutineMaintenanceReportWriteSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {'success': False, 'errors': serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )

        image_files = collect_rm_images(request)
        try:
            report = create_routine_maintenance_report(
                username=username,
                validated=serializer.validated_data,
                image_files=image_files,
            )
        except ValidationError as exc:
            return Response(
                {'success': False, 'errors': exc.detail},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except RuntimeError as exc:
            logger.exception('Routine maintenance S3 upload failed for user=%s', username)
            return Response(
                {'success': False, 'message': str(exc)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        except Exception as exc:
            logger.exception('Routine maintenance create failed for user=%s', username)
            return Response(
                {'success': False, 'message': f'Failed to save report: {exc}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        return Response(
            {
                'success': True,
                'message': 'Routine maintenance report created successfully.',
                'data': RoutineMaintenanceReportReadSerializer(report).data,
            },
            status=status.HTTP_201_CREATED,
        )


class RoutineMaintenanceReportDetailView(APIView):
    """GET / PATCH /api/inventory/routine-maintenance/report/{id}/"""

    authentication_classes = [JWTAuthentication]
    permission_classes = [IsJWTAuthenticated]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def _load_report(self, report_id: int):
        try:
            return (
                RoutineMaintenanceReport.objects.select_related(
                    'category', 'subcategory', 'side', 'severity'
                ).get(pk=report_id)
            )
        except RoutineMaintenanceReport.DoesNotExist:
            return None

    @swagger_auto_schema(
        operation_description='Retrieve a single routine maintenance report.',
        responses={
            200: RoutineMaintenanceReportReadSerializer,
            401: 'Unauthorized',
            403: 'Forbidden',
            404: 'Not Found',
        },
        security=[{'Bearer': []}],
        tags=['Routine Maintenance'],
    )
    def get(self, request, report_id):
        report = self._load_report(report_id)
        if report is None:
            return Response(
                {'success': False, 'message': 'Routine maintenance report not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )
        if report.username != _jwt_username(request):
            return Response(
                {
                    'success': False,
                    'message': 'You are not authorized to access this report.',
                },
                status=status.HTTP_403_FORBIDDEN,
            )
        return Response(
            {
                'success': True,
                'data': RoutineMaintenanceReportReadSerializer(report).data,
            },
            status=status.HTTP_200_OK,
        )

    @swagger_auto_schema(
        operation_description=(
            'Partial update of a routine maintenance report (PATCH). '
            'Send only fields to change. Username is immutable. '
            'Optional project_name refreshes stored project snapshot. '
            'Images: use `retain_image_urls` / `keep_images`, `remove_images`, '
            '`clear_images`, and upload via `images` / `new_images`.'
        ),
        responses={
            200: openapi.Response('Updated'),
            400: 'Validation error',
            401: 'Unauthorized',
            403: 'Forbidden',
            404: 'Not Found',
            500: 'Upload or persistence failure',
        },
        security=[{'Bearer': []}],
        tags=['Routine Maintenance'],
    )
    def patch(self, request, report_id):
        report = self._load_report(report_id)
        if report is None:
            return Response(
                {'success': False, 'message': 'Routine maintenance report not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )
        if report.username != _jwt_username(request):
            return Response(
                {
                    'success': False,
                    'message': 'You are not authorized to update this report.',
                },
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = RoutineMaintenanceReportWriteSerializer(
            instance=report,
            data=request.data,
            partial=True,
        )
        if not serializer.is_valid():
            return Response(
                {'success': False, 'errors': serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )

        image_files = collect_rm_images(request)
        try:
            report = update_routine_maintenance_report(
                report=report,
                validated=serializer.validated_data,
                image_files=image_files,
                partial=True,
            )
        except ValidationError as exc:
            return Response(
                {'success': False, 'errors': exc.detail},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except RuntimeError as exc:
            logger.exception(
                'Routine maintenance S3 upload failed for user=%s report=%s',
                report.username,
                report_id,
            )
            return Response(
                {'success': False, 'message': str(exc)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        except Exception as exc:
            logger.exception(
                'Routine maintenance update failed for user=%s report=%s',
                report.username,
                report_id,
            )
            return Response(
                {'success': False, 'message': f'Failed to update report: {exc}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        return Response(
            {
                'success': True,
                'message': 'Report updated successfully.',
                'data': RoutineMaintenanceReportReadSerializer(report).data,
            },
            status=status.HTTP_200_OK,
        )


class RoutineMaintenanceHistoryView(APIView):
    """GET /api/inventory/routine-maintenance/history"""

    authentication_classes = [JWTAuthentication]
    permission_classes = [IsJWTAuthenticated]

    @swagger_auto_schema(
        operation_description=(
            'Return routine maintenance reports for the authenticated user, newest first.'
        ),
        responses={200: RoutineMaintenanceReportReadSerializer(many=True), 401: 'Unauthorized'},
        security=[{'Bearer': []}],
        tags=['Routine Maintenance'],
    )
    def get(self, request):
        reports = list_routine_maintenance_reports(_jwt_username(request))
        return Response(
            {
                'success': True,
                'data': RoutineMaintenanceReportReadSerializer(reports, many=True).data,
            },
            status=status.HTTP_200_OK,
        )
