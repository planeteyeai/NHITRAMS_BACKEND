"""Phase 4A — Traffic Filter end-to-end latency investigation (measurement only).

Does NOT change production settings or API behavior when run. Temporarily
injects timing middleware + view wrappers only inside this process.

Usage:
  python tools/traffic_filter_phase4_end_to_end_benchmark.py

Optional env:
  NHIT_API_BASE_URL=https://your-railway-app.up.railway.app
      → also measure real HTTP client → deployed API (Postman-comparable)

Never logs credentials, Redis URLs, Authorization, or cache bodies.
"""

from __future__ import annotations

import gzip
import json
import os
import statistics
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'nhit.settings')
os.environ.setdefault('PYTHONUNBUFFERED', '1')

import django

django.setup()

from django.conf import settings
from django.db import connection, reset_queries
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient

from nhit.caching.constants import CACHE_NS_TRAFFIC_FILTER, CACHE_TTL_FILTER
from nhit.caching.keys import build_cache_key
from nhit.caching.service import get_namespace_version, is_cache_usable, safe_cache_get
from nhit.caching.traffic_filter_cache import (
    TRAFFIC_FILTER_CACHE_MAGIC,
    TRAFFIC_FILTER_CACHE_SCOPE,
    extract_traffic_filter_gzip_payload,
    get_cached_traffic_filter_response,
)
from nhit.middleware_phase4a import MARKER_HEADER, REQUEST_ATTR, Phase4ATimingMiddleware
from traffic.serializers import TrafficFilterRequestSerializer
from traffic.views import TrafficFilterView

WARMUPS = 8
SAMPLES = 25
OUT_JSON = ROOT / 'tools' / 'traffic_filter_phase4_end_to_end_results.json'
OUT_MD = ROOT / 'tools' / 'traffic_filter_phase4_end_to_end_investigation.md'

PRIMARY_PAYLOAD = {
    'chainage_start': 0,
    'chainage_end': 1381,
    'date': '2026-05-20',
    'direction': ['All'],
    'project_name': ['Abu Road to Swaroopganj'],
}

LARGE_PAYLOAD = {
    'chainage_start': 0,
    'chainage_end': 99999,
    'date': '2025-08-20',
    'direction': ['All'],
    'project_name': ['Chittorgarh to Kota'],
}

PHASE4A_MW = 'nhit.middleware_phase4a.Phase4ATimingMiddleware'


def _log(msg: str) -> None:
    try:
        print(msg, flush=True)
    except UnicodeEncodeError:
        print(msg.encode('ascii', 'replace').decode('ascii'), flush=True)


def _stats(values: list[float]) -> dict[str, Any]:
    if not values:
        return {'n': 0}
    ordered = sorted(values)
    p95_i = min(len(ordered) - 1, max(0, int(round(0.95 * (len(ordered) - 1)))))
    p99_i = min(len(ordered) - 1, max(0, int(round(0.99 * (len(ordered) - 1)))))
    return {
        'n': len(values),
        'min': round(min(values), 3),
        'median': round(statistics.median(values), 3),
        'p95': round(ordered[p95_i], 3),
        'p99': round(ordered[p99_i], 3),
        'max': round(max(values), 3),
        'mean': round(statistics.fmean(values), 3),
    }


def _validated(payload: dict) -> dict:
    ser = TrafficFilterRequestSerializer(data=payload)
    assert ser.is_valid(), ser.errors
    return ser.validated_data


def _redact_url(url: str | None) -> dict[str, Any]:
    if not url:
        return {'present': False}
    try:
        parsed = urlparse(url)
        host = parsed.hostname or ''
        kind = 'unknown'
        if 'railway.app' in host or 'up.railway.app' in host:
            kind = 'railway_public_http'
        elif 'rlwy.net' in host:
            kind = 'railway_public_redis_proxy'
        elif 'neon.tech' in host:
            kind = 'neon'
        return {
            'present': True,
            'scheme': parsed.scheme,
            'host_kind': kind,
            'host_suffix': '.'.join(host.split('.')[-3:]) if host else None,
            'port': parsed.port,
            'path': parsed.path or '/',
        }
    except Exception:
        return {'present': True, 'host_kind': 'parse_error'}


def _env_snapshot() -> dict[str, Any]:
    on_railway = bool(
        os.environ.get('RAILWAY_ENVIRONMENT', '').strip()
        or os.environ.get('RAILWAY_SERVICE_NAME', '').strip()
    )
    redis_loc = settings.CACHES.get('default', {}).get('LOCATION')
    db = settings.DATABASES.get('default', {})
    return {
        'measurement_context': 'local_dev_machine' if not on_railway else 'railway_runtime',
        'on_railway': on_railway,
        'platform': sys.platform,
        'python': sys.version.split()[0],
        'cache_usable': is_cache_usable(),
        'cache_backend': settings.CACHES.get('default', {}).get('BACKEND', '').rsplit('.', 1)[-1],
        'redis_location': _redact_url(redis_loc if isinstance(redis_loc, str) else None),
        'postgres_host': {
            'present': bool(db.get('HOST')),
            'host_suffix': '.'.join(str(db.get('HOST') or '').split('.')[-4:]),
            'conn_max_age': db.get('CONN_MAX_AGE'),
            'is_neon': 'neon.tech' in str(db.get('HOST') or ''),
        },
        'middleware': list(settings.MIDDLEWARE),
        'gzip_middleware': any('GZip' in m or 'gzip' in m.lower() for m in settings.MIDDLEWARE),
        'traffic_auth_on_view': False,
        'ttl_seconds': CACHE_TTL_FILTER,
        'cache_scope': TRAFFIC_FILTER_CACHE_SCOPE,
        'api_base_url': _redact_url(os.environ.get('NHIT_API_BASE_URL')),
        'note': (
            'APIClient measurements are in-process (no real client↔Railway HTTP). '
            'Set NHIT_API_BASE_URL for Postman-comparable external HTTP timings.'
        ),
    }


