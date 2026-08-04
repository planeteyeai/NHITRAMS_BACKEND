import os
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "nhit.settings")
django.setup()

from django.db import connection

SQL = """
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM information_schema.tables
        WHERE table_schema = 'public'
          AND table_name = 'distress'
    ) THEN
        ALTER TABLE distress
            ADD COLUMN IF NOT EXISTS sync_id UUID;

        CREATE UNIQUE INDEX IF NOT EXISTS distress_sync_id_uidx
            ON distress (sync_id)
            WHERE sync_id IS NOT NULL;
    END IF;
END $$;
"""

with connection.cursor() as c:
    c.execute(SQL)
    c.execute(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'distress'
          AND column_name = 'sync_id'
        """
    )
    print("sync_id column:", c.fetchall())

from django.test import Client

client = Client()
response = client.get("/test/api/data/?project_id=2&date=2026-12-07")
print("status:", response.status_code)
if response.status_code == 200:
    data = response.json()
    print(
        "total:",
        data.get("total_records"),
        "rows:",
        data.get("returned_rows"),
        "types:",
        len(data.get("by_type") or []),
    )
else:
    print(response.content[:500])
