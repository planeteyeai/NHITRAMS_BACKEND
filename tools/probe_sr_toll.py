"""Probe service road rows and unrestricted column sums (quoted identifiers)."""

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

P = 'Kothakota to Kurnool'
D = '2026-07-28'

with connection.cursor() as cur:
    cur.execute(
        '''
        SELECT inventory_id, "Asset type", "Service Road",
               "Chainage start", "Chainage end",
               ("Chainage end" - "Chainage start") AS dchain,
               "Latitude", "Longitude", "Direction"
        FROM inventory_wide
        WHERE "Project Name" = %s AND "Date" = %s
          AND "Chainage start" >= 0 AND "Chainage end" <= 99999
          AND ("Asset type" = 'Service Road' OR COALESCE("Service Road",0) <> 0)
        ORDER BY "Service Road" DESC NULLS LAST, inventory_id
        ''',
        [P, D],
    )
    rows = cur.fetchall()
    print('n', len(rows))
    for r in rows:
        print(r)
    print('sum', sum(float(r[2] or 0) for r in rows))

    cur.execute(
        '''
        SELECT
          COALESCE(SUM("Trees"),0) AS trees,
          COALESCE(SUM("Delineators"),0) AS delineators,
          COALESCE(SUM("Crash Barrier"),0) AS crash_barrier,
          COALESCE(SUM("Service Road"),0) AS service_road,
          COALESCE(SUM("Toll Plaza"),0) AS toll_plaza_sum,
          COUNT(DISTINCT ("Latitude", "Longitude"))
            FILTER (WHERE "Asset type" = 'Toll Plaza' OR COALESCE("Toll Plaza",0) > 0) AS toll_uniq_ll,
          COUNT(*) FILTER (WHERE "Asset type" = 'Toll Plaza') AS toll_rows,
          COALESCE(SUM("Kerb"),0) AS kerb,
          COALESCE(SUM("Bus Bays"),0) AS bus_bays,
          COALESCE(SUM("Bus Stop"),0) AS bus_stop,
          COALESCE(SUM("Sign Boards"),0) AS sign_boards,
          COALESCE(SUM("Street Lights"),0) AS street_lights,
          COALESCE(SUM("Guard Post"),0) AS guard_post,
          COALESCE(SUM("KM Stones"),0) AS km_stones,
          COALESCE(SUM("Solar Blinker"),0) AS solar_blinker,
          COALESCE(SUM("Fuel Station"),0) AS fuel_station,
          COALESCE(SUM("Emergency Call Box"),0) AS emergency_call_box,
          COALESCE(SUM("Median Opening"),0) AS median_opening,
          COALESCE(SUM("Slip Roads"),0) AS slip_roads,
          COALESCE(SUM("Toilet Blocks"),0) AS toilet_blocks,
          COALESCE(SUM("Culvert"),0) AS culvert,
          COALESCE(SUM("Junction"),0) AS junction,
          COALESCE(SUM("Adjacent Road"),0) AS adjacent_road,
          COALESCE(SUM("Truck LayBy"),0) AS truck_layby,
          COUNT(*) FILTER (WHERE "Asset type" = 'Hazard Markers') AS hazard_markers_rows,
          COUNT(*) FILTER (WHERE "Asset type" = 'Delineator') AS delineator_rows,
          COUNT(*) FILTER (WHERE "Asset type" IN ('Tree','Trees')) AS tree_rows
        FROM inventory_wide
        WHERE "Project Name" = %s AND "Date" = %s
          AND "Chainage start" >= 0 AND "Chainage end" <= 99999
        ''',
        [P, D],
    )
    cols = [c[0] for c in cur.description]
    print(json.dumps(dict(zip(cols, cur.fetchone())), indent=2, default=str))

    cur.execute(
        '''
        SELECT "Asset type", COUNT(*), COALESCE(SUM("Crash Barrier"),0)
        FROM inventory_wide
        WHERE "Project Name" = %s AND "Date" = %s
          AND "Chainage start" >= 0 AND "Chainage end" <= 99999
          AND COALESCE("Crash Barrier",0) <> 0
        GROUP BY 1
        ''',
        [P, D],
    )
    print('cb by type', cur.fetchall())

    # Maybe service road dashboard uses ROUND(SUM(...),2) from a different expression
    # Try summing service road only where direction is not Median, etc.
    for label, sql in [
        ('all', 'COALESCE(SUM("Service Road"),0)'),
        ('non_median', 'COALESCE(SUM("Service Road") FILTER (WHERE "Direction" <> \'Median\'),0)'),
        ('inc_dec', "COALESCE(SUM(\"Service Road\") FILTER (WHERE \"Direction\" IN ('Increasing','Decreasing')),0)"),
        ('uniq_ll_max', '''COALESCE(SUM(sr),0) FROM (
            SELECT MAX("Service Road") AS sr FROM inventory_wide
            WHERE "Project Name" = %s AND "Date" = %s
              AND "Chainage start" >= 0 AND "Chainage end" <= 99999
              AND ("Asset type" = 'Service Road' OR COALESCE("Service Road",0) <> 0)
            GROUP BY "Latitude", "Longitude"
          ) t'''),
    ]:
        if label == 'uniq_ll_max':
            cur.execute(
                'SELECT COALESCE(SUM(sr),0) FROM ('
                ' SELECT MAX("Service Road") AS sr FROM inventory_wide'
                ' WHERE "Project Name" = %s AND "Date" = %s'
                ' AND "Chainage start" >= 0 AND "Chainage end" <= 99999'
                ' AND ("Asset type" = \'Service Road\' OR COALESCE("Service Road",0) <> 0)'
                ' GROUP BY "Latitude", "Longitude"'
                ') t',
                [P, D],
            )
        else:
            cur.execute(
                f'''
                SELECT {sql}
                FROM inventory_wide
                WHERE "Project Name" = %s AND "Date" = %s
                  AND "Chainage start" >= 0 AND "Chainage end" <= 99999
                ''',
                [P, D],
            )
        print(label, cur.fetchone()[0])
