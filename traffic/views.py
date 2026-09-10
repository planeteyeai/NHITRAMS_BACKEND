from __future__ import annotations

import os
import time

from django.db.models import Q
from django.http import HttpResponse
from django.utils.cache import patch_vary_headers

from drf_yasg.utils import swagger_auto_schema
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from nhit.caching.accept_encoding import accepts_gzip_encoding
from nhit.caching.constants import CACHE_NS_TRAFFIC_FILTER, CACHE_TTL_FILTER
from nhit.caching.traffic_filter_cache import get_cached_traffic_filter_response

from .models import Traffic
from .serializers import TrafficFilterRequestSerializer
from .services.traffic_filter_perf import (
    REQUEST_PERF_ATTR,
    is_traffic_filter_perf_debug_enabled,
    resolve_request_id,
)
from .services.traffic_filter_response import build_traffic_filter_groups


class TrafficFilterView(APIView):
    @swagger_auto_schema(
        operation_description='Filter traffic data based on criteria',
        request_body=TrafficFilterRequestSerializer,
        responses={200: 'Success', 400: 'Bad Request', 500: 'Internal Server Error'},
        tags=['Traffic'],
    )
    def post(self, request):
        # Cheap flag check only when instrumentation is disabled.
        perf_debug = is_traffic_filter_perf_debug_enabled()
        t0 = time.perf_counter() if perf_debug else None
        request_id = resolve_request_id(request) if perf_debug else None
        stage_timings: dict = {} if perf_debug else {}

        serializer = TrafficFilterRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        data = serializer.validated_data

        def build_response():
            queryset = Traffic.objects.select_related('project', 'segment')

            if data['chainage_start'] is not None and data['chainage_end'] is not None:
                queryset = queryset.filter(
                    segment__chainage_start__gte=data['chainage_start'],
                    segment__chainage_end__lte=data['chainage_end'],
                )

            if data['date']:
                queryset = queryset.filter(survey_date=data['date'])

            if data['project_name'] and not any(
                str(name).strip().casefold() == 'all' for name in data['project_name']
            ):
                q_objects = Q()
                for name in data['project_name']:
                    q_objects |= Q(project__project_name__iexact=name)
                queryset = queryset.filter(q_objects)

            if data['direction'] and not any(
                str(value).strip().casefold() == 'all' for value in data['direction']
            ):
                queryset = queryset.filter(segment__direction__in=data['direction'])

            if perf_debug:
                return build_traffic_filter_groups(queryset, timings=stage_timings)
            return build_traffic_filter_groups(queryset)

        accept_gzip = accepts_gzip_encoding(request.META.get('HTTP_ACCEPT_ENCODING'))
        serve_mode = 'gzip_bytes' if accept_gzip else 'json_bytes'

        body, meta = get_cached_traffic_filter_response(
            payload=data,
            builder=build_response,
            timeout=CACHE_TTL_FILTER,
            serve_mode=serve_mode,
        )

        response_path = meta.get('response_path') or 'python_object'
        if response_path == 'direct_gzip_bytes':
            response = HttpResponse(
                body,
                content_type='application/json',
                status=status.HTTP_200_OK,
            )
            response['Content-Encoding'] = 'gzip'
            response['Content-Length'] = str(len(body))
            patch_vary_headers(response, ('Accept-Encoding',))
        elif response_path == 'direct_json_bytes':
            response = HttpResponse(
                body,
                content_type='application/json',
                status=status.HTTP_200_OK,
            )
            response['Content-Length'] = str(len(body))
            patch_vary_headers(response, ('Accept-Encoding',))
        else:
            # Compat path: nested Python via DRF (should be rare for this endpoint).
            response = Response(body, status=status.HTTP_200_OK)

        if perf_debug:
            cache_hit = bool(meta.get('cache_hit'))
            stage_timings['cache_get_ms'] = meta.get('redis_get_ms')
            stage_timings['cache_lookup_ms'] = meta.get('redis_get_ms')
            stage_timings['cache_decode_ms'] = meta.get('decode_ms') or meta.get('extract_ms') or 0
            # Distinguish encode CPU from Redis SET (do not overwrite redis timing).
            if meta.get('encode_ms') is not None:
                stage_timings['encode_ms'] = meta.get('encode_ms')
                # Legacy field retained for existing log consumers; means encode, not SET.
                stage_timings['cache_write_ms'] = meta.get('encode_ms')
            if meta.get('cache_set_ms') is not None:
                stage_timings['cache_set_ms'] = meta.get('cache_set_ms')
                stage_timings['redis_set_ms'] = meta.get('cache_set_ms')
            for key in (
                'cache_set_result',
                'cache_set_ok',
                'cache_set_raw_type',
                'cache_set_exception',
                'lock_acquired',
                'lock_add_result',
                'lock_add_raw_type',
                'lock_add_ms',
                'lock_add_exception',
                'wait_sleep_ms',
                'wait_poll_count',
                'wait_found_entry',
                'cache_delete_result',
                'cache_delete_raw_type',
                'cache_delete_ms',
                'cache_delete_exception',
                'redis_round_trips',
                'namespace_version',
                'cache_key_suffix',
                'response_path',
            ):
                if key in meta and meta.get(key) is not None:
                    stage_timings[key] = meta.get(key)
            if cache_hit and 'python_ms' not in stage_timings:
                stage_timings['python_ms'] = 0
            stage_timings['json_ms'] = meta.get('render_ms') or 0
            if response_path == 'direct_gzip_bytes':
                stage_timings['json_ms'] = 0

            response_bytes = meta.get('http_body_bytes')
            if response_bytes is None and isinstance(body, (bytes, bytearray)):
                response_bytes = len(body)

            perf_payload = {
                'request_id': request_id,
                't0': t0,
                'cache': 'HIT' if cache_hit else 'MISS',
                'rows': None,
                'groups': None,
                'response_bytes': response_bytes,
                'stage_timings': stage_timings,
                'logged': False,
                'response_path': response_path,
                'namespace': CACHE_NS_TRAFFIC_FILTER,
                'worker_pid': os.getpid(),
            }
            django_request = getattr(request, '_request', request)
            setattr(django_request, REQUEST_PERF_ATTR, perf_payload)
            setattr(request, REQUEST_PERF_ATTR, perf_payload)

        return response
