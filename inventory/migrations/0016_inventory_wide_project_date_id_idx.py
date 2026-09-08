# Index for inventory_filter on unmanaged inventory_wide.
# Guarded so test DBs without the table can still migrate.

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('inventory', '0015_rename_chainage_project_name_indexes'),
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
                  AND table_name = 'inventory_wide'
              ) THEN
                CREATE INDEX IF NOT EXISTS inventory_wide_project_date_id_idx
                ON public.inventory_wide
                USING btree ("Project Name", "Date", inventory_id DESC);
              END IF;
            END $$;
            """,
            reverse_sql="""
            DROP INDEX IF EXISTS public.inventory_wide_project_date_id_idx;
            """,
        ),
    ]
