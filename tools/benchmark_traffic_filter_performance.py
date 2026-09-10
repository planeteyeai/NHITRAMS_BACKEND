"""Traffic Filter API — MEASUREMENT-ONLY performance investigation.

Does NOT modify production code, cache, Redis contents of real keys, or API behavior.

Primary request (from contract tests / Railway benchmark suite):

{
  "chainage_start": 0,
  "chainage_end": 1381,
  "date": "2026-05-20",
  "direction": ["All"],
  "project_name": ["Abu Road to Swaroopganj"]
}

Also measures a larger known project/date for scale context (same API fields).

Run:

    python tools/benchmark_traffic_filter_performance.py

Prefer Railway runtime for authoritative numbers.
"""

from __future__ import annotations

import concurrent.futures
import gzip
import hashlib
import json
import math
import os
import statistics
import sys
import time
from copy import deepcopy
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
from django.test.utils import CaptureQueriesContext
from rest_framework.renderers import JSONRenderer
from rest_framework.test import APIClient

from nhit.caching.constants import CACHE_NS_TRAFFIC_FILTER, CACHE_TTL_FILTER
from nhit.caching.keys import build_cache_key
from nhit.caching.service import get_namespace_version, is_cache_usable, safe_cache_get
from traffic.models import Traffic
from traffic.serializers import TrafficFilterRequestSerializer
from traffic.services.traffic_filter_response import (
    TRAFFIC_FILTER_VALUE_FIELDS,
    build_traffic_filter_groups,
    group_traffic_rows,
    serialize_traffic_row,
)
from django.db.models import Q

OUT_JSON = ROOT / 'tools' / 'benchmark_traffic_filter_performance_results.json'
OUT_MD = ROOT / 'tools' / 'traffic_filter_performance_investigation.md'

WARMUPS = 5
SAMPLES = 30
CONCURRENCY_LEVELS = [1, 2, 5, 10]
CONCURRENCY_REQUESTS = 20

# Exact contract / Railway-benchmark payload
PRIMARY_PAYLOAD = {
    'chainage_start': 0,
    'chainage_end': 1381,
    'date': '2026-05-20',
    'direction': ['All'],
    'project_name': ['Abu Road to Swaroopganj'],
}

# Same field schema; largest observed project/date combo (scale context only)
LARGE_PAYLOAD = {
    'chainage_start': 0,
    'chainage_end': 99999,
    'date': '2025-08-20',
    'direction': ['All'],
    'project_name': ['Chittorgarh to Kota'],
}


def _log(msg: str) -> None:
    print(msg, flush=True)


def _percentile(ordered: list[float], p: float) -> float:
    if not ordered:
        return float('nan')
    if len(ordered) == 1:
        return ordered[0]
    k = (len(ordered) - 1) * (p / 100.0)
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return ordered[int(k)]
    return ordered[f] * (c - k) + ordered[c] * (k - f)


def _stats(values: list[float]) -> dict[str, Any]:
    if not values:
        return {'n': 0}
    ordered = sorted(values)
    return {
        'n': len(values),
        'min': round(ordered[0], 3),
        'mean': round(statistics.fmean(ordered), 3),
        'median': round(statistics.median(ordered), 3),
        'p95': round(_percentile(ordered, 95), 3),
        'p99': round(_percentile(ordered, 99), 3),
        'max': round(ordered[-1], 3),
        'stdev': round(statistics.stdev(ordered), 3) if len(ordered) > 1 else 0.0,
        'samples_ms': [round(v, 3) for v in values],
    }


def _redact_host(host: str | None) -> dict[str, Any]:
    if not host:
        return {'present': False}
    h = host.lower()
    kind = (
        'neon_pooler'
        if 'pooler' in h and 'neon.tech' in h
        else ('neon_direct' if 'neon.tech' in h else ('local' if h in {'localhost', '127.0.0.1'} else 'other'))
    )
    parts = h.split('.')
    region = next((p for p in parts if p.startswith(('ap-', 'eu-', 'us-', 'sa-'))), None)
    suffix = '.'.join(parts[-4:]) if len(parts) >= 4 else '.'.join(parts[-3:]) if len(parts) >= 3 else h[-32:]
    return {'present': True, 'endpoint_kind': kind, 'region_hint': region, 'host_suffix': suffix, 'is_neon': 'neon.tech' in h}


