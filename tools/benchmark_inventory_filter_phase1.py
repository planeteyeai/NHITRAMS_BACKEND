"""Phase 1 inventory_filter cache benchmark (analysis helper; not a public API)."""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'nhit.settings')

import django

django.setup()

from django.conf import settings
from django.db import connection, reset_queries
from django.test import Client


def _payload(**overrides):
    body = {
        'chainage_start': 0,
        'chainage_end': 99999,
        'date': '2026-07-28',
        'direction': ['All'],
        'project_name': ['Kothakota to Kurnool'],
        'asset_type': ['All'],
        'page': 1,
        'page_size': 5000,
    }
    body.update(overrides)
    return body


def _run(label: str, client: Client, body: dict) -> dict:
    reset_queries()
    # Force query logging even when DEBUG is false for this process.
    settings.DEBUG = True
    t0 = time.perf_counter()
    response = client.post(
        '/inventory_filter/',
        data=json.dumps(body),
        content_type='application/json',
        HTTP_ACCEPT_ENCODING='gzip',
    )
    elapsed = (time.perf_counter() - t0) * 1000
    queries = list(connection.queries)
    count_queries = sum(1 for q in queries if 'COUNT(' in q['sql'].upper() or 'COUNT(*)' in q['sql'].upper())
    select_queries = len(queries) - count_queries
    return {
        'label': label,
        'status': response.status_code,
        'total_ms': round(elapsed, 1),
        'db_queries': len(queries),
        'count_queries': count_queries,
        'select_queries': select_queries,
        'row_count': response.get('X-Inventory-Row-Count'),
        'page': response.get('X-Inventory-Page'),
        'wire_bytes': len(response.content),
        'encoding': response.get('Content-Encoding'),
    }


def main() -> None:
    client = Client()
    # Warm nothing — clear is environment-dependent; sequential scenarios show HIT/MISS.
    results = []
    results.append(_run('page1_first', client, _payload(page=1)))
    results.append(_run('page1_second', client, _payload(page=1)))
    results.append(_run('page2', client, _payload(page=2)))
    results.append(_run('page1_page_size_7500', client, _payload(page=1, page_size=7500)))
    results.append(_run(
        'different_filter',
        client,
        _payload(project_name=['Agra Bypass Road'], date='2026-04-20'),
    ))

    print('| Scenario | Status | total_ms | DB queries | COUNT queries | SELECT-ish | Row-Count | wire_bytes | encoding |')
    print('|---|---:|---:|---:|---:|---:|---:|---:|---|')
    for row in results:
        print(
            f"| {row['label']} | {row['status']} | {row['total_ms']} | {row['db_queries']} | "
            f"{row['count_queries']} | {row['select_queries']} | {row['row_count']} | "
            f"{row['wire_bytes']} | {row['encoding']} |"
        )


if __name__ == '__main__':
    main()
