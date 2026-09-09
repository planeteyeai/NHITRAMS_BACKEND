"""Phase 2 END-TO-END Redis cache path benchmark for inventory_filter 50k page.

Compares:
  OLD: get_or_set_cached_value storing the Python nested object (pickle via django-redis)
  NEW: get_cached_inventory_filter_page storing IFGZ1+gzip(JSON) bytes

Does not change production code. Writes JSON + prints a markdown table.
"""

from __future__ import annotations

import gzip
import json
import os
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'nhit.settings')
os.environ.setdefault('PYTHONUNBUFFERED', '1')

import django

django.setup()

from django.core.cache import cache
from rest_framework.renderers import JSONRenderer
from rest_framework.response import Response

from inventory.serializers import InventoryFilterSerializer
from inventory.services.inventory_filter_queryset import build_inventory_filter_queryset
from inventory.services.inventory_filter_response import build_inventory_filter_groups
from nhit.caching.constants import CACHE_NS_INVENTORY_FILTER, CACHE_TTL_INVENTORY_FILTER
from nhit.caching.inventory_filter_page import (
    INVENTORY_FILTER_PAGE_SCOPE,
    decode_inventory_filter_page_cache,
    encode_inventory_filter_page_cache,
    get_cached_inventory_filter_page,
)
from nhit.caching.keys import build_cache_key
from nhit.caching.service import (
    get_namespace_version,
    get_or_set_cached_value,
    is_cache_usable,
    safe_cache_get,
    safe_cache_set,
)

PROJECT = 'Kothakota to Kurnool'
DATE = '2026-07-28'
SAMPLES = 5
OUT_JSON = ROOT / 'tools' / 'benchmark_inventory_filter_phase2_cache_results.json'
OUT_MD = ROOT / 'tools' / 'inventory_filter_phase2_redis_cache_report.md'


def _log(m: str) -> None:
    print(m, flush=True)


def _stats(values: list[float]) -> dict:
    ordered = sorted(values)
    p95_idx = min(len(ordered) - 1, max(0, int(round(0.95 * (len(ordered) - 1)))))
    return {
        'samples': [round(v, 2) for v in values],
        'median': round(statistics.median(values), 2),
        'p95': round(ordered[p95_idx], 2),
        'mean': round(statistics.fmean(values), 2),
    }


def _build_groups(data):
    return build_inventory_filter_groups(
        build_inventory_filter_queryset(data),
        offset=0,
        limit=50000,
    )


