"""Benchmark POST /inventory_filter/ page_size sweep for Kothakota to Kurnool.

Measures (cache-disabled / first-hit path):
  HTTP total, DB query count, COUNT, SELECT, serialize, JSON render, gzip,
  uncompressed/compressed bytes, peak RSS.

Usage:
    set PYTHONUNBUFFERED=1
    python -u tools/benchmark_inventory_filter_page_sizes.py
"""

from __future__ import annotations

import gzip
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'nhit.settings')
os.environ.setdefault('PYTHONUNBUFFERED', '1')

import django

django.setup()

from django.conf import settings
from django.core.cache import cache
from django.db import connection, reset_queries
from django.test import Client, override_settings
from rest_framework.renderers import JSONRenderer

from inventory.models import InventoryWide
from inventory.services.inventory_filter_queryset import build_inventory_filter_queryset
from inventory.services.inventory_filter_response import build_inventory_filter_groups
from inventory.serializers import InventoryFilterSerializer
from nhit.caching.inventory_filter_count import get_cached_inventory_filter_count


PAGE_SIZES = (5000, 15000, 25000, 50000)
DEFAULT_PROJECT = 'Kothakota to Kurnool'


def _log(msg: str) -> None:
    print(msg, flush=True)


def _rss_mb() -> float | None:
    try:
        import psutil

        return round(psutil.Process(os.getpid()).memory_info().rss / (1024 * 1024), 1)
    except Exception:
        try:
            import resource

            val = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            if sys.platform == 'darwin':
                return round(val / (1024 * 1024), 1)
            return round(val / 1024.0, 1)
        except Exception:
            return None


def _discover_date(project_name: str) -> str | None:
    # Prefer the date with the most rows for this project.
    from django.db.models import Count

    row = (
        InventoryWide.objects.filter(project_name=project_name)
        .exclude(survey_date__isnull=True)
        .values('survey_date')
        .annotate(n=Count('inventory_id'))
        .order_by('-n')
        .first()
    )
    if not row:
        return None
    return row['survey_date'].isoformat()


def _payload(project: str, date: str, page_size: int) -> dict[str, Any]:
    return {
        'chainage_start': 0,
        'chainage_end': 99999,
        'date': date,
        'direction': ['All'],
        'project_name': [project],
        'asset_type': ['All'],
        'page': 1,
        'page_size': page_size,
    }


def _measure_phases(data: dict[str, Any]) -> dict[str, Any]:
    """Cold path: COUNT miss + page build miss, with phase timings."""
    settings.DEBUG = True
    reset_queries()
    connection.force_debug_cursor = True

    rss0 = _rss_mb()
    t_http0 = time.perf_counter()

    queryset = build_inventory_filter_queryset(data)

    t_count0 = time.perf_counter()
    total_count = queryset.count()
    count_ms = (time.perf_counter() - t_count0) * 1000

    page = data['page']
    page_size = data['page_size']
    offset = (page - 1) * page_size
    if offset >= total_count:
        return {
            'status': 'no_match',
            'total_count': total_count,
            'http_ms': round((time.perf_counter() - t_http0) * 1000, 1),
        }

    timings: dict = {}
    groups = build_inventory_filter_groups(
        queryset,
        offset=offset,
        limit=page_size,
        timings=timings,
    )

    t_render0 = time.perf_counter()
    uncompressed = JSONRenderer().render(groups)
    render_ms = (time.perf_counter() - t_render0) * 1000

    t_gzip0 = time.perf_counter()
    compressed = gzip.compress(uncompressed, compresslevel=6)
    gzip_ms = (time.perf_counter() - t_gzip0) * 1000

    http_ms = (time.perf_counter() - t_http0) * 1000
    queries = list(connection.queries)
    rss1 = _rss_mb()

    return {
        'status': 'ok',
        'total_count': total_count,
        'returned_rows': timings.get('row_count'),
        'http_ms': round(http_ms, 1),
        'db_queries': len(queries),
        'count_ms': round(count_ms, 1),
        'select_ms': timings.get('select_ms'),
        'serialize_ms': timings.get('serialize_ms'),
        'render_ms': round(render_ms, 1),
        'gzip_ms': round(gzip_ms, 1),
        'uncompressed_bytes': len(uncompressed),
        'compressed_bytes': len(compressed),
        'rss0_mb': rss0,
        'rss1_mb': rss1,
        'peak_rss_mb': max(x for x in (rss0, rss1) if x is not None) if rss0 or rss1 else None,
    }