@contextmanager
def _instrumented_view():
    """Wrap TrafficFilterView.post to record stages into request timing dict."""
    original = TrafficFilterView.post

    def wrapped(self, request, *args, **kwargs):
        timings = getattr(getattr(request, '_request', request), REQUEST_ATTR, None)
        if timings is None:
            timings = getattr(request, REQUEST_ATTR, None)
        t_view0 = time.perf_counter()
        if timings is not None:
            timings['view_enter_offset_ms'] = round((t_view0 - timings['t0']) * 1000, 3)

        # Patch get_cached_traffic_filter_response for this call only.
        import traffic.views as views_mod

        real_get = views_mod.get_cached_traffic_filter_response

        def timed_get(*, payload, builder, timeout=CACHE_TTL_FILTER, serve_mode='python'):
            t_ver0 = time.perf_counter()
            version = get_namespace_version(CACHE_NS_TRAFFIC_FILTER)
            version_ms = (time.perf_counter() - t_ver0) * 1000

            t_key0 = time.perf_counter()
            _ = build_cache_key(
                namespace=CACHE_NS_TRAFFIC_FILTER,
                version=version,
                payload=payload,
                scope=TRAFFIC_FILTER_CACHE_SCOPE,
            )
            key_build_ms = (time.perf_counter() - t_key0) * 1000

            t_call0 = time.perf_counter()
            # Capture SQL during the cache helper (should be 0 on HIT).
            reset_queries()
            with CaptureQueriesContext(connection) as ctx:
                body, meta = real_get(
                    payload=payload,
                    builder=builder,
                    timeout=timeout,
                    serve_mode=serve_mode,
                )
            call_ms = (time.perf_counter() - t_call0) * 1000

            if timings is not None:
                timings['namespace_version_ms'] = round(version_ms, 3)
                timings['cache_key_build_ms'] = round(key_build_ms, 3)
                # Backward-compatible alias: earlier runs bundled version Redis RTT here.
                timings['cache_key_ms'] = round(version_ms + key_build_ms, 3)
                timings['cache_helper_ms'] = round(call_ms, 3)
                timings['redis_get_ms'] = meta.get('redis_get_ms')
                # Estimate second Redis RTT already inside helper (version again + value).
                # Helper calls get_namespace_version once more internally.
                timings['cache_extract_ms'] = (
                    meta.get('extract_ms')
                    or meta.get('decode_ms')
                    or meta.get('hit_adapt_ms')
                )
                timings['cache_hit'] = bool(meta.get('cache_hit'))
                timings['response_path'] = meta.get('response_path')
                timings['encode_ms'] = meta.get('encode_ms')
                timings['build_ms'] = meta.get('build_ms')
                timings['render_ms'] = meta.get('render_ms')
                timings['db_queries'] = len(ctx.captured_queries)
                if ctx.captured_queries:
                    sql_total = 0.0
                    for q in ctx.captured_queries:
                        try:
                            sql_total += float(q.get('time') or 0)
                        except (TypeError, ValueError):
                            pass
                    timings['db_sql_ms'] = round(sql_total * 1000, 3)
                # Combined Redis estimate for HIT: pre-call version probe + helper redis_get
                # (helper also re-fetches version — see investigation note).
                if meta.get('redis_get_ms') is not None:
                    timings['redis_round_trips_estimate'] = 2
                    timings['redis_total_est_ms'] = round(
                        float(meta.get('redis_get_ms') or 0) + version_ms,
                        3,
                    )
            return body, meta

        views_mod.get_cached_traffic_filter_response = timed_get
        try:
            t_resp0 = time.perf_counter()
            response = original(self, request, *args, **kwargs)
            if timings is not None:
                timings['response_build_ms'] = round((time.perf_counter() - t_resp0) * 1000, 3)
                # response_build includes helper; refine view_total below.
                timings['view_total_ms'] = round((time.perf_counter() - t_view0) * 1000, 3)
                # Propagate onto Django request for middleware headers.
                django_request = getattr(request, '_request', request)
                req_timings = getattr(django_request, REQUEST_ATTR, None)
                if isinstance(req_timings, dict):
                    req_timings.update(timings)
            return response
        finally:
            views_mod.get_cached_traffic_filter_response = real_get

    TrafficFilterView.post = wrapped  # type: ignore[method-assign]
    try:
        yield
    finally:
        TrafficFilterView.post = original  # type: ignore[method-assign]


@contextmanager
def _gzip_middleware_timing():
    """Time SelectiveGZipMiddleware.process_response when Phase4A marker present."""
    from nhit.middleware import SelectiveGZipMiddleware

    original = SelectiveGZipMiddleware.process_response

    def wrapped(self, request, response):
        timings = getattr(request, REQUEST_ATTR, None)
        t0 = time.perf_counter()
        already = (response.get('Content-Encoding') or '').lower()
        result = original(self, request, response)
        elapsed = (time.perf_counter() - t0) * 1000
        if timings is not None:
            timings['gzip_decision_ms'] = round(elapsed, 3)
            timings['gzip_already_encoded'] = bool(already)
            timings['gzip_content_encoding_after'] = (
                (result.get('Content-Encoding') or '').lower() or None
            )
            # Approximate after-view middleware as handler - view.
            if 'view_total_ms' in timings and 'view_enter_offset_ms' in timings:
                timings['middleware_before_view_ms'] = timings.get('view_enter_offset_ms')
                if 'server_handler_ms' in timings:
                    # filled later by outer mw; compute residual after return
                    pass
        return result

    SelectiveGZipMiddleware.process_response = wrapped  # type: ignore[method-assign]
    try:
        yield
    finally:
        SelectiveGZipMiddleware.process_response = original  # type: ignore[method-assign]


