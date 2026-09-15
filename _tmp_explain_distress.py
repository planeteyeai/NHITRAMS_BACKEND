import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'nhit.settings')
django.setup()

from django.db import connection

with connection.cursor() as c:
    c.execute(
        """
        SELECT indexname, indexdef FROM pg_indexes
        WHERE tablename IN ('distress','road_segments')
        ORDER BY tablename, indexname
        """
    )
    rows = c.fetchall()
    print('INDEXES', len(rows))
    for r in rows:
        print(r[0], '=>', r[1][:200])
    c.execute('SELECT COUNT(*) FROM distress')
    print('distress_count', c.fetchone()[0])
    c.execute(
        """
        EXPLAIN (FORMAT TEXT)
        SELECT d.distress_id
        FROM distress d
        JOIN road_segments s ON s.segment_id = d.segment_id
        JOIN projects p ON p.project_id = d.project_id
        WHERE d.latitude IS NOT NULL AND d.longitude IS NOT NULL AND d.project_id IS NOT NULL
          AND p.project_name ILIKE 'Abu Road to Swaroopganj'
        ORDER BY s.chainage_start, d.distress_id
        LIMIT 200000 OFFSET 0
        """
    )
    print('EXPLAIN:')
    for line in c.fetchall():
        print(line[0])
