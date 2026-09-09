"""Benchmark map branch of POST /inventory_filter/ (viewport+zoom) on Kothakota."""

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
from rest_framework.renderers import JSONRenderer

from inventory.serializers import InventoryFilterSerializer
from inventory.services.inventory_filter_map import build_inventory_filter_map_payload
from nhit.caching.inventory_filter_map import get_cached_inventory_filter_map

PROJECT = 'Kothakota to Kurnool'
DATE = '2026-07-28'

CASES = {
    'A_tight': {
        'viewport': {
            'min_lat': 16.36117622,
            'min_lng': 77.93994608,
            'max_lat': 16.36517622,
            'max_lng': 77.94394608,
        },
        'zoom': 15,
    },
    'B_medium': {
        'viewport': {
            'min_lat': 15.95,
            'min_lng': 77.95,
            'max_lat': 16.05,
            'max_lng': 78.00,
        },
        'zoom': 12,
    },
    'C_full_project': {
        'viewport': {
            'min_lat': 15.80,
            'min_lng': 77.88,
            'max_lat': 16.37,
            'max_lng': 78.04,
        },
        'zoom': 10,
    },
}


def _payload(viewport, zoom):
    return {
        'chainage_start': 0,
        'chainage_end': 99999,
        'date': DATE,
        'direction': ['All'],
        'project_name': [PROJECT],
        'asset_type': ['All'],
        'viewport': viewport,
        'zoom': zoom,
        # Intentionally include page fields — map path must ignore them.
        'page': 1,
        'page_size': 5000,
    }


def _count_viewport_sql(viewport):
    with connection.cursor() as cur:
        cur.execute(
            """
            SELECT COUNT(*)
            FROM inventory_wide
            WHERE "Project Name" = %s AND "Date" = %s
              AND "Chainage start" >= 0 AND "Chainage end" <= 99999
              AND "Latitude" IS NOT NULL AND "Longitude" IS NOT NULL
              AND NOT ("Latitude" = 0 AND "Longitude" = 0)
              AND "Latitude" BETWEEN %s AND %s
              AND "Longitude" BETWEEN %s AND %s
            """,
            [
                PROJECT,
                DATE,
                viewport['min_lat'],
                viewport['max_lat'],
                viewport['min_lng'],
                viewport['max_lng'],
            ],
        )
        return int(cur.fetchone()[0])


def _run_case(name, spec):
    body = _payload(spec['viewport'], spec['zoom'])
    ser = InventoryFilterSerializer(data=body)
    assert ser.is_valid(), ser.errors
    data = ser.validated_data
    truth_count = _count_viewport_sql(spec['viewport'])

    settings.DEBUG = True
    reset_queries()
    timings = {}
    started = time.perf_counter()
    payload, meta = get_cached_inventory_filter_map(
        payload=data,
        builder=lambda: build_inventory_filter_map_payload(data, timings=timings),
    )
    total_ms = (time.perf_counter() - started) * 1000
    query_count = len(connection.queries)
    settings.DEBUG = False

    raw = JSONRenderer().render(payload)
    cluster_sum = None
    if payload.get('mode') == 'clusters':
        cluster_sum = sum(c['count'] for c in payload['clusters'])

    return {
        'case': name,
        'endpoint': '/inventory_filter/',
        'cache_hit': bool(meta.get('cache_hit')),
        'truth_viewport_count': truth_count,
        'mode': payload.get('mode'),
        'marker_count': len(payload.get('markers') or []),
        'cluster_count': len(payload.get('clusters') or []),
        'cluster_sum': cluster_sum,
        'cluster_sum_matches_truth': (
            cluster_sum == truth_count if cluster_sum is not None else None
        ),
        'total_ms': round(total_ms, 2),
        'timings': timings,
        'query_count': query_count,
        'response_bytes': len(raw),
        'build_ms': meta.get('build_ms'),
    }


def main() -> int:
    out = {
        'project': PROJECT,
        'date': DATE,
        'public_endpoint': 'POST /inventory_filter/',
        'map_detection': 'viewport + zoom',
        'cache_enabled': bool(getattr(settings, 'NHIT_CACHE_ENABLED', False)),
        'cases': [],
    }
    for name, spec in CASES.items():
        out['cases'].append(_run_case(name, spec))  # MISS
        out['cases'].append(_run_case(name, spec))  # HIT
    path = ROOT / 'tools' / 'benchmark_inventory_filter_map_results.json'
    path.write_text(json.dumps(out, indent=2, default=str), encoding='utf-8')
    print(json.dumps(out, indent=2, default=str))
    print('wrote', path)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
