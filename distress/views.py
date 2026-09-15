import logging
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from django.db.models import Sum, Avg, Q, Min, Max
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from drf_yasg.utils import swagger_auto_schema
from drf_yasg import openapi
from .models import Distress, Project, DistressType, DistressReport
from .serializers import (
    DistressFilterSerializer,
    DistressReportSerializer,
    ChainageSummarySerializer,
    DistressReportWriteSerializer,
    DistressReportReadSerializer,
)
from .authentication import JWTAuthentication
from .permissions import IsJWTAuthenticated
from inventory.models import Inventory
from inventory.utils import apply_media_uploads_to_payload
from collections import defaultdict
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from .services.distress_repair import (
    annotate_resolved_quantity,
    quantity_breakdown,
    resolved_quantities_by_distress_ids,
)
from .services.filter_helpers import (
    CARRIAGE_TYPE_DROPDOWN,
    apply_carriage_type_filter as _apply_carriage_type_filter,
    json_safe_float as _json_safe_float,
)
from .services.distress_predict_filter_queryset import build_distress_predict_filter_queryset
from .services.distress_predict_filter_response import (
    DISTRESS_PREDICT_HEADER_HAS_MORE,
    DISTRESS_PREDICT_HEADER_PAGE,
    DISTRESS_PREDICT_HEADER_PAGE_SIZE,
    DISTRESS_PREDICT_HEADER_PAGINATED,
    DISTRESS_PREDICT_HEADER_ROW_COUNT,
    build_distress_predict_filter_page,
)
from .services.distress_predict_filter_perf import (
    is_distress_predict_filter_perf_debug_enabled,
    log_distress_predict_filter_performance,
    resolve_request_id,
    should_log_distress_predict_filter_perf,
)
from nhit.caching import CACHE_NS_DISTRESS_REPORT_FILTER, cached_filter_response
from nhit.caching.accept_encoding import accepts_gzip_encoding
from nhit.caching.constants import CACHE_TTL_FILTER
from nhit.caching.distress_predict_filter_count import (
    distress_predict_filter_filter_hash,
    get_cached_distress_predict_filter_count,
)
from nhit.caching.distress_predict_filter_page import get_cached_distress_predict_filter_page
from django.http import HttpResponse
from django.utils.cache import patch_vary_headers
from rest_framework.renderers import JSONRenderer
import time

logger = logging.getLogger(__name__)


class DistressReportFilterView(APIView):
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsJWTAuthenticated]

    @swagger_auto_schema(
        operation_description="Filter distress reports with aggregation",
        request_body=DistressFilterSerializer,
        responses={200: openapi.Response('Success', DistressReportSerializer(many=True)), 400: 'Bad Request'}
    )
    def post(self, request):
        serializer = DistressFilterSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        data = serializer.validated_data

        # Build queryset
        queryset = Distress.objects.select_related('distress_type', 'project', 'segment')

        # Apply filters if provided
        if 'project_name' in data and data['project_name']:
            q_objects = Q()
            for name in data['project_name']:
                q_objects |= Q(project__project_name__iexact=name)
            queryset = queryset.filter(q_objects)

        if 'distress_type' in data and data['distress_type']:
            q_objects = Q()
            for dtype in data['distress_type']:
                q_objects |= Q(distress_type__distress_type_name__iexact=dtype)
            queryset = queryset.filter(q_objects)

        if 'date' in data:
            queryset = queryset.filter(distress_date=data['date'])

        if 'chainage_start' in data or 'chainage_end' in data:
            chainage_filters = Q()
            if 'chainage_start' in data:
                chainage_filters &= Q(segment__chainage_end__gte=data['chainage_start'])
            if 'chainage_end' in data:
                chainage_filters &= Q(segment__chainage_start__lte=data['chainage_end'])
            queryset = queryset.filter(chainage_filters)

        if 'direction' in data and data['direction']:
            queryset = queryset.filter(segment__direction__in=data['direction'])

        logger.debug('Generated SQL: %s', queryset.query)

        results = queryset.values(
            'project__project_name',
            'segment__segment_id',
            'segment__chainage_start',
            'segment__chainage_end',
            'segment__direction',
            'distress_type__distress_type_name',
        ).annotate(
            total_count=Sum('count_val'),
            latitude=Avg('latitude'),
            longitude=Avg('longitude'),
        ).order_by('project__project_name', 'segment__segment_id')

        response_data = []
        for item in results:
            response_data.append({
                'project_name': item['project__project_name'],
                'segment_id': item['segment__segment_id'],
                'chainage_start': _json_safe_float(item['segment__chainage_start']),
                'chainage_end': _json_safe_float(item['segment__chainage_end']),
                'direction': item['segment__direction'],
                'distress_type': item['distress_type__distress_type_name'],
                'total_count': _json_safe_float(item['total_count']),
                'latitude': _json_safe_float(item['latitude']),
                'longitude': _json_safe_float(item['longitude']),
            })

        return Response(response_data, status=status.HTTP_200_OK)