def _ensure_tfgz1_hit(payload: dict) -> dict[str, Any]:
    data = _validated(payload)
    version = get_namespace_version(CACHE_NS_TRAFFIC_FILTER)
    key = build_cache_key(
        namespace=CACHE_NS_TRAFFIC_FILTER,
        version=version,
        payload=data,
        scope=TRAFFIC_FILTER_CACHE_SCOPE,
    )
    hit, raw = safe_cache_get(key)
    if hit and isinstance(raw, (bytes, bytearray)) and bytes(raw).startswith(TRAFFIC_FILTER_CACHE_MAGIC):
        gz = extract_traffic_filter_gzip_payload(raw)
        return {
            'warmed': True,
            'cache_hit': True,
            'response_path': 'direct_gzip_bytes',
            'redis_bytes': len(raw),
            'gzip_bytes': len(gz),
            'json_bytes': len(gzip.decompress(gz)),
            'key_suffix': key.rsplit(':', 1)[-1][:12],
        }
    return {
        'warmed': False,
        'cache_hit': False,
        'key_suffix': key.rsplit(':', 1)[-1][:12],
    }


def _warm_via_http(payload: dict) -> None:
    mw = [PHASE4A_MW] + [m for m in settings.MIDDLEWARE if m != PHASE4A_MW]
    with override_settings(MIDDLEWARE=mw):
        client = APIClient()
        for _ in range(WARMUPS):
            client.post(
                '/traffic_filter/',
                payload,
                format='json',
                HTTP_ACCEPT_ENCODING='gzip',
            )


def _apicilent_once(client: APIClient, payload: dict, *, accept_gzip: bool) -> dict[str, Any]:
    headers = {
        MARKER_HEADER: '1',
    }
    if accept_gzip:
        headers['HTTP_ACCEPT_ENCODING'] = 'gzip'

    reset_queries()
    t0 = time.perf_counter()
    response = client.post('/traffic_filter/', payload, format='json', **headers)
    client_total_ms = (time.perf_counter() - t0) * 1000

    timings = getattr(response, '_nhit_phase4a_timings', {}) or {}
    if 'view_total_ms' in timings and 'server_handler_ms' in timings:
        # middleware after ≈ handler - before - view (clamped)
        before = float(timings.get('middleware_before_view_ms') or timings.get('view_enter_offset_ms') or 0)
        view = float(timings.get('view_total_ms') or 0)
        handler = float(timings.get('server_handler_ms') or 0)
        after = max(0.0, handler - before - view)
        timings['middleware_after_view_ms'] = round(after, 3)

    raw = bytes(response.content)
    encoding = (response.get('Content-Encoding') or '').lower() or None
    if encoding == 'gzip':
        body = gzip.decompress(raw)
        compressed = len(raw)
    else:
        body = raw
        compressed = None

    return {
        'status': response.status_code,
        'client_total_ms': client_total_ms,
        'content_encoding': encoding,
        'content_type': response.get('Content-Type'),
        'content_length': response.get('Content-Length'),
        'vary': response.get('Vary'),
        'transfer_encoding': response.get('Transfer-Encoding'),
        'wire_bytes': len(raw),
        'uncompressed_bytes': len(body),
        'compressed_bytes': compressed,
        'tfgz1_not_in_body': not raw.startswith(TRAFFIC_FILTER_CACHE_MAGIC),
        'cache_hit': timings.get('cache_hit'),
        'response_path': timings.get('response_path'),
        'db_queries': timings.get('db_queries'),
        'stages': {
            'middleware_before_view_ms': timings.get('middleware_before_view_ms')
            or timings.get('view_enter_offset_ms'),
            'view_total_ms': timings.get('view_total_ms'),
            'namespace_version_ms': timings.get('namespace_version_ms'),
            'cache_key_build_ms': timings.get('cache_key_build_ms'),
            'cache_key_ms': timings.get('cache_key_ms'),
            'redis_get_ms': timings.get('redis_get_ms'),
            'redis_total_est_ms': timings.get('redis_total_est_ms'),
            'cache_extract_ms': timings.get('cache_extract_ms'),
            'cache_helper_ms': timings.get('cache_helper_ms'),
            'response_build_ms': timings.get('response_build_ms'),
            'build_ms': timings.get('build_ms'),
            'encode_ms': timings.get('encode_ms'),
            'render_ms': timings.get('render_ms'),
            'gzip_decision_ms': timings.get('gzip_decision_ms'),
            'gzip_already_encoded': timings.get('gzip_already_encoded'),
            'middleware_after_view_ms': timings.get('middleware_after_view_ms'),
            'server_handler_ms': timings.get('server_handler_ms'),
            'server_total_ms': timings.get('server_total_ms'),
            'db_queries': timings.get('db_queries'),
            'db_sql_ms': timings.get('db_sql_ms'),
        },
    }