def _redact_redis_location(loc: Any) -> dict[str, Any]:
    if not isinstance(loc, str) or not loc:
        return {'present': False}
    try:
        p = urlparse(loc)
    except Exception:
        return {'present': True, 'parse_error': True}
    host = (p.hostname or '').lower()
    kind = (
        'railway_internal'
        if 'railway.internal' in host
        else ('railway_public_proxy' if 'rlwy.net' in host or 'proxy' in host else 'other')
    )
    suffix = '.'.join(host.split('.')[-3:]) if host.count('.') >= 2 else host[-24:]
    return {
        'present': True,
        'scheme': p.scheme,
        'tls': (p.scheme or '').lower() in {'rediss', 'redis+ssl'},
        'host_kind': kind,
        'host_suffix': suffix,
        'port': p.port,
    }


def _env_snapshot() -> dict[str, Any]:
    import django as dj

    on_railway = bool(
        os.environ.get('RAILWAY_ENVIRONMENT', '').strip()
        or os.environ.get('RAILWAY_SERVICE_NAME', '').strip()
    )
    db = settings.DATABASES.get('default', {})
    return {
        'runtime': {
            'platform': sys.platform,
            'python': sys.version.split()[0],
            'on_railway': on_railway,
            'measurement_context': (
                'railway_app_runtime' if on_railway else 'local_dev_machine'
            ),
            'railway_environment': os.environ.get('RAILWAY_ENVIRONMENT') or None,
            'railway_region': os.environ.get('RAILWAY_REGION') or None,
        },
        'django_version': dj.get_version(),
        'cache_enabled': bool(getattr(settings, 'NHIT_CACHE_ENABLED', False)),
        'cache_backend': settings.CACHES.get('default', {}).get('BACKEND'),
        'cache_ttl_filter_seconds': CACHE_TTL_FILTER,
        'redis_location': _redact_redis_location(settings.CACHES.get('default', {}).get('LOCATION')),
        'postgres_host': _redact_host(db.get('HOST')),
        'gzip_middleware': any(
            'gzip' in m.lower() or 'SelectiveGZip' in m for m in settings.MIDDLEWARE
        ),
    }


def _validated(payload: dict) -> dict:
    ser = TrafficFilterRequestSerializer(data=payload)
    assert ser.is_valid(), ser.errors
    return dict(ser.validated_data)


def _build_queryset(data: dict):
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
    return queryset


def _cache_key_meta(data: dict) -> dict[str, Any]:
    version = get_namespace_version(CACHE_NS_TRAFFIC_FILTER)
    logical = build_cache_key(
        namespace=CACHE_NS_TRAFFIC_FILTER,
        version=version,
        payload=data,
        scope='',
    )
    digest = logical.rsplit(':', 1)[-1]
    return {
        'namespace': CACHE_NS_TRAFFIC_FILTER,
        'version': version,
        'ttl_seconds': CACHE_TTL_FILTER,
        'logical_key_hash': hashlib.sha256(logical.encode()).hexdigest()[:16],
        'logical_key_suffix': digest[:16],
        'logical_key': logical,
    }


def _http_once(client: APIClient, payload: dict, *, accept_gzip: bool) -> dict[str, Any]:
    headers = {}
    if accept_gzip:
        headers['HTTP_ACCEPT_ENCODING'] = 'gzip'
    t0 = time.perf_counter()
    response = client.post('/traffic_filter/', payload, format='json', **headers)
    total_ms = (time.perf_counter() - t0) * 1000
    raw = bytes(response.content)
    encoding = response.get('Content-Encoding')
    if encoding == 'gzip':
        body = gzip.decompress(raw)
        compressed_bytes = len(raw)
    else:
        body = raw
        compressed_bytes = None
    return {
        'status': response.status_code,
        'total_ms': total_ms,
        'wire_bytes': len(raw),
        'uncompressed_bytes': len(body),
        'compressed_bytes': compressed_bytes,
        'content_encoding': encoding,
        'content_type': response.get('Content-Type'),
        'content_length': response.get('Content-Length'),
        'body': body,
    }


def measure_http_e2e(payload: dict, *, label: str) -> dict[str, Any]:
    client = APIClient()
    # warmups
    for _ in range(WARMUPS):
        _http_once(client, payload, accept_gzip=True)

    gzip_samples = []
    plain_samples = []
    last_gzip = None
    last_plain = None
    for _ in range(SAMPLES):
        g = _http_once(client, payload, accept_gzip=True)
        gzip_samples.append(g['total_ms'])
        last_gzip = g
        p = _http_once(client, payload, accept_gzip=False)
        plain_samples.append(p['total_ms'])
        last_plain = p

    assert last_gzip and last_plain
    assert last_gzip['status'] == 200 and last_plain['status'] == 200
    # decompressed gzip body should match plain JSON bytes
    body_match = last_gzip['body'] == last_plain['body']
    try:
        parsed = json.loads(last_plain['body'])
        groups = len(parsed) if isinstance(parsed, list) else None
        rows = sum(len(g) for g in parsed) if isinstance(parsed, list) else None
    except Exception:
        groups = rows = None

    return {
        'label': label,
        'payload': payload,
        'http_accept_gzip_ms': _stats(gzip_samples),
        'http_no_accept_gzip_ms': _stats(plain_samples),
        'response': {
            'status': last_gzip['status'],
            'uncompressed_bytes': last_plain['uncompressed_bytes'],
            'gzip_wire_bytes': last_gzip['compressed_bytes'] or last_gzip['wire_bytes'],
            'content_encoding_when_requested': last_gzip['content_encoding'],
            'content_encoding_when_not_requested': last_plain['content_encoding'],
            'content_type': last_plain['content_type'],
            'decompressed_equals_plain': body_match,
            'group_count': groups,
            'row_count': rows,
        },
    }