class DistressReportSummaryView(APIView):
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsJWTAuthenticated]

    @swagger_auto_schema(
        operation_description="Get distress summary by type",
        request_body=DistressFilterSerializer,
        responses={200: openapi.Schema(type=openapi.TYPE_OBJECT), 400: 'Bad Request'}
    )
    def post(self, request):
        # For summary, we can apply similar filters, but group only by distress_type
        serializer = DistressFilterSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        data = serializer.validated_data

        queryset = Distress.objects.select_related('distress_type', 'project', 'segment')

        # Apply same filters
        if 'project_name' in data and data['project_name']:
            q_objects = Q()
            for name in data['project_name']:
                q_objects |= Q(project__project_name__iexact=name)
            queryset = queryset.filter(q_objects)

        if 'distress_type' in data and data['distress_type']:
            q_objects = Q()
            for dtype in data['distress_type']:
                q_objects |= Q(distress_type__distress_type_name__iexact=dtype)
            queryset = queryset.filter(q_objects)

        if 'date' in data:
            queryset = queryset.filter(distress_date=data['date'])

        if 'chainage_start' in data or 'chainage_end' in data:
            chainage_filters = Q()
            if 'chainage_start' in data:
                chainage_filters &= Q(segment__chainage_end__gte=data['chainage_start'])
            if 'chainage_end' in data:
                chainage_filters &= Q(segment__chainage_start__lte=data['chainage_end'])
            queryset = queryset.filter(chainage_filters)

        if 'direction' in data and data['direction']:
            queryset = queryset.filter(segment__direction__in=data['direction'])

        # Debug: Log the generated SQL query
        logger.debug(f"Summary Generated SQL: {queryset.query}")

        # Aggregate by distress_type
        results = queryset.values('distress_type__distress_type_name').annotate(
            total_count=Sum('count_val')
        )

        # Build response as dict
        response_data = {
            item['distress_type__distress_type_name']: _json_safe_float(item['total_count'])
            for item in results
        }

        return Response(response_data, status=status.HTTP_200_OK)

class DistressReportFilterNewView(APIView):
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsJWTAuthenticated]

    @swagger_auto_schema(
        operation_description="Filter distress reports and aggregate by type",
        request_body=DistressFilterSerializer,
        responses={200: openapi.Response('Success'), 400: 'Bad Request'}
    )
    def post(self, request):
        serializer = DistressFilterSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        data = serializer.validated_data

        # Convert project_name to project_ids (case-insensitive)
        project_ids = []
        if 'project_name' in data and data['project_name']:
            q_objects = Q()
            for name in data['project_name']:
                q_objects |= Q(project_name__iexact=name)
            project_ids = Project.objects.filter(q_objects).values_list('project_id', flat=True)

        # Convert distress_type to distress_type_ids (case-insensitive)
        distress_type_ids = []
        if 'distress_type' in data and data['distress_type']:
            q_objects = Q()
            for dtype in data['distress_type']:
                q_objects |= Q(distress_type_name__iexact=dtype)
            distress_type_ids = DistressType.objects.filter(q_objects).values_list('distress_type_id', flat=True)

        # Build queryset
        queryset = Distress.objects.select_related('distress_type', 'project', 'segment')

        # Apply filters
        if project_ids:
            queryset = queryset.filter(project_id__in=project_ids)

        if distress_type_ids:
            queryset = queryset.filter(distress_type_id__in=distress_type_ids)

        if 'date' in data:
            queryset = queryset.filter(distress_date=data['date'])

        if 'chainage_start' in data:
            queryset = queryset.filter(segment__chainage_start__gte=data['chainage_start'])

        if 'chainage_end' in data:
            queryset = queryset.filter(segment__chainage_end__lte=data['chainage_end'])

        if 'direction' in data and data['direction']:
            queryset = queryset.filter(segment__direction__in=data['direction'])

        # Aggregate
        results = queryset.values(
            'project__project_name',
            'distress_type__distress_type_name',
            'segment__chainage_start',
            'segment__chainage_end',
            'segment__direction'
        ).annotate(
            total_count=Sum('count_val')
        ).order_by('project__project_name', 'distress_type__distress_type_name')

        # Return as list of dicts (JSON-safe floats)
        response_data = []
        for item in results:
            response_data.append({
                'project__project_name': item['project__project_name'],
                'distress_type__distress_type_name': item['distress_type__distress_type_name'],
                'segment__chainage_start': _json_safe_float(item['segment__chainage_start']),
                'segment__chainage_end': _json_safe_float(item['segment__chainage_end']),
                'segment__direction': item['segment__direction'],
                'total_count': _json_safe_float(item['total_count']),
            })
        return Response(response_data, status=status.HTTP_200_OK)


