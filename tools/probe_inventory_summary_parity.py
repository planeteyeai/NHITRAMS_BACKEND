"""Read-only probe: why inventory_summary mismatches dashboard totals."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'nhit.settings')

import django

django.setup()

from django.db import connection
from inventory.serializers import InventoryFilterSerializer
from inventory.services.inventory_filter_queryset import build_inventory_filter_queryset

PROJECT = 'Kothakota to Kurnool'
DATE = '2026-07-28'


def main() -> int:
    raw = {
        'chainage_start': 0,
        'chainage_end': 99999,
        'date': DATE,
        'direction': ['All'],
        'project_name': [PROJECT],
        'asset_type': ['All'],
        'page': 1,
        'page_size': 15000,
    }
    ser = InventoryFilterSerializer(data=raw)
    assert ser.is_valid(), ser.errors
    data = dict(ser.validated_data)
    qs = build_inventory_filter_queryset(data)
    print('filtered_count', qs.count())

    with connection.cursor() as cur:
        cur.execute(
            '''
            SELECT "Asset type", COUNT(*) AS n,
                   COALESCE(SUM("Trees"),0) AS sum_trees,
                   COALESCE(SUM("Delineators"),0) AS sum_del,
                   COALESCE(SUM("Crash Barrier"),0) AS sum_cb,
                   COALESCE(SUM("Service Road"),0) AS sum_sr,
                   COALESCE(SUM("Toll Plaza"),0) AS sum_tp,
                   COALESCE(SUM("Sign Boards"),0) AS sum_sb
            FROM inventory_wide
            WHERE "Project Name" = %s AND "Date" = %s
              AND "Chainage start" >= 0 AND "Chainage end" <= 99999
            GROUP BY 1
            ORDER BY n DESC
            ''',
            [PROJECT, DATE],
        )
        rows = cur.fetchall()
        print('=== by asset_type ===')
        for r in rows:
            print(r)

        cur.execute(
            '''
            SELECT
              COALESCE(SUM("Trees"),0) AS sum_trees_all_cols,
              COALESCE(SUM(CASE WHEN "Asset type" = 'Trees' THEN "Trees" ELSE 0 END),0) AS sum_trees_typed,
              COALESCE(SUM(CASE WHEN lower("Asset type") LIKE '%%tree%%' THEN "Trees" ELSE 0 END),0) AS sum_trees_ilike_col,
              COALESCE(SUM(CASE WHEN lower("Asset type") LIKE '%%tree%%' THEN 1 ELSE 0 END),0) AS cnt_tree_rows,
              COUNT(*) FILTER (WHERE "Trees" > 0) AS cnt_trees_col_gt0,
              COALESCE(SUM("Trees") FILTER (WHERE "Trees" > 0),0) AS sum_trees_gt0,
              COALESCE(SUM("Delineators"),0) AS sum_del_all,
              COALESCE(SUM(CASE WHEN "Asset type" = 'Delineators' THEN "Delineators" ELSE 0 END),0) AS sum_del_typed,
              COUNT(*) FILTER (WHERE "Delineators" > 0) AS cnt_del_gt0,
              COALESCE(SUM("Crash Barrier"),0) AS sum_cb_all,
              COALESCE(SUM(CASE WHEN "Asset type" = 'Crash Barrier' THEN "Crash Barrier" ELSE 0 END),0) AS sum_cb_typed,
              COALESCE(SUM("Service Road"),0) AS sum_sr_all,
              COALESCE(SUM(CASE WHEN "Asset type" = 'Service Road' THEN "Service Road" ELSE 0 END),0) AS sum_sr_typed,
              COALESCE(SUM("Toll Plaza"),0) AS sum_tp_all,
              COALESCE(SUM(CASE WHEN "Asset type" = 'Toll Plaza' THEN "Toll Plaza" ELSE 0 END),0) AS sum_tp_typed,
              COUNT(*) FILTER (WHERE "Asset type" = 'Toll Plaza') AS cnt_tp_rows,
              COUNT(*) FILTER (WHERE "Toll Plaza" > 0) AS cnt_tp_col_gt0
            FROM inventory_wide
            WHERE "Project Name" = %s AND "Date" = %s
              AND "Chainage start" >= 0 AND "Chainage end" <= 99999
            ''',
            [PROJECT, DATE],
        )
        cols = [c[0] for c in cur.description]
        print('=== candidates ===')
        print(json.dumps(dict(zip(cols, cur.fetchone())), indent=2, default=str))

        cur.execute(
            '''
            SELECT "Asset type", "Sub Asset Type", COUNT(*),
                   COALESCE(SUM("Delineators"),0),
                   COALESCE(SUM("Sign Boards"),0),
                   COALESCE(SUM("Guard Post"),0),
                   COALESCE(SUM("Solar Blinker"),0)
            FROM inventory_wide
            WHERE "Project Name" = %s AND "Date" = %s
              AND "Chainage start" >= 0 AND "Chainage end" <= 99999
              AND (
                lower(COALESCE("Asset type",'')) LIKE '%%delin%%'
                OR lower(COALESCE("Sub Asset Type",'')) LIKE '%%delin%%'
                OR lower(COALESCE("Asset type",'')) LIKE '%%hazard%%'
                OR lower(COALESCE("Sub Asset Type",'')) LIKE '%%hazard%%'
                OR "Delineators" > 0
              )
            GROUP BY 1, 2
            ORDER BY 3 DESC
            LIMIT 40
            ''',
            [PROJECT, DATE],
        )
        print('=== delineator/hazard related ===')
        for r in cur.fetchall():
            print(r)

        cur.execute(
            '''
            SELECT inventory_id, "Asset type", "Sub Asset Type", "Toll Plaza",
                   "Direction", "Chainage start", "Chainage end"
            FROM inventory_wide
            WHERE "Project Name" = %s AND "Date" = %s
              AND "Chainage start" >= 0 AND "Chainage end" <= 99999
              AND ("Asset type" = 'Toll Plaza' OR "Toll Plaza" > 0)
            ORDER BY inventory_id
            ''',
            [PROJECT, DATE],
        )
        print('=== toll plaza rows ===')
        for r in cur.fetchall():
            print(r)

        # Trees: compare count_val on inventory vs wide
        cur.execute(
            '''
            SELECT "Asset type",
                   COUNT(*) AS n,
                   COALESCE(SUM("Trees"),0) AS sum_trees,
                   MIN("Trees") AS min_t,
                   MAX("Trees") AS max_t
            FROM inventory_wide
            WHERE "Project Name" = %s AND "Date" = %s
              AND "Chainage start" >= 0 AND "Chainage end" <= 99999
              AND (lower(COALESCE("Asset type",'')) LIKE '%%tree%%' OR "Trees" <> 0)
            GROUP BY 1
            ORDER BY n DESC
            ''',
            [PROJECT, DATE],
        )
        print('=== tree-related ===')
        for r in cur.fetchall():
            print(r)

        # Service road: values distribution
        cur.execute(
            '''
            SELECT "Asset type", COUNT(*),
                   COALESCE(SUM("Service Road"),0),
                   COALESCE(SUM("Chainage end" - "Chainage start"),0) AS sum_chainage_len
            FROM inventory_wide
            WHERE "Project Name" = %s AND "Date" = %s
              AND "Chainage start" >= 0 AND "Chainage end" <= 99999
              AND (lower(COALESCE("Asset type",'')) LIKE '%%service%%' OR "Service Road" <> 0)
            GROUP BY 1
            ''',
            [PROJECT, DATE],
        )
        print('=== service road related ===')
        for r in cur.fetchall():
            print(r)

        # Crash barrier: typed vs all + chainage length proxy
        cur.execute(
            '''
            SELECT
              COALESCE(SUM("Crash Barrier"),0) AS sum_cb_all,
              COALESCE(SUM(CASE WHEN "Asset type"='Crash Barrier' THEN "Crash Barrier" ELSE 0 END),0) AS sum_cb_typed,
              COALESCE(SUM(CASE WHEN "Asset type"='Crash Barrier' THEN ("Chainage end"-"Chainage start") ELSE 0 END),0) AS sum_cb_chainage,
              COUNT(*) FILTER (WHERE "Asset type"='Crash Barrier') AS n_cb,
              COUNT(*) FILTER (WHERE "Crash Barrier" > 0) AS n_cb_gt0
            FROM inventory_wide
            WHERE "Project Name" = %s AND "Date" = %s
              AND "Chainage start" >= 0 AND "Chainage end" <= 99999
            ''',
            [PROJECT, DATE],
        )
        print('=== crash barrier candidates ===')
        print(cur.fetchone())

        # Service Road detail + abs + rounded
        cur.execute(
            '''
            SELECT inventory_id, "Asset type", "Service Road",
                   "Chainage start", "Chainage end",
                   ("Chainage end" - "Chainage start") AS dchain,
                   Latitude, Longitude, "Direction"
            FROM inventory_wide
            WHERE "Project Name" = %s AND "Date" = %s
              AND "Chainage start" >= 0 AND "Chainage end" <= 99999
              AND ("Asset type" = 'Service Road' OR COALESCE("Service Road",0) <> 0)
            ORDER BY "Service Road" DESC NULLS LAST, inventory_id
            ''',
            [PROJECT, DATE],
        )
        sr_rows = cur.fetchall()
        print('=== service road detail ===')
        print('n', len(sr_rows))
        for r in sr_rows:
            print(r)
        print('sum', sum(float(r[2] or 0) for r in sr_rows))
        print('sum_abs', sum(abs(float(r[2] or 0)) for r in sr_rows))
        print('sum_dchain', sum(float(r[5] or 0) for r in sr_rows))

        # Unrestricted column sums for all ASSET map fields
        cur.execute(
            '''
            SELECT
              COALESCE(SUM("Trees"),0) AS trees,
              COALESCE(SUM("Culvert"),0) AS culvert,
              COALESCE(SUM("Street Lights"),0) AS street_lights,
              COALESCE(SUM("Bridges"),0) AS bridges,
              COALESCE(SUM("Traffic Signals"),0) AS traffic_signals,
              COALESCE(SUM("Bus Stop"),0) AS bus_stop,
              COALESCE(SUM("Truck LayBy"),0) AS truck_layby,
              COALESCE(SUM("Toll Plaza"),0) AS toll_plaza_sum,
              COUNT(DISTINCT (Latitude, Longitude))
                FILTER (WHERE "Asset type" = 'Toll Plaza' OR COALESCE("Toll Plaza",0) > 0) AS toll_plaza_uniq_ll,
              COUNT(DISTINCT ROUND("Chainage start"::numeric, 3))
                FILTER (WHERE "Asset type" = 'Toll Plaza') AS toll_plaza_uniq_ch,
              COALESCE(SUM("Adjacent Road"),0) AS adjacent_road,
              COALESCE(SUM("Toilet Blocks"),0) AS toilet_blocks,
              COALESCE(SUM("Rest Area"),0) AS rest_area,
              COALESCE(SUM("RCC Drain"),0) AS rcc_drain,
              COALESCE(SUM("Fuel Station"),0) AS fuel_station,
              COALESCE(SUM("Emergency Call Box"),0) AS emergency_call_box,
              COALESCE(SUM("Tunnels"),0) AS tunnels,
              COALESCE(SUM("Footpath"),0) AS footpath,
              COALESCE(SUM("Junction"),0) AS junction,
              COALESCE(SUM("Sign Boards"),0) AS sign_boards,
              COALESCE(SUM("Solar Blinker"),0) AS solar_blinker,
              COALESCE(SUM("Median Plants"),0) AS median_plants,
              COALESCE(SUM("Service Road"),0) AS service_road,
              COALESCE(SUM("KM Stones"),0) AS km_stones,
              COALESCE(SUM("Crash Barrier"),0) AS crash_barrier,
              COALESCE(SUM("Median Opening"),0) AS median_opening,
              COALESCE(SUM("Kerb"),0) AS kerb,
              COALESCE(SUM("Guard Post"),0) AS guard_post,
              COALESCE(SUM("Delineators"),0) AS delineators,
              COALESCE(SUM("Pedestrian Guard Rail"),0) AS pedestrian_guard_rail,
              COALESCE(SUM("Slip Roads"),0) AS slip_roads,
              COALESCE(SUM("Bus Bays"),0) AS bus_bays,
              COUNT(*) FILTER (WHERE "Asset type" = 'Hazard Markers') AS hazard_markers_count,
              COUNT(*) FILTER (WHERE "Asset type" IN ('Culvert','Bridges','Tunnels')) AS structures_count,
              COUNT(*) FILTER (WHERE "Asset type" = 'Junction') AS junction_count,
              COUNT(*) FILTER (WHERE "Asset type" = 'Adjacent Road') AS adjacent_road_count
            FROM inventory_wide
            WHERE "Project Name" = %s AND "Date" = %s
              AND "Chainage start" >= 0 AND "Chainage end" <= 99999
            ''',
            [PROJECT, DATE],
        )
        cols = [c[0] for c in cur.description]
        print('=== unrestricted column sums / counts ===')
        print(json.dumps(dict(zip(cols, cur.fetchone())), indent=2, default=str))

        # Crash barrier on non-CB asset types
        cur.execute(
            '''
            SELECT "Asset type", COUNT(*), COALESCE(SUM("Crash Barrier"),0)
            FROM inventory_wide
            WHERE "Project Name" = %s AND "Date" = %s
              AND "Chainage start" >= 0 AND "Chainage end" <= 99999
              AND COALESCE("Crash Barrier",0) <> 0
            GROUP BY 1
            ''',
            [PROJECT, DATE],
        )
        print('=== crash barrier nonzero by asset_type ===')
        for r in cur.fetchall():
            print(r)

    return 0


if __name__ == '__main__':
    raise SystemExit(main())
