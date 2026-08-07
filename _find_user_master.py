import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'nhit.settings')
django.setup()

from django.db import connection

with connection.cursor() as c:
    c.execute(
        """
        SELECT table_name, column_name
        FROM information_schema.columns
        WHERE table_schema='public'
          AND (
            column_name ILIKE '%%user%%'
            OR column_name ILIKE '%%login%%'
            OR column_name ILIKE '%%employee%%'
          )
        ORDER BY table_name, ordinal_position
        """
    )
    for row in c.fetchall():
        print(row)

    # any table with both id-like and name
    c.execute(
        """
        SELECT c.table_name
        FROM information_schema.columns c
        WHERE c.table_schema='public'
        GROUP BY c.table_name
        HAVING
          BOOL_OR(c.column_name IN ('id','user_id','userid','uid'))
          AND BOOL_OR(c.column_name IN ('username','user_name','name','login_name','email'))
        ORDER BY 1
        """
    )
    print('candidate', [r[0] for r in c.fetchall()])
