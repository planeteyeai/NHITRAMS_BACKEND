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
    DPRReportWriteSerializer,
    DPRReportReadSerializer,
    AutoFetchChainageRequestSerializer,
    resolve_dpr_uploaded_images,
)
from .serializers_inventory_report import (
    InventoryReportWriteSerializer as InventoryRamsWriteSerializer,
    InventoryReportReadSerializer as InventoryRamsReadSerializer,
)
from .authentication import JWTAuthentication
from .permissions import IsJWTAuthenticated
from .services import ProjectDetailsNotFound, get_project_details
from .services.inventory_report import (
    create_inventory_report,
    get_inventory_master,
    get_owned_inventory_report,
    list_inventory_reports,
    update_inventory_report,
)
from .services.chainage_lookup import find_nearest_chainage
from .services.inventory_filter_queryset import build_inventory_filter_queryset
from .services.inventory_filter_response import (
    INVENTORY_FILTER_EXPOSED_HEADERS,
    INVENTORY_FILTER_HEADER_HAS_MORE,
    INVENTORY_FILTER_HEADER_PAGE,
    INVENTORY_FILTER_HEADER_PAGE_SIZE,
    INVENTORY_FILTER_HEADER_PAGINATED,
    INVENTORY_FILTER_HEADER_ROW_COUNT,
    build_inventory_filter_groups,
)
from .utils import apply_media_uploads_to_payload, upload_dpr_images
from nhit.caching import CACHE_NS_INVENTORY_FILTER, CACHE_NS_INVENTORY_MASTER, cached_filter_response, cached_get_response
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
        page = data['page']
        page_size = data['page_size']
        offset = (page - 1) * page_size

        queryset = build_inventory_filter_queryset(data)
        total_count = queryset.count()

        if offset >= total_count:
            return Response({'message': 'No match'}, status=status.HTTP_200_OK)

        def build_response():
            return build_inventory_filter_groups(
                queryset,
                offset=offset,
                limit=page_size,
            )

        response = cached_filter_response(
            namespace=CACHE_NS_INVENTORY_FILTER,
            payload=data,
            builder=build_response,
        )

        has_more = (offset + page_size) < total_count
        response[INVENTORY_FILTER_HEADER_PAGINATED] = 'true'
        response[INVENTORY_FILTER_HEADER_PAGE] = str(page)
        response[INVENTORY_FILTER_HEADER_PAGE_SIZE] = str(page_size)
        response[INVENTORY_FILTER_HEADER_ROW_COUNT] = str(total_count)
        response[INVENTORY_FILTER_HEADER_HAS_MORE] = 'true' if has_more else 'false'
        return response

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


def _extract_report_id(data) -> int | None:
    """Accept `id` or `report_id` from a request body."""
    raw = None
    if hasattr(data, 'get'):
        raw = data.get('id', data.get('report_id'))
    if raw is None or raw == '':
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _mutable_request_data(request) -> dict:
    """Copy request.data into a plain mutable dict (works for JSON and multipart)."""
    data = request.data
    if hasattr(data, 'dict'):
        # QueryDict: keep only the last value per key for scalar fields.
        return {key: data.get(key) for key in data.keys()}
    if hasattr(data, 'copy'):
        copied = data.copy()
        return dict(copied) if not isinstance(copied, dict) else copied
    return dict(data)


