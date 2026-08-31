"""Railway internal Redis benchmark — Redis OFF vs MISS vs HIT for six filter APIs.

Run inside the Railway deployment (private network to redis.railway.internal):

    python manage.py benchmark_railway_redis

Or directly:

    python tools/benchmark_railway_internal_redis.py

Analysis-only — does not modify production API or cache code.
"""
from __future__ import annotations

import json
import logging
import os
import statistics
import sys
import time
import uuid
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent

WARMUP_RUNS = 1
MEASURED_RUNS = 10
LATENCY_SAMPLES = 10
PMS_LATENCY_PAYLOAD_BYTES = 4_000_000
CONNECTIVITY_KEY = 'nhit:benchmark:railway:connectivity'

ENDPOINTS: list[dict[str, Any]] = [
    {
        'name': 'inventory_filter',
        'path': '/inventory_filter/',
        'cached': True,
        'namespace': 'inventory_filter',
        'invalidate_name': 'invalidate_inventory_filter_cache',
        'payload_source': 'compare_inventory_filter',
        'payload': {
            'chainage_start': 0,
            'chainage_end': 1381,
            'date': '2026-04-20',
            'direction': ['All'],
            'project_name': ['Agra Bypass Road'],
            'asset_type': ['All'],
        },
        'auth': False,
        'serializer_import': ('inventory.serializers', 'InventoryFilterSerializer'),
        'pms_extra_key': False,
    },
    {
        'name': 'distress_report_filter',
        'path': '/distress_report_filter/',
        'cached': True,
        'namespace': 'distress_report_filter',
        'invalidate_name': 'invalidate_distress_report_filter_cache',
        'payload_source': 'profile_distress_report_filter',
        'payload': {
            'chainage_start': 0,
            'chainage_end': 1381,
            'date': '2026-07-20',
            'direction': ['All'],
            'distress_type': ['All'],
            'project_name': ['Abu Road to Swaroopganj'],
        },
        'auth': True,
        'serializer_import': ('distress.serializers', 'DistressFilterSerializer'),
        'pms_extra_key': False,
    },
    {
        'name': 'traffic_filter',
        'path': '/traffic_filter/',
        'cached': True,
        'namespace': 'traffic_filter',
        'invalidate_name': 'invalidate_traffic_filter_cache',
        'payload_source': 'validate_traffic_filter',
        'payload': {
            'chainage_start': 0,
            'chainage_end': 1381,
            'date': '2026-05-20',
            'direction': ['All'],
            'project_name': ['Abu Road to Swaroopganj'],
        },
        'auth': False,
        'serializer_import': ('traffic.serializers', 'TrafficFilterRequestSerializer'),
        'pms_extra_key': False,
    },
    {
        'name': 'ais_filter',
        'path': '/ais_filter/',
        'cached': True,
        'namespace': 'ais_filter',
        'invalidate_name': 'invalidate_ais_filter_cache',
        'payload_source': 'compare_ais_filter',
        'payload': {
            'chainage_start': 0,
            'chainage_end': 1381,
            'date': '2025-06-20',
            'direction': ['All'],
            'project_name': ['Abu Road to Swaroopganj'],
        },
        'auth': False,
        'serializer_import': ('ais.serializers', 'AisFilterRequestSerializer'),
        'pms_extra_key': False,
    },
    {
        'name': 'pms_filter',
        'path': '/pms_filter/',
        'cached': True,
        'namespace': 'pms_filter',
        'invalidate_name': 'invalidate_pms_filter_cache',
        'payload_source': 'compare_pms_filter',
        'payload': {
            'chainage_start': 0,
            'chainage_end': 1381,
            'date': '2026-04-20',
            'direction': ['All'],
            'project_name': ['Abu Road to Swaroopganj'],
        },
        'auth': False,
        'serializer_import': ('pavement.serializers', 'PmsFilterRequestSerializer'),
        'pms_extra_key': True,
    },
    {
        'name': 'rwfis_filter',
        'path': '/rwfis_filter/',
        'cached': False,
        'namespace': None,
        'invalidate_name': None,
        'payload_source': 'Abu Road sibling filters; direction Increasing',
        'payload': {
            'chainage_start': 0,
            'chainage_end': 1381,
            'date': '2025-06-20',
            'direction': ['Increasing'],
            'project_name': ['Abu Road to Swaroopganj'],
        },
        'auth': False,
        'serializer_import': None,
        'pms_extra_key': False,
    },
]

