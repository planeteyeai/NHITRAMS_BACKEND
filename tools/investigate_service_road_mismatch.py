"""INVESTIGATION ONLY — Service Road mismatch: inventory_filter vs inventory_summary.

Does not modify production code.
Project/date: Kothakota to Kurnool / 2026-07-28
"""

from __future__ import annotations

import json
import os
import sys
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
    ASSET_TYPE_FIELD_MAP,
    serialize_inventory_wide_row,
    build_inventory_filter_groups,
)
from inventory.services.inventory_summary import build_inventory_summary_aggregates

PROJECT = 'Kothakota to Kurnool'
DATE = '2026-07-28'
RAW = {
    'chainage_start': 0,
    'chainage_end': 99999,
    'date': DATE,
    'direction': ['All'],
    'project_name': [PROJECT],
    'asset_type': ['All'],
    'page': 1,
    'page_size': 15000,
}


def _log(msg: str) -> None:
    print(msg, flush=True)


def main() -> int:
    ser = InventoryFilterSerializer(data=RAW)
    assert ser.is_valid(), ser.errors
    data = dict(ser.validated_data)
    qs = build_inventory_filter_queryset(data)
    total = qs.count()
    _log(f'filtered_rows={total}')

    summary = build_inventory_summary_aggregates(data)
    _log(f'summary.service_road={summary.get("service_road")!r}')

    report: dict = {
        'project': PROJECT,
        'date': DATE,
        'filtered_rows': total,
        'summary_service_road': summary.get('service_road'),
    }

    with connection.cursor() as cur:
        # A) Unrestricted column sum (current summary rule)
        cur.execute(
            '''
            SELECT
              COALESCE(SUM("Service Road"), 0) AS sum_all,
              COALESCE(SUM("Service Road") FILTER (WHERE "Asset type" = 'Service Road'), 0) AS sum_typed,
              COALESCE(SUM("Service Road") FILTER (WHERE COALESCE("Service Road",0) <> 0), 0) AS sum_nonzero,
              COUNT(*) FILTER (WHERE "Asset type" = 'Service Road') AS n_typed,
              COUNT(*) FILTER (WHERE COALESCE("Service Road",0) <> 0) AS n_nonzero_col,
              COUNT(*) FILTER (WHERE "Asset type" = 'Service Road' AND COALESCE("Service Road",0) <> 0) AS n_typed_nonzero,
              COUNT(*) FILTER (WHERE "Asset type" = 'Service Road' AND COALESCE("Service Road",0) = 0) AS n_typed_zero
            FROM inventory_wide
            WHERE "Project Name" = %s AND "Date" = %s
              AND "Chainage start" >= 0 AND "Chainage end" <= 99999
            ''',
            [PROJECT, DATE],
        )
        cols = [c[0] for c in cur.description]
        report['sql_column_sums'] = dict(zip(cols, cur.fetchone()))
        _log(f"sql_column_sums={report['sql_column_sums']}")

        # B) Per-row values that inventory_filter would expose (quantity present iff != 0)
        cur.execute(
            '''
            SELECT inventory_id, "Asset type", "Service Road",
                   "Direction", "Chainage start", "Chainage end",
                   "Latitude", "Longitude"
            FROM inventory_wide
            WHERE "Project Name" = %s AND "Date" = %s
              AND "Chainage start" >= 0 AND "Chainage end" <= 99999
              AND ("Asset type" = 'Service Road' OR COALESCE("Service Road",0) <> 0)
            ORDER BY "Chainage start", "Direction", inventory_id
            ''',
            [PROJECT, DATE],
        )
        rows = cur.fetchall()
        report['service_road_rows'] = [
            {
                'inventory_id': r[0],
                'asset_type': r[1],
                'service_road': r[2],
                'direction': r[3],
                'chainage_start': r[4],
                'chainage_end': r[5],
                'latitude': r[6],
                'longitude': r[7],
                'filter_would_emit_quantity': (
                    r[1] == 'Service Road' and r[2] is not None and float(r[2]) != 0.0
                ),
            }
            for r in rows
        ]

    # C) inventory_filter serialize-equivalent sum (SQL — no 230k row transfer)
    # serialize emits service_road only when:
    #   asset_type exactly 'Service Road' AND quantity != 0 (after _quantity_value)
    with connection.cursor() as cur:
        cur.execute(
            '''
            SELECT COALESCE(SUM("Service Road"), 0)
            FROM inventory_wide
            WHERE "Project Name" = %s AND "Date" = %s
              AND "Chainage start" >= 0 AND "Chainage end" <= 99999
              AND "Asset type" = 'Service Road'
              AND COALESCE("Service Road", 0) <> 0
            ''',
            [PROJECT, DATE],
        )
        filter_equiv_sum = float(cur.fetchone()[0])
        cur.execute(
            '''
            SELECT COUNT(*)
            FROM inventory_wide
            WHERE "Project Name" = %s AND "Date" = %s
              AND "Chainage start" >= 0 AND "Chainage end" <= 99999
              AND "Asset type" = 'Service Road'
              AND COALESCE("Service Road", 0) <> 0
            ''',
            [PROJECT, DATE],
        )
        filter_equiv_n = int(cur.fetchone()[0])

    report['inventory_filter_serialize_equivalent'] = {
        'rule': (
            "SUM(\"Service Road\") WHERE asset_type = 'Service Road' "
            "AND Service Road <> 0  — same rows serialize_inventory_wide_row "
            "would emit a service_road key for"
        ),
        'sum': filter_equiv_sum,
        'row_emissions': filter_equiv_n,
    }
    _log(f"filter_serialize_equiv={report['inventory_filter_serialize_equivalent']}")

    # D) Page-1 spot check via real inventory_filter builder (ordering -inventory_id)
    groups_p1 = build_inventory_filter_groups(qs, offset=0, limit=15000)
    p1_sum = 0.0
    p1_n = 0
    p1_sr_rows = []
    for group in groups_p1:
        for row in group:
            if 'service_road' in row:
                p1_sum += float(row['service_road'])
                p1_n += 1
                p1_sr_rows.append(
                    {
                        'asset_type': row.get('asset_type'),
                        'service_road': row.get('service_road'),
                        'direction': row.get('direction'),
                        'chainage_start': row.get('chainage_start'),
                        'chainage_end': row.get('chainage_end'),
                    }
                )
    report['inventory_filter_page1_only'] = {
        'page_size': 15000,
        'emissions': p1_n,
        'sum': p1_sum,
        'rows': p1_sr_rows,
        'note': (
            'Page 1 is ordered by -inventory_id. Service Road rows may be absent '
            'from page 1 even though they exist in the full filtered set.'
        ),
    }
    _log(f"page1_sum={p1_sum} emissions={p1_n}")

    # E) Where are Service Road inventory_ids relative to page-1 window?
    with connection.cursor() as cur:
        cur.execute(
            '''
            WITH ordered AS (
              SELECT inventory_id, "Asset type", "Service Road",
                     ROW_NUMBER() OVER (ORDER BY inventory_id DESC) AS rn
              FROM inventory_wide
              WHERE "Project Name" = %s AND "Date" = %s
                AND "Chainage start" >= 0 AND "Chainage end" <= 99999
            )
            SELECT inventory_id, "Asset type", "Service Road", rn,
                   CASE WHEN rn <= 15000 THEN 'page1' ELSE 'later_page' END AS page_bucket
            FROM ordered
            WHERE "Asset type" = 'Service Road' OR COALESCE("Service Road",0) <> 0
            ORDER BY rn
            ''',
            [PROJECT, DATE],
        )
        page_pos = []
        for r in cur.fetchall():
            page_pos.append(
                {
                    'inventory_id': r[0],
                    'asset_type': r[1],
                    'service_road': r[2],
                    'row_number_desc_id': r[3],
                    'page_bucket': r[4],
                }
            )
        report['service_road_pagination_position'] = page_pos
        in_p1 = sum(1 for r in page_pos if r['page_bucket'] == 'page1')
        _log(f'service_road_rows_in_page1_window={in_p1} of {len(page_pos)}')

    # E) Alternate hypotheses that could yield ~39.33
    with connection.cursor() as cur:
        cur.execute(
            '''
            SELECT
              COALESCE(SUM("Service Road"),0) AS sum_sr,
              COALESCE(SUM(ABS("Chainage end" - "Chainage start")),0) AS sum_dchain,
              COALESCE(SUM("Service Road") FILTER (WHERE "Direction" = 'Increasing'),0) AS sum_inc,
              COALESCE(SUM("Service Road") FILTER (WHERE "Direction" = 'Decreasing'),0) AS sum_dec,
              COALESCE(SUM("Adjacent Road"),0) AS sum_adj,
              COALESCE(SUM("Slip Roads"),0) AS sum_slip,
              COALESCE(SUM("Footpath"),0) AS sum_foot,
              COALESCE(SUM("Service Road"),0) + COALESCE(SUM("Adjacent Road"),0) AS sr_plus_adj,
              COALESCE(SUM("Service Road"),0) + COALESCE(SUM("Slip Roads"),0) AS sr_plus_slip
            FROM inventory_wide
            WHERE "Project Name" = %s AND "Date" = %s
              AND "Chainage start" >= 0 AND "Chainage end" <= 99999
            ''',
            [PROJECT, DATE],
        )
        cols = [c[0] for c in cur.description]
        report['hypotheses'] = dict(zip(cols, (float(x) for x in cur.fetchone())))
        _log(f"hypotheses={report['hypotheses']}")

        # Any other date for same project closer to 39.33?
        cur.execute(
            '''
            SELECT "Date"::text, ROUND(SUM("Service Road")::numeric, 3) AS s, COUNT(*) AS n
            FROM inventory_wide
            WHERE "Project Name" = %s
            GROUP BY 1
            ORDER BY ABS(SUM("Service Road") - 39.33)
            LIMIT 10
            ''',
            [PROJECT],
        )
        report['other_dates_nearest_39_33'] = [
            {'date': r[0], 'sum_service_road': float(r[1]), 'rows': r[2]} for r in cur.fetchall()
        ]
        _log(f"nearest_dates={report['other_dates_nearest_39_33']}")

    # F) Conclusion fields
    filter_full = report['inventory_filter_serialize_equivalent']['sum']
    summary_val = float(summary.get('service_road') or 0)
    report['comparison'] = {
        'summary_api': summary_val,
        'inventory_filter_serialize_equivalent_sum': filter_full,
        'inventory_filter_sum_page1': p1_sum,
        'sql_sum_service_road_column': float(report['sql_column_sums']['sum_all']),
        'summary_equals_filter_serialize_equiv': abs(summary_val - filter_full) < 1e-9,
        'summary_equals_sql_column_sum': abs(summary_val - float(report['sql_column_sums']['sum_all'])) < 1e-9,
        'claimed_dashboard_39_33_reproduced': any(
            abs(x - 39.33) < 0.01
            for x in (filter_full, p1_sum, float(report['sql_column_sums']['sum_all']))
        ),
    }

    out = ROOT / 'tools' / 'investigate_service_road_mismatch_results.json'
    out.write_text(json.dumps(report, indent=2, default=str), encoding='utf-8')
    _log(f'wrote {out}')
    _log('COMPARISON=' + json.dumps(report['comparison'], indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