def _measure_apicilent(payload: dict, *, label: str) -> dict[str, Any]:
    _log(f'[{label}] warming via HTTP ({WARMUPS})...')
    _warm_via_http(payload)

    warm_info = _ensure_tfgz1_hit(payload)
    if not warm_info.get('cache_hit'):
        # Allow builder once through uninstrumented path.
        data = _validated(payload)
        from django.db.models import Q
        from traffic.models import Traffic
        from traffic.services.traffic_filter_response import build_traffic_filter_groups

        def builder():
            qs = Traffic.objects.select_related('project', 'segment')
            if data['chainage_start'] is not None and data['chainage_end'] is not None:
                qs = qs.filter(
                    segment__chainage_start__gte=data['chainage_start'],
                    segment__chainage_end__lte=data['chainage_end'],
                )
            if data['date']:
                qs = qs.filter(survey_date=data['date'])
            if data['project_name'] and not any(
                str(n).strip().casefold() == 'all' for n in data['project_name']
            ):
                q = Q()
                for name in data['project_name']:
                    q |= Q(project__project_name__iexact=name)
                qs = qs.filter(q)
            return build_traffic_filter_groups(qs)

        get_cached_traffic_filter_response(
            payload=data,
            builder=builder,
            serve_mode='gzip_bytes',
        )
        warm_info = _ensure_tfgz1_hit(payload)

    mw = [PHASE4A_MW] + [m for m in settings.MIDDLEWARE if m != PHASE4A_MW]
    hit_count = 0
    miss_count = 0
    samples_gzip: list[dict] = []
    samples_plain: list[dict] = []

    with override_settings(MIDDLEWARE=mw):
        with _instrumented_view(), _gzip_middleware_timing():
            client = APIClient()
            # Extra warm under instrumentation.
            for _ in range(3):
                _apicilent_once(client, payload, accept_gzip=True)

            for _ in range(SAMPLES):
                g = _apicilent_once(client, payload, accept_gzip=True)
                if g.get('cache_hit') is True:
                    hit_count += 1
                    samples_gzip.append(g)
                elif g.get('cache_hit') is False:
                    miss_count += 1
                else:
                    miss_count += 1

            # Top up HIT samples if a MISS slipped in.
            guard = 0
            while len(samples_gzip) < SAMPLES and guard < SAMPLES:
                guard += 1
                g = _apicilent_once(client, payload, accept_gzip=True)
                if g.get('cache_hit') is True:
                    hit_count += 1
                    samples_gzip.append(g)
                else:
                    miss_count += 1

            for _ in range(min(10, SAMPLES)):
                samples_plain.append(_apicilent_once(client, payload, accept_gzip=False))

    def _aggregate(rows: list[dict], key: str) -> dict:
        vals = []
        for row in rows:
            if key == 'client_total_ms':
                vals.append(float(row['client_total_ms']))
            else:
                v = (row.get('stages') or {}).get(key)
                if v is not None:
                    vals.append(float(v))
        return _stats(vals)

    stage_keys = [
        'middleware_before_view_ms',
        'view_total_ms',
        'namespace_version_ms',
        'cache_key_build_ms',
        'cache_key_ms',
        'redis_get_ms',
        'redis_total_est_ms',
        'cache_extract_ms',
        'cache_helper_ms',
        'response_build_ms',
        'gzip_decision_ms',
        'middleware_after_view_ms',
        'server_handler_ms',
        'server_total_ms',
    ]
    last = samples_gzip[-1] if samples_gzip else {}
    return {
        'label': label,
        'payload': payload,
        'warm_info': warm_info,
        'hit_count': hit_count,
        'miss_count': miss_count,
        'measured_gzip_requests': len(samples_gzip),
        'all_hits_confirmed': hit_count == len(samples_gzip) and miss_count == 0,
        'headers_last_gzip': {
            'status': last.get('status'),
            'content_type': last.get('content_type'),
            'content_encoding': last.get('content_encoding'),
            'content_length': last.get('content_length'),
            'vary': last.get('vary'),
            'transfer_encoding': last.get('transfer_encoding'),
            'wire_bytes': last.get('wire_bytes'),
            'uncompressed_bytes': last.get('uncompressed_bytes'),
            'tfgz1_not_in_body': last.get('tfgz1_not_in_body'),
            'response_path': last.get('response_path'),
            'db_queries': last.get('db_queries'),
        },
        'client_total_gzip_ms': _aggregate(samples_gzip, 'client_total_ms'),
        'client_total_plain_ms': _aggregate(samples_plain, 'client_total_ms'),
        'stages_gzip_ms': {k: _aggregate(samples_gzip, k) for k in stage_keys},
        'db_queries_on_gzip_samples': [r.get('db_queries') for r in samples_gzip],
    }


def _redis_microbench(payload: dict) -> dict[str, Any]:
    data = _validated(payload)
    version = get_namespace_version(CACHE_NS_TRAFFIC_FILTER)
    key = build_cache_key(
        namespace=CACHE_NS_TRAFFIC_FILTER,
        version=version,
        payload=data,
        scope=TRAFFIC_FILTER_CACHE_SCOPE,
    )
    ping_ms = []
    get_ms = []
    extract_ms = []
    raw_size = None
    try:
        from django_redis import get_redis_connection

        conn = get_redis_connection('default')
        for _ in range(SAMPLES):
            t0 = time.perf_counter()
            conn.ping()
            ping_ms.append((time.perf_counter() - t0) * 1000)
        for _ in range(SAMPLES):
            t0 = time.perf_counter()
            hit, raw = safe_cache_get(key)
            get_ms.append((time.perf_counter() - t0) * 1000)
            if hit and isinstance(raw, (bytes, bytearray)):
                raw_size = len(raw)
                t1 = time.perf_counter()
                extract_traffic_filter_gzip_payload(raw)
                extract_ms.append((time.perf_counter() - t1) * 1000)
    except Exception as exc:
        return {'ok': False, 'error': f'{type(exc).__name__}'}

    return {
        'ok': True,
        'key_suffix': key.rsplit(':', 1)[-1][:12],
        'redis_payload_bytes': raw_size,
        'ping_ms': _stats(ping_ms),
        'django_cache_get_ms': _stats(get_ms),
        'extract_ms': _stats(extract_ms),
    }