_cache_events: list[dict[str, Any]] = []
_response_get_or_set_original = None


def _import_serializer(endpoint: dict[str, Any]):
    if not endpoint.get('serializer_import'):
        return None
    module_name, class_name = endpoint['serializer_import']
    import importlib

    module = importlib.import_module(module_name)
    return getattr(module, class_name)


def _get_invalidate(endpoint: dict[str, Any]):
    if not endpoint.get('invalidate_name'):
        return None
    import nhit.caching.invalidation as inv

    return getattr(inv, endpoint['invalidate_name'])


def _install_instrumentation() -> None:
    global _response_get_or_set_original
    import nhit.caching.response as caching_response

    if _response_get_or_set_original is None:
        _response_get_or_set_original = caching_response.get_or_set_cached_value

    def _instrumented(*args, **kwargs):
        value, meta = _response_get_or_set_original(*args, **kwargs)
        _cache_events.append(deepcopy(meta))
        return value, meta

    caching_response.get_or_set_cached_value = _instrumented


def _remove_instrumentation() -> None:
    import nhit.caching.response as caching_response

    if _response_get_or_set_original is not None:
        caching_response.get_or_set_cached_value = _response_get_or_set_original


def _redis_networking_label(redis_url: str) -> str:
    host = urlparse(redis_url).hostname or ''
    if 'railway.internal' in host:
        return 'INTERNAL'
    if 'rlwy.net' in host or 'proxy' in host:
        return 'PUBLIC'
    return 'UNKNOWN'


def _verify_redis_configured() -> tuple[bool, str, str]:
    """Return (connected, networking_label, error_message). Never prints URL/password."""
    redis_url = os.environ.get('REDIS_URL', '').strip()
    if not redis_url:
        return False, 'NONE', 'REDIS_URL is not set'

    networking = _redis_networking_label(redis_url)
    import redis as redis_lib

    token = f'ok-{uuid.uuid4().hex[:8]}'
    client = None
    try:
        client = redis_lib.from_url(redis_url, socket_connect_timeout=8)
        client.set(CONNECTIVITY_KEY, token, ex=30)
        got = client.get(CONNECTIVITY_KEY)
        client.delete(CONNECTIVITY_KEY)
        if got is None:
            return False, networking, 'SET/GET test returned None'
        if isinstance(got, bytes):
            got = got.decode('utf-8')
        if got != token:
            return False, networking, 'SET/GET value mismatch'
        return True, networking, ''
    except Exception as exc:
        return False, networking, type(exc).__name__
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                pass


def _measure_redis_latency(redis_url: str) -> dict[str, Any]:
    import redis as redis_lib

    client = redis_lib.from_url(redis_url, socket_connect_timeout=15)
    results: dict[str, Any] = {}

    try:
        for label, size in (('small_1kb', 1024), ('large_4mb', PMS_LATENCY_PAYLOAD_BYTES)):
            payload = 'x' * size
            set_times: list[float] = []
            get_times: list[float] = []

            for _ in range(LATENCY_SAMPLES):
                key = f'nhit:benchmark:latency:{label}:{uuid.uuid4().hex}'
                start = time.perf_counter()
                client.set(key, payload, ex=60)
                set_times.append((time.perf_counter() - start) * 1000)

                start = time.perf_counter()
                client.get(key)
                get_times.append((time.perf_counter() - start) * 1000)

                client.delete(key)

            sorted_get = sorted(get_times)
            sorted_set = sorted(set_times)
            p95_idx = max(0, min(len(sorted_get) - 1, int(len(sorted_get) * 0.95) - 1))

            results[label] = {
                'get_avg_ms': round(statistics.mean(get_times), 2),
                'get_median_ms': round(statistics.median(get_times), 2),
                'get_p95_ms': round(sorted_get[p95_idx], 2),
                'set_avg_ms': round(statistics.mean(set_times), 2),
                'set_median_ms': round(statistics.median(set_times), 2),
                'set_p95_ms': round(sorted_set[p95_idx], 2),
                'payload_bytes': size,
            }
    finally:
        client.close()

    small = results['small_1kb']
    return {
        'get_avg_ms': small['get_avg_ms'],
        'get_median_ms': small['get_median_ms'],
        'get_p95_ms': small['get_p95_ms'],
        'set_avg_ms': small['set_avg_ms'],
        'set_median_ms': small['set_median_ms'],
        'set_p95_ms': small['set_p95_ms'],
        'small_payload': results['small_1kb'],
        'large_payload': results['large_4mb'],
    }