def measure_cache_hit_redis(data: dict) -> dict[str, Any]:
    if not is_cache_usable():
        return {'skipped': True, 'reason': 'cache_disabled'}
    meta = _cache_key_meta(data)
    logical = meta['logical_key']
    # ensure populated via API
    client = APIClient()
    r = client.post('/traffic_filter/', {
        'chainage_start': data['chainage_start'],
        'chainage_end': data['chainage_end'],
        'date': data['date'].isoformat() if hasattr(data['date'], 'isoformat') else data['date'],
        'direction': data['direction'],
        'project_name': data['project_name'],
    }, format='json')
    assert r.status_code == 200

    hit, value = safe_cache_get(logical)
    if not hit:
        return {'skipped': True, 'reason': 'cache_miss_after_warm', 'key_meta': {k: v for k, v in meta.items() if k != 'logical_key'}}

    size_est = None
    try:
        import pickle

        size_est = len(pickle.dumps(value, protocol=pickle.HIGHEST_PROTOCOL))
    except Exception:
        size_est = None

    # django-redis / safe_cache_get timings
    for _ in range(WARMUPS):
        safe_cache_get(logical)
    get_times = []
    for _ in range(SAMPLES):
        t0 = time.perf_counter()
        h, v = safe_cache_get(logical)
        get_times.append((time.perf_counter() - t0) * 1000)
        assert h and v is not None

    # redis-py raw GET of django key if possible
    raw_get = None
    try:
        import redis as redis_lib

        loc = settings.CACHES['default'].get('LOCATION')
        if isinstance(loc, str):
            client_r = redis_lib.Redis.from_url(loc, socket_timeout=60)
            redis_key = cache_make_key(logical)
            for _ in range(WARMUPS):
                client_r.get(redis_key)
            raw_times = []
            payload_len = None
            for _ in range(SAMPLES):
                t0 = time.perf_counter()
                raw = client_r.get(redis_key)
                raw_times.append((time.perf_counter() - t0) * 1000)
                if raw is not None:
                    payload_len = len(raw)
            client_r.close()
            raw_get = {
                'payload_bytes': payload_len,
                'latency_ms': _stats(raw_times),
                'redis_key_hash': hashlib.sha256(redis_key.encode()).hexdigest()[:16],
            }
    except Exception as exc:
        raw_get = {'error': type(exc).__name__, 'detail': str(exc)[:120]}

    # post-get: Response construction + JSON render + gzip model
    render_times = []
    gzip_times = []
    for _ in range(SAMPLES):
        t0 = time.perf_counter()
        rendered = JSONRenderer().render(value)
        render_times.append((time.perf_counter() - t0) * 1000)
        t1 = time.perf_counter()
        gz = gzip.compress(rendered, compresslevel=6)
        gzip_times.append((time.perf_counter() - t1) * 1000)

    return {
        'key_meta': {k: v for k, v in meta.items() if k != 'logical_key'},
        'cache_hit': True,
        'value_type': type(value).__name__,
        'pickle_size_estimate_bytes': size_est,
        'django_safe_cache_get_ms': _stats(get_times),
        'redis_py_raw_get': raw_get,
        'json_render_from_cached_object_ms': _stats(render_times),
        'gzip_compress_rendered_ms': _stats(gzip_times),
        'rendered_json_bytes': len(rendered),
        'gzip_bytes': len(gz),
    }


def cache_make_key(logical_key: str) -> str:
    from django.core.cache import cache

    try:
        return cache.make_key(logical_key)
    except Exception:
        prefix = settings.CACHES.get('default', {}).get('KEY_PREFIX') or ''
        version = settings.CACHES.get('default', {}).get('VERSION', 1)
        return f'{prefix}:{version}:{logical_key}' if prefix else f'{version}:{logical_key}'