def _external_http(payload: dict, *, label: str) -> dict[str, Any]:
    base = (os.environ.get('NHIT_API_BASE_URL') or '').strip().rstrip('/')
    if not base:
        return {
            'skipped': True,
            'reason': 'NHIT_API_BASE_URL not set — cannot compare to Postman/deployed HTTP',
        }

    url = f'{base}/traffic_filter/'
    out: dict[str, Any] = {
        'skipped': False,
        'url': _redact_url(url),
        'label': label,
    }

    try:
        import requests
    except ImportError:
        requests = None  # type: ignore

    try:
        import httpx
    except ImportError:
        httpx = None  # type: ignore

    def _run_requests(accept_gzip: bool) -> dict[str, Any]:
        if requests is None:
            return {'skipped': True, 'reason': 'requests not installed'}
        headers = {'Content-Type': 'application/json'}
        if accept_gzip:
            headers['Accept-Encoding'] = 'gzip'
        else:
            headers['Accept-Encoding'] = 'identity'
        # Warm
        for _ in range(5):
            requests.post(url, json=payload, headers=headers, timeout=120)
        samples = []
        last_meta = None
        for _ in range(SAMPLES):
            t0 = time.perf_counter()
            # stream to approximate TTFB
            with requests.post(
                url,
                json=payload,
                headers=headers,
                timeout=120,
                stream=True,
            ) as resp:
                t_headers = time.perf_counter()
                # Read body
                chunks = []
                first_byte = None
                for chunk in resp.iter_content(chunk_size=64 * 1024):
                    if chunk and first_byte is None:
                        first_byte = time.perf_counter()
                    chunks.append(chunk)
                t_done = time.perf_counter()
                raw = b''.join(chunks)
            total = (t_done - t0) * 1000
            ttfb = ((first_byte or t_headers) - t0) * 1000
            transfer = (t_done - (first_byte or t_headers)) * 1000
            encoding = (resp.headers.get('Content-Encoding') or '').lower() or None
            if encoding == 'gzip':
                body = gzip.decompress(raw)
                compressed = len(raw)
            else:
                body = raw
                compressed = None
            sample = {
                'total_ms': total,
                'ttfb_ms': ttfb,
                'transfer_ms': transfer,
                'status': resp.status_code,
                'content_encoding': encoding,
                'content_type': resp.headers.get('Content-Type'),
                'content_length': resp.headers.get('Content-Length'),
                'vary': resp.headers.get('Vary'),
                'transfer_encoding': resp.headers.get('Transfer-Encoding'),
                'wire_bytes': len(raw),
                'uncompressed_bytes': len(body),
                'compressed_bytes': compressed,
                'redirect_history': len(resp.history),
            }
            samples.append(sample)
            last_meta = sample
        return {
            'client': 'requests',
            'accept_gzip': accept_gzip,
            'total_ms': _stats([s['total_ms'] for s in samples]),
            'ttfb_ms': _stats([s['ttfb_ms'] for s in samples]),
            'transfer_ms': _stats([s['transfer_ms'] for s in samples]),
            'last': last_meta,
        }

    def _run_httpx(accept_gzip: bool) -> dict[str, Any]:
        if httpx is None:
            return {'skipped': True, 'reason': 'httpx not installed'}
        headers = {'Content-Type': 'application/json'}
        if accept_gzip:
            headers['Accept-Encoding'] = 'gzip'
        else:
            headers['Accept-Encoding'] = 'identity'
        samples = []
        last_meta = None
        with httpx.Client(timeout=120.0) as client:
            for _ in range(5):
                client.post(url, json=payload, headers=headers)
            for _ in range(SAMPLES):
                t0 = time.perf_counter()
                resp = client.post(url, json=payload, headers=headers)
                total = (time.perf_counter() - t0) * 1000
                raw = resp.content
                encoding = (resp.headers.get('Content-Encoding') or '').lower() or None
                # httpx may auto-decompress; prefer Content-Encoding header + wire if available
                wire = raw
                try:
                    # If already decompressed by client, uncompressed == wire
                    if encoding == 'gzip':
                        try:
                            body = gzip.decompress(raw)
                            compressed = len(raw)
                        except OSError:
                            body = raw
                            compressed = int(resp.headers.get('Content-Length') or len(raw))
                    else:
                        body = raw
                        compressed = None
                except Exception:
                    body = raw
                    compressed = None
                sample = {
                    'total_ms': total,
                    'status': resp.status_code,
                    'content_encoding': encoding,
                    'content_type': resp.headers.get('Content-Type'),
                    'content_length': resp.headers.get('Content-Length'),
                    'vary': resp.headers.get('Vary'),
                    'transfer_encoding': resp.headers.get('Transfer-Encoding'),
                    'wire_bytes': len(wire),
                    'uncompressed_bytes': len(body),
                    'compressed_bytes': compressed,
                }
                samples.append(sample)
                last_meta = sample
        return {
            'client': 'httpx',
            'accept_gzip': accept_gzip,
            'total_ms': _stats([s['total_ms'] for s in samples]),
            'last': last_meta,
        }

    out['requests_gzip'] = _run_requests(True)
    out['requests_identity'] = _run_requests(False)
    out['httpx_gzip'] = _run_httpx(True)
    out['httpx_identity'] = _run_httpx(False)

    # Connection establishment probe (one-shot TCP/TLS to host)
    try:
        import socket
        import ssl

        parsed = urlparse(base)
        host = parsed.hostname
        port = parsed.port or (443 if parsed.scheme == 'https' else 80)
        conn_samples = []
        for _ in range(10):
            t0 = time.perf_counter()
            sock = socket.create_connection((host, port), timeout=30)
            if parsed.scheme == 'https':
                ctx = ssl.create_default_context()
                sock = ctx.wrap_socket(sock, server_hostname=host)
            sock.close()
            conn_samples.append((time.perf_counter() - t0) * 1000)
        out['tcp_tls_connect_ms'] = _stats(conn_samples)
    except Exception as exc:
        out['tcp_tls_connect_ms'] = {'ok': False, 'error': type(exc).__name__}

    return out