def _disable_cache(settings) -> None:
    settings.NHIT_CACHE_ENABLED = False


def _enable_cache(settings) -> None:
    redis_url = os.environ.get('REDIS_URL', '').strip()
    if not redis_url:
        raise RuntimeError('REDIS_URL is not set')
    settings.NHIT_CACHE_ENABLED = True
    settings.CACHES = {
        'default': {
            'BACKEND': 'django_redis.cache.RedisCache',
            'LOCATION': redis_url,
            'OPTIONS': {
                'CLIENT_CLASS': 'django_redis.client.DefaultClient',
                'IGNORE_EXCEPTIONS': True,
            },
            'KEY_PREFIX': 'nhit',
        }
    }
    from django.core.cache import caches

    for alias in list(getattr(caches, '_caches', {}).keys()):
        try:
            caches[alias].close()
        except Exception:
            pass
    caches._caches = {}


def _validated_payload(endpoint: dict[str, Any]) -> dict[str, Any]:
    serializer_cls = _import_serializer(endpoint)
    if serializer_cls is None:
        return deepcopy(endpoint['payload'])
    serializer = serializer_cls(data=endpoint['payload'])
    if not serializer.is_valid():
        raise ValueError(f"{endpoint['name']} payload invalid: {serializer.errors}")
    return dict(serializer.validated_data)


def _cache_payload(endpoint: dict[str, Any]) -> Any:
    data = _validated_payload(endpoint)
    if endpoint.get('pms_extra_key'):
        direction_filter_all = any(
            str(v).strip().casefold() == 'all' for v in data.get('direction', [])
        )
        return {**data, '_direction_filter_all': direction_filter_all}
    return data


def _production_cache_key(endpoint: dict[str, Any], version: int) -> str:
    from nhit.caching.keys import build_cache_key

    return build_cache_key(
        namespace=endpoint['namespace'],
        version=version,
        payload=_cache_payload(endpoint),
    )


def _key_exists(cache_key: str) -> bool:
    from nhit.caching.service import safe_cache_get

    hit, _ = safe_cache_get(cache_key)
    return hit


@dataclass
class RunStats:
    state: str
    samples: list[dict[str, Any]] = field(default_factory=list)

    def add(self, sample: dict[str, Any]) -> None:
        self.samples.append(sample)

    def summary(self) -> dict[str, Any]:
        times = [s['elapsed_ms'] for s in self.samples]
        queries = [s['queries'] for s in self.samples]
        sizes = [s['size_bytes'] for s in self.samples]
        sorted_times = sorted(times)
        p95_index = max(0, min(len(sorted_times) - 1, int(len(sorted_times) * 0.95) - 1))

        return {
            'state': self.state,
            'runs': len(self.samples),
            'avg_ms': round(statistics.mean(times), 2),
            'median_ms': round(statistics.median(times), 2),
            'p95_ms': round(sorted_times[p95_index], 2),
            'min_ms': round(min(times), 2),
            'max_ms': round(max(times), 2),
            'avg_queries': round(statistics.mean(queries), 2),
            'avg_size_bytes': round(statistics.mean(sizes)),
            'status_codes': sorted({s['status'] for s in self.samples}),
        }