class DistressFullResponseView(APIView):
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsJWTAuthenticated]

    @swagger_auto_schema(
        operation_description=(
            "Filter distress reports and return full response with all fields. "
            "Use direction/distress_type/project_name/carriage_type value \"All\" to skip that filter."
        ),
        request_body=DistressFilterSerializer,
        responses={
            200: openapi.Response(
                description='Success — wrapped list of distress records',
                examples={
                    'application/json': [[
                        {
                            'distress_id': 73,
                            'latitude': 24.55851,
                            'longitude': 72.84584,
                            'chainage_start': 659.9,
                            'chainage_end': 660.0,
                            'project_name': 'Abu Road to Swaroopganj',
                            'distress_type': 'Pothole',
                            'direction': 'Increasing',
                            'total_distress': 1.0,
                            'pothole': 1.0,
                            'date': '2025-04-20',
                            'length': 0.0,
                            'area': 0.0,
                            'carriage_type': 'Flexible',
                            'width': 0.0,
                            'depth': 0.0,
                            'lane': 'L1',
                            'image_url': '',
                        }
                    ]]
                },
            ),
            400: 'Bad Request',
            401: 'Unauthorized',
        },
        security=[{'Bearer': []}],
        tags=['Distress'],
    )
    def post(self, request):
        serializer = DistressFilterSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        data = serializer.validated_data

        distress_type_fields = [
            'pothole', 'oblique_crack', 'edge_break', 'patching', 'bleeding', 'hotspots',
            'alligator_crack', 'roughness', 'repair', 'block_crack', 'rutting',
            'longitudinal_crack', 'raveling', 'single_discrete_crack', 'multiple_cracks',
            'joint_crack', 'joint_seal_defects', 'punchout', 'slippage', 'heaves',
            'simple_crack', 'transverse_crack', 'hairline_crack', 'hungry_surface',
            'settlement', 'shoving', 'stripping',
            'faulting_(stepping)_in_cracks', 'popout_(small_hole)',
            'single_longitudinal_crack', 'blowup_or_bulking',
            'polished_surface/_glazing', 'depression',
        ]

        def build_response():
            queryset = Distress.objects.select_related('project', 'distress_type', 'segment')

            if (
                'project_name' in data
                and data['project_name']
                and not any(p.lower() == 'all' for p in data['project_name'])
            ):
                q_objects = Q()
                for name in data['project_name']:
                    q_objects |= Q(project__project_name__iexact=name)
                queryset = queryset.filter(q_objects)

            if (
                'distress_type' in data
                and data['distress_type']
                and not any(dt.lower() == 'all' for dt in data['distress_type'])
            ):
                q_objects = Q()
                for dtype in data['distress_type']:
                    q_objects |= Q(distress_type__distress_type_name__iexact=dtype)
                queryset = queryset.filter(q_objects)

            if 'date' in data:
                queryset = queryset.filter(distress_date=data['date'])

            if 'chainage_start' in data or 'chainage_end' in data:
                chainage_filters = Q()
                if 'chainage_start' in data:
                    chainage_filters &= Q(segment__chainage_end__gte=data['chainage_start'])
                if 'chainage_end' in data:
                    chainage_filters &= Q(segment__chainage_start__lte=data['chainage_end'])
                queryset = queryset.filter(chainage_filters)

            if (
                'direction' in data
                and data['direction']
                and not any(d.lower() == 'all' for d in data['direction'])
            ):
                queryset = queryset.filter(segment__direction__in=data['direction'])

            queryset = _apply_carriage_type_filter(queryset, data)
            queryset = annotate_resolved_quantity(queryset)

            final_data = []
            rows = list(queryset)
            for obj in rows:
                distress_type_name = obj.distress_type.distress_type_name.lower().replace('_', ' ')
                breakdown = quantity_breakdown(
                    obj.count_val,
                    obj.resolved_quantity_db,
                )
                display_qty = breakdown['remaining_quantity']
                item = {
                    'distress_id': obj.distress_id,
                    'latitude': _json_safe_float(obj.latitude),
                    'longitude': _json_safe_float(obj.longitude),
                    'chainage_start': _json_safe_float(obj.segment.chainage_start),
                    'chainage_end': _json_safe_float(obj.segment.chainage_end),
                    'project_name': obj.project.project_name,
                    'distress_type': obj.distress_type.distress_type_name.title(),
                    'direction': obj.segment.direction,
                    'total_distress': display_qty,
                    'predicted_quantity': breakdown['predicted_quantity'],
                    'resolved_quantity': breakdown['resolved_quantity'],
                    'remaining_quantity': breakdown['remaining_quantity'],
                    'length': _json_safe_float(obj.length_val),
                    'area': _json_safe_float(obj.area_val),
                    'carriage_type': obj.segment.carriage_type or '',
                    'width': _json_safe_float(obj.width_val),
                    'depth': _json_safe_float(obj.depth_val),
                    'lane': obj.segment.lane or '',
                    'date': str(obj.distress_date) if obj.distress_date else None,
                    'image_url': '',
                }
                for field in distress_type_fields:
                    field_normalized = field.lower().replace('_', ' ')
                    item[field] = display_qty if distress_type_name == field_normalized else 0
                final_data.append(item)
            return [final_data]

        return cached_filter_response(
            namespace=CACHE_NS_DISTRESS_REPORT_FILTER,
            payload=data,
            builder=build_response,
        )


