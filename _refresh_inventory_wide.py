"""Refresh inventory_wide from normalized inventory (full rebuild)."""
import os
import time

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "nhit.settings")
django.setup()

from django.db import connection

ASSET_BEFORE_LAT = [
    "Trees",
    "Culvert",
    "Street Lights",
    "Bridges",
    "Traffic Signals",
    "Bus Stop",
    "Truck LayBy",
    "Toll Plaza",
    "Adjacent Road",
    "Toilet Blocks",
    "Rest Area",
    "RCC Drain",
    "Fuel Station",
    "Emergency Call Box",
    "Tunnels",
    "Footpath",
    "Junction",
    "Sign Boards",
    "Solar Blinker",
    "Median Plants",
    "Service Road",
    "KM Stones",
    "Crash Barrier",
    "Median Opening",
]
ASSET_AFTER_LANE = [
    "Kerb",
    "Guard Post",
    "Delineators",
    "Pedestrian Guard Rail",
    "Slip Roads",
    "Bus Bays",
]


def q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def case_expr(col: str) -> str:
    safe = col.replace("'", "''")
    return (
        f"CASE WHEN at.asset_type_name = '{safe}' "
        f"THEN COALESCE(i.count_val, 0) ELSE 0 END AS {q(col)}"
    )


def main():
    asset_defs_before = ",\n  ".join(
        f"{q(c)} double precision NOT NULL DEFAULT 0" for c in ASSET_BEFORE_LAT
    )
    asset_defs_after = ",\n  ".join(
        f"{q(c)} double precision NOT NULL DEFAULT 0" for c in ASSET_AFTER_LANE
    )
    select_assets_before = ",\n      ".join(case_expr(c) for c in ASSET_BEFORE_LAT)
    select_assets_after = ",\n      ".join(case_expr(c) for c in ASSET_AFTER_LANE)

    create_sql = f"""
    DROP TABLE IF EXISTS inventory_wide;
    CREATE TABLE inventory_wide (
      inventory_id bigint PRIMARY KEY,
      {q('Project Name')} text,
      {q('Chainage start')} double precision,
      {q('Chainage end')} double precision,
      {q('Direction')} text,
      {q('Asset type')} text,
      {asset_defs_before},
      {q('Latitude')} double precision,
      {q('Longitude')} double precision,
      {q('Date')} date,
      {q('Sub Asset Type')} text,
      {q('Carriage Type')} text,
      {q('Lane')} text,
      {asset_defs_after},
      {q('Image URL')} text,
      {q('Source')} text
    );
    CREATE INDEX inventory_wide_project_idx ON inventory_wide ({q('Project Name')});
    CREATE INDEX inventory_wide_date_idx ON inventory_wide ({q('Date')});
    CREATE INDEX inventory_wide_asset_idx ON inventory_wide ({q('Asset type')});
    CREATE INDEX inventory_wide_chainage_idx
      ON inventory_wide ({q('Chainage start')}, {q('Chainage end')});
    """

    # LEFT JOIN segment so rows without segment are still kept.
    insert_sql = f"""
    INSERT INTO inventory_wide
    SELECT
      i.inventory_id,
      p.project_name,
      rs.chainage_start,
      rs.chainage_end,
      rs.direction,
      at.asset_type_name,
      {select_assets_before},
      i.latitude,
      i.longitude,
      i.survey_date,
      sat.sub_asset_type_name,
      rs.carriage_type,
      rs.lane,
      {select_assets_after},
      i.{q('Image URL')},
      COALESCE(i.{q('Source')}, i.source_file)
    FROM inventory i
    JOIN projects p ON p.project_id = i.project_id
    LEFT JOIN road_segments rs ON rs.segment_id = i.segment_id
    JOIN asset_types at ON at.asset_type_id = i.asset_type_id
    LEFT JOIN sub_asset_types sat ON sat.sub_asset_type_id = i.sub_asset_type_id
    """

    with connection.cursor() as c:
        c.execute("SELECT COUNT(*) FROM inventory")
        inv_count = c.fetchone()[0]
        print(f"Normalized inventory rows: {inv_count}")

        print("Rebuilding inventory_wide...")
        t0 = time.time()
        c.execute(create_sql)
        c.execute(insert_sql)
        inserted = c.rowcount
        print(f"Inserted {inserted} rows in {time.time() - t0:.1f}s")

        c.execute(
            """
            SELECT COUNT(*) FROM inventory_wide
            WHERE "Project Name" ILIKE %s
            """,
            ["%agra%bypass%"],
        )
        print("Agra Bypass Road in inventory_wide:", c.fetchone()[0])

        c.execute(
            """
            SELECT COUNT(*)
            FROM inventory i
            JOIN projects p ON p.project_id = i.project_id
            WHERE p.project_name ILIKE %s
            """,
            ["%agra%bypass%"],
        )
        print("Agra Bypass Road in inventory:", c.fetchone()[0])

        c.execute(
            """
            SELECT COUNT(*)
            FROM inventory i
            LEFT JOIN inventory_wide w ON w.inventory_id = i.inventory_id
            WHERE w.inventory_id IS NULL
            """
        )
        print("Still missing from wide:", c.fetchone()[0])


if __name__ == "__main__":
    main()