def main() -> int:
    body = {
        'chainage_start': 0,
        'chainage_end': 99999,
        'date': DATE,
        'direction': ['All'],
        'project_name': [PROJECT],
        'asset_type': ['All'],
        'page': 1,
        'page_size': 50000,
    }
    ser = InventoryFilterSerializer(data=body)
    assert ser.is_valid(), ser.errors
    data = ser.validated_data

    if not is_cache_usable():
        _log('ERROR: Redis/cache not usable (NHIT_CACHE_ENABLED / REDIS_URL). Aborting.')
        return 2

    _log('Building 50k page once from DB...')
    t0 = time.perf_counter()
    groups = _build_groups(data)
    build_ms = (time.perf_counter() - t0) * 1000
    _log(f'build_ms={build_ms:.1f} groups={len(groups)}')

    json_bytes = JSONRenderer().render(groups)
    encoded_new = encode_inventory_filter_page_cache(groups)
    http_gzip = gzip.compress(json_bytes, compresslevel=6)

    # --- OLD path: pickle object via get_or_set ---
    old_scope = 'phase2_bench_old_pickle'
    old_miss_set = []
    old_hit_get = []
    old_hit_total = []

    for i in range(SAMPLES):
        version = get_namespace_version(CACHE_NS_INVENTORY_FILTER)
        key = build_cache_key(
            namespace=CACHE_NS_INVENTORY_FILTER,
            version=version,
            payload=data,
            scope=f'{old_scope}:{i}',
        )
        cache.delete(key)

        t_set0 = time.perf_counter()
        safe_cache_set(key, groups, timeout=CACHE_TTL_INVENTORY_FILTER)
        old_miss_set.append((time.perf_counter() - t_set0) * 1000)

        t_get0 = time.perf_counter()
        hit, value = safe_cache_get(key)
        get_ms = (time.perf_counter() - t_get0) * 1000
        assert hit and value is not None
        t_r0 = time.perf_counter()
        rendered = JSONRenderer().render(value)
        render_ms = (time.perf_counter() - t_r0) * 1000
        t_g0 = time.perf_counter()
        gzip.compress(rendered, compresslevel=6)
        gzip_ms = (time.perf_counter() - t_g0) * 1000
        old_hit_get.append(get_ms)
        old_hit_total.append(get_ms + render_ms + gzip_ms)
        cache.delete(key)

    # --- NEW path: gzip JSON bytes via production helper ---
    new_miss = []
    new_hit = []
    new_encode = []
    new_decode = []
    new_set = []
    new_get = []
    new_render = []
    new_gzip = []

    for i in range(SAMPLES):
        payload = {**data, '_bench_nonce': i}

        # MISS
        def builder_miss():
            return groups

        # Force unique key via nonce in payload
        t_miss0 = time.perf_counter()
        value, meta = get_cached_inventory_filter_page(
            payload=payload,
            builder=builder_miss,
            timeout=CACHE_TTL_INVENTORY_FILTER,
        )
        miss_total = (time.perf_counter() - t_miss0) * 1000
        assert value == groups
        assert meta.get('cache_hit') is False
        new_miss.append(miss_total)
        new_encode.append(meta.get('encode_ms') or 0)

        # Measure SET alone for new representation
        version = get_namespace_version(CACHE_NS_INVENTORY_FILTER)
        key = build_cache_key(
            namespace=CACHE_NS_INVENTORY_FILTER,
            version=version,
            payload=payload,
            scope=INVENTORY_FILTER_PAGE_SCOPE,
        )
        # HIT path timings
        t_hit0 = time.perf_counter()
        value2, meta2 = get_cached_inventory_filter_page(
            payload=payload,
            builder=builder_miss,
            timeout=CACHE_TTL_INVENTORY_FILTER,
        )
        hit_helper_ms = (time.perf_counter() - t_hit0) * 1000
        assert meta2.get('cache_hit') is True
        assert value2 == groups
        new_hit.append(hit_helper_ms)
        new_decode.append(meta2.get('decode_ms') or 0)

        t_r0 = time.perf_counter()
        rendered = JSONRenderer().render(value2)
        render_ms = (time.perf_counter() - t_r0) * 1000
        t_g0 = time.perf_counter()
        gzip.compress(rendered, compresslevel=6)
        gzip_ms = (time.perf_counter() - t_g0) * 1000
        new_render.append(render_ms)
        new_gzip.append(gzip_ms)

        # Isolated GET of raw bytes
        t_g0 = time.perf_counter()
        hit, raw = safe_cache_get(key)
        get_ms = (time.perf_counter() - t_g0) * 1000
        assert hit
        new_get.append(get_ms)
        assert isinstance(raw, (bytes, bytearray))

        # Isolated SET of encoded bytes
        cache.delete(key)
        t_s0 = time.perf_counter()
        safe_cache_set(key, encoded_new, timeout=CACHE_TTL_INVENTORY_FILTER)
        new_set.append((time.perf_counter() - t_s0) * 1000)
        cache.delete(key)

    # Correctness: NEW decode == original groups; JSON render matches
    assert decode_inventory_filter_page_cache(encoded_new) == json.loads(json_bytes)
    assert JSONRenderer().render(decode_inventory_filter_page_cache(encoded_new)) == json_bytes

    report = {
        'project': PROJECT,
        'date': DATE,
        'page': 1,
        'page_size': 50000,
        'group_count': len(groups),
        'build_ms': round(build_ms, 2),
        'sizes': {
            'json_uncompressed_bytes': len(json_bytes),
            'redis_new_ifgz1_gzip_bytes': len(encoded_new),
            'http_gzip_bytes': len(http_gzip),
        },
        'old_pickle_object': {
            'redis_set_ms': _stats(old_miss_set),
            'redis_get_unpickle_ms': _stats(old_hit_get),
            'hit_get_plus_render_plus_gzip_ms': _stats(old_hit_total),
        },
        'new_gzip_json_bytes': {
            'helper_miss_total_ms': _stats(new_miss),
            'helper_hit_total_ms': _stats(new_hit),
            'encode_ms': _stats(new_encode),
            'decode_ms': _stats(new_decode),
            'redis_set_ms': _stats(new_set),
            'redis_get_ms': _stats(new_get),
            'json_render_ms': _stats(new_render),
            'http_gzip_ms': _stats(new_gzip),
            'hit_get_decode_render_gzip_ms': _stats(
                [
                    new_get[i] + new_decode[i] + new_render[i] + new_gzip[i]
                    for i in range(SAMPLES)
                ]
            ),
        },
        'cache_usable': True,
        'cache_repr': INVENTORY_FILTER_PAGE_SCOPE,
    }

    OUT_JSON.write_text(json.dumps(report, indent=2), encoding='utf-8')
    _log(json.dumps(report, indent=2))
    _write_md(report)
    _log(f'Wrote {OUT_JSON}')
    _log(f'Wrote {OUT_MD}')
    return 0