class DistressPredictFilterView(APIView):
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsJWTAuthenticated]

    @swagger_auto_schema(
        operation_description="Filter distress predictions with exact FastAPI logic",
        request_body=DistressFilterSerializer,
        responses={200: openapi.Response('Success'), 400: 'Bad Request'}
    )
    def post(self, request):
        request_started = time.perf_counter()
        request_id = resolve_request_id(request)

        serializer = DistressFilterSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        data = serializer.validated_data
        page = data['page']
        page_size = data['page_size']
        offset = (page - 1) * page_size
        filter_hash = distress_predict_filter_filter_hash(data)

        queryset = build_distress_predict_filter_queryset(data)

        count_ms = None

        def count_builder():
            nonlocal count_ms
            started = time.perf_counter()
            value = queryset.count()
            count_ms = round((time.perf_counter() - started) * 1000, 2)
            return value

        total_count, count_meta = get_cached_distress_predict_filter_count(
            payload=data,
            builder=count_builder,
        )

        if offset >= total_count:
            return Response({'message': 'No match'}, status=status.HTTP_200_OK)

        page_timings: dict = {}

        def build_response():
            return build_distress_predict_filter_page(
                queryset,
                offset=offset,
                limit=page_size,
                timings=page_timings,
            )

        accept_gzip = accepts_gzip_encoding(request.META.get('HTTP_ACCEPT_ENCODING'))
        serve_mode = 'gzip_bytes' if accept_gzip else 'json_bytes'

        page_body, page_meta = get_cached_distress_predict_filter_page(
            payload=data,
            builder=build_response,
            timeout=CACHE_TTL_FILTER,
            serve_mode=serve_mode,
        )

        response_path = page_meta.get('response_path') or 'python_object'
        if response_path == 'direct_gzip_bytes':
            response = HttpResponse(
                page_body,
                content_type='application/json',
                status=status.HTTP_200_OK,
            )
            response['Content-Encoding'] = 'gzip'
            response['Content-Length'] = str(len(page_body))
            patch_vary_headers(response, ('Accept-Encoding',))
        elif response_path == 'direct_json_bytes':
            response = HttpResponse(
                page_body,
                content_type='application/json',
                status=status.HTTP_200_OK,
            )
            response['Content-Length'] = str(len(page_body))
            patch_vary_headers(response, ('Accept-Encoding',))
        else:
            response = Response(page_body, status=status.HTTP_200_OK)

        has_more = (offset + page_size) < total_count
        response[DISTRESS_PREDICT_HEADER_PAGINATED] = 'true'
        response[DISTRESS_PREDICT_HEADER_PAGE] = str(page)
        response[DISTRESS_PREDICT_HEADER_PAGE_SIZE] = str(page_size)
        response[DISTRESS_PREDICT_HEADER_ROW_COUNT] = str(total_count)
        response[DISTRESS_PREDICT_HEADER_HAS_MORE] = 'true' if has_more else 'false'

        total_ms = round((time.perf_counter() - request_started) * 1000, 2)
        count_hit = bool(count_meta.get('cache_hit'))
        page_hit = bool(page_meta.get('cache_hit'))
        query_count = (0 if count_hit else 1) + (0 if page_hit else 1)

        if should_log_distress_predict_filter_perf(total_ms):
            if response_path == 'direct_gzip_bytes':
                render_ms = 0
                response_bytes = int(page_meta.get('http_body_bytes') or len(page_body))
                http_gzip = 'passthrough'
            elif response_path == 'direct_json_bytes':
                render_ms = page_meta.get('render_ms')
                if render_ms is None and page_hit:
                    render_ms = 0
                response_bytes = int(page_meta.get('http_body_bytes') or len(page_body))
                http_gzip = 'skipped'
            else:
                render_started = time.perf_counter()
                response_bytes = len(JSONRenderer().render(page_body))
                render_ms = round((time.perf_counter() - render_started) * 1000, 2)
                http_gzip = 'middleware'
            log_fields = {
                'request_id': request_id,
                'endpoint': '/distress_predict_filter/',
                'page': page,
                'page_size': page_size,
                'filter_hash': filter_hash,
                'count_cache_hit': count_hit,
                'cache_hit': page_hit,
                'cache_format': page_meta.get('cache_repr'),
                'response_path': response_path,
                'accept_gzip': accept_gzip,
                'count_ms': count_ms,
                'select_ms': page_timings.get('select_ms'),
                'repair_lookup_ms': page_timings.get('repair_lookup_ms'),
                'serialize_ms': page_timings.get('serialize_ms'),
                'build_ms': page_meta.get('build_ms'),
                'redis_get_ms': page_meta.get('redis_get_ms'),
                'cache_decode_ms': page_meta.get('decode_ms') or page_meta.get('extract_ms'),
                'render_ms': render_ms,
                'http_gzip': http_gzip,
                'total_ms': total_ms,
                'server_total_ms': total_ms,
                'query_count': query_count,
                'response_bytes': response_bytes,
                'content_length': response.get('Content-Length'),
            }
            if is_distress_predict_filter_perf_debug_enabled():
                log_fields.update({
                    'count_version_get_ms': count_meta.get('version_get_ms'),
                    'page_version_get_ms': page_meta.get('version_get_ms'),
                    'page_data_get_ms': page_meta.get('page_data_get_ms') or page_meta.get('redis_get_ms'),
                    'redis_payload_bytes': page_meta.get('redis_payload_bytes'),
                })
            log_distress_predict_filter_performance(log_fields)

        return response


