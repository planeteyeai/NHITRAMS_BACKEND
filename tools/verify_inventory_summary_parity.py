"""Verify corrected inventory_summary against Kothakota dashboard targets."""

from __future__ import annotations

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
from django.test import Client
from rest_framework.renderers import JSONRenderer

from inventory.serializers import InventoryFilterSerializer
from inventory.services.inventory_summary import build_inventory_summary_aggregates

RAW = {
    'chainage_start': 0,
    'chainage_end': 99999,
    'date': '2026-07-28',
    'direction': ['All'],
    'project_name': ['Kothakota to Kurnool'],
    'asset_type': ['All'],
    'page': 1,
    'page_size': 15000,
}

# Dashboard targets supplied for this exact filter.
DASHBOARD = {
    'trees': 14367,
    'delineators': 481,
    'crash_barrier': 62787,
    'service_road': 39.33,  # claimed; DB column sum is 21.334 — see report
    'toll_plaza': 1,
}


def main() -> int:
    ser = InventoryFilterSerializer(data=RAW)
    assert ser.is_valid(), ser.errors
    data = dict(ser.validated_data)

    settings.DEBUG = True
    reset_queries()
    connection.force_debug_cursor = True
    timings: dict = {}
    t0 = time.perf_counter()
    summary = build_inventory_summary_aggregates(data, timings=timings)
    wall_ms = (time.perf_counter() - t0) * 1000
    qn = len(connection.queries)
    body = JSONRenderer().render(summary)

    print('project=', RAW['project_name'][0], 'date=', RAW['date'])
    print('query_count=', qn, 'aggregate_ms=', timings.get('aggregate_ms'), 'wall_ms=', round(wall_ms, 2))
    print('response_bytes=', len(body))
    print('summary=', json.dumps(summary, indent=2))
    print('parity:')
    for key, expected in DASHBOARD.items():
        got = summary.get(key)
        if isinstance(got, (int, float)) and isinstance(expected, (int, float)):
            diff = got - expected
        else:
            diff = None
        print(f'  {key}: dashboard={expected} api={got} diff={diff}')

    # HTTP cold + hit
    from django.core.cache import cache
    from django.test import override_settings

    with override_settings(NHIT_CACHE_ENABLED=True):
        cache.clear()
        client = Client()
        t0 = time.perf_counter()
        r1 = client.post('/inventory_summary/', data=RAW, content_type='application/json')
        cold = (time.perf_counter() - t0) * 1000
        t0 = time.perf_counter()
        r2 = client.post('/inventory_summary/', data=RAW, content_type='application/json')
        hit = (time.perf_counter() - t0) * 1000
        print('http_status', r1.status_code, r2.status_code)
        print('http_cold_ms', round(cold, 2), 'http_hit_ms', round(hit, 2))
        print('bodies_equal', r1.content == r2.content)
        print('http_body', r1.content.decode()[:500])

    out = ROOT / 'tools' / 'inventory_summary_parity_results.json'
    out.write_text(
        json.dumps(
            {
                'filter': RAW,
                'summary': summary,
                'dashboard_targets': DASHBOARD,
                'query_count': qn,
                'timings': timings,
                'wall_ms': wall_ms,
                'response_bytes': len(body),
                'http_cold_ms': cold,
                'http_hit_ms': hit,
            },
            indent=2,
            default=str,
        ),
        encoding='utf-8',
    )
    print('wrote', out)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
