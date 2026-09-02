"""Measure gzip compression for POST /inventory_filter/ (analysis-only).

Usage:
    python tools/benchmark_inventory_gzip.py
    python tools/benchmark_inventory_gzip.py --project "Kothakota to Kurnool" --date 2026-04-20
"""
from __future__ import annotations

import argparse
import gzip
import json
import os
import statistics
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'nhit.settings')

import django

django.setup()

from django.conf import settings
from django.db import connection, reset_queries
from django.test import Client

from inventory.models import InventoryWide
from inventory.serializers import InventoryFilterSerializer

WARMUP_RUNS = 1
MEASURED_RUNS = 10

DEFAULT_PAYLOAD = {
    'chainage_start': 0,
    'chainage_end': 99999,
    'date': '2026-04-20',
    'direction': ['All'],
    'project_name': ['Kothakota to Kurnool'],
    'asset_type': ['All'],
}


def _discover_kothakota_date(project_name: str) -> str | None:
    row = (
        InventoryWide.objects.filter(project_name=project_name)
        .exclude(survey_date__isnull=True)
        .values_list('survey_date', flat=True)
        .first()
    )
    return row.isoformat() if row else None


def _validated_payload(raw: dict[str, Any]) -> dict[str, Any]:
    serializer = InventoryFilterSerializer(data=raw)
    if not serializer.is_valid():
        raise ValueError(serializer.errors)
    return dict(serializer.validated_data)


def _build_queryset(data: dict[str, Any]):
    queryset = InventoryWide.objects.all()
    if data['project_name']:
        queryset = queryset.filter(project_name__in=data['project_name'])
    if data['asset_type']:
        queryset = queryset.filter(asset_type__in=data['asset_type'])
    if data['date']:
        queryset = queryset.filter(survey_date=data['date'])
    if data['chainage_start'] is not None:
        queryset = queryset.filter(chainage_start__gte=data['chainage_start'])
    if data['chainage_end'] is not None:
        queryset = queryset.filter(chainage_end__lte=data['chainage_end'])
    if data['direction']:
        queryset = queryset.filter(direction__in=data['direction'])
    return queryset


def _measure_builder_phases(data: dict[str, Any]) -> dict[str, float]:
    from inventory.services.inventory_filter_response import (
        group_serialized_rows,
        serialize_inventory_wide_row,
    )

    reset_queries()
    connection.force_debug_cursor = True
    t0 = time.perf_counter()
    queryset = _build_queryset(data)

    t_query_start = time.perf_counter()
    items = list(queryset.order_by('-inventory_id'))
    db_ms = (time.perf_counter() - t_query_start) * 1000
    query_count = len(connection.queries)

    t_ser_start = time.perf_counter()
    rows = [serialize_inventory_wide_row(item) for item in items]
    groups = group_serialized_rows(rows)
    serialization_ms = (time.perf_counter() - t_ser_start) * 1000

    t_json_start = time.perf_counter()
    json_bytes = json.dumps(groups, separators=(',', ':'), default=str).encode('utf-8')
    json_ms = (time.perf_counter() - t_json_start) * 1000

    t_gzip_start = time.perf_counter()
    gzip_bytes = gzip.compress(json_bytes)
    gzip_ms = (time.perf_counter() - t_gzip_start) * 1000

    connection.force_debug_cursor = False
    total_ms = (time.perf_counter() - t0) * 1000

    return {
        'db_ms': round(db_ms, 2),
        'serialization_ms': round(serialization_ms, 2),
        'json_ms': round(json_ms, 2),
        'gzip_ms': round(gzip_ms, 2),
        'total_builder_ms': round(total_ms, 2),
        'query_count': query_count,
        'uncompressed_bytes': len(json_bytes),
        'gzip_bytes': len(gzip_bytes),
        'record_count': sum(len(g) for g in groups),
        'group_count': len(groups),
    }


