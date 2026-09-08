"""Phase 3 investigation: SQL dump, indexes, EXPLAIN ANALYZE, ORM timing split.

Read-only diagnostics — does not modify production schema.
"""

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

from django.db import connection
from inventory.serializers import InventoryFilterSerializer
from inventory.services.inventory_filter_queryset import build_inventory_filter_queryset
from inventory.services.inventory_filter_response import (
    INVENTORY_FILTER_ONLY_FIELDS,
    build_inventory_filter_groups,
    serialize_inventory_wide_row,
    group_serialized_rows,
)


def _log(msg: str) -> None:
    print(msg, flush=True)


def _payload(page_size: int) -> dict:
    raw = {
        'chainage_start': 0,
        'chainage_end': 99999,
        'date': '2026-07-28',
        'direction': ['All'],
        'project_name': ['Kothakota to Kurnool'],
        'asset_type': ['All'],
        'page': 1,
        'page_size': page_size,
    }
    ser = InventoryFilterSerializer(data=raw)
    assert ser.is_valid(), ser.errors
    return dict(ser.validated_data)


def main() -> int:
    data = _payload(5000)
    qs = build_inventory_filter_queryset(data)
    ordered = qs.only(*INVENTORY_FILTER_ONLY_FIELDS).order_by('-inventory_id')
    page_qs = ordered[0:5000]
    sql, params = page_qs.query.sql_with_params()

    _log('=== Django SQL (page_size=5000) ===')
    _log(sql)
    _log(f'params={params!r}')

    with connection.cursor() as cur:
        cur.execute(
            """
            SELECT indexname, indexdef
            FROM pg_indexes
            WHERE tablename = 'inventory_wide'
            ORDER BY indexname
            """
        )
        indexes = cur.fetchall()
        _log('\n=== Existing indexes on inventory_wide ===')
        for name, definition in indexes:
            _log(f'{name}: {definition}')

        # Approximate table size
        cur.execute(
            """
            SELECT pg_size_pretty(pg_total_relation_size('inventory_wide')),
                   pg_size_pretty(pg_relation_size('inventory_wide')),
                   (SELECT reltuples::bigint FROM pg_class WHERE relname = 'inventory_wide')
            """
        )
        _log(f'table_size/total, heap, est_rows: {cur.fetchone()}')

    for page_size in (5000, 15000, 25000, 50000):
        data = _payload(page_size)
        qs = build_inventory_filter_queryset(data)
        ordered = qs.only(*INVENTORY_FILTER_ONLY_FIELDS).order_by('-inventory_id')
        page_qs = ordered[0:page_size]
        sql, params = page_qs.query.sql_with_params()
        explain_sql = f'EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT) {sql}'
        _log(f'\n=== EXPLAIN ANALYZE page_size={page_size} ===')
        with connection.cursor() as cur:
            cur.execute(explain_sql, params)
            plan = '\n'.join(r[0] for r in cur.fetchall())
            _log(plan)

    # Split SELECT wall into: cursor fetch vs ORM construct vs serialize/group
    _log('\n=== Materialization split (page_size=15000) ===')
    data = _payload(15000)
    qs = build_inventory_filter_queryset(data)
    ordered = qs.only(*INVENTORY_FILTER_ONLY_FIELDS).order_by('-inventory_id')[0:15000]

    # A) Raw cursor fetch of same SQL (no ORM)
    sql, params = ordered.query.sql_with_params()
    t0 = time.perf_counter()
    with connection.cursor() as cur:
        cur.execute(sql, params)
        raw_rows = cur.fetchall()
        colnames = [c[0] for c in cur.description]
    raw_ms = (time.perf_counter() - t0) * 1000
    _log(f'raw_cursor_fetch_ms={raw_ms:.1f} rows={len(raw_rows)} cols={len(colnames)}')

    # B) Django ORM list()
    t0 = time.perf_counter()
    items = list(ordered)
    orm_ms = (time.perf_counter() - t0) * 1000
    _log(f'orm_list_ms={orm_ms:.1f} rows={len(items)}')

    # C) values() list
    t0 = time.perf_counter()
    value_rows = list(
        qs.order_by('-inventory_id').values(*INVENTORY_FILTER_ONLY_FIELDS)[0:15000]
    )
    values_ms = (time.perf_counter() - t0) * 1000
    _log(f'values_list_ms={values_ms:.1f} rows={len(value_rows)}')

    # D) serialize + group from ORM
    t0 = time.perf_counter()
    rows = [serialize_inventory_wide_row(item) for item in items]
    groups = group_serialized_rows(rows)
    ser_ms = (time.perf_counter() - t0) * 1000
    _log(f'serialize_group_from_orm_ms={ser_ms:.1f} groups={len(groups)}')

    # Approximate ORM overhead
    _log(f'orm_overhead_vs_raw_ms≈{orm_ms - raw_ms:.1f}')
    _log(f'values_vs_orm_delta_ms≈{orm_ms - values_ms:.1f}')

    out = {
        'sql_5000': sql,
        'indexes': indexes,
        'raw_ms': raw_ms,
        'orm_ms': orm_ms,
        'values_ms': values_ms,
        'ser_ms': ser_ms,
    }
    Path('tools/inventory_filter_phase3_diag.json').write_text(
        json.dumps({k: (str(v) if k == 'indexes' else v) for k, v in out.items()}, indent=2, default=str),
        encoding='utf-8',
    )
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