def _request(client, endpoint: dict[str, Any], *, create_access_token) -> dict[str, Any]:
    global _cache_events
    _cache_events = []

    from django.db import connection, reset_queries

    reset_queries()
    connection.force_debug_cursor = True
    start = time.perf_counter()
    kwargs: dict[str, Any] = {'content_type': 'application/json'}
    if endpoint.get('auth'):
        token = create_access_token('76')
        kwargs['HTTP_AUTHORIZATION'] = f'Bearer {token}'

    response = client.post(endpoint['path'], endpoint['payload'], **kwargs)
    elapsed_ms = (time.perf_counter() - start) * 1000
    connection.force_debug_cursor = False

    meta = _cache_events[-1] if _cache_events else {}
    return {
        'status': response.status_code,
        'elapsed_ms': round(elapsed_ms, 2),
        'queries': len(connection.queries),
        'size_bytes': len(response.content),
        'body': response.content,
        'cache_hit': meta.get('cache_hit'),
        'build_ms': meta.get('build_ms'),
    }


def _run_off(client, endpoint: dict[str, Any], settings, create_access_token) -> RunStats:
    stats = RunStats(state='OFF')
    _disable_cache(settings)

    for _ in range(WARMUP_RUNS):
        _request(client, endpoint, create_access_token=create_access_token)

    for _ in range(MEASURED_RUNS):
        stats.add(_request(client, endpoint, create_access_token=create_access_token))

    return stats


def _run_miss(client, endpoint: dict[str, Any], settings, create_access_token) -> tuple[RunStats, list[dict[str, Any]]]:
    stats = RunStats(state='MISS')
    verifications: list[dict[str, Any]] = []
    invalidate = _get_invalidate(endpoint)
    _enable_cache(settings)

    for _ in range(MEASURED_RUNS):
        version = invalidate()
        cache_key = _production_cache_key(endpoint, version)
        key_before = _key_exists(cache_key)

        sample = _request(client, endpoint, create_access_token=create_access_token)
        key_after = _key_exists(cache_key)

        from nhit.caching.service import safe_cache_get

        hit, cached_value = safe_cache_get(cache_key)

        verification = {
            'version': version,
            'cache_key_suffix': cache_key.rsplit(':', 1)[-1][:12],
            'key_found_before_miss': key_before,
            'key_found_after_miss': key_after,
            'miss_confirmed': (
                not key_before
                and sample.get('cache_hit') is False
                and sample.get('build_ms') is not None
            ),
            'set_confirmed': key_after and cached_value is not None,
            'db_queries': sample['queries'],
            'cache_hit': sample.get('cache_hit'),
            'build_ms': sample.get('build_ms'),
        }
        verifications.append(verification)
        stats.add(sample)

    return stats, verifications


def _run_hit(
    client,
    endpoint: dict[str, Any],
    settings,
    create_access_token,
    *,
    populate_version: int,
) -> tuple[RunStats, list[dict[str, Any]]]:
    stats = RunStats(state='HIT')
    verifications: list[dict[str, Any]] = []
    _enable_cache(settings)
    cache_key = _production_cache_key(endpoint, populate_version)

    if not _key_exists(cache_key):
        raise RuntimeError(f'Cache not populated for HIT phase: {endpoint["name"]}')

    from nhit.caching.service import safe_cache_get

    for _ in range(MEASURED_RUNS):
        key_before = _key_exists(cache_key)
        hit_before, cached_before = safe_cache_get(cache_key)

        sample = _request(client, endpoint, create_access_token=create_access_token)

        verification = {
            'key_exists_before': key_before,
            'production_cache_hit': hit_before,
            'cache_value_present': cached_before is not None,
            'hit_confirmed': (
                key_before
                and hit_before
                and sample.get('cache_hit') is True
                and sample.get('build_ms') is None
                and sample['queries'] == 0
            ),
            'db_queries': sample['queries'],
            'cache_hit': sample.get('cache_hit'),
            'build_ms': sample.get('build_ms'),
        }
        verifications.append(verification)
        stats.add(sample)

    return stats, verifications


