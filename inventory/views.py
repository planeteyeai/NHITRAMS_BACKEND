import logging
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from drf_yasg.utils import swagger_auto_schema
from drf_yasg import openapi
from .models import DPRReport, InventoryWide, InventoryReport
from .serializers import (
    InventoryFilterSerializer,
    InventoryResponseSerializer,
    InventoryReportWriteSerializer,
    InventoryReportReadSerializer,
    DPRReportWriteSerializer,
    DPRReportReadSerializer,
    resolve_dpr_uploaded_images,
)
from .authentication import JWTAuthentication
from .permissions import IsJWTAuthenticated
from .services import ProjectDetailsNotFound, get_project_details
from .utils import upload_dpr_images
from django.db import connection, transaction
from datetime import datetime

logger = logging.getLogger(__name__)


class InventoryFilterView(APIView):
    @swagger_auto_schema(
        operation_description="Filter inventory data based on criteria",
        request_body=InventoryFilterSerializer,
        responses={200: InventoryResponseSerializer(many=True), 400: 'Bad Request'}
    )
    def post(self, request):
        serializer = InventoryFilterSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        data = serializer.validated_data

        queryset = InventoryWide.objects.all()

        if data['project_name']:
            queryset = queryset.filter(project_name__in=data['project_name'])

        if data['asset_type']:
            queryset = queryset.filter(asset_type__in=data['asset_type'])

        if data['date']:
            queryset = queryset.filter(survey_date=data['date'])

        if data['chainage_start'] is not None:
            queryset = queryset.filter(chainage_start__gte=data['chainage_start'])

        if data['chainage_end'] is not None:
            queryset = queryset.filter(chainage_end__lte=data['chainage_end'])

        directions = data['direction']
        if directions and 'all' not in directions:
            queryset = queryset.filter(direction__in=directions)

        results = []
        for item in queryset.iterator(chunk_size=2000):
            results.append(
                {
                    'project_name': item.project_name,
                    'chainage_start': item.chainage_start,
                    'chainage_end': item.chainage_end,
                    'direction': item.direction,
                    'asset_type': item.asset_type,
                    'trees': item.trees,
                    'culvert': item.culvert,
                    'street_lights': item.street_lights,
                    'bridges': item.bridges,
                    'traffic_signals': item.traffic_signals,
                    'bus_stop': item.bus_stop,
                    'truck_layby': item.truck_layby,
                    'toll_plaza': item.toll_plaza,
                    'adjacent_road': item.adjacent_road,
                    'toilet_blocks': item.toilet_blocks,
                    'rest_area': item.rest_area,
                    'rcc_drain': item.rcc_drain,
                    'fuel_station': item.fuel_station,
                    'emergency_call_box': item.emergency_call_box,
                    'tunnels': item.tunnels,
                    'footpath': item.footpath,
                    'junction': item.junction,
                    'sign_boards': item.sign_boards,
                    'solar_blinker': item.solar_blinker,
                    'median_plants': item.median_plants,
                    'service_road': item.service_road,
                    'km_stones': item.km_stones,
                    'crash_barrier': item.crash_barrier,
                    'median_opening': str(int(item.median_opening or 0)),
                    'kerb': item.kerb,
                    'guard_post': item.guard_post,
                    'delineators': item.delineators,
                    'pedestrian_guard_rail': item.pedestrian_guard_rail,
                    'slip_roads': item.slip_roads,
                    'bus_bays': item.bus_bays,
                    'latitude': item.latitude,
                    'longitude': item.longitude,
                    'date': item.survey_date,
                    'sub_asset_type': item.sub_asset_type,
                    'carriage_type': item.carriage_type or 'Flexible',
                    'lane': item.lane or 'L2',
                    'image_url': item.image_url or '',
                    'source': item.source or '',
                }
            )

        return Response(results, status=status.HTTP_200_OK)

