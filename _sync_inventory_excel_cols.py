"""Inspect asset_types PK and insert missing Excel asset types."""
import os
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "nhit.settings")
django.setup()

from django.db import connection

EXCEL_ASSET_TYPES = [
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
    "Kerb",
    "Guard Post",
    "Delineators",
    "Pedestrian Guard Rail",
    "Slip Roads",
    "Bus Bays",
]

with connection.cursor() as c:
    c.execute(
        """
        SELECT column_name, column_default, is_nullable, data_type
        FROM information_schema.columns
        WHERE table_name='asset_types'
        ORDER BY ordinal_position
        """
    )
    print("asset_types schema:")
    for row in c.fetchall():
        print(" ", row)

    c.execute(
        """
        SELECT pg_get_serial_sequence('asset_types', 'asset_type_id')
        """
    )
    print("sequence:", c.fetchone())

    c.execute("SELECT COALESCE(MAX(asset_type_id), 0) FROM asset_types")
    max_id = c.fetchone()[0]
    print("max id:", max_id)

    c.execute("SELECT asset_type_name FROM asset_types")
    existing = {r[0] for r in c.fetchall()}
    missing = [a for a in EXCEL_ASSET_TYPES if a not in existing]
    print("missing:", missing)

    next_id = max_id + 1
    for name in missing:
        c.execute(
            "INSERT INTO asset_types (asset_type_id, asset_type_name) VALUES (%s, %s)",
            [next_id, name],
        )
        print(f"  + {next_id}: {name}")
        next_id += 1

    # inventory columns
    c.execute(
        """
        SELECT column_name FROM information_schema.columns
        WHERE table_schema='public' AND table_name='inventory'
        """
    )
    inv_cols = {r[0] for r in c.fetchall()}
    alters = []
    if "Image URL" not in inv_cols and "image_url" not in inv_cols:
        alters.append('ADD COLUMN "Image URL" text')
    if "Source" not in inv_cols:
        alters.append('ADD COLUMN "Source" text')
    if alters:
        sql = "ALTER TABLE inventory " + ", ".join(alters)
        print(sql)
        c.execute(sql)
    else:
        print("inventory Image URL / Source already present or nothing to add")

    if "source_file" in inv_cols and "Source" in (
        inv_cols | ({"Source"} if alters else set())
    ):
        # refresh cols after alter
        c.execute(
            """
            SELECT column_name FROM information_schema.columns
            WHERE table_schema='public' AND table_name='inventory'
            """
        )
        inv_cols = {r[0] for r in c.fetchall()}
        if "Source" in inv_cols and "source_file" in inv_cols:
            c.execute(
                """
                UPDATE inventory
                SET "Source" = source_file
                WHERE "Source" IS NULL AND source_file IS NOT NULL
                """
            )
            print("backfilled Source rows:", c.rowcount)

    c.execute(
        """
        SELECT column_name FROM information_schema.columns
        WHERE table_schema='public' AND table_name='inventory_reports'
        """
    )
    rpt_cols = {r[0] for r in c.fetchall()}
    if "Source" not in rpt_cols:
        c.execute(
            "ALTER TABLE inventory_reports ADD COLUMN \"Source\" varchar(255) NOT NULL DEFAULT ''"
        )
        print('added inventory_reports."Source"')

    print("\nDONE")
    c.execute("SELECT asset_type_id, asset_type_name FROM asset_types ORDER BY asset_type_id")
    for row in c.fetchall():
        mark = "OK" if row[1] in EXCEL_ASSET_TYPES else "extra"
        if row[1] in EXCEL_ASSET_TYPES:
            print(f"  [{mark}] {row[0]} {row[1]}")
    still = [a for a in EXCEL_ASSET_TYPES if a not in {r[1] for r in c.fetchall()}]
    c.execute("SELECT asset_type_name FROM asset_types")
    final = {r[0] for r in c.fetchall()}
    still = [a for a in EXCEL_ASSET_TYPES if a not in final]
    print("still missing:", still)
    c.execute(
        """
        SELECT column_name FROM information_schema.columns
        WHERE table_name='inventory' ORDER BY ordinal_position
        """
    )
    print("inventory cols:", [r[0] for r in c.fetchall()])