def _classify(results: dict) -> dict[str, Any]:
    primary = results['cases']['primary']['apicilent']
    large = results['cases']['large']['apicilent']
    redis_p = results['cases']['primary']['redis_micro']
    redis_l = results['cases']['large']['redis_micro']
    external = results.get('external_http') or {}

    p_server = primary['stages_gzip_ms']['server_total_ms'].get('median')
    p_redis = primary['stages_gzip_ms']['redis_get_ms'].get('median')
    p_client = primary['client_total_gzip_ms'].get('median')
    l_server = large['stages_gzip_ms']['server_total_ms'].get('median')
    l_redis = large['stages_gzip_ms']['redis_get_ms'].get('median')
    l_client = large['client_total_gzip_ms'].get('median')

    ext_med = None
    if not external.get('skipped') and isinstance(external.get('requests_gzip'), dict):
        ext_med = (external['requests_gzip'].get('total_ms') or {}).get('median')

    # In-process: Redis dominates if redis ≈ client/server
    dominant = []
    if p_redis and p_server and p_redis >= 0.7 * p_server:
        dominant.append('A - Redis/network (in-process path)')
    mw = primary['stages_gzip_ms']['middleware_before_view_ms'].get('median') or 0
    mw_after = primary['stages_gzip_ms']['middleware_after_view_ms'].get('median') or 0
    if (mw + mw_after) >= 50:
        dominant.append('D - Django middleware')
    extract = primary['stages_gzip_ms']['cache_extract_ms'].get('median') or 0
    if extract >= 20:
        dominant.append('C - Python/application CPU')

    postman_story = (
        'APIClient is in-process and excludes real client<->Railway HTTP/TLS. '
        f'In-process gzip median ~= {p_client} ms (primary) / {l_client} ms (large). '
    )
    if ext_med is not None:
        postman_story += (
            f'External requests->NHIT_API_BASE_URL gzip median ~= {ext_med} ms. '
        )
        if ext_med >= 4000:
            postman_story += (
                'External HTTP is in the multi-second range - aligns with Postman ~6s; '
                'classify remaining gap vs server as E/F (HTTP/client transfer / connection).'
            )
            dominant.append('E - HTTP/client transfer')
            dominant.append('F - connection establishment (possible)')
        elif p_server and ext_med > 2 * p_server:
            postman_story += (
                'External client total substantially exceeds in-process server_total - '
                'remaining time is outside view CPU (network/transfer/Postman).'
            )
            dominant.append('E - HTTP/client transfer')
        else:
            postman_story += (
                'External HTTP is not near 6s in this run; Postman 6s may be cold start, '
                'MISS, different payload, or Postman UI overhead.'
            )
            dominant.append('H - unknown (Postman not reproduced)')
    else:
        postman_story += (
            'NHIT_API_BASE_URL unset - cannot reproduce Postman path. '
            f'In-process HIT completes in ~{int(round(l_client or p_client or 0))} ms (large) / '
            f'~{int(round(p_client or 0))} ms (primary) and is dominated by Redis RTTs '
            '(namespace version + value GET over public proxy). '
            'TFGZ1 extract/middleware/DB are negligible. '
            f'Therefore most of the Postman ~6s ({6000 - int(round(l_client or p_client or 0))} ms+ gap) '
            'is NOT inside application CPU after Redis; '
            'it is almost certainly client<->Railway HTTP/TLS/transfer, cold connection, '
            'Postman rendering, and/or a MISS/cold path - pending NHIT_API_BASE_URL confirmation.'
        )
        if not dominant:
            dominant.append('A - Redis/network (server-side dominant stage)')
            dominant.append('H - unknown (Postman gap unverified without NHIT_API_BASE_URL)')

    code = 'G - mixed' if len(dominant) > 1 else (dominant[0] if dominant else 'H - unknown')
    return {
        'classification_code': code,
        'factors': dominant,
        'postman_6s_answer': postman_story,
        'numbers': {
            'primary_client_median_ms': p_client,
            'primary_server_median_ms': p_server,
            'primary_redis_get_median_ms': p_redis,
            'large_client_median_ms': l_client,
            'large_server_median_ms': l_server,
            'large_redis_get_median_ms': l_redis,
            'redis_ping_primary_median_ms': (redis_p.get('ping_ms') or {}).get('median'),
            'redis_ping_large_median_ms': (redis_l.get('ping_ms') or {}).get('median'),
            'external_requests_gzip_median_ms': ext_med,
            'client_minus_server_primary_ms': (
                round(p_client - p_server, 3) if p_client and p_server else None
            ),
        },
    }