class DistressCarriageTypeMasterView(APIView):
    """GET /api/distress/carriage-types/ — dropdown values for AI Identifier."""

    authentication_classes = [JWTAuthentication]
    permission_classes = [IsJWTAuthenticated]

    @swagger_auto_schema(
        operation_description='Carriage type dropdown values (Rigid, Flexible).',
        responses={200: openapi.Response('Success'), 401: 'Unauthorized'},
        security=[{'Bearer': []}],
        tags=['Distress'],
    )
    def get(self, request):
        return Response(
            {'success': True, 'data': CARRIAGE_TYPE_DROPDOWN},
            status=status.HTTP_200_OK,
        )


@api_view(["GET"])
@authentication_classes([JWTAuthentication])
@permission_classes([IsJWTAuthenticated])
def project_dates(request, dataset):
    # Dataset mapping
    dataset_map = {
        "distress_reported": (Distress, "distress_date"),
        "distress_predicted": (Distress, "distress_date"),
        "inventory": (Inventory, "survey_date"),
        "inventory2": (Inventory, "survey_date"),
    }

    if dataset not in dataset_map:
        return Response({"error": "Invalid dataset"}, status=400)

    model, date_field = dataset_map[dataset]

    # Fetch data
    data = model.objects.select_related("project").values(
        "project__project_name", date_field
    )

    result = defaultdict(set)

    for row in data:
        project = row["project__project_name"]
        date = row[date_field]

        if project and date:
            result[project].add(str(date))

    # Convert set → sorted list
    final_response = {
        project: sorted(list(dates))
        for project, dates in result.items()
    }

    return Response(final_response)