def _deep_equal(a: Any, b: Any) -> bool:
    return json.dumps(a, sort_keys=True, default=str) == json.dumps(b, sort_keys=True, default=str)


def _parse_body(body: bytes) -> Any:
    return json.loads(body.decode('utf-8'))


def _summarize_verification(miss_v: list[dict], hit_v: list[dict]) -> dict[str, Any]:
    miss_valid = all(
        v['miss_confirmed'] and v['set_confirmed'] and not v['key_found_before_miss'] and v['db_queries'] > 0
        for v in miss_v
    )
    hit_valid = all(v['hit_confirmed'] for v in hit_v)
    return {
        'miss_confirmed': miss_valid,
        'set_confirmed': all(v['set_confirmed'] for v in miss_v),
        'hit_confirmed': hit_valid,
        'miss_samples_valid': sum(1 for v in miss_v if v['miss_confirmed'] and v['set_confirmed']),
        'hit_samples_valid': sum(1 for v in hit_v if v['hit_confirmed']),
    }


def _cache_decision(before_avg: float, hit_avg: float) -> str:
    if hit_avg < before_avg * 0.95:
        return 'KEEP CACHE'
    if hit_avg < before_avg:
        return 'INVESTIGATE'
    return 'REMOVE CACHE'


def _build_markdown(report: dict[str, Any]) -> str:
    env = report['environment']
    lines = [
        '# Railway Internal Redis Performance Benchmark',
        '',
        '## Environment',
        '',
        f"Django: {env['django']}",
        f"Database: {env['database']}",
        f"Redis: {env['redis_status']}",
        f"Redis networking: {env['redis_networking']}",
        '',
        '## Performance',
        '',
        '| API | Redis OFF Avg | Redis MISS Avg | Redis HIT Avg | Time Saved | Improvement % |',
        '|-----|---------------|----------------|---------------|------------|---------------|',
    ]

    for ep in report['endpoints']:
        name = ep['name']
        off = ep['off']['avg_ms']
        if ep['cached']:
            miss = ep['miss']['avg_ms']
            hit = ep['hit']['avg_ms']
            saved = round(off - hit, 2)
            imp = round(((off - hit) / off) * 100, 1) if off else 0
            lines.append(f'| {name} | {off} | {miss} | {hit} | {saved} | {imp}% |')
        else:
            lines.append(f'| {name} | {off} | N/A | N/A | N/A | N/A |')

    lines.extend(['', '## Detailed Statistics', ''])
    lines.append('| API | State | Avg | Median | P95 | Min | Max | DB Queries | Response Size |')
    lines.append('|-----|-------|-----|--------|-----|-----|-----|------------|---------------|')

    for ep in report['endpoints']:
        for state_key, label in (('off', 'OFF'), ('miss', 'MISS'), ('hit', 'HIT')):
            if state_key == 'miss' and not ep['cached']:
                continue
            if state_key in ('miss', 'hit') and not ep['cached']:
                continue
            s = ep[state_key]
            lines.append(
                f"| {ep['name']} | {label} | {s['avg_ms']} | {s['median_ms']} | {s['p95_ms']} | "
                f"{s['min_ms']} | {s['max_ms']} | {s['avg_queries']} | {s['avg_size_bytes']} |"
            )

    lines.extend(['', '## Query Count', ''])
    lines.append('| API | OFF | MISS | HIT |')
    lines.append('|-----|-----|------|-----|')
    for ep in report['endpoints']:
        if ep['cached']:
            lines.append(
                f"| {ep['name']} | {ep['off']['avg_queries']} | "
                f"{ep['miss']['avg_queries']} | {ep['hit']['avg_queries']} |"
            )
        else:
            lines.append(f"| {ep['name']} | {ep['off']['avg_queries']} | N/A | N/A |")

    lat = report.get('redis_latency', {})
    lines.extend(['', '## Redis Latency', ''])
    lines.append('| Operation | Avg | Median | P95 |')
    lines.append('|-----------|-----|--------|-----|')
    lines.append(f"| GET (1KB) | {lat.get('get_avg_ms', 'N/A')} | {lat.get('get_median_ms', 'N/A')} | {lat.get('get_p95_ms', 'N/A')} |")
    lines.append(f"| SET (1KB) | {lat.get('set_avg_ms', 'N/A')} | {lat.get('set_median_ms', 'N/A')} | {lat.get('set_p95_ms', 'N/A')} |")
    if lat.get('large_payload'):
        lg = lat['large_payload']
        lines.append(f"| GET (4MB) | {lg['get_avg_ms']} | {lg['get_median_ms']} | {lg['get_p95_ms']} |")
        lines.append(f"| SET (4MB) | {lg['set_avg_ms']} | {lg['set_median_ms']} | {lg['set_p95_ms']} |")

    lines.extend(['', '## Response Correctness', ''])
    lines.append('| API | OFF vs MISS | MISS vs HIT | Exact Match |')
    lines.append('|-----|-------------|-------------|-------------|')
    for ep in report['endpoints']:
        if not ep['cached']:
            lines.append(f"| {ep['name']} | N/A | N/A | MATCH |")
            continue
        c = ep['correctness']
        lines.append(
            f"| {ep['name']} | {'MATCH' if c['off_vs_miss'] else 'FAIL'} | "
            f"{'MATCH' if c['miss_vs_hit'] else 'FAIL'} | {'MATCH' if c['exact_match'] else 'FAIL'} |"
        )

    lines.extend(['', '## Cache Verification', ''])
    lines.append('| API | MISS Verified | SET Verified | HIT Verified | HIT DB Queries |')
    lines.append('|-----|---------------|--------------|--------------|----------------|')
    for ep in report['endpoints']:
        if not ep['cached']:
            continue
        cs = ep['cache_status']
        lines.append(
            f"| {ep['name']} | {'YES' if cs['miss_confirmed'] else 'NO'} | "
            f"{'YES' if cs['set_confirmed'] else 'NO'} | "
            f"{'YES' if cs['hit_confirmed'] else 'NO'} | {ep['hit']['avg_queries']} |"
        )

    rwfis = next(ep for ep in report['endpoints'] if ep['name'] == 'rwfis_filter')
    rb = rwfis['off']
    lines.extend([
        '',
        '## RWFIS',
        '',
        f"Uncached baseline: avg={rb['avg_ms']}ms, median={rb['median_ms']}ms, "
        f"p95={rb['p95_ms']}ms, DB queries={rb['avg_queries']}, size={rb['avg_size_bytes']} bytes",
    ])

    pms = next((ep for ep in report['endpoints'] if ep['name'] == 'pms_filter'), None)
    if pms and pms.get('cached'):
        lines.extend([
            '',
            '## PMS Analysis',
            '',
            f"- Response size: {pms['off']['avg_size_bytes']} bytes",
            f"- Redis OFF: {pms['off']['avg_ms']} ms",
            f"- Redis MISS: {pms['miss']['avg_ms']} ms",
            f"- Redis HIT: {pms['hit']['avg_ms']} ms",
            f"- MISS overhead: {round(pms['miss']['avg_ms'] - pms['off']['avg_ms'], 2)} ms",
            f"- Time saved (OFF → HIT): {round(pms['off']['avg_ms'] - pms['hit']['avg_ms'], 2)} ms",
        ])

    lines.extend(['', '## Final Decision', ''])
    for name, decision in report.get('decisions', {}).items():
        lines.append(f'- **{name}**: {decision}')

    lines.extend(['', '## Answers', ''])
    for answer in report.get('answers', []):
        lines.append(answer)

    lines.extend(['', f"**{report['verdict']}**", ''])
    return '\n'.join(lines)