def _update_inventory_report_view(request, report, *, partial: bool):
    """Apply PUT/PATCH with optional media uploads. Username remains JWT-owned."""
    mutable = _mutable_request_data(request)
    mutable.pop('id', None)
    mutable.pop('report_id', None)
    mutable.pop('username', None)
    # Project details are immutable after create.
    mutable.pop('project_name', None)
    mutable.pop('project_id', None)
    mutable.pop('start_chainage', None)
    mutable.pop('end_chainage', None)
    mutable.pop('total_project_length', None)

    try:
        apply_media_uploads_to_payload(request, mutable, allow_video=True)
    except ValidationError as exc:
        return Response(
            {'success': False, 'errors': exc.detail if hasattr(exc, 'detail') else {'detail': str(exc)}},
            status=status.HTTP_400_BAD_REQUEST,
        )
    except RuntimeError as exc:
        logger.exception('Inventory report media upload failed')
        return Response(
            {'success': False, 'message': str(exc)},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    serializer = InventoryRamsWriteSerializer(
        report,
        data=mutable,
        partial=partial,
    )
    if not serializer.is_valid():
        return Response(
            {'success': False, 'errors': serializer.errors},
            status=status.HTTP_400_BAD_REQUEST,
        )

    report = update_inventory_report(
        report=report,
        validated=serializer.validated_data,
        partial=partial,
    )
    return Response(
        {
            'success': True,
            'message': 'Data updated successfully',
            'data': InventoryRamsReadSerializer(report).data,
        },
        status=status.HTTP_200_OK,
    )


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

class InventoryMasterView(APIView):
    """GET /api/inventory/master — inventory dropdown masters."""

    authentication_classes = [JWTAuthentication]
    permission_classes = [IsJWTAuthenticated]

    @swagger_auto_schema(
        operation_description=(
            'Return projects, inventory categories/subcategories, side and severity masters.'
        ),
        responses={200: openapi.Response('Master data'), 401: 'Unauthorized'},
        security=[{'Bearer': []}],
        tags=['Inventory Reports'],
    )
    def get(self, request):
        return cached_get_response(
            namespace=CACHE_NS_INVENTORY_MASTER,
            payload={},
            builder=lambda: {'success': True, 'data': get_inventory_master()},
        )


class AppendInventoryExcelView(APIView):
    """POST / PUT / PATCH /append_inventory_excel/

    RAMS APK inventory report create/update.
    PUT/PATCH require `id` (or `report_id`) in the body.
    Reuses existing S3 image/video upload helpers.
    """

    authentication_classes = [JWTAuthentication]
    permission_classes = [IsJWTAuthenticated]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    @swagger_auto_schema(
        operation_description=(
            'Create an inventory report (RAMS APK flow). '
            'Send project_name, inventory_category_id, inventory_subcategory_id, '
            'side_id, severity_id, GPS, chainage, dimensions. '
            'Optional multipart `image`/`images` and `video`/`videos`. '
            'Username is taken from the JWT.'
        ),
        request_body=InventoryRamsWriteSerializer,
        responses={
            200: openapi.Response('Created'),
            400: 'Validation error',
            401: 'Unauthorized — missing or invalid JWT',
            500: 'Upload failure',
        },
        security=[{'Bearer': []}],
        tags=['Inventory Reports'],
    )
    def post(self, request):
        mutable = _mutable_request_data(request)
        mutable.pop('username', None)
        try:
            apply_media_uploads_to_payload(request, mutable, allow_video=True)
        except ValidationError as exc:
            return Response(
                {
                    'success': False,
                    'errors': exc.detail if hasattr(exc, 'detail') else {'detail': str(exc)},
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        except RuntimeError as exc:
            logger.exception('Inventory report media upload failed')
            return Response(
                {'success': False, 'message': str(exc)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        serializer = InventoryRamsWriteSerializer(data=mutable)
        if not serializer.is_valid():
            return Response(
                {'success': False, 'errors': serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )

        report = create_inventory_report(
            username=_jwt_username(request),
            validated=serializer.validated_data,
        )
        return Response(
            {
                'success': True,
                'message': 'Data appended successfully',
                'data': InventoryRamsReadSerializer(report).data,
            },
            status=status.HTTP_200_OK,
        )

    @swagger_auto_schema(
        operation_description=(
            'Full update of an owned inventory report. '
            'Send `id` plus fields to replace. Project details and username are immutable. '
            'Optional multipart image/video uploads.'
        ),
        responses={
            200: openapi.Response('Updated'),
            400: 'Validation error',
            401: 'Unauthorized',
            403: 'Forbidden',
            404: 'Not Found',
            500: 'Upload failure',
        },
        security=[{'Bearer': []}],
        tags=['Inventory Reports'],
    )
    def put(self, request):
        return self._update(request, partial=False)

    @swagger_auto_schema(
        operation_description=(
            'Partial update of an owned inventory report. '
            'Send `id` plus only fields to change. Project details and username are immutable.'
        ),
        responses={
            200: openapi.Response('Updated'),
            400: 'Validation error',
            401: 'Unauthorized',
            403: 'Forbidden',
            404: 'Not Found',
            500: 'Upload failure',
        },
        security=[{'Bearer': []}],
        tags=['Inventory Reports'],
    )
    def patch(self, request):
        return self._update(request, partial=True)

    def _update(self, request, *, partial: bool):
        report_id = _extract_report_id(request.data)
        if report_id is None:
            return Response(
                {'success': False, 'errors': {'id': ['This field is required.']}},
                status=status.HTTP_400_BAD_REQUEST,
            )
        report = _get_owned_inventory_report(request, report_id)
        return _update_inventory_report_view(request, report, partial=partial)


class InventoryReportHistoryView(APIView):
    """GET /inventory/history/ — reports for the JWT username only."""

    authentication_classes = [JWTAuthentication]
    permission_classes = [IsJWTAuthenticated]

    @swagger_auto_schema(
        operation_description='Return inventory reports belonging to the authenticated user, newest first.',
        responses={
            200: InventoryRamsReadSerializer(many=True),
            401: 'Unauthorized',
        },
        security=[{'Bearer': []}],
        tags=['Inventory Reports'],
    )
    def get(self, request):
        reports = list_inventory_reports(_jwt_username(request))
        return Response(
            {
                'success': True,
                'data': InventoryRamsReadSerializer(reports, many=True).data,
            }
        )


class InventoryReportDetailView(APIView):
    """GET / PUT / PATCH / DELETE /inventory/{id}/ — retrieve, update, or delete an owned report."""

    authentication_classes = [JWTAuthentication]
    permission_classes = [IsJWTAuthenticated]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    @swagger_auto_schema(
        operation_description='Retrieve a single inventory report. Returns 403 if owned by another user.',
        responses={
            200: InventoryRamsReadSerializer,
            401: 'Unauthorized',
            403: 'Forbidden',
            404: 'Not Found',
        },
        security=[{'Bearer': []}],
        tags=['Inventory Reports'],
    )
    def get(self, request, report_id):
        report = _get_owned_inventory_report(request, report_id)
        return Response(
            {'success': True, 'data': InventoryRamsReadSerializer(report).data}
        )

    @swagger_auto_schema(
        operation_description=(
            'Full update of an owned inventory report (PUT). '
            'Username and project details are immutable.'
        ),
        request_body=InventoryRamsWriteSerializer,
        responses={
            200: openapi.Response('Updated'),
            400: 'Validation error',
            401: 'Unauthorized',
            403: 'Forbidden',
            404: 'Not Found',
        },
        security=[{'Bearer': []}],
        tags=['Inventory Reports'],
    )
    def put(self, request, report_id):
        report = _get_owned_inventory_report(request, report_id)
        return _update_inventory_report_view(request, report, partial=False)

    @swagger_auto_schema(
        operation_description=(
            'Partial update of an owned inventory report (PATCH). '
            'Send only fields to change. Username and project details are immutable.'
        ),
        request_body=InventoryRamsWriteSerializer,
        responses={
            200: openapi.Response('Updated'),
            400: 'Validation error',
            401: 'Unauthorized',
            403: 'Forbidden',
            404: 'Not Found',
        },
        security=[{'Bearer': []}],
        tags=['Inventory Reports'],
    )
    def patch(self, request, report_id):
        report = _get_owned_inventory_report(request, report_id)
        return _update_inventory_report_view(request, report, partial=True)

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


class AutoFetchChainageView(APIView):
    """POST/GET /api/inventory/autofetch-chainage/ — nearest chainage from GPS."""

    authentication_classes = [JWTAuthentication]
    permission_classes = [IsJWTAuthenticated]

    @swagger_auto_schema(
        operation_description=(
            'Return the nearest road chainage for a GPS coordinate using '
            'KML-imported reference points. Optional project_name filters to one project. '
            'Legacy road_name is accepted for one release cycle.'
        ),
        request_body=AutoFetchChainageRequestSerializer,
        responses={
            200: openapi.Response('Nearest chainage'),
            400: 'Validation error',
            401: 'Unauthorized',
            404: 'No nearby reference points',
        },
        security=[{'Bearer': []}],
        tags=['Inventory Reports'],
    )
    def post(self, request):
        return self._lookup(request.data)

    @swagger_auto_schema(
        operation_description=(
            'Return the nearest road chainage for a GPS coordinate '
            '(query params: latitude, longitude, optional project_name; '
            'legacy road_name also accepted).'
        ),
        manual_parameters=[
            openapi.Parameter(
                'latitude',
                openapi.IN_QUERY,
                type=openapi.TYPE_NUMBER,
                required=True,
            ),
            openapi.Parameter(
                'longitude',
                openapi.IN_QUERY,
                type=openapi.TYPE_NUMBER,
                required=True,
            ),
            openapi.Parameter(
                'project_name',
                openapi.IN_QUERY,
                type=openapi.TYPE_STRING,
                required=False,
            ),
            openapi.Parameter(
                'road_name',
                openapi.IN_QUERY,
                type=openapi.TYPE_STRING,
                required=False,
                description='Deprecated alias for project_name',
            ),
        ],
        responses={
            200: openapi.Response('Nearest chainage'),
            400: 'Validation error',
            401: 'Unauthorized',
            404: 'No nearby reference points',
        },
        security=[{'Bearer': []}],
        tags=['Inventory Reports'],
    )
    def get(self, request):
        return self._lookup(request.query_params)

    def _lookup(self, raw):
        serializer = AutoFetchChainageRequestSerializer(data=raw)
        if not serializer.is_valid():
            return Response(
                {'success': False, 'errors': serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )

        data = serializer.validated_data
        try:
            result = find_nearest_chainage(
                data['latitude'],
                data['longitude'],
                project_name=data.get('project_name'),
            )
        except NotFound as exc:
            return Response(
                {'success': False, 'message': str(exc.detail)},
                status=status.HTTP_404_NOT_FOUND,
            )
        except ValidationError as exc:
            return Response(
                {
                    'success': False,
                    'errors': exc.detail if hasattr(exc, 'detail') else {'detail': str(exc)},
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response({'success': True, 'data': result}, status=status.HTTP_200_OK)
