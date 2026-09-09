"""Measure Redis representations for inventory_filter 50k page (READ-ONLY).

Compares pickle vs JSON vs gzip/zlib JSON for SET/GET timing and size.
Does not change production code.
"""

from __future__ import annotations

import gzip
import json
import os
import pickle
import sys
import time
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'nhit.settings')
os.environ.setdefault('PYTHONUNBUFFERED', '1')

import django

django.setup()

from django.core.cache import cache
from rest_framework.renderers import JSONRenderer

from inventory.serializers import InventoryFilterSerializer
from inventory.services.inventory_filter_queryset import build_inventory_filter_queryset
from inventory.services.inventory_filter_response import build_inventory_filter_groups
from nhit.caching.constants import CACHE_TTL_INVENTORY_FILTER
from nhit.caching.service import is_cache_usable, safe_cache_get, safe_cache_set

PROJECT = 'Kothakota to Kurnool'
DATE = '2026-07-28'
SAMPLES = 3


def _log(m: str) -> None:
    print(m, flush=True)


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

    _log('Building 50k page groups (one-time DB build)...')
    t0 = time.perf_counter()
    groups = build_inventory_filter_groups(
        build_inventory_filter_queryset(data),
        offset=0,
        limit=50000,
    )
    build_ms = (time.perf_counter() - t0) * 1000
    _log(f'build_ms={build_ms:.1f} groups={len(groups)}')

    # Serialize once for JSON paths
    t0 = time.perf_counter()
    json_bytes = JSONRenderer().render(groups)
    json_ms = (time.perf_counter() - t0) * 1000

    t0 = time.perf_counter()
    pickle_bytes = pickle.dumps(groups, protocol=pickle.HIGHEST_PROTOCOL)
    pickle_ms = (time.perf_counter() - t0) * 1000

    t0 = time.perf_counter()
    gzip_bytes = gzip.compress(json_bytes, compresslevel=6)
    gzip_ms = (time.perf_counter() - t0) * 1000

    t0 = time.perf_counter()
    zlib_bytes = zlib.compress(json_bytes, level=6)
    zlib_ms = (time.perf_counter() - t0) * 1000

    # Decompress/deserialize CPU
    t0 = time.perf_counter()
    _ = pickle.loads(pickle_bytes)
    pickle_load_ms = (time.perf_counter() - t0) * 1000

    t0 = time.perf_counter()
    _ = json.loads(json_bytes)
    json_load_ms = (time.perf_counter() - t0) * 1000

    t0 = time.perf_counter()
    raw = gzip.decompress(gzip_bytes)
    gunzip_ms = (time.perf_counter() - t0) * 1000
    t0 = time.perf_counter()
    _ = json.loads(raw)
    gzip_json_load_ms = (time.perf_counter() - t0) * 1000

    t0 = time.perf_counter()
    rawz = zlib.decompress(zlib_bytes)
    zunzip_ms = (time.perf_counter() - t0) * 1000
    t0 = time.perf_counter()
    _ = json.loads(rawz)
    zlib_json_load_ms = (time.perf_counter() - t0) * 1000

    out = {
        'build_ms': round(build_ms, 2),
        'sizes': {
            'pickle': len(pickle_bytes),
            'json': len(json_bytes),
            'gzip_json': len(gzip_bytes),
            'zlib_json': len(zlib_bytes),
        },
        'cpu': {
            'pickle_dumps_ms': round(pickle_ms, 2),
            'json_dumps_ms': round(json_ms, 2),
            'gzip_compress_ms': round(gzip_ms, 2),
            'zlib_compress_ms': round(zlib_ms, 2),
            'pickle_loads_ms': round(pickle_load_ms, 2),
            'json_loads_ms': round(json_load_ms, 2),
            'gunzip_ms': round(gunzip_ms, 2),
            'gzip_then_json_loads_ms': round(gunzip_ms + gzip_json_load_ms, 2),
            'zunz_ms': round(zunzip_ms, 2),
            'zlib_then_json_loads_ms': round(zunzip_ms + zlib_json_load_ms, 2),
        },
        'redis': {},
        'cache_usable': is_cache_usable(),
    }

    if is_cache_usable():
        variants = {
            'pickle_obj': groups,  # current django-redis path (pickle internally)
            'json_bytes': json_bytes,
            'gzip_json_bytes': gzip_bytes,
            'zlib_json_bytes': zlib_bytes,
        }
        for name, value in variants.items():
            key = f'nhit:bench:inv50k:{name}:{int(time.time())}'
            set_samples = []
            get_samples = []
            for i in range(SAMPLES):
                k = f'{key}:{i}'
                try:
                    cache.delete(k)
                except Exception:
                    pass
                t0 = time.perf_counter()
                ok = safe_cache_set(k, value, timeout=120)
                set_samples.append((time.perf_counter() - t0) * 1000)
                t0 = time.perf_counter()
                hit, got = safe_cache_get(k)
                get_samples.append((time.perf_counter() - t0) * 1000)
                assert ok and hit, (name, ok, hit, type(got))
                try:
                    cache.delete(k)
                except Exception:
                    pass
            out['redis'][name] = {
                'set_ms_samples': [round(x, 2) for x in set_samples],
                'set_ms_median': round(sorted(set_samples)[len(set_samples) // 2], 2),
                'get_ms_samples': [round(x, 2) for x in get_samples],
                'get_ms_median': round(sorted(get_samples)[len(get_samples) // 2], 2),
                'got_type': type(got).__name__,
                'got_len': len(got) if hasattr(got, '__len__') else None,
            }
            _log(f'{name}: SET med={out["redis"][name]["set_ms_median"]} GET med={out["redis"][name]["get_ms_median"]}')

    path = ROOT / 'tools' / 'benchmark_inventory_filter_cache_format_results.json'
    path.write_text(json.dumps(out, indent=2), encoding='utf-8')
    _log(json.dumps(out, indent=2))
    _log(f'wrote {path}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