def run_benchmark(*, require_internal: bool = True) -> dict[str, Any]:
    """Execute the full benchmark. Requires Django to be configured."""
    import django
    from django.conf import settings
    from django.test import Client

    from authentication.jwt_utils import create_access_token

    logging.getLogger('nhit.cache').setLevel(logging.WARNING)

    connected, networking, error = _verify_redis_configured()
    print(f'Redis: {"CONNECTED" if connected else "FAILED"}')
    if not connected:
        print(f'Redis networking detected: {networking}')
        if error:
            print(f'Redis error type: {error}')
        if require_internal:
            return {
                'verdict': 'BENCHMARK INVALID',
                'benchmark_valid': False,
                'error': 'Redis connection failed',
                'environment': {'redis_status': 'FAILED', 'redis_networking': networking},
            }

    if require_internal and networking != 'INTERNAL':
        print('ERROR: REDIS_URL is not using Railway internal networking.')
        print('Run inside Railway: railway ssh -- python manage.py benchmark_railway_redis')
        return {
            'verdict': 'BENCHMARK INVALID',
            'benchmark_valid': False,
            'error': f'Redis networking is {networking}, expected INTERNAL',
            'environment': {
                'django': django.get_version(),
                'database': settings.DATABASES['default']['ENGINE'].rsplit('.', 1)[-1],
                'redis_status': 'CONNECTED' if connected else 'FAILED',
                'redis_networking': networking,
            },
        }

    redis_url = os.environ.get('REDIS_URL', '').strip()
    redis_latency = _measure_redis_latency(redis_url) if connected else {}

    client = Client()
    endpoint_results: list[dict[str, Any]] = []

    for endpoint in ENDPOINTS:
        ep_result: dict[str, Any] = {
            'name': endpoint['name'],
            'path': endpoint['path'],
            'cached': endpoint['cached'],
            'payload': endpoint['payload'],
        }

        off = _run_off(client, endpoint, settings, create_access_token)
        off_body = _parse_body(off.samples[-1]['body'])
        ep_result['off'] = off.summary()

        if not endpoint['cached']:
            ep_result['benchmark_valid'] = True
            endpoint_results.append(ep_result)
            continue

        _install_instrumentation()
        try:
            miss, miss_v = _run_miss(client, endpoint, settings, create_access_token)
            miss_body = _parse_body(miss.samples[-1]['body'])
            populate_version = miss_v[-1]['version']

            hit, hit_v = _run_hit(
                client,
                endpoint,
                settings,
                create_access_token,
                populate_version=populate_version,
            )
        finally:
            _remove_instrumentation()
            _disable_cache(settings)

        hit_body = _parse_body(hit.samples[-1]['body'])
        verification = _summarize_verification(miss_v, hit_v)

        ep_result['miss'] = miss.summary()
        ep_result['hit'] = hit.summary()
        ep_result['correctness'] = {
            'off_vs_miss': _deep_equal(off_body, miss_body),
            'miss_vs_hit': _deep_equal(miss_body, hit_body),
            'exact_match': _deep_equal(off_body, miss_body) and _deep_equal(miss_body, hit_body),
        }
        ep_result['cache_status'] = verification
        ep_result['benchmark_valid'] = (
            verification['miss_confirmed']
            and verification['hit_confirmed']
            and ep_result['correctness']['exact_match']
            and verification['miss_samples_valid'] == MEASURED_RUNS
            and verification['hit_samples_valid'] == MEASURED_RUNS
        )
        endpoint_results.append(ep_result)

    cached = [ep for ep in endpoint_results if ep['cached']]
    all_hit_zero = all(ep['hit']['avg_queries'] == 0 for ep in cached)
    decisions = {}
    best_abs = ('', 0.0)
    best_pct = ('', 0.0)

    for ep in cached:
        off_avg = ep['off']['avg_ms']
        hit_avg = ep['hit']['avg_ms']
        decisions[ep['name']] = _cache_decision(off_avg, hit_avg)
        saved = off_avg - hit_avg
        pct = ((off_avg - hit_avg) / off_avg) * 100 if off_avg else 0
        if saved > best_abs[1]:
            best_abs = (ep['name'], saved)
        if pct > best_pct[1]:
            best_pct = (ep['name'], pct)

    decisions['rwfis_filter'] = 'DO NOT CACHE'

    answers = [
        f"1. Is internal Redis faster than direct PostgreSQL? "
        f"{'Yes' if all(ep['hit']['avg_ms'] < ep['off']['avg_ms'] for ep in cached) else 'Mixed — see per-API results'}.",
        '2. How much faster is each API on Redis HIT? '
        + ', '.join(
            f"{ep['name']}: {round(((ep['off']['avg_ms'] - ep['hit']['avg_ms']) / ep['off']['avg_ms']) * 100, 1)}%"
            for ep in cached
        ),
        f"3. Does Redis HIT consistently execute 0 DB queries? {'Yes' if all_hit_zero else 'No'}.",
        f"4. Redis internal GET latency (1KB): {redis_latency.get('get_avg_ms', 'N/A')} ms avg.",
        f"5. Redis internal SET latency (1KB): {redis_latency.get('set_avg_ms', 'N/A')} ms avg.",
    ]

    pms = next((ep for ep in cached if ep['name'] == 'pms_filter'), None)
    if pms:
        pms_imp = round(((pms['off']['avg_ms'] - pms['hit']['avg_ms']) / pms['off']['avg_ms']) * 100, 1)
        answers.append(f"6. Does PMS benefit significantly from Redis? {'Yes' if pms_imp >= 20 else 'Partially' if pms_imp > 0 else 'No'} ({pms_imp}% improvement).")

    small_apis = [ep for ep in cached if ep['name'] != 'pms_filter']
    benefit_count = sum(1 for ep in small_apis if ep['hit']['avg_ms'] < ep['off']['avg_ms'])
    answers.append(
        f"7. Do Inventory, Traffic, AIS and Distress benefit? "
        f"{benefit_count}/{len(small_apis)} show HIT faster than OFF."
    )
    answers.append('8. Should RWFIS remain uncached? Yes — measured baseline only, no caching added.')
    answers.append(f"9. Largest absolute improvement: {best_abs[0]} ({round(best_abs[1], 2)} ms saved).")
    answers.append(f"10. Largest percentage improvement: {best_pct[0]} ({round(best_pct[1], 1)}%).")

    all_valid = (
        connected
        and (networking == 'INTERNAL' if require_internal else True)
        and all(ep.get('benchmark_valid', False) for ep in cached)
        and all_hit_zero
    )

    report = {
        'environment': {
            'django': django.get_version(),
            'database': settings.DATABASES['default']['ENGINE'].rsplit('.', 1)[-1],
            'redis_status': 'CONNECTED' if connected else 'FAILED',
            'redis_networking': networking,
            'warmup_runs': WARMUP_RUNS,
            'measured_runs': MEASURED_RUNS,
        },
        'redis_latency': redis_latency,
        'endpoints': endpoint_results,
        'decisions': decisions,
        'answers': answers,
        'benchmark_valid': all_valid,
        'verdict': 'BENCHMARK VALID' if all_valid else 'BENCHMARK INVALID',
    }

    return report


def main() -> int:
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'nhit.settings')

    import django

    django.setup()

    require_internal = os.environ.get('BENCHMARK_ALLOW_PUBLIC_REDIS', '').strip().lower() not in {
        '1', 'true', 'yes',
    }
    report = run_benchmark(require_internal=require_internal)

    out_dir = ROOT / 'tools'
    out_dir.mkdir(exist_ok=True)
    json_path = out_dir / 'railway_internal_redis_benchmark_report.json'
    md_path = out_dir / 'railway_internal_redis_benchmark_report.md'

    json_path.write_text(json.dumps(report, indent=2, default=str), encoding='utf-8')
    if report.get('endpoints'):
        md_path.write_text(_build_markdown(report), encoding='utf-8')
        print('\n' + _build_markdown(report))
    print(f'\nJSON: {json_path}')
    if report.get('endpoints'):
        print(f'Markdown: {md_path}')
    print(f"\n{report.get('verdict', 'BENCHMARK INVALID')}")

    return 0 if report.get('benchmark_valid') else 1


if __name__ == '__main__':
    raise SystemExit(main())