class ChainageSummaryView(APIView):
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsJWTAuthenticated]

    @swagger_auto_schema(
        operation_description="Get chainage summary grouped by project",
        request_body=ChainageSummarySerializer,
        responses={200: openapi.Response('Success'), 400: 'Bad Request'}
    )
    def post(self, request):
        serializer = ChainageSummarySerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        data = serializer.validated_data
        dataset = data['dataset']
        project_name = data.get('project_name')

        dataset_map = {
            "distress_predicted": Distress,
            "distress_predicted2": Distress,
            "distress_reported": Distress,
            "distress_reported2": Distress,
            "inventory": Inventory,
            "inventory2": Inventory,
            "pis": Inventory,
            "pis2": Inventory,
            "tis": Inventory,
            "tis2": Inventory,
            "pms": Inventory,
            "pms2": Inventory,
            "rwfis": Inventory,
            "rwfis2": Inventory,
            "ais": Inventory,
            "ais2": Inventory,
            "bms": Inventory,
        }

        if dataset not in dataset_map:
            return Response({"error": "Invalid dataset"}, status=400)

        model = dataset_map[dataset]

        queryset = model.objects.select_related("project")

        # Optional project filter
        if project_name:
            queryset = queryset.filter(project__project_name__iexact=project_name)

        # Group and aggregate
        summary = queryset.values("project__project_name").annotate(
            start_first=Min("segment__chainage_start"),
            end_first=Min("segment__chainage_end"),
            start_last=Max("segment__chainage_start"),
            end_last=Max("segment__chainage_end"),
        )

        result = {}

        for row in summary:
            project = row["project__project_name"]

            result[project] = {
                "Start First": row["start_first"] or 0,
                "End First": row["end_first"] or 0,
                "Start Last": row["start_last"] or 0,
                "End Last": row["end_last"] or 0,
            }

        # Dataset title formatting
        dataset_title = dataset.replace("_", " ").title()

        return Response({
            dataset_title: result
        })


# ─────────────────────────────────────────────
# Distress reports (PostgreSQL replacement for Excel storage)
# ─────────────────────────────────────────────


def _jwt_username(request) -> str:
    """Extract username exclusively from the authenticated JWT principal."""
    return request.user.username


def _get_owned_distress_report(request, report_id: int) -> DistressReport:
    """Fetch a distress report and enforce ownership (404 / 403)."""
    try:
        report = DistressReport.objects.get(pk=report_id)
    except DistressReport.DoesNotExist:
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
        return {key: data.get(key) for key in data.keys()}
    if hasattr(data, 'copy'):
        copied = data.copy()
        return dict(copied) if not isinstance(copied, dict) else copied
    return dict(data)


