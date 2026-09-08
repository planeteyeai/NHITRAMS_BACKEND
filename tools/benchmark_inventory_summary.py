"""Benchmark POST /inventory_summary/ vs paginated inventory_filter transfer.

Usage:
    set PYTHONUNBUFFERED=1
    python -u tools/benchmark_inventory_summary.py
"""

from __future__ import annotations

import gzip
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'nhit.settings')
os.environ.setdefault('PYTHONUNBUFFERED', '1')

import django

django.setup()

from django.conf import settings
from django.db import connection, reset_queries
from django.test import Client, override_settings
from rest_framework.renderers import JSONRenderer

from inventory.serializers import InventoryFilterSerializer
from inventory.services.inventory_filter_queryset import build_inventory_filter_queryset
from inventory.services.inventory_summary import build_inventory_summary_aggregates

OUT_JSON = ROOT / 'tools' / 'benchmark_inventory_summary_results.json'
OUT_MD = ROOT / 'tools' / 'benchmark_inventory_summary_report.md'
PROJECT = 'Kothakota to Kurnool'
SURVEY_DATE = '2026-07-28'


def _log(msg: str) -> None:
    print(msg, flush=True)


def _rss_mb():
    try:
        import psutil

        return round(psutil.Process(os.getpid()).memory_info().rss / (1024 * 1024), 1)
    except Exception:
        return None


def _payload(**overrides):
    raw = {
        'chainage_start': 0,
        'chainage_end': 99999,
        'date': SURVEY_DATE,
        'direction': ['All'],
        'project_name': [PROJECT],
        'asset_type': ['All'],
        'page': 1,
        'page_size': 15000,
    }
    raw.update(overrides)
    ser = InventoryFilterSerializer(data=raw)
    assert ser.is_valid(), ser.errors
    return dict(ser.validated_data), raw


def main() -> int:
    report: dict = {'project': PROJECT, 'date': SURVEY_DATE}
    data, raw = _payload()

    # EXPLAIN ANALYZE of aggregate SQL
    qs = build_inventory_filter_queryset(data)
    # Force compile via a throwaway aggregate path used by the service
    timings = {}
    _log('=== cold aggregate (service) ===')
    rss0 = _rss_mb()
    t0 = time.perf_counter()
    summary = build_inventory_summary_aggregates(data, timings=timings)
    cold_ms = (time.perf_counter() - t0) * 1000
    rss1 = _rss_mb()
    body = JSONRenderer().render(summary)
    gz = gzip.compress(body, compresslevel=6)
    report['cold_service'] = {
        'total_ms': round(cold_ms, 2),
        'aggregate_ms': timings.get('aggregate_ms'),
        'response_bytes': len(body),
        'gzip_bytes': len(gz),
        'rss_before_mb': rss0,
        'rss_after_mb': rss1,
        'summary_sample_keys': list(summary.keys())[:5],
        'summary': summary,
    }
    _log(json.dumps(report['cold_service'], indent=2, default=str))

    # SQL + EXPLAIN
    from django.db.models import Case, F, FloatField, Sum, Value, When
    from django.db.models.functions import Coalesce
    from inventory.services.inventory_filter_response import ASSET_TYPE_FIELD_MAP

    agg = {}
    for label, field in ASSET_TYPE_FIELD_MAP.items():
        agg[field] = Coalesce(
            Sum(
                Case(
                    When(asset_type=label, then=F(field)),
                    default=Value(0.0),
                    output_field=FloatField(),
                )
            ),
            Value(0.0),
            output_field=FloatField(),
        )
    # Django doesn't expose aggregate SQL easily; use connection queries under DEBUG
    settings.DEBUG = True
    reset_queries()
    connection.force_debug_cursor = True
    build_inventory_summary_aggregates(data)
    queries = list(connection.queries)
    report['query_count_cold_debug'] = len(queries)
    if queries:
        sql = queries[-1]['sql']
        report['aggregate_sql_preview'] = sql[:2000]
        _log(f'query_count={len(queries)}')
        with connection.cursor() as cur:
            cur.execute(f'EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT) {sql}')
            plan = '\n'.join(r[0] for r in cur.fetchall())
        report['explain_analyze'] = plan
        _log(plan[:3000])

    # HTTP cold + HIT via test client (LocMem if redis off — still measures builder path)
    client = Client()
    with override_settings(NHIT_CACHE_ENABLED=True):
        from django.core.cache import cache

        cache.clear()
        t0 = time.perf_counter()
        r1 = client.post('/inventory_summary/', data=raw, content_type='application/json')
        http_cold = (time.perf_counter() - t0) * 1000
        t0 = time.perf_counter()
        r2 = client.post('/inventory_summary/', data=raw, content_type='application/json')
        http_hit = (time.perf_counter() - t0) * 1000
        # different page should still hit
        raw_page = {**raw, 'page': 3, 'page_size': 50000}
        t0 = time.perf_counter()
        r3 = client.post('/inventory_summary/', data=raw_page, content_type='application/json')
        http_page_variant = (time.perf_counter() - t0) * 1000

    report['http'] = {
        'cold_ms': round(http_cold, 2),
        'hit_ms': round(http_hit, 2),
        'page_variant_ms': round(http_page_variant, 2),
        'cold_status': r1.status_code,
        'hit_status': r2.status_code,
        'bodies_equal': r1.content == r2.content == r3.content,
        'response_bytes': len(r1.content),
    }
    _log(json.dumps(report['http'], indent=2))

    # Compare vs one inventory_filter page size (transfer cost proxy)
    t0 = time.perf_counter()
    rf = client.post('/inventory_filter/', data=raw, content_type='application/json')
    filter_ms = (time.perf_counter() - t0) * 1000
    report['inventory_filter_page_proxy'] = {
        'status': rf.status_code,
        'ms': round(filter_ms, 2),
        'response_bytes': len(rf.content),
        'gzip_bytes': len(gzip.compress(rf.content, compresslevel=6)),
    }
    _log(json.dumps(report['inventory_filter_page_proxy'], indent=2))

    OUT_JSON.write_text(json.dumps(report, indent=2, default=str), encoding='utf-8')
    md = [
        '# Inventory Summary Benchmark',
        '',
        f'Project: `{PROJECT}` date `{SURVEY_DATE}`',
        '',
        f"- Cold aggregate_ms: {report['cold_service'].get('aggregate_ms')}",
        f"- Cold total_ms: {report['cold_service']['total_ms']}",
        f"- Summary JSON bytes: {report['cold_service']['response_bytes']}",
        f"- Summary gzip bytes: {report['cold_service']['gzip_bytes']}",
        f"- HTTP cold/hit ms: {report['http']['cold_ms']} / {report['http']['hit_ms']}",
        f"- inventory_filter page bytes: {report['inventory_filter_page_proxy']['response_bytes']}",
        '',
        '## EXPLAIN ANALYZE',
        '',
        '```',
        report.get('explain_analyze', 'n/a')[:5000],
        '```',
        '',
    ]
    OUT_MD.write_text('\n'.join(md), encoding='utf-8')
    _log(f'Wrote {OUT_JSON} and {OUT_MD}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