def measure_miss_pipeline(data: dict) -> dict[str, Any]:
    """Measure DB + Python build path without invalidating production cache.

    Calls the same queryset/builder code the view uses, outside Redis.
    """
    # Connection warm
    connection.ensure_connection()

    db_total = []
    query_counts = []
    sql_times = []
    values_fetch = []
    serialize_ms = []
    group_ms = []
    build_total = []
    render_ms = []
    gzip_ms = []
    row_counts = []
    last_groups = None
    last_rendered = b''
    last_gz = b''

    for i in range(SAMPLES):
        queryset = _build_queryset(data)

        # Query evaluation with CaptureQueriesContext
        reset_queries()
        t_build0 = time.perf_counter()
        with CaptureQueriesContext(connection) as ctx:
            t_db0 = time.perf_counter()
            t_vals0 = time.perf_counter()
            records = list(
                queryset.values(*TRAFFIC_FILTER_VALUE_FIELDS).order_by(
                    'segment__chainage_start',
                    'segment__chainage_end',
                )
            )
            values_fetch.append((time.perf_counter() - t_vals0) * 1000)
            db_elapsed = (time.perf_counter() - t_db0) * 1000
            db_total.append(db_elapsed)

            t_ser0 = time.perf_counter()
            rows = [serialize_traffic_row(record) for record in records]
            serialize_ms.append((time.perf_counter() - t_ser0) * 1000)

            t_g0 = time.perf_counter()
            groups = group_traffic_rows(rows)
            group_ms.append((time.perf_counter() - t_g0) * 1000)

        build_total.append((time.perf_counter() - t_build0) * 1000)
        query_counts.append(len(ctx.captured_queries))
        # sum of DB times from Django debug (may be empty if DEBUG False)
        qtime = 0.0
        for q in ctx.captured_queries:
            try:
                qtime += float(q.get('time') or 0) * 1000
            except Exception:
                pass
        sql_times.append(qtime)
        row_counts.append(len(records))
        last_groups = groups

        t_r0 = time.perf_counter()
        rendered = JSONRenderer().render(groups)
        render_ms.append((time.perf_counter() - t_r0) * 1000)
        t_z0 = time.perf_counter()
        gz = gzip.compress(rendered, compresslevel=6)
        gzip_ms.append((time.perf_counter() - t_z0) * 1000)
        last_rendered = rendered
        last_gz = gz

    # Also one EXPLAIN-ish count using connection for connection establish
    establish = []
    for _ in range(10):
        connection.close()
        t0 = time.perf_counter()
        connection.ensure_connection()
        establish.append((time.perf_counter() - t0) * 1000)
        with connection.cursor() as cur:
            cur.execute('SELECT 1')
            cur.fetchone()

    select1 = []
    connection.ensure_connection()
    with connection.cursor() as cur:
        for _ in range(SAMPLES):
            t0 = time.perf_counter()
            cur.execute('SELECT 1')
            cur.fetchone()
            select1.append((time.perf_counter() - t0) * 1000)

    parity_ok = False
    if last_groups is not None:
        parity_ok = last_groups == build_traffic_filter_groups(_build_queryset(data))

    return {
        'note': 'MISS pipeline measured in-process without Redis invalidation',
        'row_count_median': statistics.median(row_counts) if row_counts else None,
        'query_count_median': statistics.median(query_counts) if query_counts else None,
        'query_count_samples': query_counts[:5],
        'db_connection_establish_ms': _stats(establish),
        'select1_reused_ms': _stats(select1),
        'values_fetch_ms': _stats(values_fetch),
        'django_debug_sql_time_sum_ms': _stats(sql_times) if any(sql_times) else {
            'n': 0,
            'note': 'DEBUG likely False — Django did not capture per-query times',
        },
        'serialize_rows_ms': _stats(serialize_ms),
        'group_rows_ms': _stats(group_ms),
        'build_total_ms': _stats(build_total),
        'json_render_ms': _stats(render_ms),
        'gzip_ms': _stats(gzip_ms),
        'rendered_json_bytes': len(last_rendered),
        'gzip_bytes': len(last_gz),
        'builder_parity_ok': parity_ok,
    }