def _update_distress_report(request, report, *, partial: bool):
    """Apply PUT/PATCH updates. Username remains JWT-owned."""
    mutable = _mutable_request_data(request)
    mutable.pop('id', None)
    mutable.pop('report_id', None)
    mutable.pop('username', None)

    try:
        apply_media_uploads_to_payload(request, mutable, allow_video=False)
    except ValidationError as exc:
        return Response(
            exc.detail if hasattr(exc, 'detail') else {'detail': str(exc)},
            status=status.HTTP_400_BAD_REQUEST,
        )
    except RuntimeError as exc:
        logger.exception('Distress report media upload failed')
        return Response(
            {'message': str(exc)},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    serializer = DistressReportWriteSerializer(
        report,
        data=mutable,
        partial=partial,
    )
    if not serializer.is_valid():
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    serializer.save()  # username never accepted from client
    report.refresh_from_db()
    return Response(
        {
            'message': 'Data updated successfully',
            'data': DistressReportReadSerializer(report).data,
        },
        status=status.HTTP_200_OK,
    )


class AppendDistressReportedExcelView(APIView):
    """POST / PUT / PATCH /append_distressReported_excel/

    Backward-compatible replacement for the legacy Excel append API.
    Same URL, same request body field names — stores in PostgreSQL instead.
    PUT/PATCH require `id` (or `report_id`) in the body.
    Supports multipart image uploads (`image` / `images`) to S3.
    """

    authentication_classes = [JWTAuthentication]
    permission_classes = [IsJWTAuthenticated]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    @swagger_auto_schema(
        operation_description=(
            'Append a distress report. Replaces Excel storage with PostgreSQL. '
            'Request body field names are unchanged for frontend compatibility. '
            'Username is taken from the JWT. '
            'Carriage_Type is required (Rigid or Flexible). '
            'Optional multipart file: `image`/`images` (uploaded to S3; stored as Image_URL).'
        ),
        request_body=DistressReportWriteSerializer,
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
            500: 'Upload failure',
        },
        security=[{'Bearer': []}],
        tags=['Distress Reports'],
    )
    def post(self, request):
        mutable = _mutable_request_data(request)
        mutable.pop('username', None)
        try:
            apply_media_uploads_to_payload(request, mutable, allow_video=False)
        except ValidationError as exc:
            return Response(
                exc.detail if hasattr(exc, 'detail') else {'detail': str(exc)},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except RuntimeError as exc:
            logger.exception('Distress report media upload failed')
            return Response(
                {'message': str(exc)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        serializer = DistressReportWriteSerializer(data=mutable)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        serializer.save(username=_jwt_username(request))

        # Legacy Excel API returned a simple success message with HTTP 200.
        return Response(
            {'message': 'Data appended successfully'},
            status=status.HTTP_200_OK,
        )

    @swagger_auto_schema(
        operation_description=(
            'Full update of an owned distress report. '
            'Send `id` (or `report_id`) plus all required report fields. '
            'Optional multipart `image`/`images` replaces Image_URL. '
            'Username is taken from the JWT and cannot be changed.'
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
        tags=['Distress Reports'],
    )
    def put(self, request):
        return self._update(request, partial=False)

    @swagger_auto_schema(
        operation_description=(
            'Partial update of an owned distress report. '
            'Send `id` (or `report_id`) plus only the fields to change. '
            'Optional multipart `image`/`images` replaces Image_URL. '
            'Username is taken from the JWT and cannot be changed.'
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
        tags=['Distress Reports'],
    )
    def patch(self, request):
        return self._update(request, partial=True)

    def _update(self, request, *, partial: bool):
        report_id = _extract_report_id(request.data)
        if report_id is None:
            return Response(
                {'id': ['This field is required.']},
                status=status.HTTP_400_BAD_REQUEST,
            )
        report = _get_owned_distress_report(request, report_id)
        return _update_distress_report(request, report, partial=partial)


class DistressReportHistoryView(APIView):
    """GET /distress/history/ — reports for the JWT username only."""

    authentication_classes = [JWTAuthentication]
    permission_classes = [IsJWTAuthenticated]

    @swagger_auto_schema(
        operation_description='Return distress reports belonging to the authenticated user, newest first.',
        responses={
            200: DistressReportReadSerializer(many=True),
            401: 'Unauthorized',
        },
        security=[{'Bearer': []}],
        tags=['Distress Reports'],
    )
    def get(self, request):
        reports = DistressReport.objects.filter(
            username=_jwt_username(request)
        ).order_by('-created_at')
        return Response(DistressReportReadSerializer(reports, many=True).data)


class DistressReportDetailView(APIView):
    """GET / PUT / PATCH / DELETE /distress/{id}/ — retrieve, update, or delete an owned report."""

    authentication_classes = [JWTAuthentication]
    permission_classes = [IsJWTAuthenticated]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    @swagger_auto_schema(
        operation_description='Retrieve a single distress report. Returns 403 if owned by another user.',
        responses={
            200: DistressReportReadSerializer,
            401: 'Unauthorized',
            403: 'Forbidden',
            404: 'Not Found',
        },
        security=[{'Bearer': []}],
        tags=['Distress Reports'],
    )
    def get(self, request, report_id):
        report = _get_owned_distress_report(request, report_id)
        return Response(DistressReportReadSerializer(report).data)

    @swagger_auto_schema(
        operation_description=(
            'Full update of an owned distress report (PUT). '
            'Username is immutable and taken from the JWT.'
        ),
        request_body=DistressReportWriteSerializer,
        responses={
            200: openapi.Response('Updated'),
            400: 'Validation error',
            401: 'Unauthorized',
            403: 'Forbidden',
            404: 'Not Found',
        },
        security=[{'Bearer': []}],
        tags=['Distress Reports'],
    )
    def put(self, request, report_id):
        report = _get_owned_distress_report(request, report_id)
        return _update_distress_report(request, report, partial=False)

    @swagger_auto_schema(
        operation_description=(
            'Partial update of an owned distress report (PATCH). '
            'Send only fields to change. Username is immutable.'
        ),
        request_body=DistressReportWriteSerializer,
        responses={
            200: openapi.Response('Updated'),
            400: 'Validation error',
            401: 'Unauthorized',
            403: 'Forbidden',
            404: 'Not Found',
        },
        security=[{'Bearer': []}],
        tags=['Distress Reports'],
    )
    def patch(self, request, report_id):
        report = _get_owned_distress_report(request, report_id)
        return _update_distress_report(request, report, partial=True)

    @swagger_auto_schema(
        operation_description='Delete a distress report. Returns 403 if owned by another user.',
        responses={
            204: 'Deleted',
            401: 'Unauthorized',
            403: 'Forbidden',
            404: 'Not Found',
        },
        security=[{'Bearer': []}],
        tags=['Distress Reports'],
    )
    def delete(self, request, report_id):
        report = _get_owned_distress_report(request, report_id)
        report.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class DistressPDFExportView(APIView):
    """GET /distress/export/pdf/ — export owned distress reports as PDF."""

    authentication_classes = [JWTAuthentication]
    permission_classes = [IsJWTAuthenticated]

    @swagger_auto_schema(
        operation_description=(
            'Export Road Distress Analysis Report PDF for the authenticated user. '
            'Supports ?id=15, ?ids=5,7,10, or all owned reports when omitted.'
        ),
        manual_parameters=[
            openapi.Parameter('id', openapi.IN_QUERY, type=openapi.TYPE_INTEGER, required=False),
            openapi.Parameter(
                'ids',
                openapi.IN_QUERY,
                type=openapi.TYPE_STRING,
                required=False,
                description='Comma-separated ids, e.g. 5,7,10',
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
        tags=['Distress Reports'],
    )
    def get(self, request):
        from pdf.builders import DistressReportBuilder
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
            records = get_owned_reports(DistressReport, request.user.username, report_ids)
            if hasattr(records, 'all'):
                records = list(records)

            workspace = TempWorkspace()
            workspace.__enter__()
            try:
                download_name = filename_for('distress', format_generated_date())
                pdf_path = workspace.make_pdf_path('distress_')
                DistressReportBuilder(records, temp_dir=workspace.dir).build(pdf_path)
                return pdf_file_response(pdf_path, download_name, cleanup=workspace.cleanup)
            except Exception:
                workspace.cleanup()
                raise
        except Exception as exc:
            from rest_framework.exceptions import APIException

            if isinstance(exc, APIException):
                raise
            logger.exception('Distress PDF generation failed')
            return Response(
                {'detail': 'PDF generation failure.', 'error': str(exc)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class DistressPDFAsyncExportView(APIView):
    """POST /distress/export/pdf/async/ — queue a multi-report PDF export job."""

    authentication_classes = [JWTAuthentication]
    permission_classes = [IsJWTAuthenticated]

    @swagger_auto_schema(
        operation_description=(
            'Queue an asynchronous Road Distress Analysis Report PDF export. '
            'Returns immediately with a job_id; poll the status endpoint for progress '
            'and a download URL. The existing GET /distress/export/pdf/ remains '
            'available for synchronous small exports.'
        ),
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            required=['ids'],
            properties={
                'ids': openapi.Schema(
                    type=openapi.TYPE_ARRAY,
                    items=openapi.Items(type=openapi.TYPE_INTEGER),
                    description='Report ids to include (ownership enforced).',
                ),
            },
        ),
        responses={
            202: openapi.Response('Export job queued'),
            400: 'Invalid ids',
            401: 'Unauthorized',
            403: 'Forbidden',
            404: 'No matching reports',
        },
        security=[{'Bearer': []}],
        tags=['Distress Reports'],
    )
    def post(self, request):
        from .export_jobs import create_export_job, serialize_job

        ids = request.data.get('ids', None)
        job = create_export_job(username=request.user.username, ids=ids)
        return Response(serialize_job(job), status=status.HTTP_202_ACCEPTED)


class DistressPDFExportJobStatusView(APIView):
    """GET /distress/export/pdf/jobs/<job_id>/status/ — job progress + download URL."""

    authentication_classes = [JWTAuthentication]
    permission_classes = [IsJWTAuthenticated]

    @swagger_auto_schema(
        operation_description=(
            'Return export job status. Progress is derived from '
            '(completed_reports + failed_reports) / total_reports. '
            'When completed, download_url is a freshly generated S3 presigned URL.'
        ),
        responses={
            200: openapi.Response('Job status'),
            401: 'Unauthorized',
            403: 'Forbidden',
            404: 'Job not found',
        },
        security=[{'Bearer': []}],
        tags=['Distress Reports'],
    )
    def get(self, request, job_id):
        from .export_jobs import get_owned_job, serialize_job

        job = get_owned_job(username=request.user.username, job_id=job_id)
        return Response(serialize_job(job), status=status.HTTP_200_OK)