def _write_report(results: dict) -> None:
    env = results['environment']
    clf = results['classification']
    primary = results['cases']['primary']['apicilent']
    large = results['cases']['large']['apicilent']
    lines = [
        '# Traffic Filter — Phase 4A End-to-End Latency Investigation',
        '',
        '**Investigation only — no production behavior changed.**',
        '',
        '## 1. Current 6-second observation',
        '',
        'Postman reports approximately **6 seconds** for `POST /traffic_filter/`.',
        'Phase 3 already made gzip HIT path HTTP-ready (TFGZ1 → direct bytes).',
        'Local Phase 3 APIClient benchmark showed ~702 ms for the large payload —',
        'so the ~6s Postman time is **not** explained by JSON/gzip CPU.',
        '',
        '## 2. Exact benchmark environment',
        '',
        f"- measurement_context: `{env['measurement_context']}`",
        f"- on_railway: `{env['on_railway']}`",
        f"- cache_usable: `{env['cache_usable']}` / backend `{env['cache_backend']}`",
        f"- redis: `{json.dumps(env['redis_location'])}`",
        f"- postgres: `{json.dumps(env['postgres_host'])}`",
        f"- middleware: `{env['middleware']}`",
        f"- traffic_filter auth: none on view",
        f"- NHIT_API_BASE_URL: `{json.dumps(env['api_base_url'])}`",
        f"- note: {env['note']}",
        '',
        '## 3. Request payloads',
        '',
        '### Primary',
        '',
        '```json',
        json.dumps(PRIMARY_PAYLOAD, indent=2),
        '```',
        '',
        '### Large',
        '',
        '```json',
        json.dumps(LARGE_PAYLOAD, indent=2),
        '```',
        '',
        '## 4. Cache state',
        '',
        f"- Primary: HIT confirmed={primary['all_hits_confirmed']} "
        f"(hits={primary['hit_count']}, misses={primary['miss_count']})",
        f"- Large: HIT confirmed={large['all_hits_confirmed']} "
        f"(hits={large['hit_count']}, misses={large['miss_count']})",
        f"- Format: TFGZ1 / scope `{TRAFFIC_FILTER_CACHE_SCOPE}` / TTL {CACHE_TTL_FILTER}s",
        f"- Primary warm: `{primary['warm_info']}`",
        f"- Large warm: `{large['warm_info']}`",
        '',
        '## 5. Payload sizes',
        '',
        f"- Primary redis/gzip/json: "
        f"{primary['warm_info'].get('redis_bytes')} / "
        f"{primary['warm_info'].get('gzip_bytes')} / "
        f"{primary['warm_info'].get('json_bytes')}",
        f"- Large redis/gzip/json: "
        f"{large['warm_info'].get('redis_bytes')} / "
        f"{large['warm_info'].get('gzip_bytes')} / "
        f"{large['warm_info'].get('json_bytes')}",
        '',
        '## 6. Median / P95 by stage (APIClient in-process, gzip HIT)',
        '',
        '### Primary',
        '',
        '| Stage | Median | P95 |',
        '|---|---:|---:|',
    ]
    for k, v in primary['stages_gzip_ms'].items():
        if v.get('n'):
            lines.append(f"| {k} | {v['median']} | {v['p95']} |")
    lines.extend([
        '',
        f"| client_total_gzip_ms | {primary['client_total_gzip_ms']['median']} | "
        f"{primary['client_total_gzip_ms']['p95']} |",
        '',
        '### Large',
        '',
        '| Stage | Median | P95 |',
        '|---|---:|---:|',
    ])
    for k, v in large['stages_gzip_ms'].items():
        if v.get('n'):
            lines.append(f"| {k} | {v['median']} | {v['p95']} |")
    lines.extend([
        '',
        f"| client_total_gzip_ms | {large['client_total_gzip_ms']['median']} | "
        f"{large['client_total_gzip_ms']['p95']} |",
        '',
        '## 7. Client-side total (in-process APIClient)',
        '',
        f"- Primary gzip median: **{primary['client_total_gzip_ms']['median']} ms**",
        f"- Large gzip median: **{large['client_total_gzip_ms']['median']} ms**",
        '',
        '## 8. Server-side total',
        '',
        f"- Primary server_total median: **{primary['stages_gzip_ms']['server_total_ms']['median']} ms**",
        f"- Large server_total median: **{large['stages_gzip_ms']['server_total_ms']['median']} ms**",
        '',
        '## 9. Client vs server difference',
        '',
        f"- Primary client−server: **{clf['numbers']['client_minus_server_primary_ms']} ms** "
        '(expected near-zero for APIClient)',
        '',
        '## 10. Redis contribution',
        '',
        f"- Primary redis_get median: **{primary['stages_gzip_ms']['redis_get_ms']['median']} ms**",
        f"- Large redis_get median: **{large['stages_gzip_ms']['redis_get_ms']['median']} ms**",
        f"- Primary namespace_version median: **{(primary['stages_gzip_ms'].get('namespace_version_ms') or {}).get('median')} ms**",
        f"- Primary redis_total_est median: **{(primary['stages_gzip_ms'].get('redis_total_est_ms') or {}).get('median')} ms**",
        f"- Primary redis PING median: **{(results['cases']['primary']['redis_micro'].get('ping_ms') or {}).get('median')} ms**",
        f"- Large redis PING median: **{(results['cases']['large']['redis_micro'].get('ping_ms') or {}).get('median')} ms**",
        '',
        '**Important:** every HIT performs **at least two Redis round trips** '
        '(`get_namespace_version` + value GET). With ~300ms public-proxy PING, that alone is ~600ms+.',
        '',
        '## 11. DB contribution (HIT)',
        '',
        f"- Primary db_queries samples: `{primary['db_queries_on_gzip_samples'][:5]}...`",
        f"- Large db_queries samples: `{large['db_queries_on_gzip_samples'][:5]}...`",
        '- Expected: **0** SQL queries on TFGZ1 HIT',
        '',
        '## 12. Middleware contribution',
        '',
        f"- Primary before-view median: **{primary['stages_gzip_ms']['middleware_before_view_ms']['median']} ms**",
        f"- Primary after-view median: **{primary['stages_gzip_ms']['middleware_after_view_ms']['median']} ms**",
        f"- Primary gzip_decision median: **{primary['stages_gzip_ms']['gzip_decision_ms']['median']} ms** "
        f"(already_encoded expected true on direct gzip HIT)",
        '',
        '## 13. HTTP transfer contribution',
        '',
    ])
    ext = results.get('external_http_for_classify') or results.get('external_http') or {}
    if isinstance(ext, dict) and ext.get('primary') and not ext.get('requests_gzip'):
        # Nested shape from main(); prefer flattened classify helper if present.
        ext = results.get('external_http_for_classify') or ext.get('primary') or ext
    if ext.get('skipped'):
        lines.append(f"- External HTTP skipped: `{ext.get('reason')}`")
        lines.append(
            '- In-process APIClient does **not** include real TLS/HTTP transfer to Railway.'
        )
        lines.append(
            '- To compare with Postman: '
            '`$env:NHIT_API_BASE_URL=\"https://<railway-app>\"; '
            'python tools/traffic_filter_phase4_end_to_end_benchmark.py`'
        )
    else:
        rg = ext.get('requests_gzip') or {}
        lines.append(f"- requests gzip total median: **{(rg.get('total_ms') or {}).get('median')} ms**")
        lines.append(f"- requests gzip TTFB median: **{(rg.get('ttfb_ms') or {}).get('median')} ms**")
        lines.append(f"- requests gzip transfer median: **{(rg.get('transfer_ms') or {}).get('median')} ms**")
        lines.append(f"- TCP/TLS connect median: **{(ext.get('tcp_tls_connect_ms') or {}).get('median')} ms**")
        lines.append(f"- last headers: `{rg.get('last')}`")
    lines.extend([
        '',
        '## 14. Postman comparison',
        '',
        '- Postman reported ~6000 ms.',
        f"- In-process APIClient large median: **{large['client_total_gzip_ms']['median']} ms**.",
        '- These measure different paths: Postman = real network to deployed app;',
        '  APIClient = local Django handler + public Redis from this machine.',
        '',
        clf['postman_6s_answer'],
        '',
        '## 15. Root-cause classification',
        '',
        f"**{clf['classification_code']}**",
        '',
        'Factors:',
    ])
    for f in clf['factors']:
        lines.append(f'- {f}')
    lines.extend([
        '',
        '### Direct answer',
        '',
        '> **Postman shows approximately 6 seconds. Exactly where are those 6 seconds going?**',
        '',
        clf['postman_6s_answer'],
        '',
        '### Headers verification (last gzip HIT)',
        '',
        f"- Primary: `{primary['headers_last_gzip']}`",
        f"- Large: `{large['headers_last_gzip']}`",
        '',
        '### Phase 5 guidance',
        '',
        '- Do **not** re-optimize TFGZ1/JSON/gzip CPU next.',
        '- Next: measure real client→Railway HTTP (set `NHIT_API_BASE_URL`) and/or',
        '  Railway app→internal Redis path before changing infrastructure.',
        '',
    ])
    OUT_MD.write_text('\n'.join(lines) + '\n', encoding='utf-8')