def _measure_http(client: Client, body: dict[str, Any]) -> dict[str, Any]:
    settings.DEBUG = True
    reset_queries()
    t0 = time.perf_counter()
    response = client.post(
        '/inventory_filter/',
        data=json.dumps(body),
        content_type='application/json',
        HTTP_ACCEPT_ENCODING='gzip',
    )
    elapsed = (time.perf_counter() - t0) * 1000
    content = response.content
    encoding = response.get('Content-Encoding')
    if encoding == 'gzip':
        raw = gzip.decompress(content)
        compressed_len = len(content)
        uncompressed_len = len(raw)
    else:
        compressed_len = None
        uncompressed_len = len(content)
    return {
        'status_code': response.status_code,
        'http_ms': round(elapsed, 1),
        'db_queries': len(connection.queries),
        'row_count_header': response.get('X-Inventory-Row-Count'),
        'page_size_header': response.get('X-Inventory-Page-Size'),
        'encoding': encoding,
        'wire_bytes': len(content),
        'uncompressed_bytes': uncompressed_len,
        'compressed_bytes': compressed_len,
    }


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument('--project', default=DEFAULT_PROJECT)
    parser.add_argument('--date', default='')
    args = parser.parse_args()

    date = args.date or _discover_date(args.project)
    if not date:
        _log(f'ERROR: no survey_date for project={args.project!r}')
        return 2

    # Confirm dataset size.
    n = InventoryWide.objects.filter(project_name=args.project, survey_date=date).count()
    _log(f'project={args.project!r} date={date} rows={n}')

    # Validate serializer accepts all page sizes.
    for ps in PAGE_SIZES:
        ser = InventoryFilterSerializer(data=_payload(args.project, date, ps))
        assert ser.is_valid(), ser.errors
    bad = InventoryFilterSerializer(data=_payload(args.project, date, 50001))
    assert not bad.is_valid() and 'page_size' in bad.errors
    _log('serializer page_size validation OK (5000..50000 accept, 50001 reject)')

    rows = []
    with override_settings(NHIT_CACHE_ENABLED=False, DEBUG=True):
        cache.clear()
        client = Client()
        for page_size in PAGE_SIZES:
            body = _payload(args.project, date, page_size)
            ser = InventoryFilterSerializer(data=body)
            assert ser.is_valid(), ser.errors
            data = dict(ser.validated_data)
            # Normalize All → [] like the view.
            _log(f'\n=== page_size={page_size} (phase timings, cache off) ===')
            phase = _measure_phases(data)
            _log(json.dumps(phase, default=str))

            cache.clear()
            _log(f'=== page_size={page_size} (HTTP Client, cache off) ===')
            http = _measure_http(client, body)
            _log(json.dumps(http, default=str))
            rows.append({'page_size': page_size, 'phase': phase, 'http': http})

    _log('\n| page_size | HTTP ms | DB qs | COUNT ms | SELECT ms | serialize ms | render ms | gzip ms | uncomp MB | comp MB | peak RSS MB |')
    _log('|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|')
    for row in rows:
        p = row['phase']
        h = row['http']
        uncomp = (p.get('uncompressed_bytes') or 0) / 1e6
        comp = (p.get('compressed_bytes') or 0) / 1e6
        _log(
            f"| {row['page_size']} | {h.get('http_ms')} | {h.get('db_queries')} | "
            f"{p.get('count_ms')} | {p.get('select_ms')} | {p.get('serialize_ms')} | "
            f"{p.get('render_ms')} | {p.get('gzip_ms')} | {uncomp:.2f} | {comp:.2f} | "
            f"{p.get('peak_rss_mb')} |"
        )

    out = ROOT / 'tools' / 'benchmark_inventory_filter_page_sizes_results.json'
    out.write_text(json.dumps({'project': args.project, 'date': date, 'rows_in_db': n, 'results': rows}, indent=2), encoding='utf-8')
    _log(f'\nWrote {out}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