def measure_concurrency(payload: dict) -> dict[str, Any]:
    # Warm
    client0 = APIClient()
    for _ in range(3):
        _http_once(client0, payload, accept_gzip=True)

    levels = []
    for conc in CONCURRENCY_LEVELS:
        latencies = []
        errors = 0

        def _one(_i: int) -> float:
            c = APIClient()
            res = _http_once(c, payload, accept_gzip=True)
            if res['status'] != 200:
                raise RuntimeError(f'status={res["status"]}')
            return res['total_ms']

        t_wall0 = time.perf_counter()
        with concurrent.futures.ThreadPoolExecutor(max_workers=conc) as pool:
            futs = [pool.submit(_one, i) for i in range(CONCURRENCY_REQUESTS)]
            for fut in concurrent.futures.as_completed(futs):
                try:
                    latencies.append(fut.result())
                except Exception:
                    errors += 1
        wall = (time.perf_counter() - t_wall0) * 1000
        levels.append(
            {
                'concurrency': conc,
                'requests': CONCURRENCY_REQUESTS,
                'successful': len(latencies),
                'errors': errors,
                'wall_ms': round(wall, 3),
                'latency_ms': _stats(latencies),
                'requests_per_sec': round(1000.0 * len(latencies) / wall, 3) if wall else None,
            }
        )
    return {'levels': levels}


def _classify(report: dict[str, Any]) -> dict[str, Any]:
    primary = report['primary']
    http = primary['http_e2e']['http_accept_gzip_ms']
    redis = primary.get('cache_hit_redis') or {}
    miss = primary.get('miss_pipeline') or {}
    env = report['environment']

    http_med = http.get('median')
    redis_med = (redis.get('django_safe_cache_get_ms') or {}).get('median')
    raw_redis = ((redis.get('redis_py_raw_get') or {}).get('latency_ms') or {}).get('median')
    render_med = (redis.get('json_render_from_cached_object_ms') or {}).get('median')
    gzip_med = (redis.get('gzip_compress_rendered_ms') or {}).get('median')
    build_med = (miss.get('build_total_ms') or {}).get('median')
    values_med = (miss.get('values_fetch_ms') or {}).get('median')
    ser_med = (miss.get('serialize_rows_ms') or {}).get('median')
    resp_bytes = primary['http_e2e']['response'].get('uncompressed_bytes')
    gzip_bytes = primary['http_e2e']['response'].get('gzip_wire_bytes')

    findings = []
    classes = []

    if redis_med is not None and http_med is not None and redis_med > http_med * 0.4:
        findings.append('Redis/cache GET is a large fraction of HIT HTTP time')
        classes.append('A. Redis/network transfer')
    if render_med is not None and http_med and render_med > http_med * 0.2:
        findings.append('JSON rendering of cached Python object is material on HIT')
        classes.append('E. Serialization/JSON')
    if gzip_med is not None and http_med and gzip_med > http_med * 0.15:
        findings.append('Gzip CPU is material')
        classes.append('F. Gzip/compression')
    if values_med is not None and build_med and values_med > build_med * 0.5:
        findings.append('DB/queryset values() fetch dominates MISS build')
        classes.append('C. PostgreSQL/database')
    if ser_med is not None and build_med and ser_med > build_med * 0.3:
        findings.append('Python row serialization material on MISS')
        classes.append('D. Python/backend processing')
    if not env['runtime']['on_railway']:
        findings.append('Run was not on Railway — absolute numbers may include local↔cloud RTT')
        classes.append('H. Infrastructure/network')

    if not classes:
        classes.append('I. Mixed')

    # largest stage among known medians for HIT
    stages = {
        'redis_get': redis_med,
        'json_render': render_med,
        'gzip': gzip_med,
        'http_total_hit': http_med,
        'miss_build_total': build_med,
        'miss_values_fetch': values_med,
    }
    known = {k: v for k, v in stages.items() if v is not None}
    largest = max(known, key=known.get) if known else None

    return {
        'classification': classes,
        'findings': findings,
        'largest_measured_stage': largest,
        'key_medians_ms': stages,
        'response_bytes': {'uncompressed': resp_bytes, 'gzip_wire': gzip_bytes},
        'answers': {
            '1_median_total_response_ms': http_med,
            '2_p95_ms': http.get('p95'),
            '3_response_size': {'uncompressed': resp_bytes, 'gzip': gzip_bytes},
            '4_is_cached': True if redis.get('cache_hit') else False,
            '5_redis_ms': {'django_cache_get': redis_med, 'redis_py': raw_redis},
            '6_postgres_ms': {
                'miss_values_fetch': values_med,
                'select1_reused': (miss.get('select1_reused_ms') or {}).get('median'),
            },
            '7_python_processing_ms': {
                'serialize_rows': ser_med,
                'group_rows': (miss.get('group_rows_ms') or {}).get('median'),
            },
            '8_serialization_json_ms': render_med,
            '9_gzip_ms': gzip_med,
            '10_largest_stage': largest,
            '11_next': (
                'Re-run on Railway for authoritative numbers; then optimize the largest HIT stage '
                '(likely Redis pickle GET and/or re-render, depending on Railway results).'
            ),
        },
    }


