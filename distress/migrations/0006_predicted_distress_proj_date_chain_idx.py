"""Add filter/order index for predicted_distress (unmanaged production table).

Supports POST /distress_predict_filter/ filters:
  upper(project_name) via project_name__iexact, distress_date, chainage bounds
and ORDER BY chainage_start, predicted_distress_id.

EXPLAIN before this migration showed Parallel Seq Scan on predicted_distress.
Skips safely when the unmanaged table is absent (e.g. empty test DBs).
"""

from django.db import migrations


INDEX_NAME = 'predicted_distress_proj_date_chain_idx'


def _table_exists(schema_editor) -> bool:
    connection = schema_editor.connection
    with connection.cursor() as cursor:
        cursor.execute("SELECT to_regclass('public.predicted_distress')")
        return cursor.fetchone()[0] is not None


def create_predicted_distress_index(apps, schema_editor):
    if schema_editor.connection.vendor != 'postgresql':
        return
    if not _table_exists(schema_editor):
        return
    connection = schema_editor.connection
    # CREATE INDEX CONCURRENTLY cannot run inside a transaction.
    was_autocommit = connection.get_autocommit()
    connection.set_autocommit(True)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                f"""
                CREATE INDEX CONCURRENTLY IF NOT EXISTS {INDEX_NAME}
                ON predicted_distress (
                    (upper(project_name)),
                    distress_date,
                    chainage_start,
                    predicted_distress_id
                );
                """
            )
    finally:
        connection.set_autocommit(was_autocommit)


def drop_predicted_distress_index(apps, schema_editor):
    if schema_editor.connection.vendor != 'postgresql':
        return
    if not _table_exists(schema_editor):
        return
    connection = schema_editor.connection
    was_autocommit = connection.get_autocommit()
    connection.set_autocommit(True)
    try:
        with connection.cursor() as cursor:
            cursor.execute(f'DROP INDEX CONCURRENTLY IF EXISTS {INDEX_NAME};')
    finally:
        connection.set_autocommit(was_autocommit)


class Migration(migrations.Migration):
    atomic = False  # CREATE INDEX CONCURRENTLY cannot run in a transaction

    dependencies = [
        ('distress', '0005_distresspdfexportjob'),
    ]

    operations = [
        migrations.RunPython(
            create_predicted_distress_index,
            drop_predicted_distress_index,
        ),
    ]
