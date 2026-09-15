#!/usr/bin/env python
"""MEASUREMENT ONLY: Railway private Redis validation for distress_predict_filter.

Safe output only — never prints passwords, full URLs, or credentials.
Deletes temporary probe keys. Does not flush Redis. Does not change app config.
"""
from __future__ import annotations

import json
import logging
import os
import statistics
import time
import uuid
from pathlib import Path
from urllib.parse import urlparse

# Force production-like settings when run via `railway run`
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'nhit.settings')
# Controlled measurement only for this process
os.environ['DISTRESS_PREDICT_FILTER_PERF_DEBUG'] = 'true'

import django

django.setup()

from django.conf import settings
from django.core.cache import cache
from django.test import Client, override_settings

from authentication.jwt_utils import create_access_token
from nhit.caching.constants import CACHE_NS_DISTRESS_PREDICT_FILTER
from nhit.caching.keys import build_cache_key
from nhit.caching.distress_predict_filter_count import (
    DISTRESS_PREDICT_FILTER_COUNT_SCOPE,
    filter_payload_without_pagination,
)
from nhit.caching.distress_predict_filter_page import DISTRESS_PREDICT_FILTER_PAGE_SCOPE
from nhit.caching.service import get_namespace_version, safe_cache_get, safe_cache_get_many

ENDPOINT = '/distress_predict_filter/'
AUDIT_PAYLOAD = {
    'chainage_start': 0,
    'chainage_end': 10000,
    'date': '2026-08-20',
    'direction': ['All'],
    'distress_type': ['All'],
    'project_name': ['Abu Road to Swaroopganj'],
    'page': 1,
    'page_size': 200000,
}
LARGE_PAYLOAD = {
    'chainage_start': 0,
    'chainage_end': 100000,
    'direction': ['All'],
    'distress_type': ['All'],
    'project_name': ['Abu Road to Swaroopganj'],
    'page': 1,
    'page_size': 200000,
}

PROBE_PREFIX = f'nhit:private_redis_validation:{uuid.uuid4().hex}:'


class MemHandler(logging.Handler):
    def __init__(self):
        super().__init__()
        self.events = []

    def emit(self, record):
        try:
            msg = record.getMessage()
            if 'distress_predict_filter_performance' in msg:
                self.events.append(json.loads(msg))
        except Exception:
            pass


def pct(values, p):
    if not values:
        return None
    s = sorted(values)
    if len(s) == 1:
        return s[0]
    k = (len(s) - 1) * (p / 100.0)
    f = int(k)
    c = min(f + 1, len(s) - 1)
    if f == c:
        return s[f]
    return round(s[f] + (s[c] - s[f]) * (k - f), 2)


def summarize(values):
    if not values:
        return None
    return {
        'n': len(values),
        'min': round(min(values), 3),
        'median': round(statistics.median(values), 3),
        'p95': pct(values, 95),
        'max': round(max(values), 3),
        'mean': round(statistics.mean(values), 3),
    }


def host_class(hostname: str) -> str:
    h = (hostname or '').lower()
    if 'railway.internal' in h:
        return 'railway_private_internal'
    if 'rlwy.net' in h or 'railway.app' in h or 'proxy' in h:
        return 'railway_public_proxy'
    if not h:
        return 'none'
    return 'other'