def _measure_case(payload_raw: dict, label: str, *, samples: int | None = None, concurrency: bool = True) -> dict[str, Any]:
    global SAMPLES
    saved = SAMPLES
    if samples is not None:
        SAMPLES = samples
    try:
        data = _validated(payload_raw)
        # Normalize payload for HTTP (date as string)
        http_payload = deepcopy(payload_raw)

        _log(f'[{label}] HTTP E2E (samples={SAMPLES})...')
        http_e2e = measure_http_e2e(http_payload, label=label)

        _log(f'[{label}] Redis HIT timings...')
        cache_hit = measure_cache_hit_redis(data)

        _log(f'[{label}] MISS pipeline (in-process, no invalidation)...')
        miss = measure_miss_pipeline(data)

        concurrency_result = {'skipped': True, 'reason': 'disabled_for_this_case'}
        if concurrency:
            _log(f'[{label}] Concurrency...')
            concurrency_result = measure_concurrency(http_payload)

        matching = Traffic.objects.filter(
            survey_date=data['date'],
            project__project_name__iexact=data['project_name'][0],
        ).count()

        return {
            'label': label,
            'request': http_payload,
            'matching_db_rows_approx': matching,
            'http_e2e': http_e2e,
            'cache_hit_redis': cache_hit,
            'miss_pipeline': miss,
            'concurrency': concurrency_result,
        }
    finally:
        SAMPLES = saved


def main() -> int:
    env = _env_snapshot()
    _log(f"context={env['runtime']['measurement_context']} on_railway={env['runtime']['on_railway']}")

    primary = _measure_case(PRIMARY_PAYLOAD, 'primary_abu_road', samples=30, concurrency=True)
    # Fewer samples for large payload to keep investigation runtime bounded
    large = _measure_case(LARGE_PAYLOAD, 'large_chittorgarh', samples=10, concurrency=False)

    report = {
        'investigation_only': True,
        'production_behavior_changed': False,
        'api': {
            'endpoint': 'POST /traffic_filter/',
            'auth': 'none (APIView default — no JWT on this view)',
            'pagination': 'none — returns full nested array',
            'cache': {
                'helper': 'cached_filter_response → get_or_set_cached_value',
                'namespace': CACHE_NS_TRAFFIC_FILTER,
                'ttl_seconds': CACHE_TTL_FILTER,
                'representation': 'pickled Python nested list (django-redis default)',
                'stampede_lock': True,
            },
            'response': 'list[list[dict]] NHIT traffic fields',
        },
        'environment': env,
        'primary': primary,
        'large': large,
    }
    report['decision'] = _classify(report)
    report['timing_breakdown_primary_hit'] = _timing_table(report)

    OUT_JSON.write_text(json.dumps(report, indent=2, default=str), encoding='utf-8')
    _write_md(report)
    _log(f'Wrote {OUT_JSON}')
    _log(f'Wrote {OUT_MD}')
    _log('Traffic Filter performance investigation complete — no production behavior changed.')
    return 0


def _timing_table(report: dict) -> list[dict[str, Any]]:
    p = report['primary']
    http = p['http_e2e']['http_accept_gzip_ms']
    redis = p.get('cache_hit_redis') or {}
    miss = p.get('miss_pipeline') or {}
    rows = [
        {'stage': 'HTTP total (Accept-Encoding:gzip, mostly HIT after warmup)', 'median': http.get('median'), 'p95': http.get('p95')},
        {'stage': 'Redis/django cache GET', 'median': (redis.get('django_safe_cache_get_ms') or {}).get('median'), 'p95': (redis.get('django_safe_cache_get_ms') or {}).get('p95')},
        {'stage': 'Redis-py raw GET', 'median': ((redis.get('redis_py_raw_get') or {}).get('latency_ms') or {}).get('median'), 'p95': ((redis.get('redis_py_raw_get') or {}).get('latency_ms') or {}).get('p95')},
        {'stage': 'JSON render from cached object', 'median': (redis.get('json_render_from_cached_object_ms') or {}).get('median'), 'p95': (redis.get('json_render_from_cached_object_ms') or {}).get('p95')},
        {'stage': 'Gzip compress (modeled)', 'median': (redis.get('gzip_compress_rendered_ms') or {}).get('median'), 'p95': (redis.get('gzip_compress_rendered_ms') or {}).get('p95')},
        {'stage': 'MISS values() fetch', 'median': (miss.get('values_fetch_ms') or {}).get('median'), 'p95': (miss.get('values_fetch_ms') or {}).get('p95')},
        {'stage': 'MISS serialize rows', 'median': (miss.get('serialize_rows_ms') or {}).get('median'), 'p95': (miss.get('serialize_rows_ms') or {}).get('p95')},
        {'stage': 'MISS group rows', 'median': (miss.get('group_rows_ms') or {}).get('median'), 'p95': (miss.get('group_rows_ms') or {}).get('p95')},
        {'stage': 'MISS build total', 'median': (miss.get('build_total_ms') or {}).get('median'), 'p95': (miss.get('build_total_ms') or {}).get('p95')},
        {'stage': 'Neon SELECT 1 reused', 'median': (miss.get('select1_reused_ms') or {}).get('median'), 'p95': (miss.get('select1_reused_ms') or {}).get('p95')},
    ]
    return rows


