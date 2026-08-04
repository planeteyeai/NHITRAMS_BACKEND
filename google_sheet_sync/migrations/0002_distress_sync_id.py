"""Add sync_id to unmanaged production distress table for Google Sheet upserts."""

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('google_sheet_sync', '0001_initial'),
    ]

    operations = [
        migrations.RunSQL(
            sql="""
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
            """,
            reverse_sql="""
                DO $$
                BEGIN
                    IF EXISTS (
                        SELECT 1
                        FROM information_schema.tables
                        WHERE table_schema = 'public'
                          AND table_name = 'distress'
                    ) THEN
                        DROP INDEX IF EXISTS distress_sync_id_uidx;
                        ALTER TABLE distress DROP COLUMN IF EXISTS sync_id;
                    END IF;
                END $$;
            """,
        ),
    ]