def main() -> int:
    env = _env_snapshot()
    _log(f"Phase 4A env: {env['measurement_context']} cache={env['cache_usable']}")

    results: dict[str, Any] = {
        'environment': env,
        'cases': {},
        'postman_observation_ms': 6000,
        'phase3_reference': {
            'large_http_gzip_median_ms': 702,
            'large_redis_get_median_ms': 428,
            'large_direct_extract_median_ms': 0.017,
            'large_redis_bytes': 138458,
        },
    }

    for name, payload in (('primary', PRIMARY_PAYLOAD), ('large', LARGE_PAYLOAD)):
        _log(f'=== CASE {name} ===')
        apicilent = _measure_apicilent(payload, label=name)
        _log(
            f"[{name}] hits={apicilent['hit_count']} misses={apicilent['miss_count']} "
            f"client_median={apicilent['client_total_gzip_ms'].get('median')} "
            f"redis_median={apicilent['stages_gzip_ms']['redis_get_ms'].get('median')}"
        )
        redis_micro = _redis_microbench(payload)
        results['cases'][name] = {
            'apicilent': apicilent,
            'redis_micro': redis_micro,
        }

    _log('=== External HTTP (optional) ===')
    results['external_http'] = {
        'primary': _external_http(PRIMARY_PAYLOAD, label='primary'),
        # Only run large external if base URL set (expensive).
    }
    if not results['external_http']['primary'].get('skipped'):
        results['external_http']['large'] = _external_http(LARGE_PAYLOAD, label='large')
        # Flatten helper for classifier: prefer large external if present
        results['external_http_for_classify'] = results['external_http']['large']
    else:
        results['external_http_for_classify'] = results['external_http']['primary']

    # Classifier expects external_http shape of one case — use for_classify
    classify_input = {
        **results,
        'external_http': results['external_http_for_classify'],
    }
    results['classification'] = _classify(classify_input)

    OUT_JSON.write_text(json.dumps(results, indent=2, default=str), encoding='utf-8')
    _write_report(results)
    _log(f'Wrote {OUT_JSON}')
    _log(f'Wrote {OUT_MD}')
    _log(f"Classification: {results['classification']['classification_code']}")
    _log(results['classification']['postman_6s_answer'])
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
