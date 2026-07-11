from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.exceptions import NotFound, PermissionDenied
from drf_yasg.utils import swagger_auto_schema
from drf_yasg import openapi
from .models import Inventory, RoadSegment, InventoryReport
from .serializers import (
    InventoryFilterSerializer,
    InventoryResponseSerializer,
    InventoryReportWriteSerializer,
    InventoryReportReadSerializer,
)
from .authentication import JWTAuthentication
from .permissions import IsJWTAuthenticated
from django.db import connection
from datetime import datetime

ASSET_TYPE_MAPPING = {
    'Trees': 'trees',
    'Culvert': 'culvert',
    'Street Lights': 'street_lights',
    'Bridges': 'bridges',
    'Traffic Signals': 'traffic_signals',
    'Bus Stop': 'bus_stop',
    'Truck Layby': 'truck_layby',
    'Toll Plaza': 'toll_plaza',
    'Adjacent Road': 'adjacent_road',
    'Toilet Blocks': 'toilet_blocks',
    'Rest Area': 'rest_area',
    'RCC Drain': 'rcc_drain',
    'Fuel Station': 'fuel_station',
    'Emergency Call Box': 'emergency_call_box',
    'Tunnels': 'tunnels',
    'Footpath': 'footpath',
    'Junction': 'junction',
    'Sign Boards': 'sign_boards',
    'Solar Blinker': 'solar_blinker',
    'Median Plants': 'median_plants',
    'Service Road': 'service_road',
    'KM Stones': 'km_stones',
    'Crash Barrier': 'crash_barrier',
    'Median Opening': 'median_opening'
}

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

        queryset = Inventory.objects.select_related('segment', 'project', 'asset_type', 'sub_asset_type')

        if data['project_name']:
            queryset = queryset.filter(project__project_name__in=data['project_name'])

        if data['asset_type']:
            queryset = queryset.filter(asset_type__asset_type_name__in=data['asset_type'])

        if data['date']:
            queryset = queryset.filter(survey_date=data['date'])

        if data['chainage_start'] is not None:
            queryset = queryset.filter(segment__chainage_start__gte=data['chainage_start'])

        if data['chainage_end'] is not None:
            queryset = queryset.filter(segment__chainage_end__lte=data['chainage_end'])

        directions = data['direction']
        if directions and 'all' not in directions:
            queryset = queryset.filter(segment__direction__in=directions)

        results = []
        for item in queryset:
            asset_field = ASSET_TYPE_MAPPING.get(item.asset_type.asset_type_name)
            if not asset_field:
                continue

            response_data = {
                'project_name': item.project.project_name,
                'chainage_start': item.segment.chainage_start,
                'chainage_end': item.segment.chainage_end,
                'direction': item.segment.direction,
                'asset_type': item.asset_type.asset_type_name,
                'trees': 0.0,
                'culvert': 0.0,
                'street_lights': 0.0,
                'bridges': 0.0,
                'traffic_signals': 0.0,
                'bus_stop': 0.0,
                'truck_layby': 0.0,
                'toll_plaza': 0.0,
                'adjacent_road': 0.0,
                'toilet_blocks': 0.0,
                'rest_area': 0.0,
                'rcc_drain': 0.0,
                'fuel_station': 0.0,
                'emergency_call_box': 0.0,
                'tunnels': 0.0,
                'footpath': 0.0,
                'junction': 0.0,
                'sign_boards': 0.0,
                'solar_blinker': 0.0,
                'median_plants': 0.0,
                'service_road': 0.0,
                'km_stones': 0.0,
                'crash_barrier': 0.0,
                'median_opening': '0',
                'latitude': item.latitude,
                'longitude': item.longitude,
                'date': item.survey_date,
                'sub_asset_type': item.sub_asset_type.sub_asset_type_name if item.sub_asset_type else None,
                'carriage_type': 'Flexible',
                'lane': 'L2'
            }

            if asset_field == 'median_opening':
                response_data[asset_field] = str(int(item.count_val))
            else:
                response_data[asset_field] = item.count_val

            results.append(response_data)

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