def _http_request(client: Client, payload: dict, *, gzip_accept: bool) -> dict[str, Any]:
    settings.NHIT_CACHE_ENABLED = False
    headers: dict[str, str] = {}
    if gzip_accept:
        headers['HTTP_ACCEPT_ENCODING'] = 'gzip'

    reset_queries()
    connection.force_debug_cursor = True
    start = time.perf_counter()
    response = client.post('/inventory_filter/', payload, content_type='application/json', **headers)
    elapsed_ms = (time.perf_counter() - start) * 1000
    queries = len(connection.queries)
    connection.force_debug_cursor = False

    raw = response.content
    encoding = response.get('Content-Encoding', '')
    compressed = encoding == 'gzip'
    wire_bytes = len(raw)

    if compressed:
        body = gzip.decompress(raw)
    else:
        body = raw

    return {
        'status': response.status_code,
        'elapsed_ms': round(elapsed_ms, 2),
        'queries': queries,
        'content_encoding': encoding or 'identity',
        'wire_bytes': wire_bytes,
        'body_bytes': len(body),
        'body': body,
        'compressed': compressed,
    }


def _run_http_phase(client: Client, payload: dict, *, gzip_accept: bool) -> dict[str, Any]:
    label = 'gzip' if gzip_accept else 'no_gzip'
    for _ in range(WARMUP_RUNS):
        _http_request(client, payload, gzip_accept=gzip_accept)

    samples = [_http_request(client, payload, gzip_accept=gzip_accept) for _ in range(MEASURED_RUNS)]
    times = [s['elapsed_ms'] for s in samples]
    sorted_times = sorted(times)
    p95_idx = max(0, min(len(sorted_times) - 1, int(len(sorted_times) * 0.95) - 1))

    return {
        'label': label,
        'runs': len(samples),
        'avg_ms': round(statistics.mean(times), 2),
        'median_ms': round(statistics.median(times), 2),
        'p95_ms': round(sorted_times[p95_idx], 2),
        'min_ms': round(min(times), 2),
        'max_ms': round(max(times), 2),
        'avg_queries': round(statistics.mean(s['queries'] for s in samples), 2),
        'avg_wire_bytes': round(statistics.mean(s['wire_bytes'] for s in samples)),
        'avg_body_bytes': round(statistics.mean(s['body_bytes'] for s in samples)),
        'content_encoding': samples[-1]['content_encoding'],
        'status_codes': sorted({s['status'] for s in samples}),
        'sample_body': samples[-1]['body'],
    }


def _deep_equal_json(a: bytes, b: bytes) -> bool:
    return json.loads(a.decode('utf-8')) == json.loads(b.decode('utf-8'))


def main() -> int:
    parser = argparse.ArgumentParser(description='Benchmark inventory_filter gzip')
    parser.add_argument('--project', default='Kothakota to Kurnool')
    parser.add_argument('--date', default=None)
    parser.add_argument('--output', default=str(ROOT / 'tools' / 'inventory_gzip_benchmark_report.json'))
    args = parser.parse_args()

    payload = dict(DEFAULT_PAYLOAD)
    payload['project_name'] = [args.project]
    if args.date:
        payload['date'] = args.date
    else:
        discovered = _discover_kothakota_date(args.project)
        if discovered:
            payload['date'] = discovered
            print(f'Using discovered survey date: {discovered}')
        else:
            print(f'WARNING: No survey_date found for {args.project!r}; using {payload["date"]}')

    gzip_enabled = 'django.middleware.gzip.GZipMiddleware' in settings.MIDDLEWARE
    print(f'GZipMiddleware enabled: {gzip_enabled}')

    data = _validated_payload(payload)
    builder = _measure_builder_phases(data)

    client = Client()
    no_gzip = _run_http_phase(client, payload, gzip_accept=False)
    with_gzip = _run_http_phase(client, payload, gzip_accept=True)

    uncompressed = builder['uncompressed_bytes']
    compressed = with_gzip['avg_wire_bytes']
    bytes_saved = uncompressed - compressed
    ratio = round(uncompressed / compressed, 2) if compressed else 0
    reduction_pct = round((bytes_saved / uncompressed) * 100, 1) if uncompressed else 0

    exact_match = (
        no_gzip['status_codes'] == [200]
        and with_gzip['status_codes'] == [200]
        and _deep_equal_json(no_gzip['sample_body'], with_gzip['sample_body'])
    )

    report = {
        'payload': payload,
        'gzip_middleware_enabled': gzip_enabled,
        'builder_phases': builder,
        'size': {
            'uncompressed_bytes': uncompressed,
            'gzip_bytes': int(compressed),
            'bytes_saved': int(bytes_saved),
            'compression_ratio': ratio,
            'reduction_percent': reduction_pct,
        },
        'http_no_gzip': {k: v for k, v in no_gzip.items() if k != 'sample_body'},
        'http_gzip': {k: v for k, v in with_gzip.items() if k != 'sample_body'},
        'correctness': {
            'exact_match': exact_match,
            'gzip_content_encoding': with_gzip['content_encoding'] == 'gzip',
        },
        'pagination': {
            'inventory_filter_paginated': False,
            'note': (
                'inventory_filter returns the full filtered dataset as nested arrays; '
                'no page/page_size parameters exist on this endpoint.'
            ),
        },
        'redis': {
            'note': (
                'GZipMiddleware compresses the HTTP response after cached_filter_response '
                'returns; Redis stores uncompressed Python objects.'
            ),
        },
        'recommendation': _recommendation(no_gzip, with_gzip, builder, reduction_pct),
    }

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, default=str), encoding='utf-8')

    _print_report(report)
    print(f'\nReport: {out}')
    return 0 if exact_match and with_gzip['content_encoding'] == 'gzip' else 1


