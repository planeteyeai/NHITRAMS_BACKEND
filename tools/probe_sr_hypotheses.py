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
    for direction in ('Increasing', 'Decreasing', 'Median'):
        cur.execute(
            '''
            SELECT COALESCE(SUM("Service Road"),0)
            FROM inventory_wide
            WHERE "Project Name"=%s AND "Date"=%s AND "Direction"=%s
              AND "Chainage start">=0 AND "Chainage end"<=99999
            ''',
            [P, D, direction],
        )
        print(direction, cur.fetchone()[0])

    cur.execute(
        '''
        WITH sr AS (
          SELECT ROUND("Chainage start"::numeric, 3) AS ch, "Service Road" AS v
          FROM inventory_wide
          WHERE "Project Name"=%s AND "Date"=%s
            AND "Chainage start">=0 AND "Chainage end"<=99999
            AND "Asset type"='Service Road'
        )
        SELECT COALESCE(SUM(mx),0) FROM (SELECT ch, MAX(v) AS mx FROM sr GROUP BY ch) t
        ''',
        [P, D],
    )
    print('max_per_chainage', cur.fetchone()[0])

    cur.execute(
        '''
        SELECT ROUND(COALESCE(SUM("Service Road"),0)::numeric, 2)
        FROM inventory_wide
        WHERE "Project Name"=%s AND "Date"=%s
          AND "Chainage start">=0 AND "Chainage end"<=99999
        ''',
        [P, D],
    )
    print('round2', float(cur.fetchone()[0]))

    # Original conceptual example also had service_road 39.33 — search any project
    cur.execute(
        '''
        SELECT "Project Name", "Date", ROUND(SUM("Service Road")::numeric, 2) AS s
        FROM inventory_wide
        GROUP BY 1, 2
        HAVING ROUND(SUM("Service Road")::numeric, 2) = 39.33
        LIMIT 20
        '''
    )
    print('projects with sr=39.33', cur.fetchall())

    cur.execute(
        '''
        SELECT "Project Name", "Date", ROUND(SUM("Service Road")::numeric, 2) AS s
        FROM inventory_wide
        WHERE ABS(SUM("Service Road") - 39.33) < 0.05
        GROUP BY 1, 2
        LIMIT 20
        '''
    )
    # fix having
    cur.execute(
        '''
        SELECT "Project Name", "Date", ROUND(SUM("Service Road")::numeric, 2) AS s
        FROM inventory_wide
        GROUP BY 1, 2
        HAVING ABS(SUM("Service Road") - 39.33) < 0.05
        LIMIT 20
        '''
    )
    print('near 39.33', cur.fetchall())
