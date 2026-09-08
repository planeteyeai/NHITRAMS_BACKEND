from django.db import connection
print('indexes check')
with connection.cursor() as cur:
    cur.execute(
        "SELECT indexname FROM pg_indexes WHERE tablename='inventory_wide' ORDER BY 1"
    )
    print([r[0] for r in cur.fetchall()])
    cur.execute(
        '''
        EXPLAIN (ANALYZE, BUFFERS)
        SELECT COUNT(*) FROM inventory_wide
        WHERE "Project Name"=%s AND "Date"=%s
          AND "Chainage start">=%s AND "Chainage end"<=%s
        ''',
        ['Kothakota to Kurnool', '2026-07-28', 0.0, 99999.0],
    )
    print('\n'.join(r[0] for r in cur.fetchall()))