def _write_md(report: dict) -> None:
    env = report['environment']
    decision = report['decision']
    p = report['primary']
    L = report['large']
    http = p['http_e2e']
    redis = p['cache_hit_redis']
    miss = p['miss_pipeline']
    answers = decision['answers']

    lines = [
        '# Traffic Filter API — Performance Investigation',
        '',
        '**Traffic Filter performance investigation complete — no production behavior changed.**',
        '',
        '## 1. Executive Summary',
        '',
        f"- **Classification:** {', '.join(decision['classification'])}",
        f"- **Largest measured stage:** `{decision.get('largest_measured_stage')}`",
        f"- **Environment:** `{env['runtime']['measurement_context']}` (on_railway={env['runtime']['on_railway']})",
        f"- **Primary median HTTP (gzip):** **{answers['1_median_total_response_ms']} ms**",
        f"- **Primary P95:** **{answers['2_p95_ms']} ms**",
        f"- **Response size:** uncompressed={answers['3_response_size']['uncompressed']}, gzip={answers['3_response_size']['gzip']}",
        f"- **Cached:** {answers['4_is_cached']} (namespace `traffic_filter`, TTL {CACHE_TTL_FILTER}s, pickle object)",
        '',
        'Findings:',
        '',
    ]
    for f in decision['findings']:
        lines.append(f'- {f}')

    lines += [
        '',
        '## 2. API / request tested',
        '',
        f"- Endpoint: `{report['api']['endpoint']}`",
        f"- Auth: {report['api']['auth']}",
        f"- Pagination: {report['api']['pagination']}",
        f"- Cache: `{json.dumps(report['api']['cache'])}`",
        '',
        'Primary request (contract / Railway suite):',
        '',
        '```json',
        json.dumps(PRIMARY_PAYLOAD, indent=2),
        '```',
        '',
        f"- Approx matching DB rows: **{p['matching_db_rows_approx']}**",
        f"- Response groups/rows: **{http['response'].get('group_count')}** / **{http['response'].get('row_count')}**",
        '',
        'Large request (scale context, same fields):',
        '',
        '```json',
        json.dumps(LARGE_PAYLOAD, indent=2),
        '```',
        '',
        f"- Approx matching DB rows: **{L['matching_db_rows_approx']}**",
        f"- Response groups/rows: **{L['http_e2e']['response'].get('group_count')}** / **{L['http_e2e']['response'].get('row_count')}**",
        '',
        '## 3. Environment',
        '',
        f"```json\n{json.dumps(env, indent=2)}\n```",
        '',
        '## 4. Response size',
        '',
        f"- Primary uncompressed: **{http['response']['uncompressed_bytes']:,}** bytes",
        f"- Primary gzip wire: **{http['response']['gzip_wire_bytes']:,}** bytes",
        f"- Content-Encoding when Accept-Encoding:gzip: `{http['response']['content_encoding_when_requested']}`",
        f"- Large uncompressed: **{L['http_e2e']['response']['uncompressed_bytes']:,}**",
        f"- Large gzip: **{L['http_e2e']['response']['gzip_wire_bytes']:,}**",
        '',
        '## 5. End-to-end timings',
        '',
        '| Case | Median | P95 | P99 | Max |',
        '|---|---:|---:|---:|---:|',
        f"| Primary + gzip | {http['http_accept_gzip_ms']['median']} | {http['http_accept_gzip_ms']['p95']} | {http['http_accept_gzip_ms']['p99']} | {http['http_accept_gzip_ms']['max']} |",
        f"| Primary no gzip | {http['http_no_accept_gzip_ms']['median']} | {http['http_no_accept_gzip_ms']['p95']} | {http['http_no_accept_gzip_ms']['p99']} | {http['http_no_accept_gzip_ms']['max']} |",
        f"| Large + gzip | {L['http_e2e']['http_accept_gzip_ms']['median']} | {L['http_e2e']['http_accept_gzip_ms']['p95']} | {L['http_e2e']['http_accept_gzip_ms']['p99']} | {L['http_e2e']['http_accept_gzip_ms']['max']} |",
        '',
        '## 6–7. Cache HIT / Redis',
        '',
        f"```json\n{json.dumps({k: redis.get(k) for k in ('key_meta','cache_hit','value_type','pickle_size_estimate_bytes','django_safe_cache_get_ms','redis_py_raw_get','json_render_from_cached_object_ms','gzip_compress_rendered_ms','rendered_json_bytes','gzip_bytes')}, indent=2, default=str)}\n```",
        '',
        '## 8–9. Database / Python (MISS pipeline, no invalidation)',
        '',
        f"```json\n{json.dumps({k: miss.get(k) for k in ('note','row_count_median','query_count_median','db_connection_establish_ms','select1_reused_ms','values_fetch_ms','serialize_rows_ms','group_rows_ms','build_total_ms','builder_parity_ok')}, indent=2, default=str)}\n```",
        '',
        '## 10–11. Serialization / Gzip',
        '',
        f"- HIT JSON render median: **{(redis.get('json_render_from_cached_object_ms') or {}).get('median')} ms**",
        f"- HIT gzip median: **{(redis.get('gzip_compress_rendered_ms') or {}).get('median')} ms**",
        f"- MISS JSON render median: **{(miss.get('json_render_ms') or {}).get('median')} ms**",
        f"- MISS gzip median: **{(miss.get('gzip_ms') or {}).get('median')} ms**",
        '',
        '## 12. Network / transfer analysis',
        '',
        '- Client-observed HTTP time includes Test Client stack + Redis GET + render + middleware.',
        '- Compare Redis GET + render + gzip sum vs HTTP total; residual is framework/transfer overhead.',
        f"- Primary gzip ratio: {round(100*(1 - (http['response']['gzip_wire_bytes'] or 0)/max(http['response']['uncompressed_bytes'] or 1,1)), 1)}% size reduction",
        '',
        '## 13. Concurrency',
        '',
        '| Conc | OK | Err | Median | P95 | req/s |',
        '|---:|---:|---:|---:|---:|---:|',
    ]
    for lvl in p['concurrency']['levels']:
        lat = lvl['latency_ms']
        lines.append(
            f"| {lvl['concurrency']} | {lvl['successful']} | {lvl['errors']} | "
            f"{lat.get('median')} | {lat.get('p95')} | {lvl.get('requests_per_sec')} |"
        )

    lines += [
        '',
        '## 14. Local vs Railway',
        '',
        f"- This run: **{env['runtime']['measurement_context']}**",
        '- Railway CLI may be required for authoritative production numbers.',
        '- Re-run: `python tools/benchmark_traffic_filter_performance.py` on Railway.',
        '',
        '## 15. Timing breakdown (primary)',
        '',
        '| Stage | Median | P95 |',
        '|---|---:|---:|',
    ]
    for row in report['timing_breakdown_primary_hit']:
        lines.append(f"| {row['stage']} | {row['median']} | {row['p95']} |")

    lines += [
        '',
        '## 16. Bottleneck classification',
        '',
        f"- Classes: {', '.join(decision['classification'])}",
        f"- Largest stage: `{decision.get('largest_measured_stage')}`",
        '',
        '## 17. Recommendation / answers',
        '',
        f"1. Median total response: **{answers['1_median_total_response_ms']} ms**",
        f"2. P95: **{answers['2_p95_ms']} ms**",
        f"3. Response size: `{answers['3_response_size']}`",
        f"4. Cached: **{answers['4_is_cached']}**",
        f"5. Redis: `{answers['5_redis_ms']}`",
        f"6. PostgreSQL: `{answers['6_postgres_ms']}`",
        f"7. Python processing: `{answers['7_python_processing_ms']}`",
        f"8. Serialization/JSON: **{answers['8_serialization_json_ms']} ms**",
        f"9. Gzip: **{answers['9_gzip_ms']} ms**",
        f"10. Largest time: **{answers['10_largest_stage']}**",
        f"11. Next: {answers['11_next']}",
        '',
        'Compare to inventory_filter: inventory HIT uses compressed JSON bytes + direct-byte serving; '
        'traffic_filter still pickles a Python nested object and re-renders JSON on every HIT. '
        'Do not assume identical bottlenecks without Railway confirmation.',
        '',
        '---',
        '',
        '**Traffic Filter performance investigation complete — no production behavior changed.**',
        '',
        f'Raw: `{OUT_JSON.name}`',
        '',
    ]
    OUT_MD.write_text('\n'.join(lines), encoding='utf-8')


if __name__ == '__main__':
    raise SystemExit(main())
