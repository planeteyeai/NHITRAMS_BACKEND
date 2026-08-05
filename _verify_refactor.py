import json
import os

import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'nhit.settings')
django.setup()

from django.db import connection
from inventory.models import ChainageReferencePoint
from inventory.services.chainage_lookup import find_nearest_chainage

count = ChainageReferencePoint.objects.count()
print('PASS data preserved' if count == 138381 else f'FAIL count={count}')

# Confirm project_name column exists / road_name gone
cols = {c.name for c in connection.introspection.get_table_description(
    connection.cursor(), 'chainage_reference_points'
)}
print('PASS project_name column' if 'project_name' in cols else 'FAIL missing project_name')
print('PASS road_name removed' if 'road_name' not in cols else 'FAIL road_name still present')

with connection.cursor() as cursor:
    cursor.execute(
        """
        SELECT indexname FROM pg_indexes
        WHERE tablename = 'chainage_reference_points'
        ORDER BY indexname
        """
    )
    indexes = [row[0] for row in cursor.fetchall()]
print('indexes:', indexes)
print(
    'PASS composite index'
    if 'crp_project_latlng_idx' in indexes
    else 'FAIL missing crp_project_latlng_idx'
)
print(
    'PASS old index removed'
    if 'crp_road_latlng_idx' not in indexes
    else 'FAIL old index still present'
)

result = find_nearest_chainage(
    20.8462077, 78.9606274, project_name='Borkhedi to Wadner'
)
print(json.dumps({'success': True, 'data': result}, indent=2))
expected_keys = {
    'project_name',
    'chainage_display',
    'chainage_meters',
    'distance_from_road_meters',
    'nearest_latitude',
    'nearest_longitude',
}
print('PASS response keys' if set(result.keys()) == expected_keys else f'FAIL keys={set(result.keys())}')
print(
    'PASS chainage_display'
    if result.get('chainage_display') == 'Km 36.600'
    else f'FAIL display={result.get("chainage_display")}'
)
print('PASS no road_name in response' if 'road_name' not in result else 'FAIL road_name leaked')
print('PASS no chainage_km in response' if 'chainage_km' not in result else 'FAIL chainage_km leaked')
