"""Inventory filter Redis payload vs RTT benchmark (measurement only).

Runs against the SAME Django Redis client/config as production.
Intended to execute **inside** the Railway container (private Redis):

    railway ssh -- python manage.py benchmark_inventory_filter_redis_payload_rtt

Or locally only if REDIS points at a reachable instance (not authoritative).

Does NOT modify inventory_filter, cache keys/TTL/format, Redis config, or flush Redis.
Uses isolated temporary keys under ``bench:inv_payload_rtt:`` and deletes them after.
Never logs full Redis URLs, passwords, or full production cache keys.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import statistics
import sys
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

OUT_JSON = ROOT / 'tools' / 'inventory_filter_redis_payload_rtt_results.json'
OUT_MD = ROOT / 'tools' / 'inventory_filter_redis_payload_rtt_investigation.md'

BENCH_NS = 'bench:inv_payload_rtt'
TTL_SECONDS = 180
WARMUPS = 3
SAMPLES = 15

# TEST 1 sizes
SIZE_LADDER = [
    1_024,
    10_240,
    51_200,
    102_400,
    256_000,
    512_000,
    768_000,
    1_048_576,
    1_330_176,  # ~1.27 MiB (matches ~1.269 MB inventory gzip)
]

# TEST 2 floor sizes
FLOOR_SIZES = [1, 10, 100, 1_024, 10_240]

PROJECT = 'Kothakota to Kurnool'
DATE = '2026-07-28'
PAGE_SIZE = 100_000


def _log(msg: str) -> None:
    print(msg, flush=True)


def _pct(values: list[float], p: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return round(ordered[0], 3)
    k = (len(ordered) - 1) * (p / 100.0)
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return round(ordered[int(k)], 3)
    return round(ordered[f] + (ordered[c] - ordered[f]) * (k - f), 3)


def _stats(values: list[float]) -> dict[str, Any]:
    if not values:
        return {'n': 0, 'median': None, 'p95': None, 'min': None, 'max': None, 'mean': None}
    return {
        'n': len(values),
        'median': _pct(values, 50),
        'p95': _pct(values, 95),
        'min': round(min(values), 3),
        'max': round(max(values), 3),
        'mean': round(statistics.fmean(values), 3),
    }


def _safe_host_meta(url: str | None) -> dict[str, Any]:
    if not url:
        return {'present': False}
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or '').lower()
        if 'railway.internal' in host:
            host_class = 'railway_private_internal'
        elif 'rlwy.net' in host or 'proxy' in host:
            host_class = 'railway_public_proxy'
        elif host in ('localhost', '127.0.0.1'):
            host_class = 'localhost'
        else:
            host_class = 'other_hostname'
        return {
            'present': True,
            'scheme': parsed.scheme or None,
            'host_class': host_class,
            'port': parsed.port,
        }
    except Exception:
        return {'present': True, 'host_class': 'parse_error'}


def _setup_django() -> None:
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'nhit.settings')
    import django

    django.setup()


def _django_make_key(logical_key: str) -> str:
    from django.conf import settings
    from django.core.cache import cache

    try:
        return cache.make_key(logical_key)
    except Exception:
        prefix = settings.CACHES.get('default', {}).get('KEY_PREFIX') or ''
        version = settings.CACHES.get('default', {}).get('VERSION', 1)
        return f'{prefix}:{version}:{logical_key}' if prefix else f'{version}:{logical_key}'


def _redis_client():
    from django.core.cache import cache

    return cache.client.get_client(write=True)


def _parse_cmdstat_get(info_commandstats: dict[str, Any]) -> dict[str, Any] | None:
    raw = info_commandstats.get('cmdstat_get') or info_commandstats.get('cmdstat_GET')
    if not raw:
        return None
    if isinstance(raw, dict):
        return {
            'calls': raw.get('calls'),
            'usec': raw.get('usec'),
            'usec_per_call': raw.get('usec_per_call'),
        }
    # "calls=N,usec=N,usec_per_call=N"
    out: dict[str, Any] = {}
    for part in str(raw).split(','):
        if '=' not in part:
            continue
        k, v = part.split('=', 1)
        try:
            out[k] = float(v) if '.' in v else int(v)
        except ValueError:
            out[k] = v
    return out


def _throughput_mibs(nbytes: int, median_ms: float | None) -> float | None:
    if not median_ms or median_ms <= 0:
        return None
    return round((nbytes / (1024 * 1024)) / (median_ms / 1000.0), 3)


def _measure_gets(client, redis_key: str, *, samples: int = SAMPLES, warmups: int = WARMUPS) -> dict[str, Any]:
    size = None
    for _ in range(warmups):
        val = client.get(redis_key)
        if val is not None:
            size = len(val)
    if size is None:
        return {'error': 'missing', 'latency_ms': _stats([])}
    times: list[float] = []
    for _ in range(samples):
        t0 = time.perf_counter()
        val = client.get(redis_key)
        times.append((time.perf_counter() - t0) * 1000)
        if val is None:
            return {'error': 'became_missing', 'latency_ms': _stats(times), 'payload_bytes': size}
    return {
        'payload_bytes': size,
        'latency_ms': _stats(times),
        'throughput_mib_s_median': _throughput_mibs(size, _stats(times)['median']),
    }


def _server_side_get_estimate(client, redis_key: str, *, samples: int = SAMPLES) -> dict[str, Any]:
    """Estimate Redis server-side GET duration via pipelined TIME / GET / TIME."""
    deltas_ms: list[float] = []
    client_totals: list[float] = []
    for _ in range(samples):
        pipe = client.pipeline(transaction=False)
        pipe.time()
        pipe.get(redis_key)
        pipe.time()
        t0 = time.perf_counter()
        t_before, value, t_after = pipe.execute()
        client_totals.append((time.perf_counter() - t0) * 1000)
        # TIME returns [seconds, microseconds]
        before = int(t_before[0]) + int(t_before[1]) / 1_000_000
        after = int(t_after[0]) + int(t_after[1]) / 1_000_000
        deltas_ms.append((after - before) * 1000)
        if value is None:
            return {'error': 'missing'}
    return {
        'server_time_delta_ms': _stats(deltas_ms),
        'pipeline_client_total_ms': _stats(client_totals),
        'note': (
            'TIME/GET/TIME pipeline: server delta approximates Redis CPU/copy time; '
            'client total still includes network transfer of the GET response.'
        ),
    }


def _set_temp_bytes(client, logical_key: str, payload: bytes) -> str:
    redis_key = _django_make_key(logical_key)
    client.set(redis_key, payload, ex=TTL_SECONDS)
    return redis_key


def _delete_keys(client, redis_keys: list[str]) -> int:
    if not redis_keys:
        return 0
    # delete only our temp keys
    return int(client.delete(*redis_keys))


def test_size_ladder(client, run_id: str) -> tuple[dict[str, Any], list[str]]:
    created: list[str] = []
    rows: list[dict[str, Any]] = []
    for size in SIZE_LADDER:
        logical = f'{BENCH_NS}:{run_id}:ladder:{size}'
        payload = os.urandom(size)  # incompressible
        redis_key = _set_temp_bytes(client, logical, payload)
        created.append(redis_key)
        # Confirm exact stored length (raw redis, no django pickle).
        got = client.get(redis_key)
        assert got is not None and len(got) == size
        result = _measure_gets(client, redis_key)
        result['requested_bytes'] = size
        result['logical_suffix'] = logical[-32:]
        rows.append(result)
        _log(
            f"ladder size={size} median={result['latency_ms']['median']} "
            f"p95={result['latency_ms']['p95']} mib/s={result.get('throughput_mib_s_median')}"
        )
    return {'sizes': rows}, created


def test_latency_floor(client, run_id: str) -> tuple[dict[str, Any], list[str]]:
    created: list[str] = []
    rows: list[dict[str, Any]] = []
    for size in FLOOR_SIZES:
        logical = f'{BENCH_NS}:{run_id}:floor:{size}'
        payload = os.urandom(size)
        redis_key = _set_temp_bytes(client, logical, payload)
        created.append(redis_key)
        result = _measure_gets(client, redis_key)
        result['requested_bytes'] = size
        rows.append(result)
        _log(f"floor size={size} median={result['latency_ms']['median']}")
    # Also PING floor
    for _ in range(WARMUPS):
        client.ping()
    ping_times = []
    for _ in range(SAMPLES):
        t0 = time.perf_counter()
        client.ping()
        ping_times.append((time.perf_counter() - t0) * 1000)
    return {'sizes': rows, 'ping_ms': _stats(ping_times)}, created


def test_get_vs_mget_pipeline(client, run_id: str) -> tuple[dict[str, Any], list[str]]:
    """Four small equivalent payloads: sequential GET vs pipeline vs MGET."""
    created: list[str] = []
    small = os.urandom(64)
    keys = []
    for i in range(4):
        logical = f'{BENCH_NS}:{run_id}:small:{i}'
        redis_key = _set_temp_bytes(client, logical, small)
        created.append(redis_key)
        keys.append(redis_key)

    for _ in range(WARMUPS):
        for k in keys:
            client.get(k)
        pipe = client.pipeline(transaction=False)
        for k in keys:
            pipe.get(k)
        pipe.execute()
        client.mget(keys)

    seq_times: list[float] = []
    pipe_times: list[float] = []
    mget_times: list[float] = []
    for _ in range(SAMPLES):
        t0 = time.perf_counter()
        for k in keys:
            client.get(k)
        seq_times.append((time.perf_counter() - t0) * 1000)

        t0 = time.perf_counter()
        pipe = client.pipeline(transaction=False)
        for k in keys:
            pipe.get(k)
        pipe.execute()
        pipe_times.append((time.perf_counter() - t0) * 1000)

        t0 = time.perf_counter()
        client.mget(keys)
        mget_times.append((time.perf_counter() - t0) * 1000)

    out = {
        'payload_bytes_each': 64,
        'operations': {
            '4_sequential_GET': {
                'redis_operation_count': 4,
                'latency_ms': _stats(seq_times),
            },
            '4_GET_pipeline': {
                'redis_operation_count': 1,
                'latency_ms': _stats(pipe_times),
            },
            '1_MGET_four_keys': {
                'redis_operation_count': 1,
                'latency_ms': _stats(mget_times),
            },
        },
    }
    _log(
        f"small ops seq={out['operations']['4_sequential_GET']['latency_ms']['median']} "
        f"pipe={out['operations']['4_GET_pipeline']['latency_ms']['median']} "
        f"mget={out['operations']['1_MGET_four_keys']['latency_ms']['median']}"
    )
    return out, created


def test_inventory_hit_simulation(client, run_id: str) -> tuple[dict[str, Any], list[str]]:
    """Simulate inventory HIT: 3 small values + ~1.27 MB page blob."""
    created: list[str] = []
    page_size = 1_330_176
    values = {
        'count_ns': os.urandom(8),
        'count_data': os.urandom(8),
        'page_ns': os.urandom(8),
        'page_data': os.urandom(page_size),
    }
    keys = {}
    for name, payload in values.items():
        logical = f'{BENCH_NS}:{run_id}:sim:{name}'
        redis_key = _set_temp_bytes(client, logical, payload)
        created.append(redis_key)
        keys[name] = redis_key

    ordered = ['count_ns', 'count_data', 'page_ns', 'page_data']
    key_list = [keys[n] for n in ordered]

    for _ in range(WARMUPS):
        for k in key_list:
            client.get(k)
        pipe = client.pipeline(transaction=False)
        for k in key_list:
            pipe.get(k)
        pipe.execute()
        client.mget(key_list)

    seq_times: list[float] = []
    seq_parts: dict[str, list[float]] = {n: [] for n in ordered}
    pipe_times: list[float] = []
    mget_times: list[float] = []

    for _ in range(SAMPLES):
        t0 = time.perf_counter()
        for name in ordered:
            t1 = time.perf_counter()
            client.get(keys[name])
            seq_parts[name].append((time.perf_counter() - t1) * 1000)
        seq_times.append((time.perf_counter() - t0) * 1000)

        t0 = time.perf_counter()
        pipe = client.pipeline(transaction=False)
        for k in key_list:
            pipe.get(k)
        pipe.execute()
        pipe_times.append((time.perf_counter() - t0) * 1000)

        t0 = time.perf_counter()
        client.mget(key_list)
        mget_times.append((time.perf_counter() - t0) * 1000)

    out = {
        'page_payload_bytes': page_size,
        'small_payload_bytes': 8,
        '4_sequential_GET': {
            'redis_operation_count': 4,
            'total_ms': _stats(seq_times),
            'per_key_ms': {n: _stats(seq_parts[n]) for n in ordered},
        },
        '4_GET_pipeline': {
            'redis_operation_count': 1,
            'total_ms': _stats(pipe_times),
        },
        '1_MGET_four_keys': {
            'redis_operation_count': 1,
            'total_ms': _stats(mget_times),
        },
    }
    _log(
        f"sim HIT seq={out['4_sequential_GET']['total_ms']['median']} "
        f"pipe={out['4_GET_pipeline']['total_ms']['median']} "
        f"mget={out['1_MGET_four_keys']['total_ms']['median']}"
    )
    return out, created


def test_real_inventory_key(client) -> dict[str, Any]:
    from django.core.cache import cache

    from inventory.serializers import InventoryFilterSerializer
    from nhit.caching.constants import CACHE_NS_INVENTORY_FILTER
    from nhit.caching.inventory_filter_page import (
        INVENTORY_FILTER_PAGE_CACHE_MAGIC,
        INVENTORY_FILTER_PAGE_SCOPE,
        extract_inventory_filter_page_gzip_payload,
    )
    from nhit.caching.keys import build_cache_key
    from nhit.caching.service import get_namespace_version, safe_cache_get

    payload = {
        'chainage_start': 0,
        'chainage_end': 99999,
        'date': DATE,
        'direction': ['All'],
        'project_name': [PROJECT],
        'asset_type': ['All'],
        'page': 1,
        'page_size': PAGE_SIZE,
    }
    ser = InventoryFilterSerializer(data=payload)
    if not ser.is_valid():
        return {'error': 'serializer_invalid', 'details': ser.errors}
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
    meta = {
        'namespace': CACHE_NS_INVENTORY_FILTER,
        'scope': INVENTORY_FILTER_PAGE_SCOPE,
        'version': version,
        'logical_key_suffix': digest[:16],
        'logical_key_hash': hashlib.sha256(logical.encode()).hexdigest()[:16],
        'redis_key_suffix': redis_key[-24:],
        'redis_key_hash': hashlib.sha256(redis_key.encode()).hexdigest()[:16],
        # intentionally omit full keys
    }

    # cmdstat before
    try:
        before_stats = _parse_cmdstat_get(client.info('commandstats'))
    except Exception:
        before_stats = None

    raw = _measure_gets(client, redis_key)
    if raw.get('error'):
        meta['cache_hit'] = False
        meta['raw_get'] = raw
        return meta

    meta['cache_hit'] = True
    meta['raw_redis_get'] = raw
    meta['server_side_estimate'] = _server_side_get_estimate(client, redis_key)

    # django-redis path (includes pickle unwrap) — matches production page_data_get_ms path
    for _ in range(WARMUPS):
        safe_cache_get(logical)
    dj_times: list[float] = []
    last_val = None
    for _ in range(SAMPLES):
        t0 = time.perf_counter()
        hit, val = safe_cache_get(logical)
        dj_times.append((time.perf_counter() - t0) * 1000)
        last_val = val if hit else None
    meta['django_safe_cache_get'] = {
        'latency_ms': _stats(dj_times),
        'payload_bytes': len(last_val) if isinstance(last_val, (bytes, bytearray)) else None,
    }

    blob_info: dict[str, Any] = {'type': type(last_val).__name__}
    if isinstance(last_val, (bytes, bytearray)):
        blob = bytes(last_val)
        blob_info['size_with_magic'] = len(blob)
        blob_info['starts_with_ifgz1'] = blob.startswith(INVENTORY_FILTER_PAGE_CACHE_MAGIC)
        try:
            gz = extract_inventory_filter_page_gzip_payload(blob)
            blob_info['gzip_payload_bytes'] = len(gz)
            blob_info['valid_ifgz1'] = True
        except Exception as exc:
            blob_info['valid_ifgz1'] = False
            blob_info['error'] = type(exc).__name__
    meta['blob'] = blob_info

    try:
        after_stats = _parse_cmdstat_get(client.info('commandstats'))
    except Exception:
        after_stats = None
    meta['cmdstat_get_before'] = before_stats
    meta['cmdstat_get_after'] = after_stats
    if before_stats and after_stats and before_stats.get('calls') is not None:
        d_calls = (after_stats.get('calls') or 0) - (before_stats.get('calls') or 0)
        d_usec = (after_stats.get('usec') or 0) - (before_stats.get('usec') or 0)
        meta['cmdstat_get_delta'] = {
            'calls': d_calls,
            'usec': d_usec,
            'usec_per_call': round(d_usec / d_calls, 3) if d_calls else None,
        }

    _log(
        f"real inventory raw_get median={raw['latency_ms']['median']} "
        f"django median={meta['django_safe_cache_get']['latency_ms']['median']} "
        f"bytes={raw.get('payload_bytes')}"
    )
    return meta


def _env_snapshot() -> dict[str, Any]:
    from django.conf import settings

    on_railway = bool(
        os.environ.get('RAILWAY_ENVIRONMENT', '').strip()
        or os.environ.get('RAILWAY_SERVICE_NAME', '').strip()
    )
    # Prefer whatever Django resolved; never print URL.
    raw = ''
    if getattr(settings, 'NHIT_REDIS_USING_INTERNAL', False):
        raw = (os.environ.get('REDIS_INTERNAL_URL') or os.environ.get('REDIS_URL') or '').strip()
    else:
        raw = (os.environ.get('REDIS_URL') or os.environ.get('REDIS_INTERNAL_URL') or '').strip()

    pool_meta: dict[str, Any] = {}
    try:
        client = _redis_client()
        pool = getattr(client, 'connection_pool', None)
        if pool is not None:
            pool_meta = {
                'pool_class': type(pool).__name__,
                'max_connections': getattr(pool, 'max_connections', None),
            }
    except Exception:
        pool_meta = {'error': True}

    return {
        'on_railway': on_railway,
        'measurement_context': (
            'railway_container_private_redis' if on_railway else 'local_or_non_railway_runtime'
        ),
        'platform': sys.platform,
        'python': sys.version.split()[0],
        'using_internal_redis': bool(getattr(settings, 'NHIT_REDIS_USING_INTERNAL', False)),
        'cache_enabled': bool(getattr(settings, 'NHIT_CACHE_ENABLED', False)),
        'cache_backend': (settings.CACHES.get('default') or {}).get('BACKEND'),
        'key_prefix': (settings.CACHES.get('default') or {}).get('KEY_PREFIX'),
        'redis': _safe_host_meta(raw),
        'pool': pool_meta,
        'samples': SAMPLES,
        'warmups': WARMUPS,
        'bench_namespace': BENCH_NS,
    }


def _derive_conclusions(report: dict[str, Any]) -> dict[str, Any]:
    floor = report['test2_latency_floor']
    ladder = report['test1_payload_size']['sizes']
    sim = report['test4_inventory_hit_simulation']
    small = report['test3_get_vs_mget']['operations']

    floor_medians = [
        row['latency_ms']['median']
        for row in floor['sizes']
        if row.get('latency_ms', {}).get('median') is not None and row.get('requested_bytes', 0) <= 100
    ]
    ping_med = (floor.get('ping_ms') or {}).get('median')
    fixed_floor = None
    if floor_medians:
        fixed_floor = round(statistics.median(floor_medians), 3)
    elif ping_med is not None:
        fixed_floor = ping_med

    # Find ~1.27 MB row
    large = None
    for row in ladder:
        if row.get('requested_bytes') == 1_330_176:
            large = row
            break
    if large is None and ladder:
        large = max(ladder, key=lambda r: r.get('requested_bytes') or 0)

    large_med = (large or {}).get('latency_ms', {}).get('median')
    large_bytes = (large or {}).get('payload_bytes') or (large or {}).get('requested_bytes')
    throughput = _throughput_mibs(int(large_bytes or 0), large_med)

    seq_med = sim['4_sequential_GET']['total_ms']['median']
    pipe_med = sim['4_GET_pipeline']['total_ms']['median']
    mget_med = sim['1_MGET_four_keys']['total_ms']['median']
    best_one_rtt = None
    for v in (pipe_med, mget_med):
        if v is None:
            continue
        best_one_rtt = v if best_one_rtt is None else min(best_one_rtt, v)
    savings = None
    if seq_med is not None and best_one_rtt is not None:
        savings = round(seq_med - best_one_rtt, 3)

    # Dominance: compare fixed*4 vs transfer component of large GET
    transfer_component = None
    if large_med is not None and fixed_floor is not None:
        transfer_component = max(0.0, round(large_med - fixed_floor, 3))

    # Size dependence: slope between 10KB and 1.27MB
    small_row = next((r for r in ladder if r.get('requested_bytes') == 10_240), None)
    small_med = (small_row or {}).get('latency_ms', {}).get('median')
    size_dependent = None
    if large_med is not None and small_med is not None and small_med > 0:
        size_dependent = large_med > small_med * 1.5

    if size_dependent and transfer_component and large_med and transfer_component / large_med >= 0.5:
        dominant = 'payload_size_transfer'
        first_optimize = 'Redis payload size (page blob transfer Redis→Django before TTFB)'
    elif fixed_floor and large_med and (fixed_floor * 4) >= 0.4 * (seq_med or large_med):
        dominant = 'rtt_command_floor'
        first_optimize = 'Redis round trips (collapse 4 GETs → 1 pipeline/MGET)'
    else:
        dominant = 'mixed_payload_and_rtt'
        first_optimize = (
            'Both: payload transfer dominates page GET; RTT collapse helps the ~3 small GETs'
        )

    # Prefer clearer guidance from sim numbers
    page_part = sim['4_sequential_GET']['per_key_ms']['page_data']['median']
    small_sum = sum(
        sim['4_sequential_GET']['per_key_ms'][n]['median'] or 0
        for n in ('count_ns', 'count_data', 'page_ns')
    )
    if page_part and seq_med and page_part / seq_med >= 0.6:
        first_optimize = (
            'Redis payload size first (page blob GET), then round-trip collapse for the 3 small GETs'
        )
        dominant = 'payload_size_transfer_with_secondary_rtt'

    return {
        'fixed_redis_latency_floor_ms': fixed_floor,
        'ping_median_ms': ping_med,
        'approx_transfer_throughput_mib_s_at_1_27mb': throughput,
        'large_get_median_ms': large_med,
        'large_get_bytes': large_bytes,
        'transfer_component_ms_approx': transfer_component,
        'expected_savings_4ops_to_1op_ms': savings,
        'sim_sequential_median_ms': seq_med,
        'sim_pipeline_median_ms': pipe_med,
        'sim_mget_median_ms': mget_med,
        'sim_page_data_median_ms': page_part,
        'sim_three_small_sum_median_ms': round(small_sum, 3),
        'small_only_seq_median_ms': small['4_sequential_GET']['latency_ms']['median'],
        'small_only_pipe_median_ms': small['4_GET_pipeline']['latency_ms']['median'],
        'small_only_mget_median_ms': small['1_MGET_four_keys']['latency_ms']['median'],
        'dominant_problem': dominant,
        'first_optimize': first_optimize,
        'size_dependent_latency': size_dependent,
    }


def _write_md(report: dict[str, Any]) -> str:
    env = report['environment']
    c = report['conclusions']
    lines = [
        '# inventory_filter Redis payload vs RTT investigation',
        '',
        '**Measurement only — no inventory_filter / cache key / TTL / Redis config changes.**',
        '',
        f"- Measurement context: `{env['measurement_context']}`",
        f"- On Railway: `{env['on_railway']}`",
        f"- Redis host class: `{env['redis'].get('host_class')}` port=`{env['redis'].get('port')}`",
        f"- Using internal Redis: `{env['using_internal_redis']}`",
        f"- Samples/warmups: {env['samples']}/{env['warmups']}",
        f"- Temp key namespace: `{env['bench_namespace']}` (deleted after run)",
        '',
        '## TEST 1 — Payload size vs GET latency',
        '',
        '| Payload size | GET median | GET P95 | GET min | GET max | MiB/s |',
        '| ---: | ---: | ---: | ---: | ---: | ---: |',
    ]
    for row in report['test1_payload_size']['sizes']:
        lat = row['latency_ms']
        size = row.get('requested_bytes')
        lines.append(
            f"| {size} | {lat.get('median')} | {lat.get('p95')} | {lat.get('min')} | "
            f"{lat.get('max')} | {row.get('throughput_mib_s_median')} |"
        )

    lines += [
        '',
        '## TEST 2 — Command latency floor',
        '',
        f"- PING median: **{report['test2_latency_floor']['ping_ms'].get('median')} ms**",
        '',
        '| Payload size | GET median | GET P95 | GET min | GET max |',
        '| ---: | ---: | ---: | ---: | ---: |',
    ]
    for row in report['test2_latency_floor']['sizes']:
        lat = row['latency_ms']
        lines.append(
            f"| {row.get('requested_bytes')} | {lat.get('median')} | {lat.get('p95')} | "
            f"{lat.get('min')} | {lat.get('max')} |"
        )

    lines += [
        '',
        '## TEST 3 — GET vs pipeline vs MGET (4×64 B)',
        '',
        '| Operation strategy | Median | P95 | Redis ops |',
        '| --- | ---: | ---: | ---: |',
    ]
    for name, block in report['test3_get_vs_mget']['operations'].items():
        lat = block['latency_ms']
        lines.append(
            f"| {name} | {lat.get('median')} | {lat.get('p95')} | {block.get('redis_operation_count')} |"
        )

    sim = report['test4_inventory_hit_simulation']
    lines += [
        '',
        '## TEST 4 — Simulated inventory HIT (3×8 B + ~1.27 MB)',
        '',
        '| Operation strategy | Median | P95 | Redis ops |',
        '| --- | ---: | ---: | ---: |',
        f"| 4 sequential GETs | {sim['4_sequential_GET']['total_ms'].get('median')} | "
        f"{sim['4_sequential_GET']['total_ms'].get('p95')} | 4 |",
        f"| 4 GET pipeline | {sim['4_GET_pipeline']['total_ms'].get('median')} | "
        f"{sim['4_GET_pipeline']['total_ms'].get('p95')} | 1 |",
        f"| MGET (4 keys) | {sim['1_MGET_four_keys']['total_ms'].get('median')} | "
        f"{sim['1_MGET_four_keys']['total_ms'].get('p95')} | 1 |",
        '',
        'Per-key sequential medians:',
        '',
    ]
    for name, st in sim['4_sequential_GET']['per_key_ms'].items():
        lines.append(f"- `{name}`: {st.get('median')} ms")

    inv = report['test5_real_inventory_key']
    lines += [
        '',
        '## TEST 5 — Real 100K inventory page key (GET only)',
        '',
        f"- cache_hit: `{inv.get('cache_hit')}`",
        f"- key hash/suffix only: `{inv.get('logical_key_hash')}` / `{inv.get('logical_key_suffix')}`",
        f"- blob: `{json.dumps(inv.get('blob'))}`",
        f"- raw redis-py GET: `{json.dumps((inv.get('raw_redis_get') or {}).get('latency_ms'))}` "
        f"bytes=`{(inv.get('raw_redis_get') or {}).get('payload_bytes')}`",
        f"- django safe_cache_get: `{json.dumps((inv.get('django_safe_cache_get') or {}).get('latency_ms'))}`",
        f"- server-side TIME estimate: `{json.dumps(inv.get('server_side_estimate'))}`",
        f"- cmdstat_get delta: `{json.dumps(inv.get('cmdstat_get_delta'))}`",
        '',
        '## Derived answers',
        '',
        f"1. Approximate fixed Redis latency floor: **{c['fixed_redis_latency_floor_ms']} ms** "
        f"(PING median {c['ping_median_ms']} ms)",
        f"2. Approx transfer throughput at ~1.27 MB: **{c['approx_transfer_throughput_mib_s_at_1_27mb']} MiB/s** "
        f"(large GET median {c['large_get_median_ms']} ms)",
        f"3. Expected savings reducing 4 ops → 1 op (sim HIT): **{c['expected_savings_4ops_to_1op_ms']} ms** "
        f"(seq {c['sim_sequential_median_ms']} → best one-RTT {min(x for x in [c['sim_pipeline_median_ms'], c['sim_mget_median_ms']] if x is not None)})",
        f"4. Dominant problem: **{c['dominant_problem']}**",
        '',
        '## Final recommendation (do not implement here)',
        '',
        f"**Should we first optimize Redis round trips, Redis payload size, or Redis/network configuration?**",
        '',
        f"**Answer: {c['first_optimize']}**",
        '',
        'Redis/network configuration is lower priority: private Redis is already in use and '
        'the pool is reused; the measured cost is payload transfer + multiple command RTTs.',
        '',
        '## Safety',
        '',
        '- Temporary keys deleted after run',
        '- Production inventory keys were GET-only (not modified)',
        '- No FLUSH, no invalidation, no inventory_filter code changes',
        '',
    ]
    return '\n'.join(lines) + '\n'


def main() -> int:
    _setup_django()
    from django.conf import settings

    if not getattr(settings, 'NHIT_CACHE_ENABLED', False):
        _log('ERROR: Redis/cache not enabled in this runtime')
        return 2

    run_id = uuid.uuid4().hex[:12]
    client = _redis_client()
    env = _env_snapshot()
    _log(f"run_id={run_id} context={env['measurement_context']} host_class={env['redis'].get('host_class')}")

    if not env['on_railway']:
        _log(
            'WARNING: not on Railway runtime — results may use public Redis proxy and are '
            'NOT authoritative for production private Redis. Prefer: '
            'railway ssh -- python manage.py benchmark_inventory_filter_redis_payload_rtt'
        )

    created: list[str] = []
    try:
        # Warm the shared connection
        client.ping()

        t1, keys = test_size_ladder(client, run_id)
        created.extend(keys)

        t2, keys = test_latency_floor(client, run_id)
        created.extend(keys)

        t3, keys = test_get_vs_mget_pipeline(client, run_id)
        created.extend(keys)

        t4, keys = test_inventory_hit_simulation(client, run_id)
        created.extend(keys)

        t5 = test_real_inventory_key(client)

        report: dict[str, Any] = {
            'environment': env,
            'run_id': run_id,
            'test1_payload_size': t1,
            'test2_latency_floor': t2,
            'test3_get_vs_mget': t3,
            'test4_inventory_hit_simulation': t4,
            'test5_real_inventory_key': t5,
        }
        report['conclusions'] = _derive_conclusions(report)
        md = _write_md(report)
        report['markdown'] = md

        # Always emit JSON to stdout for railway ssh capture.
        print('-----BEGIN_JSON-----', flush=True)
        print(json.dumps(report, indent=2, default=str), flush=True)
        print('-----END_JSON-----', flush=True)

        # Best-effort local write when filesystem is the workspace.
        try:
            OUT_JSON.write_text(json.dumps(report, indent=2, default=str), encoding='utf-8')
            OUT_MD.write_text(md, encoding='utf-8')
            _log(f'Wrote {OUT_JSON}')
            _log(f'Wrote {OUT_MD}')
        except Exception as exc:
            _log(f'NOTE: could not write local artifacts ({type(exc).__name__}); use stdout JSON')

        _log(f"Dominant={report['conclusions']['dominant_problem']}")
        _log(f"First optimize: {report['conclusions']['first_optimize']}")
        return 0
    finally:
        deleted = _delete_keys(client, created)
        _log(f'Cleanup deleted_temp_keys={deleted}')


if __name__ == '__main__':
    raise SystemExit(main())
