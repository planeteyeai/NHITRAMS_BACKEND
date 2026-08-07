import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'nhit.settings')
django.setup()

from django.db import connection

with connection.cursor() as c:
    c.execute(
        """
        SELECT table_name FROM information_schema.columns
        WHERE column_name='username' AND table_schema='public'
        ORDER BY table_name
        """
    )
    print('username tables', [r[0] for r in c.fetchall()])

    c.execute(
        """
        SELECT table_name FROM information_schema.tables
        WHERE table_schema='public'
          AND (
            table_name ILIKE '%%user%%'
            OR table_name ILIKE '%%login%%'
            OR table_name ILIKE '%%employee%%'
            OR table_name ILIKE '%%staff%%'
          )
        ORDER BY 1
        """
    )
    print('user-ish', [r[0] for r in c.fetchall()])

    c.execute(
        """
        SELECT username FROM inventory_reports
        ORDER BY id DESC LIMIT 10
        """
    )
    print('inventory_reports usernames', c.fetchall())

    c.execute(
        """
        SELECT username FROM distress_reports
        ORDER BY id DESC LIMIT 10
        """
    )
    print('distress_reports usernames', c.fetchall())

    try:
        c.execute(
            """
            SELECT username, project_name FROM user_project_assignments
            ORDER BY username LIMIT 20
            """
        )
        print('assignments', c.fetchall())
    except Exception as e:
        print('assignments err', e)

    # sample users table columns if exists
    for t in ('users', 'user', 'nhit_users', 'tbl_users', 'auth_user'):
        c.execute(
            """
            SELECT EXISTS (
              SELECT 1 FROM information_schema.tables
              WHERE table_schema='public' AND table_name=%s
            )
            """,
            [t],
        )
        if c.fetchone()[0]:
            c.execute(
                """
                SELECT column_name FROM information_schema.columns
                WHERE table_name=%s ORDER BY ordinal_position
                """,
                [t],
            )
            print(t, 'cols', [r[0] for r in c.fetchall()])