def _write_md(report: dict) -> None:
    old = report['old_pickle_object']
    new = report['new_gzip_json_bytes']
    sizes = report['sizes']
    lines = [
        '# Phase 2 — inventory_filter Redis cache representation',
        '',
        '## Target request',
        '',
        '```json',
        json.dumps(
            {
                'chainage_start': 0,
                'chainage_end': 99999,
                'date': DATE,
                'direction': ['All'],
                'project_name': [PROJECT],
                'asset_type': ['All'],
                'page': 1,
                'page_size': 50000,
            },
            indent=2,
        ),
        '```',
        '',
        f"- Groups in page: **{report['group_count']}**",
        f"- One-time DB/build: **{report['build_ms']} ms** (out of Phase 2 scope)",
        '',
        '## Representation',
        '',
        '| | OLD | NEW |',
        '|---|---|---|',
        '| Redis value | Pickled Python nested list/dict | `IFGZ1\\n` + gzip(JSON bytes) |',
        f"| Scope | `` (empty, historical) | `{INVENTORY_FILTER_PAGE_SCOPE}` |",
        f"| Stored size (this run) | ~8.3 MB pickle (format bench) | **{sizes['redis_new_ifgz1_gzip_bytes']:,}** bytes |",
        f"| JSON uncompressed | {sizes['json_uncompressed_bytes']:,} | same |",
        f"| HTTP gzip (unchanged) | {sizes['http_gzip_bytes']:,} | same |",
        '',
        '## Redis SET / HIT (this run, medians)',
        '',
        '| Metric | OLD median | NEW median |',
        '|---|---:|---:|',
        f"| Redis SET | {old['redis_set_ms']['median']} ms | {new['redis_set_ms']['median']} ms |",
        f"| Redis GET (+unpickle OLD / raw bytes NEW) | {old['redis_get_unpickle_ms']['median']} ms | {new['redis_get_ms']['median']} ms |",
        f"| HIT helper / pipeline | {old['hit_get_plus_render_plus_gzip_ms']['median']} ms (get+render+gzip) | {new['helper_hit_total_ms']['median']} ms (helper) / {new['hit_get_decode_render_gzip_ms']['median']} ms (get+decode+render+gzip) |",
        '',
        'See also `tools/benchmark_inventory_filter_cache_format_results.json` for pickle vs JSON vs gzip vs zlib size/timing comparison that selected gzip.',
        '',
        f'Raw results: `{OUT_JSON.name}`',
        '',
    ]
    OUT_MD.write_text('\n'.join(lines), encoding='utf-8')


if __name__ == '__main__':
    raise SystemExit(main())