class HealthCheckView(APIView):
    @swagger_auto_schema(
        operation_description="Health check endpoint for monitoring API status",
        responses={200: openapi.Response('Success'), 500: 'Internal Server Error'},
        tags=["Health"]
    )
    def get(self, request):
        try:
            timestamp = datetime.utcnow().isoformat() + "Z"
            return Response({
                "status": "ok",
                "message": "API is running successfully",
                "timestamp": timestamp
            }, status=status.HTTP_200_OK)
        except Exception as e:
            return Response({
                "status": "error",
                "message": f"Health check failed: {str(e)}",
                "timestamp": datetime.utcnow().isoformat() + "Z"
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

class ReadinessCheckView(APIView):
    @swagger_auto_schema(
        operation_description="Readiness check endpoint that verifies database connection",
        responses={200: openapi.Response('Ready'), 503: 'Service Unavailable'},
        tags=["Health"]
    )
    def get(self, request):
        try:
            timestamp = datetime.utcnow().isoformat() + "Z"
            # Check database connection
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
            db_status = "ready"
            overall_status = "ready"
            message = "All systems ready"
            http_status = status.HTTP_200_OK
        except Exception as e:
            db_status = "not ready"
            overall_status = "not ready"
            message = f"Database not ready: {str(e)}"
            http_status = status.HTTP_503_SERVICE_UNAVAILABLE
        
        return Response({
            "status": overall_status,
            "message": message,
            "timestamp": timestamp,
            "database": db_status
        }, status=http_status)


# ─────────────────────────────────────────────
# Inventory reports (PostgreSQL replacement for Excel storage)
# ─────────────────────────────────────────────


def _jwt_username(request) -> str:
    """Extract username exclusively from the authenticated JWT principal."""
    return request.user.username


def _get_owned_inventory_report(request, report_id: int) -> InventoryReport:
    """Fetch an inventory report and enforce ownership (404 / 403)."""
    try:
        report = InventoryReport.objects.get(pk=report_id)
    except InventoryReport.DoesNotExist:
        raise NotFound('Report not found.')

    if report.username != _jwt_username(request):
        raise PermissionDenied('You do not have permission to access this report.')

    return report


def _get_owned_dpr_report(request, report_id: int) -> DPRReport:
    """Fetch a DPR report and enforce ownership (404 / 403)."""
    try:
        report = DPRReport.objects.get(pk=report_id)
    except DPRReport.DoesNotExist:
        raise NotFound('DPR report not found.')

    if report.username != _jwt_username(request):
        raise PermissionDenied('You do not have permission to access this report.')

    return report


def _dpr_report_response_data(report: DPRReport) -> dict:
    return {
        'username': report.username,
        'project_name': report.project_name,
        'start_chainage': report.start_chainage,
        'end_chainage': report.end_chainage,
        'total_project_length': report.total_project_length,
        'report_id': report.id,
        'uploaded_images': report.uploaded_images,
        'overall_summary': report.overall_summary,
        'created_at': report.created_at.isoformat(),
        'updated_at': report.updated_at.isoformat(),
    }

class AppendInventoryExcelView(APIView):
    """POST /append_inventory_excel/

    Backward-compatible replacement for the legacy Excel append API.
    Same URL, same request body field names — stores in PostgreSQL instead.
    """

    authentication_classes = [JWTAuthentication]
    permission_classes = [IsJWTAuthenticated]

    @swagger_auto_schema(
        operation_description=(
            'Append an inventory report. Replaces Excel storage with PostgreSQL. '
            'Request body field names are unchanged for frontend compatibility. '
            'Username is taken from the JWT. Includes Video_URL.'
        ),
        request_body=InventoryReportWriteSerializer,
        responses={
            200: openapi.Response(
                description='Success (legacy-compatible)',
                examples={
                    'application/json': {
                        'message': 'Data appended successfully',
                    }
                },
            ),
            400: 'Validation error',
            401: 'Unauthorized — missing or invalid JWT',
        },
        security=[{'Bearer': []}],
        tags=['Inventory Reports'],
    )
    def post(self, request):
        serializer = InventoryReportWriteSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        serializer.save(username=_jwt_username(request))

        return Response(
            {'message': 'Data appended successfully'},
            status=status.HTTP_200_OK,
        )


class InventoryReportHistoryView(APIView):
    """GET /inventory/history/ — reports for the JWT username only."""

    authentication_classes = [JWTAuthentication]
    permission_classes = [IsJWTAuthenticated]

    @swagger_auto_schema(
        operation_description='Return inventory reports belonging to the authenticated user, newest first.',
        responses={
            200: InventoryReportReadSerializer(many=True),
            401: 'Unauthorized',
        },
        security=[{'Bearer': []}],
        tags=['Inventory Reports'],
    )
    def get(self, request):
        reports = InventoryReport.objects.filter(
            username=_jwt_username(request)
        ).order_by('-created_at')
        return Response(InventoryReportReadSerializer(reports, many=True).data)


class InventoryReportDetailView(APIView):
    """GET / DELETE /inventory/{id}/ — retrieve or delete an owned report."""

    authentication_classes = [JWTAuthentication]
    permission_classes = [IsJWTAuthenticated]

    @swagger_auto_schema(
        operation_description='Retrieve a single inventory report. Returns 403 if owned by another user.',
        responses={
            200: InventoryReportReadSerializer,
            401: 'Unauthorized',
            403: 'Forbidden',
            404: 'Not Found',
        },
        security=[{'Bearer': []}],
        tags=['Inventory Reports'],
    )
    def get(self, request, report_id):
        report = _get_owned_inventory_report(request, report_id)
        return Response(InventoryReportReadSerializer(report).data)

    @swagger_auto_schema(
        operation_description='Delete an inventory report. Returns 403 if owned by another user.',
        responses={
            204: 'Deleted',
            401: 'Unauthorized',
            403: 'Forbidden',
            404: 'Not Found',
        },
        security=[{'Bearer': []}],
        tags=['Inventory Reports'],
    )
    def delete(self, request, report_id):
        report = _get_owned_inventory_report(request, report_id)
        report.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class InventoryPDFExportView(APIView):
    """GET /inventory/export/pdf/ — export owned inventory reports as PDF."""

    authentication_classes = [JWTAuthentication]
    permission_classes = [IsJWTAuthenticated]

    @swagger_auto_schema(
        operation_description=(
            'Export Road Inventory Collection Report PDF for the authenticated user. '
            'Supports ?id=20, ?ids=2,3,9, or all owned reports when omitted. '
            'Includes Video_URL indicator.'
        ),
        manual_parameters=[
            openapi.Parameter('id', openapi.IN_QUERY, type=openapi.TYPE_INTEGER, required=False),
            openapi.Parameter(
                'ids',
                openapi.IN_QUERY,
                type=openapi.TYPE_STRING,
                required=False,
                description='Comma-separated ids, e.g. 2,3,9',
            ),
        ],
        responses={
            200: openapi.Response('PDF file'),
            401: 'Unauthorized',
            403: 'Forbidden',
            404: 'No matching reports',
            500: 'PDF generation failure',
        },
        security=[{'Bearer': []}],
        tags=['Inventory Reports'],
    )
    def get(self, request):
        from pdf.builders import InventoryReportBuilder
        from pdf.utils import (
            TempWorkspace,
            filename_for,
            format_generated_date,
            get_owned_reports,
            parse_export_ids,
            pdf_file_response,
        )

        try:
            report_ids = parse_export_ids(request.query_params)
            records = get_owned_reports(InventoryReport, request.user.username, report_ids)
            if hasattr(records, 'all'):
                records = list(records)

            workspace = TempWorkspace()
            workspace.__enter__()
            try:
                download_name = filename_for('inventory', format_generated_date())
                pdf_path = workspace.make_pdf_path('inventory_')
                InventoryReportBuilder(records, temp_dir=workspace.dir).build(pdf_path)
                return pdf_file_response(pdf_path, download_name, cleanup=workspace.cleanup)
            except Exception:
                workspace.cleanup()
                raise
        except Exception as exc:
            from rest_framework.exceptions import APIException

            if isinstance(exc, APIException):
                raise
            logger.exception('Inventory PDF generation failed')
            return Response(
                {'detail': 'PDF generation failure.', 'error': str(exc)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


# ─────────────────────────────────────────────
# Daily Progress Report (DPR)
# ─────────────────────────────────────────────


def _collect_dpr_images(request):
    """Collect uploaded evidence files from common multipart field names."""
    files = []
    for key in (
        'images',
        'images[]',
        'image',
        'evidence',
        'evidence[]',
        'new_images',
        'new_images[]',
    ):
        files.extend(request.FILES.getlist(key))
    # Deduplicate by object identity while preserving order
    seen = set()
    unique = []
    for f in files:
        marker = id(f)
        if marker not in seen:
            seen.add(marker)
            unique.append(f)
    return unique


def _extra_dpr_report_data(request_data, validated: dict) -> dict:
    """Capture additional multipart fields that are not first-class columns."""
    reserved = {
        'report_date',
        'reporting_engineer',
        'assessment_date',
        'remarks',
        'asset_details',
        'overall_summary',
        'status',
        'keep_images',
        'retain_image_urls',
        'remove_images',
        'clear_images',
        'images',
        'images[]',
        'image',
        'evidence',
        'evidence[]',
        'new_images',
        'new_images[]',
        'username',
        'project_name',
        'start_chainage',
        'end_chainage',
        'total_project_length',
    }
    extra = {}
    if hasattr(request_data, 'keys'):
        for key in request_data.keys():
            if key in reserved or key in validated:
                continue
            value = request_data.get(key)
            if value not in (None, ''):
                extra[key] = value
    return extra


class DPRReportCreateView(APIView):
    """POST /api/inventory/dpr/report — full DPR workflow for the JWT user."""

    authentication_classes = [JWTAuthentication]
    permission_classes = [IsJWTAuthenticated]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    @swagger_auto_schema(
        operation_description=(
            'Create a Daily Progress Report. '
            'Username and project fields are resolved on the server from the JWT. '
            'Upload evidence images via multipart field `images` / `images[]`.'
        ),
        responses={
            201: openapi.Response('DPR created'),
            400: 'Validation error',
            401: 'Unauthorized',
            404: 'No project assignment for user',
            500: 'Upload or persistence failure',
        },
        security=[{'Bearer': []}],
        tags=['Inventory DPR'],
    )
    def post(self, request):
        username = _jwt_username(request)

        serializer = DPRReportWriteSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {'success': False, 'errors': serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            project = get_project_details(username)
        except ProjectDetailsNotFound as exc:
            return Response(
                {'success': False, 'message': str(exc)},
                status=status.HTTP_404_NOT_FOUND,
            )
        except ValueError as exc:
            return Response(
                {'success': False, 'message': str(exc)},
                status=status.HTTP_400_BAD_REQUEST,
            )

        image_files = _collect_dpr_images(request)
        try:
            uploaded_urls = upload_dpr_images(image_files) if image_files else []
        except ValidationError as exc:
            return Response(
                {'success': False, 'errors': exc.detail},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except RuntimeError as exc:
            logger.exception('DPR S3 upload failed for user=%s', username)
            return Response(
                {'success': False, 'message': str(exc)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        validated = serializer.validated_data
        try:
            report = DPRReport.objects.create(
                username=username,
                project_name=project['project_name'],
                start_chainage=project['start_chainage'],
                end_chainage=project['end_chainage'],
                total_project_length=project['total_project_length'],
                report_date=validated['report_date'],
                reporting_engineer=validated['reporting_engineer'],
                assessment_date=validated.get('assessment_date'),
                remarks=validated.get('remarks', ''),
                asset_details=validated.get('asset_details') or {},
                overall_summary=validated.get('overall_summary') or {},
                report_data=_extra_dpr_report_data(request.data, validated),
                uploaded_images=uploaded_urls,
                status=validated.get('status') or DPRReport.STATUS_SUBMITTED,
            )
        except Exception as exc:
            logger.exception('DPR report persistence failed for user=%s', username)
            return Response(
                {'success': False, 'message': f'Failed to save DPR report: {exc}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        return Response(
            {
                'success': True,
                'message': 'DPR report generated successfully.',
                'data': _dpr_report_response_data(report),
            },
            status=status.HTTP_201_CREATED,
        )


class DPRReportDetailView(APIView):
    """GET / PUT / PATCH /api/inventory/dpr/report/{id}/ — owned DPR report."""

    authentication_classes = [JWTAuthentication]
    permission_classes = [IsJWTAuthenticated]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    @swagger_auto_schema(
        operation_description='Retrieve a single DPR report. Returns 403 if owned by another user.',
        responses={
            200: DPRReportReadSerializer,
            401: 'Unauthorized',
            403: 'Forbidden',
            404: 'Not Found',
        },
        security=[{'Bearer': []}],
        tags=['Inventory DPR'],
    )
    def get(self, request, report_id):
        report = _get_owned_dpr_report(request, report_id)
        return Response(
            {
                'success': True,
                'data': DPRReportReadSerializer(report).data,
            },
            status=status.HTTP_200_OK,
        )

    @swagger_auto_schema(
        operation_description=(
            'Full update of a DPR report (PUT). '
            'Username and project fields remain server-owned. '
            'Images: upload `images` to replace (or combine with `keep_images`). '
            'Use `clear_images=true` to remove all images. '
            'Omit image fields to leave existing images unchanged.'
        ),
        responses={
            200: openapi.Response('DPR updated'),
            400: 'Validation error',
            401: 'Unauthorized',
            403: 'Forbidden',
            404: 'Not Found',
            500: 'Upload or persistence failure',
        },
        security=[{'Bearer': []}],
        tags=['Inventory DPR'],
    )
    def put(self, request, report_id):
        return self._update(request, report_id, partial=False)

    @swagger_auto_schema(
        operation_description=(
            'Partial update of a DPR report (PATCH). '
            'Send only fields to change. '
            'Images: new uploads are appended; use `keep_images` / `remove_images` / '
            '`clear_images` to reshape the list.'
        ),
        responses={
            200: openapi.Response('DPR updated'),
            400: 'Validation error',
            401: 'Unauthorized',
            403: 'Forbidden',
            404: 'Not Found',
            500: 'Upload or persistence failure',
        },
        security=[{'Bearer': []}],
        tags=['Inventory DPR'],
    )
    def patch(self, request, report_id):
        return self._update(request, report_id, partial=True)

    def _update(self, request, report_id, *, partial: bool):
        try:
            report = DPRReport.objects.get(pk=report_id)
        except DPRReport.DoesNotExist:
            return Response(
                {'success': False, 'message': 'DPR report not found.'},
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

        serializer = DPRReportWriteSerializer(data=request.data, partial=partial)
        if not serializer.is_valid():
            return Response(
                {'success': False, 'errors': serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )

        image_files = _collect_dpr_images(request)
        validated = serializer.validated_data

        try:
            with transaction.atomic():
                new_urls = upload_dpr_images(image_files) if image_files else []

                if 'report_date' in validated:
                    report.report_date = validated['report_date']
                if 'reporting_engineer' in validated:
                    report.reporting_engineer = validated['reporting_engineer']
                if 'assessment_date' in validated:
                    report.assessment_date = validated['assessment_date']
                if 'remarks' in validated:
                    report.remarks = validated['remarks']
                if 'asset_details' in validated:
                    report.asset_details = validated['asset_details'] or {}
                if 'overall_summary' in validated:
                    report.overall_summary = validated['overall_summary'] or {}
                if 'status' in validated:
                    report.status = validated['status']

                extras = _extra_dpr_report_data(request.data, validated)
                if extras:
                    merged = dict(report.report_data or {})
                    merged.update(extras)
                    report.report_data = merged

                report.uploaded_images = resolve_dpr_uploaded_images(
                    existing=report.uploaded_images,
                    new_urls=new_urls,
                    keep_images=validated['keep_images'] if 'keep_images' in validated else None,
                    remove_images=(
                        validated['remove_images'] if 'remove_images' in validated else None
                    ),
                    clear_images=bool(validated.get('clear_images')),
                    partial=partial,
                    has_new_files=bool(image_files),
                )

                # username + project fields are never updated from the client
                report.save()
        except ValidationError as exc:
            return Response(
                {'success': False, 'errors': exc.detail},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except RuntimeError as exc:
            logger.exception(
                'DPR S3 upload failed for user=%s report=%s',
                report.username,
                report_id,
            )
            return Response(
                {'success': False, 'message': str(exc)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        except Exception as exc:
            logger.exception(
                'DPR report update failed for user=%s report=%s',
                report.username,
                report_id,
            )
            return Response(
                {'success': False, 'message': f'Failed to update DPR report: {exc}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        return Response(
            {
                'success': True,
                'message': 'Report updated successfully.',
                'data': _dpr_report_response_data(report),
            },
            status=status.HTTP_200_OK,
        )


class DPRReportHistoryView(APIView):
    """GET /api/inventory/dpr/history — DPR reports for the JWT username only."""

    authentication_classes = [JWTAuthentication]
    permission_classes = [IsJWTAuthenticated]

    @swagger_auto_schema(
        operation_description=(
            'Return DPR reports belonging to the authenticated user, newest first. '
            'Username is taken from the JWT — never from query parameters.'
        ),
        responses={
            200: DPRReportReadSerializer(many=True),
            401: 'Unauthorized',
        },
        security=[{'Bearer': []}],
        tags=['Inventory DPR'],
    )
    def get(self, request):
        reports = DPRReport.objects.filter(
            username=_jwt_username(request)
        ).order_by('-created_at')
        return Response(
            {
                'success': True,
                'data': DPRReportReadSerializer(reports, many=True).data,
            },
            status=status.HTTP_200_OK,
        )