def inspect_redis_connection() -> dict:
    raw_internal = (os.environ.get('REDIS_INTERNAL_URL') or '').strip()
    raw_public = (os.environ.get('REDIS_URL') or '').strip()
    raw_public_alt = (os.environ.get('REDIS_PUBLIC_URL') or '').strip()
    railway_runtime = bool(
        (os.environ.get('RAILWAY_ENVIRONMENT') or '').strip()
        or (os.environ.get('RAILWAY_SERVICE_NAME') or '').strip()
    )
    location = (settings.CACHES.get('default') or {}).get('LOCATION') or ''
    parsed = urlparse(location) if location else None
    hostname = (parsed.hostname or '') if parsed else ''
    opts = (settings.CACHES.get('default') or {}).get('OPTIONS') or {}

    # Selection logic mirror (do not print chosen URL)
    from nhit.caching.redis_config import resolve_redis_url

    resolved = resolve_redis_url(
        redis_internal_url=raw_internal or None,
        redis_url=raw_public or None,
        use_internal=railway_runtime,
    )
    selected = 'none'
    if resolved:
        if railway_runtime and raw_internal and resolved == raw_internal:
            selected = 'REDIS_INTERNAL_URL'
        elif raw_public and resolved == raw_public:
            selected = 'REDIS_URL'
        elif raw_internal and resolved == raw_internal:
            selected = 'REDIS_INTERNAL_URL'
        else:
            selected = 'unknown_non_secret'

    pool_info = {'class': None, 'max_connections': None, 'created_connections': None}
    client_info = {'client_class': opts.get('CLIENT_CLASS')}
    try:
        client = cache.client.get_client(write=True)
        pool = getattr(client, 'connection_pool', None)
        if pool is not None:
            pool_info = {
                'class': type(pool).__name__,
                'max_connections': getattr(pool, 'max_connections', None),
                'created_connections': getattr(pool, '_created_connections', None),
            }
        client_info['redis_client_type'] = type(client).__name__
        # connection reuse: DefaultClient uses a shared ConnectionPool
        client_info['connection_reuse'] = 'django_redis_DefaultClient_shared_pool'
    except Exception as exc:
        client_info['inspect_error'] = type(exc).__name__

    return {
        'railway_runtime': railway_runtime,
        'railway_environment_set': bool((os.environ.get('RAILWAY_ENVIRONMENT') or '').strip()),
        'railway_service_name_set': bool((os.environ.get('RAILWAY_SERVICE_NAME') or '').strip()),
        'env_var_present': {
            'REDIS_INTERNAL_URL': bool(raw_internal),
            'REDIS_URL': bool(raw_public),
            'REDIS_PUBLIC_URL': bool(raw_public_alt),
        },
        'selected_env_var': selected,
        'NHIT_REDIS_USING_INTERNAL': bool(getattr(settings, 'NHIT_REDIS_USING_INTERNAL', False)),
        'NHIT_CACHE_ENABLED': bool(getattr(settings, 'NHIT_CACHE_ENABLED', False)),
        'scheme': (parsed.scheme if parsed else None),
        'hostname_class': host_class(hostname),
        'hostname_suffix': '.'.join(hostname.split('.')[-2:]) if hostname else '',
        'port': parsed.port if parsed else None,
        'using_internal_redis': bool(
            getattr(settings, 'NHIT_REDIS_USING_INTERNAL', False)
            and host_class(hostname) == 'railway_private_internal'
        ),
        'backend': (settings.CACHES.get('default') or {}).get('BACKEND'),
        'key_prefix': (settings.CACHES.get('default') or {}).get('KEY_PREFIX'),
        'options_keys': sorted(opts.keys()),
        'client': client_info,
        'connection_pool': pool_info,
    }


def redis_raw_client():
    return cache.client.get_client(write=True)


def microbench(n_small=30, n_large=15) -> dict:
    client = redis_raw_client()
    results = {}
    keys = []

    # PING
    samples = []
    for _ in range(n_small):
        t0 = time.perf_counter()
        client.ping()
        samples.append((time.perf_counter() - t0) * 1000)
    results['PING'] = summarize(samples)

    sizes = {
        'GET_1B': 1,
        'GET_64B': 64,
        'GET_1KB': 1024,
        'GET_10KB': 10 * 1024,
        'GET_100KB': 100 * 1024,
        'GET_500KB': 500 * 1024,
        'GET_1MB': 1024 * 1024,
    }
    for label, size in sizes.items():
        key = PROBE_PREFIX + label
        keys.append(key)
        value = b'x' * size
        client.set(key, value, ex=120)
        iters = n_small if size <= 10 * 1024 else n_large
        # discard cold
        client.get(key)
        samples = []
        for _ in range(iters):
            t0 = time.perf_counter()
            got = client.get(key)
            samples.append((time.perf_counter() - t0) * 1000)
            if got is None:
                break
        results[label] = summarize(samples)

    # MGET of 4 small keys (synthetic)
    mkeys = []
    for i in range(4):
        k = PROBE_PREFIX + f'MGET_{i}'
        mkeys.append(k)
        keys.append(k)
        client.set(k, b'y' * 64, ex=120)
    client.mget(mkeys)
    samples = []
    for _ in range(n_small):
        t0 = time.perf_counter()
        client.mget(mkeys)
        samples.append((time.perf_counter() - t0) * 1000)
    results['MGET_4x64B'] = summarize(samples)

    # Cleanup synthetic keys
    if keys or mkeys:
        try:
            client.delete(*(keys + mkeys))
        except Exception:
            for k in set(keys + mkeys):
                try:
                    client.delete(k)
                except Exception:
                    pass

    return results


def actual_page_get_bench(payload: dict, n=20) -> dict:
    """GET the real page cache key if present (no builder)."""
    version = get_namespace_version(CACHE_NS_DISTRESS_PREDICT_FILTER)
    page_key = build_cache_key(
        namespace=CACHE_NS_DISTRESS_PREDICT_FILTER,
        version=version,
        payload=payload,
        scope=DISTRESS_PREDICT_FILTER_PAGE_SCOPE,
    )
    count_key = build_cache_key(
        namespace=CACHE_NS_DISTRESS_PREDICT_FILTER,
        version=version,
        payload=filter_payload_without_pagination(payload),
        scope=DISTRESS_PREDICT_FILTER_COUNT_SCOPE,
    )

    # Warm
    hit, raw = safe_cache_get(page_key)
    page_bytes = len(raw) if hit and isinstance(raw, (bytes, bytearray)) else None

    get_samples = []
    mget_samples = []
    version_samples = []
    for _ in range(n):
        t0 = time.perf_counter()
        get_namespace_version(CACHE_NS_DISTRESS_PREDICT_FILTER)
        version_samples.append((time.perf_counter() - t0) * 1000)

        t1 = time.perf_counter()
        safe_cache_get(page_key)
        get_samples.append((time.perf_counter() - t1) * 1000)

        t2 = time.perf_counter()
        safe_cache_get_many([count_key, page_key])
        mget_samples.append((time.perf_counter() - t2) * 1000)

    return {
        'page_present': bool(hit),
        'page_payload_bytes': page_bytes,
        'version_get_ms': summarize(version_samples),
        'page_get_ms': summarize(get_samples),
        'mget_count_page_ms': summarize(mget_samples),
        'expected_hit_round_trips': 2,
        'hit_path_ops': ['namespace_version_GET', 'MGET(count_key, page_key)'],
    }