def _recommendation(no_gzip: dict, with_gzip: dict, builder: dict, reduction_pct: float) -> str:
    time_delta = with_gzip['avg_ms'] - no_gzip['avg_ms']
    if reduction_pct >= 50 and time_delta < no_gzip['avg_ms'] * 0.1:
        return 'KEEP GZIP — significant byte reduction with acceptable server overhead'
    if reduction_pct >= 30:
        return (
            'KEEP GZIP for bandwidth savings; '
            f'compression adds ~{round(builder["gzip_ms"], 1)}ms CPU but saves '
            f'{reduction_pct}% bytes on the wire'
        )
    if time_delta > no_gzip['avg_ms'] * 0.2:
        return 'INVESTIGATE — gzip increases end-to-end time on this host; benefit is network-side'
    return 'KEEP GZIP — transparent to clients; monitor end-to-end latency on Railway'


def _print_report(report: dict[str, Any]) -> None:
    size = report['size']
    builder = report['builder_phases']
    ng = report['http_no_gzip']
    gz = report['http_gzip']

    print('\n=== SIZE ===')
    print(f"| Uncompressed size | {size['uncompressed_bytes']:,} bytes |")
    print(f"| Gzip size         | {size['gzip_bytes']:,} bytes |")
    print(f"| Bytes saved       | {size['bytes_saved']:,} |")
    print(f"| Compression ratio | {size['compression_ratio']}:1 |")
    print(f"| Reduction %       | {size['reduction_percent']}% |")

    print('\n=== BUILDER PHASES (single run) ===')
    print(f"  DB/query: {builder['db_ms']} ms ({builder['query_count']} queries)")
    print(f"  Serialization: {builder['serialization_ms']} ms")
    print(f"  JSON render: {builder['json_ms']} ms")
    print(f"  Gzip compress: {builder['gzip_ms']} ms")
    print(f"  Records: {builder['record_count']} in {builder['group_count']} groups")

    print('\n=== HTTP PERFORMANCE (10 runs) ===')
    print(f"| Mode | Avg | Median | P95 | Wire bytes |")
    print(f"| No gzip | {ng['avg_ms']} ms | {ng['median_ms']} ms | {ng['p95_ms']} ms | {ng['avg_wire_bytes']:,} |")
    print(f"| Gzip    | {gz['avg_ms']} ms | {gz['median_ms']} ms | {gz['p95_ms']} ms | {gz['avg_wire_bytes']:,} |")
    print(f"| Delta   | {round(gz['avg_ms'] - ng['avg_ms'], 2)} ms | | | saved {ng['avg_wire_bytes'] - gz['avg_wire_bytes']:,} |")

    print(f"\nCorrectness: exact_match={report['correctness']['exact_match']}")
    print(f"Content-Encoding gzip: {report['correctness']['gzip_content_encoding']}")
    print(f"Pagination: {report['pagination']['note']}")
    print(f"Recommendation: {report['recommendation']}")


if __name__ == '__main__':
    raise SystemExit(main())
