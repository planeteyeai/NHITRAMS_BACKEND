"""Phase 4 INVESTIGATION ONLY — Redis/network latency from app runtime.

Measures Railway/app → Redis GET cost for ~640 KB inventory_filter page cache.
Does NOT modify production API, cache keys, TTL, or Redis contents of real keys.

Temporary synthetic keys use prefix ``nhit:phase4_bench:`` and are deleted after.

Run on Railway (preferred):

    python manage.py benchmark_inventory_filter_phase4_redis_network
    # or
    python tools/benchmark_inventory_filter_phase4_redis_network.py

Local (secondary — labels environment accordingly):

    python tools/benchmark_inventory_filter_phase4_redis_network.py
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
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'nhit.settings')
os.environ.setdefault('PYTHONUNBUFFERED', '1')

import django

django.setup()

import redis as redis_lib
from django.conf import settings
from django.core.cache import cache
from django.db import connection

from inventory.serializers import InventoryFilterSerializer
from nhit.caching.constants import CACHE_NS_INVENTORY_FILTER
from nhit.caching.inventory_filter_page import (
    INVENTORY_FILTER_PAGE_CACHE_MAGIC,
    INVENTORY_FILTER_PAGE_SCOPE,
    extract_inventory_filter_page_gzip_payload,
)
from nhit.caching.keys import build_cache_key
from nhit.caching.service import get_namespace_version, is_cache_usable, safe_cache_get

OUT_JSON = ROOT / 'tools' / 'benchmark_inventory_filter_phase4_redis_network_results.json'
OUT_MD = ROOT / 'tools' / 'inventory_filter_phase4_redis_network_investigation.md'

BENCH_PREFIX = 'nhit:phase4_bench:'
WARMUPS = 10
SAMPLES = 30
PROJECT = 'Kothakota to Kurnool'
DATE = '2026-07-28'
PAGE_SIZE = 50000
SIZE_LADDER = [64 * 1024, 256 * 1024, 512 * 1024, 640 * 1024, 1024 * 1024, 2 * 1024 * 1024]
CONCURRENCY_LEVELS = [1, 2, 5, 10]
CONCURRENCY_REQUESTS = 40


def _log(msg: str) -> None:
    print(msg, flush=True)


def _percentile(sorted_vals: list[float], p: float) -> float:
    if not sorted_vals:
        return float('nan')
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    k = (len(sorted_vals) - 1) * (p / 100.0)
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return sorted_vals[int(k)]
    return sorted_vals[f] * (c - k) + sorted_vals[c] * (k - f)


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


def _throughput_mbs(size_bytes: int, time_ms: float) -> float:
    if time_ms <= 0:
        return float('inf')
    return round((size_bytes / (1024 * 1024)) / (time_ms / 1000.0), 3)


def _throughput_stats(size_bytes: int, times_ms: list[float]) -> dict[str, Any]:
    rates = [_throughput_mbs(size_bytes, t) for t in times_ms if t > 0]
    if not rates:
        return {'n': 0}
    ordered = sorted(rates)
    return {
        'n': len(rates),
        'min': round(ordered[0], 3),
        'mean': round(statistics.fmean(ordered), 3),
        'median': round(statistics.median(ordered), 3),
        'p95': round(_percentile(ordered, 95), 3),
        'max': round(ordered[-1], 3),
    }


def _redact_redis_url(url: str | None) -> dict[str, Any]:
    if not url:
        return {'present': False}
    try:
        parsed = urlparse(url)
    except Exception:
        return {'present': True, 'parse_error': True}
    host = (parsed.hostname or '').lower()
    scheme = (parsed.scheme or '').lower()
    kind = 'unknown'
    if 'railway.internal' in host:
        kind = 'railway_internal'
    elif 'rlwy.net' in host or 'proxy' in host:
        kind = 'railway_public_proxy'
    elif host in {'localhost', '127.0.0.1'}:
        kind = 'localhost'
    suffix = '.'.join(host.split('.')[-3:]) if host.count('.') >= 2 else (host[-24:] if host else '')
    return {
        'present': True,
        'scheme': scheme,
        'tls': scheme in {'rediss', 'redis+ssl'},
        'host_kind': kind,
        'host_suffix': suffix,
        'port': parsed.port,
        'has_password': bool(parsed.password),
        'path': parsed.path or '/',
    }


def _redact_db_host(host: str | None) -> dict[str, Any]:
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
    return {
        'present': True,
        'endpoint_kind': kind,
        'region_hint': region,
        'host_suffix': suffix,
        'is_neon': 'neon.tech' in h,
    }


def _env_snapshot() -> dict[str, Any]:
    import django as dj

    on_railway = bool(
        os.environ.get('RAILWAY_ENVIRONMENT', '').strip()
        or os.environ.get('RAILWAY_SERVICE_NAME', '').strip()
    )
    redis_url = os.environ.get('REDIS_URL')
    redis_internal = os.environ.get('REDIS_INTERNAL_URL')
    redis_public = os.environ.get('REDIS_PUBLIC_URL')
    db = settings.DATABASES.get('default', {})
    return {
        'runtime': {
            'platform': sys.platform,
            'python': sys.version.split()[0],
            'on_railway': on_railway,
            'railway_environment': os.environ.get('RAILWAY_ENVIRONMENT') or None,
            'railway_service_name': os.environ.get('RAILWAY_SERVICE_NAME') or None,
            'railway_region': os.environ.get('RAILWAY_REGION') or None,
            'measurement_context': (
                'railway_app_runtime'
                if on_railway
                else 'local_dev_machine_to_configured_redis'
            ),
        },
        'versions': {
            'django': dj.get_version(),
            'redis_py': redis_lib.__version__,
            'django_redis': _django_redis_version(),
        },
        'cache': {
            'enabled': bool(getattr(settings, 'NHIT_CACHE_ENABLED', False)),
            'backend': settings.CACHES.get('default', {}).get('BACKEND'),
            'key_prefix': settings.CACHES.get('default', {}).get('KEY_PREFIX'),
            'using_internal_flag': bool(getattr(settings, 'NHIT_REDIS_USING_INTERNAL', False)),
            'resolved_location': _redact_redis_url(
                settings.CACHES.get('default', {}).get('LOCATION')
                if isinstance(settings.CACHES.get('default', {}).get('LOCATION'), str)
                else None
            ),
            'env_REDIS_URL': _redact_redis_url(redis_url),
            'env_REDIS_INTERNAL_URL': _redact_redis_url(redis_internal),
            'env_REDIS_PUBLIC_URL': _redact_redis_url(redis_public),
        },
        'postgres': {
            'engine': db.get('ENGINE'),
            'port': db.get('PORT'),
            'conn_max_age': db.get('CONN_MAX_AGE'),
            'sslmode': (db.get('OPTIONS') or {}).get('sslmode'),
            'host': _redact_db_host(db.get('HOST')),
        },
    }


def _django_redis_version() -> str:
    try:
        import django_redis

        return getattr(django_redis, '__version__', 'unknown')
    except Exception:
        return 'unavailable'


def _make_redis_client(url: str, *, decode_responses: bool = False) -> redis_lib.Redis:
    return redis_lib.Redis.from_url(
        url,
        decode_responses=decode_responses,
        socket_connect_timeout=10,
        socket_timeout=60,
    )


def _django_redis_client() -> Any:
    return cache.client.get_client(write=True)


def _django_make_key(logical_key: str) -> str:
    """Return the versioned Redis key django-redis uses for cache.get(logical_key)."""
    try:
        return cache.make_key(logical_key)
    except Exception:
        prefix = settings.CACHES.get('default', {}).get('KEY_PREFIX') or ''
        version = settings.CACHES.get('default', {}).get('VERSION', 1)
        return f'{prefix}:{version}:{logical_key}' if prefix else f'{version}:{logical_key}'


def _payload_body() -> dict[str, Any]:
    return {
        'chainage_start': 0,
        'chainage_end': 99999,
        'date': DATE,
        'direction': ['All'],
        'project_name': [PROJECT],
        'asset_type': ['All'],
        'page': 1,
        'page_size': PAGE_SIZE,
    }


def _resolve_inventory_page_key() -> dict[str, Any]:
    ser = InventoryFilterSerializer(data=_payload_body())
    assert ser.is_valid(), ser.errors
    data = ser.validated_data
    version = get_namespace_version(CACHE_NS_INVENTORY_FILTER)
    logical = build_cache_key(
        namespace=CACHE_NS_INVENTORY_FILTER,
        version=version,
        payload=data,
        scope=INVENTORY_FILTER_PAGE_SCOPE,
    )
    redis_key = _django_make_key(logical)
    digest = logical.rsplit(':', 1)[-1]
    return {
        'namespace': CACHE_NS_INVENTORY_FILTER,
        'scope': INVENTORY_FILTER_PAGE_SCOPE,
        'version': version,
        'logical_key_suffix': digest[:16],
        'logical_key_hash': hashlib.sha256(logical.encode()).hexdigest()[:16],
        'redis_key_suffix': redis_key[-24:],
        'redis_key_hash': hashlib.sha256(redis_key.encode()).hexdigest()[:16],
        'logical_key': logical,
        'redis_key': redis_key,
    }


def _validate_inventory_blob(raw: Any) -> dict[str, Any]:
    info: dict[str, Any] = {
        'type': type(raw).__name__,
        'present': raw is not None,
    }
    if not isinstance(raw, (bytes, bytearray, memoryview)):
        info['valid_ifgz1'] = False
        info['error'] = 'not_bytes'
        return info
    blob = bytes(raw)
    info['size_with_magic'] = len(blob)
    info['starts_with_ifgz1'] = blob.startswith(INVENTORY_FILTER_PAGE_CACHE_MAGIC)
    try:
        gz = extract_inventory_filter_page_gzip_payload(blob)
        info['gzip_payload_bytes'] = len(gz)
        info['valid_ifgz1'] = True
        # cheap integrity: gunzip header only already checked; optionally decompress size
        t0 = time.perf_counter()
        json_bytes = gzip.decompress(gz)
        info['gunzip_ms'] = round((time.perf_counter() - t0) * 1000, 3)
        info['uncompressed_json_bytes'] = len(json_bytes)
    except Exception as exc:
        info['valid_ifgz1'] = False
        info['error'] = type(exc).__name__
    return info


def measure_ping(client: redis_lib.Redis, *, samples: int = SAMPLES, warmups: int = WARMUPS) -> dict[str, Any]:
    for _ in range(warmups):
        client.ping()
    times = []
    for _ in range(samples):
        t0 = time.perf_counter()
        client.ping()
        times.append((time.perf_counter() - t0) * 1000)
    return {'label': 'PING', 'latency_ms': _stats(times)}


def measure_raw_gets(
    client: redis_lib.Redis,
    redis_key: str,
    *,
    samples: int = SAMPLES,
    warmups: int = WARMUPS,
    label: str = 'raw_GET',
) -> dict[str, Any]:
    size = None
    for _ in range(warmups):
        val = client.get(redis_key)
        if val is not None:
            size = len(val)
    if size is None:
        return {'label': label, 'error': 'key_missing_or_empty'}
    times = []
    for _ in range(samples):
        t0 = time.perf_counter()
        val = client.get(redis_key)
        elapsed = (time.perf_counter() - t0) * 1000
        times.append(elapsed)
        if val is None or len(val) != size:
            return {'label': label, 'error': 'inconsistent_get', 'elapsed_ms': elapsed}
    return {
        'label': label,
        'payload_bytes': size,
        'latency_ms': _stats(times),
        'throughput_mbs': _throughput_stats(size, times),
    }


def measure_django_cache_gets(
    logical_key: str,
    *,
    samples: int = SAMPLES,
    warmups: int = WARMUPS,
) -> dict[str, Any]:
    size = None
    for _ in range(warmups):
        hit, val = safe_cache_get(logical_key)
        if hit and isinstance(val, (bytes, bytearray)):
            size = len(val)
    if size is None:
        # try once more without warm assumption
        hit, val = safe_cache_get(logical_key)
        if not hit:
            return {'label': 'django_redis_safe_cache_get', 'error': 'miss'}
        size = len(val) if isinstance(val, (bytes, bytearray)) else None
    times = []
    extract_times = []
    for _ in range(samples):
        t0 = time.perf_counter()
        hit, val = safe_cache_get(logical_key)
        get_ms = (time.perf_counter() - t0) * 1000
        times.append(get_ms)
        if not hit:
            return {'label': 'django_redis_safe_cache_get', 'error': 'miss_during_sample'}
        t1 = time.perf_counter()
        if isinstance(val, (bytes, bytearray)) and bytes(val).startswith(INVENTORY_FILTER_PAGE_CACHE_MAGIC):
            extract_inventory_filter_page_gzip_payload(val)
        extract_times.append((time.perf_counter() - t1) * 1000)
    return {
        'label': 'django_redis_safe_cache_get',
        'payload_bytes': size,
        'latency_ms': _stats(times),
        'extract_ms': _stats(extract_times),
        'throughput_mbs': _throughput_stats(size or 0, times) if size else None,
    }


def measure_connection_vs_reuse(url: str, redis_key: str) -> dict[str, Any]:
    """Compare new connection per GET vs reused connection (benchmark only)."""
    # Reused
    reused_client = _make_redis_client(url)
    for _ in range(WARMUPS):
        reused_client.get(redis_key)
    reused_get = []
    size = None
    for _ in range(SAMPLES):
        t0 = time.perf_counter()
        val = reused_client.get(redis_key)
        reused_get.append((time.perf_counter() - t0) * 1000)
        if val is not None:
            size = len(val)
    reused_client.close()

    # New connection each sample
    connect_ms = []
    get_ms = []
    total_ms = []
    for _ in range(min(SAMPLES, 15)):  # fewer — expensive
        t_total0 = time.perf_counter()
        t0 = time.perf_counter()
        c = _make_redis_client(url)
        # force handshake
        c.ping()
        connect_ms.append((time.perf_counter() - t0) * 1000)
        t1 = time.perf_counter()
        val = c.get(redis_key)
        get_ms.append((time.perf_counter() - t1) * 1000)
        if val is not None:
            size = len(val)
        c.close()
        total_ms.append((time.perf_counter() - t_total0) * 1000)

    return {
        'payload_bytes': size,
        'reused_connection_get_ms': _stats(reused_get),
        'reused_throughput_mbs': _throughput_stats(size or 0, reused_get) if size else None,
        'new_connection': {
            'connect_plus_ping_ms': _stats(connect_ms),
            'get_ms': _stats(get_ms),
            'total_connect_ping_get_ms': _stats(total_ms),
            'n': len(total_ms),
        },
    }


def measure_size_ladder(url: str) -> dict[str, Any]:
    """Temporary synthetic keys under BENCH_PREFIX — deleted after."""
    client = _make_redis_client(url)
    results = []
    created_keys = []
    skipped = []
    try:
        for size in SIZE_LADDER:
            key = f'{BENCH_PREFIX}size:{size}'
            # Mostly compressible repeating pattern + small entropy header
            payload = (b'NHIT-PHASE4-' + os.urandom(16)) + (b'ABCDEFGHIJKLMNOP' * ((size // 16) + 1))
            payload = payload[:size]
            try:
                ok = client.setex(key, 120, payload)
                if not ok:
                    skipped.append({'payload_bytes': size, 'reason': 'setex_false'})
                    continue
                got0 = client.get(key)
                if got0 is None or len(got0) != size:
                    skipped.append(
                        {
                            'payload_bytes': size,
                            'reason': 'get_after_set_mismatch',
                            'got': None if got0 is None else len(got0),
                        }
                    )
                    client.delete(key)
                    continue
            except Exception as exc:
                skipped.append({'payload_bytes': size, 'reason': type(exc).__name__, 'detail': str(exc)[:120]})
                continue
            created_keys.append(key)
            for _ in range(5):
                client.get(key)
            times = []
            failed = False
            for _ in range(SAMPLES):
                t0 = time.perf_counter()
                got = client.get(key)
                times.append((time.perf_counter() - t0) * 1000)
                if got is None or len(got) != size:
                    skipped.append({'payload_bytes': size, 'reason': 'inconsistent_during_samples'})
                    failed = True
                    break
            if failed:
                continue
            results.append(
                {
                    'payload_bytes': size,
                    'latency_ms': _stats(times),
                    'throughput_mbs': _throughput_stats(size, times),
                }
            )
    finally:
        if created_keys:
            try:
                client.delete(*created_keys)
            except Exception:
                pass
        try:
            client.close()
        except Exception:
            pass
    return {
        'note': 'synthetic keys deleted after run; skipped sizes listed separately',
        'sizes': results,
        'skipped': skipped,
    }


def measure_concurrency(url: str, redis_key: str) -> dict[str, Any]:
    client = _make_redis_client(url)
    val = client.get(redis_key)
    client.close()
    if val is None:
        return {'error': 'key_missing'}
    size = len(val)

    levels = []
    for conc in CONCURRENCY_LEVELS:
        latencies: list[float] = []
        errors = 0

        def _one(_i: int) -> float:
            c = _make_redis_client(url)
            try:
                t0 = time.perf_counter()
                got = c.get(redis_key)
                elapsed = (time.perf_counter() - t0) * 1000
                if got is None or len(got) != size:
                    raise RuntimeError('bad_get')
                return elapsed
            finally:
                c.close()

        # Warm shared path
        for _ in range(3):
            _one(0)

        t_wall0 = time.perf_counter()
        with concurrent.futures.ThreadPoolExecutor(max_workers=conc) as pool:
            futs = [pool.submit(_one, i) for i in range(CONCURRENCY_REQUESTS)]
            for fut in concurrent.futures.as_completed(futs):
                try:
                    latencies.append(fut.result())
                except Exception:
                    errors += 1
        wall_ms = (time.perf_counter() - t_wall0) * 1000
        ok = len(latencies)
        aggregate_bytes = ok * size
        levels.append(
            {
                'concurrency': conc,
                'requests': CONCURRENCY_REQUESTS,
                'successful': ok,
                'errors': errors,
                'wall_ms': round(wall_ms, 3),
                'latency_ms': _stats(latencies),
                'aggregate_throughput_mbs': _throughput_mbs(aggregate_bytes, wall_ms),
                'per_request_throughput_mbs_median': (
                    _throughput_mbs(size, statistics.median(latencies)) if latencies else None
                ),
            }
        )
    return {'payload_bytes': size, 'levels': levels}


def measure_internal_vs_public() -> dict[str, Any]:
    on_railway = bool(
        os.environ.get('RAILWAY_ENVIRONMENT', '').strip()
        or os.environ.get('RAILWAY_SERVICE_NAME', '').strip()
    )
    internal = (os.environ.get('REDIS_INTERNAL_URL') or '').strip()
    public = (os.environ.get('REDIS_PUBLIC_URL') or os.environ.get('REDIS_URL') or '').strip()
    out: dict[str, Any] = {
        'on_railway': on_railway,
        'internal_configured': bool(internal),
        'public_configured': bool(public),
    }
    # Need a small key for fair compare — use PING + small SET/GET under bench prefix
    probe_key = f'{BENCH_PREFIX}probe'
    probe_payload = os.urandom(64 * 1024)

    def _run(url: str, label: str) -> dict[str, Any]:
        meta = _redact_redis_url(url)
        try:
            c = _make_redis_client(url)
            c.setex(probe_key, 60, probe_payload)
            ping = measure_ping(c, samples=20, warmups=5)
            gets = measure_raw_gets(c, probe_key, samples=20, warmups=5, label=f'{label}_64kib_GET')
            c.delete(probe_key)
            c.close()
            return {'ok': True, 'endpoint': meta, 'ping': ping, 'get_64kib': gets}
        except Exception as exc:
            return {
                'ok': False,
                'endpoint': meta,
                'error_type': type(exc).__name__,
                'error': str(exc)[:120],
            }

    if internal:
        if on_railway:
            out['internal'] = _run(internal, 'internal')
        else:
            out['internal'] = {
                'ok': False,
                'endpoint': _redact_redis_url(internal),
                'skipped': True,
                'reason': 'REDIS_INTERNAL_URL not reachable from non-Railway runtime (private DNS)',
            }
    else:
        out['internal'] = {'skipped': True, 'reason': 'REDIS_INTERNAL_URL not set'}

    if public:
        out['public'] = _run(public, 'public')
    else:
        out['public'] = {'skipped': True, 'reason': 'REDIS_URL/REDIS_PUBLIC_URL not set'}

    return out


def measure_tls(url: str) -> dict[str, Any]:
    meta = _redact_redis_url(url)
    if not meta.get('tls'):
        return {
            'tls_enabled': False,
            'endpoint': meta,
            'note': 'Resolved Redis URL scheme is not TLS (rediss). Handshake overhead N/A for this endpoint.',
        }
    # TLS enabled — measure connect vs reuse
    connect = []
    reused = []
    c = _make_redis_client(url)
    for _ in range(WARMUPS):
        c.ping()
    for _ in range(SAMPLES):
        t0 = time.perf_counter()
        c.ping()
        reused.append((time.perf_counter() - t0) * 1000)
    c.close()
    for _ in range(min(SAMPLES, 15)):
        t0 = time.perf_counter()
        c2 = _make_redis_client(url)
        c2.ping()
        connect.append((time.perf_counter() - t0) * 1000)
        c2.close()
    return {
        'tls_enabled': True,
        'endpoint': meta,
        'reused_connection_ping_ms': _stats(reused),
        'new_tls_connection_ping_ms': _stats(connect),
    }


def measure_django_vs_redispy(logical_key: str, redis_key: str, url: str) -> dict[str, Any]:
    django_part = measure_django_cache_gets(logical_key, samples=SAMPLES, warmups=WARMUPS)
    client = _make_redis_client(url)
    raw_part = measure_raw_gets(client, redis_key, samples=SAMPLES, warmups=WARMUPS, label='redis_py_GET')
    # Also measure django-redis client.get directly (bypass safe_cache_get wrapper timing noise)
    dj_client = _django_redis_client()
    for _ in range(WARMUPS):
        dj_client.get(redis_key)
    direct_dj = []
    size = None
    for _ in range(SAMPLES):
        t0 = time.perf_counter()
        val = dj_client.get(redis_key)
        direct_dj.append((time.perf_counter() - t0) * 1000)
        if val is not None:
            size = len(val)
    client.close()
    django_med = (django_part.get('latency_ms') or {}).get('median')
    raw_med = (raw_part.get('latency_ms') or {}).get('median')
    dj_client_med = statistics.median(direct_dj) if direct_dj else None
    return {
        'django_safe_cache_get': django_part,
        'redis_py_get': raw_part,
        'django_redis_client_get': {
            'label': 'django_redis_client.get(redis_key)',
            'payload_bytes': size,
            'latency_ms': _stats(direct_dj),
            'throughput_mbs': _throughput_stats(size or 0, direct_dj) if size else None,
        },
        'overhead_estimates_ms': {
            'safe_cache_get_minus_redis_py_median': (
                round(django_med - raw_med, 3)
                if django_med is not None and raw_med is not None
                else None
            ),
            'django_client_get_minus_redis_py_median': (
                round(dj_client_med - raw_med, 3)
                if dj_client_med is not None and raw_med is not None
                else None
            ),
        },
    }


def measure_redis_server_side(client: redis_lib.Redis) -> dict[str, Any]:
    info: dict[str, Any] = {}
    try:
        i = client.info(section='server')
        info['server'] = {
            'redis_version': i.get('redis_version'),
            'os': i.get('os'),
            'tcp_port': i.get('tcp_port'),
            'uptime_in_seconds': i.get('uptime_in_seconds'),
        }
    except Exception as exc:
        info['server_error'] = type(exc).__name__
    try:
        i = client.info(section='stats')
        info['stats'] = {
            'total_connections_received': i.get('total_connections_received'),
            'total_commands_processed': i.get('total_commands_processed'),
            'instantaneous_ops_per_sec': i.get('instantaneous_ops_per_sec'),
            'total_net_input_bytes': i.get('total_net_input_bytes'),
            'total_net_output_bytes': i.get('total_net_output_bytes'),
            'instantaneous_input_kbps': i.get('instantaneous_input_kbps'),
            'instantaneous_output_kbps': i.get('instantaneous_output_kbps'),
        }
    except Exception as exc:
        info['stats_error'] = type(exc).__name__
    try:
        i = client.info(section='commandstats')
        get_stats = i.get('cmdstat_get') or {}
        info['commandstats_get'] = get_stats
    except Exception as exc:
        info['commandstats_error'] = type(exc).__name__
    try:
        # LATENCY LATEST — may be empty
        latest = client.execute_command('LATENCY', 'LATEST')
        info['latency_latest'] = latest
    except Exception as exc:
        info['latency_latest_error'] = type(exc).__name__
    try:
        slow = client.execute_command('SLOWLOG', 'GET', 5)
        # sanitize: keep only id, duration_us, command name
        sanitized = []
        for entry in slow or []:
            # redis-py returns [id, timestamp, duration_us, [cmd...], client, name]
            if isinstance(entry, (list, tuple)) and len(entry) >= 4:
                cmd = entry[3]
                cmd_name = cmd[0] if isinstance(cmd, (list, tuple)) and cmd else cmd
                if isinstance(cmd_name, bytes):
                    cmd_name = cmd_name.decode('utf-8', errors='replace')
                sanitized.append(
                    {
                        'id': entry[0],
                        'duration_us': entry[2],
                        'command': str(cmd_name)[:40],
                    }
                )
        info['slowlog_top5'] = sanitized
    except Exception as exc:
        info['slowlog_error'] = type(exc).__name__
    return info


def measure_neon() -> dict[str, Any]:
    out: dict[str, Any] = {'host': _redact_db_host(settings.DATABASES['default'].get('HOST'))}
    # connection establishment
    establish = []
    select1_fresh = []
    select1_reuse = []
    for _ in range(10):
        connection.close()
        t0 = time.perf_counter()
        connection.ensure_connection()
        establish.append((time.perf_counter() - t0) * 1000)
        with connection.cursor() as cur:
            t1 = time.perf_counter()
            cur.execute('SELECT 1')
            cur.fetchone()
            select1_fresh.append((time.perf_counter() - t1) * 1000)
    connection.ensure_connection()
    with connection.cursor() as cur:
        for _ in range(SAMPLES):
            t0 = time.perf_counter()
            cur.execute('SELECT 1')
            cur.fetchone()
            select1_reuse.append((time.perf_counter() - t0) * 1000)

    # lightweight inventory-ish query: count rows for project/date (uses existing indexes if any)
    inventory_times = []
    inventory_error = None
    try:
        with connection.cursor() as cur:
            # Warm
            cur.execute(
                '''SELECT COUNT(*) FROM inventory_wide
                   WHERE "Project Name" = %s AND "Date" = %s''',
                [PROJECT, DATE],
            )
            cur.fetchone()
            for _ in range(15):
                t0 = time.perf_counter()
                cur.execute(
                    '''SELECT COUNT(*) FROM inventory_wide
                       WHERE "Project Name" = %s AND "Date" = %s''',
                    [PROJECT, DATE],
                )
                cur.fetchone()
                inventory_times.append((time.perf_counter() - t0) * 1000)
    except Exception as exc:
        inventory_error = f'{type(exc).__name__}: {str(exc)[:160]}'

    out['connection_establishment_ms'] = _stats(establish)
    out['select1_after_connect_ms'] = _stats(select1_fresh)
    out['select1_reused_connection_ms'] = _stats(select1_reuse)
    out['inventory_count_kothakota_ms'] = (
        _stats(inventory_times) if inventory_times else {'error': inventory_error}
    )
    return out


def _classify(report: dict[str, Any]) -> dict[str, Any]:
    env = report['environment']
    inv = report.get('inventory_50k') or {}
    raw = ((inv.get('redis_py_get') or {}).get('latency_ms') or {})
    dj = ((inv.get('django_vs_redispy') or {}).get('django_safe_cache_get') or {}).get('latency_ms') or {}
    extract = ((inv.get('django_vs_redispy') or {}).get('django_safe_cache_get') or {}).get('extract_ms') or {}
    conn = report.get('connection_reuse') or {}
    reused = (conn.get('reused_connection_get_ms') or {}).get('median')
    new_total = ((conn.get('new_connection') or {}).get('total_connect_ping_get_ms') or {}).get('median')
    neon = report.get('neon') or {}
    neon_select = (neon.get('select1_reused_connection_ms') or {}).get('median')
    redis_ping = ((report.get('ping') or {}).get('latency_ms') or {}).get('median')
    server = report.get('redis_server_side') or {}
    get_cmd = server.get('commandstats_get') or {}

    raw_med = raw.get('median')
    dj_med = dj.get('median')
    extract_med = extract.get('median')
    payload = inv.get('payload_bytes') or ((inv.get('redis_py_get') or {}).get('payload_bytes'))

    findings = []
    classification = 'G. Mixed bottleneck'
    evidence = []

    if raw_med is not None and extract_med is not None and raw_med > 50 and extract_med < 5:
        findings.append('Redis GET >> extract (Phase 3 conclusion confirmed)')
        evidence.append(f'redis_py GET median={raw_med}ms; extract median={extract_med}ms')

    if (
        reused is not None
        and new_total is not None
        and new_total > reused * 3
        and (new_total - reused) > 50
    ):
        findings.append('New connection substantially slower than reuse')
        evidence.append(f'reused GET={reused}ms; new connect+GET={new_total}ms')

    overhead = ((inv.get('django_vs_redispy') or {}).get('overhead_estimates_ms') or {}).get(
        'safe_cache_get_minus_redis_py_median'
    )
    if overhead is not None and abs(overhead) < 20 and raw_med and raw_med > 100:
        findings.append('django-redis overhead small vs network GET')
        evidence.append(f'overhead≈{overhead}ms on ~{raw_med}ms GET')

    # Server-side: usec_per_call in commandstats is microseconds
    usec = get_cmd.get('usec_per_call')
    if usec is not None and raw_med is not None:
        server_ms = float(usec) / 1000.0
        if server_ms < 5 and raw_med > 50:
            findings.append('Redis server GET usec_per_call tiny vs app-observed GET')
            evidence.append(f'server usec_per_call={usec}µs (~{server_ms:.3f}ms) vs app {raw_med}ms')
            classification = 'A. Redis network/transfer bottleneck'

    if redis_ping is not None and raw_med is not None and payload:
        # expected transfer-dominated if GET >> ping
        if raw_med > redis_ping * 5:
            findings.append('Large-payload GET much slower than PING (transfer-dominated)')
            evidence.append(f'PING={redis_ping}ms; GET({payload}B)={raw_med}ms')
            if classification.startswith('G'):
                classification = 'A. Redis network/transfer bottleneck'

    if neon_select is not None and redis_ping is not None:
        evidence.append(f'Neon SELECT 1 reused={neon_select}ms; Redis PING={redis_ping}ms')
        if neon_select > 50 and redis_ping > 50:
            findings.append('Both Redis PING and Neon SELECT 1 elevated — possible general network')
            if raw_med and raw_med > 200:
                classification = 'E. Railway infrastructure/network bottleneck'
        elif neon_select < 20 and raw_med and raw_med > 200:
            findings.append('Neon RTT small while large Redis GET slow — Redis path/throughput issue')
            classification = 'A. Redis network/transfer bottleneck'

    if not env['runtime']['on_railway']:
        findings.append(
            'IMPORTANT: this run was NOT on Railway app runtime — local→Redis path may inflate latency'
        )
        classification = 'G. Mixed bottleneck (pending Railway runtime confirmation)'

    # Ranked recommendations
    recs = []
    if not env['runtime']['on_railway']:
        recs.append('Re-run this exact script inside Railway (`railway run` / one-off service) before Phase 5.')
    if classification.startswith('A') or 'transfer' in classification.lower():
        recs.append('Investigate Railway Redis region/placement and internal vs public URL from app runtime.')
        recs.append('Confirm production uses REDIS_INTERNAL_URL (NHIT_REDIS_USING_INTERNAL).')
        recs.append('Do not spend Phase 5 on Python JSON/render — already optimized in Phase 3.')
    if overhead is not None and abs(overhead) < 20:
        recs.append('Do not prioritize replacing django-redis yet — overhead is small vs GET.')
    if reused is not None and new_total is not None and new_total > reused * 3:
        recs.append('Ensure connection pooling/reuse remains enabled (avoid per-request Redis connects).')
    recs.append('Do not change cache format/keys/TTL based on this phase alone.')

    return {
        'classification': classification,
        'findings': findings,
        'evidence': evidence,
        'ranked_recommendations': recs,
        'key_medians_ms': {
            'redis_ping': redis_ping,
            'redis_py_get': raw_med,
            'django_safe_cache_get': dj_med,
            'extract': extract_med,
            'reused_get': reused,
            'new_connection_total': new_total,
            'neon_select1_reuse': neon_select,
            'payload_bytes': payload,
        },
    }


def main() -> int:
    if not is_cache_usable():
        _log('ERROR: Redis/cache not usable')
        return 2

    env = _env_snapshot()
    _log(f"measurement_context={env['runtime']['measurement_context']} on_railway={env['runtime']['on_railway']}")

    location = settings.CACHES['default'].get('LOCATION')
    if not isinstance(location, str) or not location:
        _log('ERROR: no Redis LOCATION in CACHES')
        return 2

    key_info = _resolve_inventory_page_key()
    logical_key = key_info['logical_key']
    redis_key = key_info['redis_key']
    _log(f"inventory key hash={key_info['logical_key_hash']} suffix={key_info['logical_key_suffix']}")

    # Load via django first
    hit, raw = safe_cache_get(logical_key)
    blob_info = _validate_inventory_blob(raw if hit else None)
    if not hit or not blob_info.get('valid_ifgz1'):
        _log('WARNING: real 50k cache entry missing/invalid — attempting redis-py GET anyway')
    else:
        _log(
            f"50k cache OK size={blob_info.get('size_with_magic')} "
            f"gzip={blob_info.get('gzip_payload_bytes')} json={blob_info.get('uncompressed_json_bytes')}"
        )

    client = _make_redis_client(location)

    _log('A/H: PING + server-side info...')
    ping = measure_ping(client)
    server_side = measure_redis_server_side(client)

    _log('A: redis-py GET on real inventory key...')
    raw_get = measure_raw_gets(client, redis_key, label='inventory_50k_redis_py_GET')

    _log('G: django-redis vs redis-py...')
    django_vs = measure_django_vs_redispy(logical_key, redis_key, location)

    _log('B: connection reuse vs new connection...')
    conn_reuse = measure_connection_vs_reuse(location, redis_key)

    _log('C: payload size ladder (synthetic temporary keys)...')
    size_ladder = measure_size_ladder(location)

    _log('D: concurrency...')
    concurrency = measure_concurrency(location, redis_key)

    _log('E: internal vs public...')
    internal_vs_public = measure_internal_vs_public()

    _log('F: TLS analysis...')
    tls = measure_tls(location)

    _log('I: Neon/PostgreSQL comparison...')
    neon = measure_neon()

    client.close()

    # Timing breakdown T1-T5 for inventory path
    timing_breakdown = {
        'T1_connection_acquisition': {
            'reused': 'amortized ~0 (pool)',
            'new_connection_median_ms': ((conn_reuse.get('new_connection') or {}).get('connect_plus_ping_ms') or {}).get(
                'median'
            ),
        },
        'T2_T3_redis_get_and_transfer_ms': (raw_get.get('latency_ms') or {}),
        'T4_extract_ms': ((django_vs.get('django_safe_cache_get') or {}).get('extract_ms') or {}),
        'T5_response_construction': 'not re-measured here (Phase 3: ~0.2ms for direct gzip)',
        'note': 'T2/T3 cannot be split further without packet capture; GET time includes server+network+transfer',
    }

    report = {
        'phase': 4,
        'investigation_only': True,
        'production_behavior_changed': False,
        'environment': env,
        'inventory_key_meta': {
            k: v
            for k, v in key_info.items()
            if k not in {'logical_key', 'redis_key'}
        },
        'inventory_50k': {
            'cache_hit': hit,
            'blob': blob_info,
            'payload_bytes': blob_info.get('size_with_magic'),
            'redis_py_get': raw_get,
            'django_vs_redispy': django_vs,
        },
        'ping': ping,
        'connection_reuse': conn_reuse,
        'size_ladder': size_ladder,
        'concurrency': concurrency,
        'internal_vs_public': internal_vs_public,
        'tls': tls,
        'redis_server_side': server_side,
        'neon': neon,
        'timing_breakdown': timing_breakdown,
    }
    report['decision'] = _classify(report)

    OUT_JSON.write_text(json.dumps(report, indent=2, default=str), encoding='utf-8')
    _write_md(report)
    _log(f'Wrote {OUT_JSON}')
    _log(f'Wrote {OUT_MD}')
    _log('Phase 4 investigation complete — no production behavior changed.')
    _log(f"Classification: {report['decision']['classification']}")
    return 0


def _write_md(report: dict[str, Any]) -> None:
    env = report['environment']
    decision = report['decision']
    inv = report['inventory_50k']
    raw = inv.get('redis_py_get') or {}
    dj = (inv.get('django_vs_redispy') or {}).get('django_safe_cache_get') or {}
    conn = report['connection_reuse']
    neon = report['neon']
    ping = report['ping']
    tls = report['tls']
    ivp = report['internal_vs_public']
    ladder = report['size_ladder'].get('sizes') or []
    conc = report['concurrency']

    def med(block, key='median'):
        if not isinstance(block, dict):
            return 'n/a'
        lat = block.get('latency_ms') if 'latency_ms' in block else block
        if not isinstance(lat, dict):
            return 'n/a'
        v = lat.get(key)
        return v if v is not None else 'n/a'

    lines = [
        '# Phase 4 — Redis / Network Investigation (measurement only)',
        '',
        '**Phase 4 investigation complete — no production behavior changed.**',
        '',
        '## 1. Executive Summary',
        '',
        f"- **Classification:** {decision['classification']}",
        f"- **Measurement context:** `{env['runtime']['measurement_context']}`",
        f"- **On Railway runtime:** `{env['runtime']['on_railway']}`",
        '',
        'Findings:',
        '',
    ]
    for f in decision['findings']:
        lines.append(f'- {f}')
    lines += ['', 'Evidence:', '']
    for e in decision['evidence']:
        lines.append(f'- {e}')
    lines += ['', 'Ranked recommendations:', '']
    for i, r in enumerate(decision['ranked_recommendations'], 1):
        lines.append(f'{i}. {r}')

    lines += [
        '',
        '## 2. Environment',
        '',
        f"- Platform: `{env['runtime']['platform']}` Python `{env['runtime']['python']}`",
        f"- Django `{env['versions']['django']}`, redis-py `{env['versions']['redis_py']}`, django-redis `{env['versions']['django_redis']}`",
        f"- Cache enabled: `{env['cache']['enabled']}`, backend `{env['cache']['backend']}`",
        f"- Resolved Redis: `{json.dumps(env['cache']['resolved_location'])}`",
        f"- NHIT_REDIS_USING_INTERNAL: `{env['cache']['using_internal_flag']}`",
        f"- Postgres host: `{json.dumps(env['postgres']['host'])}`",
        '',
        '## 3. Test Methodology',
        '',
        f'- Warmups: {WARMUPS}; measured samples: {SAMPLES} (concurrency uses {CONCURRENCY_REQUESTS} requests/level)',
        '- Real inventory key via application `build_cache_key` + django-redis `make_key` (GET only; not modified)',
        f'- Synthetic size-ladder keys under `{BENCH_PREFIX}` with TTL 120s, deleted after',
        '- Secrets redacted (no URLs/passwords in report)',
        '',
        '## 4. Real 50K Inventory Cache Results',
        '',
        f"- Key hash: `{report['inventory_key_meta']['logical_key_hash']}` suffix `{report['inventory_key_meta']['logical_key_suffix']}`",
        f"- Namespace/scope/version: `{report['inventory_key_meta']['namespace']}` / `{report['inventory_key_meta']['scope']}` / v{report['inventory_key_meta']['version']}",
        f"- Cache HIT: `{inv.get('cache_hit')}`",
        f"- Blob: `{json.dumps(inv.get('blob'))}`",
        '',
        '## 5. Redis GET Results',
        '',
        f"- PING median: **{med(ping)} ms**",
        f"- redis-py GET median: **{med(raw)} ms** (p95={med(raw,'p95')}, max={med(raw,'max')})",
        f"- django safe_cache_get median: **{med(dj)} ms**",
        f"- extract median: **{((dj.get('extract_ms') or {}).get('median'))} ms**",
        f"- Throughput (redis-py): `{json.dumps(raw.get('throughput_mbs'))}`",
        '',
        '## 6. Connection Reuse Results',
        '',
        f"- Reused GET median: **{(conn.get('reused_connection_get_ms') or {}).get('median')} ms**",
        f"- New connection+ping median: **{((conn.get('new_connection') or {}).get('connect_plus_ping_ms') or {}).get('median')} ms**",
        f"- New total connect+GET median: **{((conn.get('new_connection') or {}).get('total_connect_ping_get_ms') or {}).get('median')} ms**",
        '',
        '## 7. Payload Size vs Latency',
        '',
        '| Bytes | Median ms | P95 ms | Median MB/s |',
        '|---:|---:|---:|---:|',
    ]
    for row in ladder:
        lat = row['latency_ms']
        thr = row['throughput_mbs']
        lines.append(
            f"| {row['payload_bytes']:,} | {lat['median']} | {lat['p95']} | {thr['median']} |"
        )

    lines += ['', '## 8. Concurrency Results', '']
    if conc.get('error'):
        lines.append(f"- Error: `{conc['error']}`")
    else:
        lines += [
            '| Conc | OK | Err | Median ms | P95 ms | Aggregate MB/s |',
            '|---:|---:|---:|---:|---:|---:|',
        ]
        for lvl in conc.get('levels') or []:
            lat = lvl['latency_ms']
            lines.append(
                f"| {lvl['concurrency']} | {lvl['successful']} | {lvl['errors']} | "
                f"{lat.get('median')} | {lat.get('p95')} | {lvl.get('aggregate_throughput_mbs')} |"
            )

    lines += [
        '',
        '## 9. Internal Redis vs Public Redis',
        '',
        f"```json\n{json.dumps(ivp, indent=2)}\n```",
        '',
        '## 10. TLS Analysis',
        '',
        f"```json\n{json.dumps(tls, indent=2)}\n```",
        '',
        '## 11. django-redis vs redis-py',
        '',
        f"```json\n{json.dumps((inv.get('django_vs_redispy') or {}).get('overhead_estimates_ms'), indent=2)}\n```",
        '',
        '## 12. Redis Server-side Measurements',
        '',
        f"```json\n{json.dumps(report.get('redis_server_side'), indent=2, default=str)}\n```",
        '',
        '## 13. Railway → Redis vs Railway → Neon',
        '',
        f"- Neon SELECT 1 reused median: **{(neon.get('select1_reused_connection_ms') or {}).get('median')} ms**",
        f"- Neon connect median: **{(neon.get('connection_establishment_ms') or {}).get('median')} ms**",
        f"- Inventory COUNT median: **{(neon.get('inventory_count_kothakota_ms') or {}).get('median')} ms**",
        f"- Redis PING median: **{med(ping)} ms**",
        f"- Redis large GET median: **{med(raw)} ms**",
        '',
        '## 14. Bottleneck Analysis',
        '',
        f"```json\n{json.dumps(report.get('timing_breakdown'), indent=2)}\n```",
        '',
        f"- Decision key medians: `{json.dumps(decision.get('key_medians_ms'))}`",
        '',
        '## 15. Recommendation',
        '',
    ]
    for i, r in enumerate(decision['ranked_recommendations'], 1):
        lines.append(f'{i}. {r}')

    lines += [
        '',
        '## 16. What NOT to Optimize Yet',
        '',
        '- Python JSON rendering / DRF (Phase 3 already removed this from gzip HIT path)',
        '- Redis cache format / IFGZ1 / gzip level / TTL / keys',
        '- PostgreSQL indexes / PostGIS / materialized views (unless Neon RTT proves primary)',
        '- Frontend / page_size policy',
        '',
        '---',
        '',
        '**Phase 4 investigation complete — no production behavior changed.**',
        '',
        f"Raw results: `{OUT_JSON.name}`",
        '',
    ]
    OUT_MD.write_text('\n'.join(lines), encoding='utf-8')


if __name__ == '__main__':
    raise SystemExit(main())