def http_hit_series(payload: dict, n_warm=20, label='audit') -> dict:
    handler = MemHandler()
    log = logging.getLogger('nhit.distress_predict_filter.perf')
    log.addHandler(handler)
    log.setLevel(logging.INFO)

    token = create_access_token(f'private.redis.validation.{label}')
    with override_settings(ALLOWED_HOSTS=['*', 'testserver', 'localhost']):
        client = Client()
        # warm / populate
        warm = client.post(
            ENDPOINT,
            data=json.dumps(payload),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Bearer {token}',
            HTTP_ACCEPT_ENCODING='gzip',
        )
        handler.events.clear()

        walls = []
        events = []
        for _ in range(n_warm):
            t0 = time.perf_counter()
            resp = client.post(
                ENDPOINT,
                data=json.dumps(payload),
                content_type='application/json',
                HTTP_AUTHORIZATION=f'Bearer {token}',
                HTTP_ACCEPT_ENCODING='gzip',
            )
            walls.append((time.perf_counter() - t0) * 1000)
            if handler.events:
                events.append(handler.events[-1])
            handler.events.clear()

    log.removeHandler(handler)

    def field(name):
        return [e[name] for e in events if e.get(name) is not None]

    last = events[-1] if events else {}
    return {
        'warm_status': warm.status_code,
        'warm_row_count': warm.get('X-Distress-Row-Count'),
        'warm_content_length': warm.get('Content-Length'),
        'hit_wall_ms': summarize(walls),
        'server_total_ms': summarize(field('server_total_ms')),
        'mget_ms': summarize(field('mget_ms')),
        'count_version_get_ms': summarize(field('count_version_get_ms')),
        'page_data_get_ms': summarize(field('page_data_get_ms')),
        'redis_get_ms': summarize(field('redis_get_ms')),
        'cache_decode_ms': summarize(field('cache_decode_ms')),
        'redis_round_trips': summarize(field('redis_round_trips')),
        'redis_payload_bytes': last.get('redis_payload_bytes'),
        'response_path': last.get('response_path'),
        'lookup_path': last.get('lookup_path'),
        'cache_hit_all': all(e.get('cache_hit') for e in events) if events else None,
        'count_cache_hit_all': all(e.get('count_cache_hit') for e in events) if events else None,
        'query_count_all_zero': all((e.get('query_count') or 0) == 0 for e in events) if events else None,
        'sample_event_safe': {
            k: last.get(k)
            for k in (
                'cache_hit',
                'count_cache_hit',
                'response_path',
                'lookup_path',
                'redis_round_trips',
                'mget_ms',
                'count_version_get_ms',
                'page_data_get_ms',
                'cache_decode_ms',
                'server_total_ms',
                'query_count',
                'response_bytes',
            )
        },
    }


def main():
    report = {
        'measurement_kind': 'MEASURED',
        'runtime_note': (
            'This script must be executed inside Railway backend runtime '
            '(railway run / railway ssh). Local public-proxy results are invalid for this report.'
        ),
        'connection': inspect_redis_connection(),
        'microbench': None,
        'actual_cache_keys_audit': None,
        'actual_cache_keys_large': None,
        'http_hit_audit': None,
        'http_hit_large': None,
    }

    conn = report['connection']
    if not conn.get('NHIT_CACHE_ENABLED'):
        report['error'] = 'cache_disabled'
        print(json.dumps(report, indent=2))
        return

    report['microbench'] = microbench()

    # Populate caches via HTTP then microbench real page keys + HIT timings
    report['http_hit_audit'] = http_hit_series(AUDIT_PAYLOAD, n_warm=20, label='audit')
    report['actual_cache_keys_audit'] = actual_page_get_bench(AUDIT_PAYLOAD, n=20)

    report['http_hit_large'] = http_hit_series(LARGE_PAYLOAD, n_warm=10, label='large')
    report['actual_cache_keys_large'] = actual_page_get_bench(LARGE_PAYLOAD, n=10)

    out_path = Path('tools') / 'distress_predict_filter_private_redis_validation.json'
    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(report, indent=2), encoding='utf-8')
        report['wrote'] = str(out_path)
    except Exception as exc:
        report['write_error'] = type(exc).__name__

    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    try:
        main()
    finally:
        # Restore process-local debug flag (does not change Railway service env)
        os.environ['DISTRESS_PREDICT_FILTER_PERF_DEBUG'] = 'false'
