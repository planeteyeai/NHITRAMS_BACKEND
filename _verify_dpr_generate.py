import json
import os

import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'nhit.settings')
django.setup()

from inventory.services.dpr_generate import (
    calculate_unit,
    generate_asset_change_dpr,
    generate_photo_references,
)
from rest_framework.exceptions import ValidationError

checks = []

def ok(name, cond, detail=''):
    checks.append((name, bool(cond), detail))
    print(('PASS' if cond else 'FAIL'), name, detail)

ok('unit Nos', calculate_unit(nos=1) == 'Nos')
ok('unit RMT', calculate_unit(length=2) == 'RMT')
ok('unit m2', calculate_unit(length=2, width=1) == 'm²')
ok('unit m3', calculate_unit(length=2, width=1, depth=1) == 'm³')
ok('photo refs', generate_photo_references(2) == ['Refer Image No. 1', 'Refer Image No. 2'])

try:
    payload = generate_asset_change_dpr(
        project_name='Abu Road to Swaroopganj',
        reporting_engineer_name='Ram Patil',
        assessment_date='2026-08-05',
        username='ram.patil',
    )
    ok('generate succeeds', True)
    pd = payload['project_details']
    ok('project name', pd['project_name'] == 'Abu Road to Swaroopganj')
    ok('start chainage present', bool(pd['start_chainage']))
    ok('end chainage present', bool(pd['end_chainage']))
    ok('length present', bool(pd['total_project_length']))
    ok('engineer', pd['reporting_engineer_name'] == 'Ram Patil')
    ok('assessment date', pd['assessment_date'] == '2026-08-05')
    ok('generation date', bool(pd['generation_date']))
    ok('sections present', all(k in payload for k in [
        'overall_summary', 'removed_damaged_assets', 'removed_damaged_asset_photos',
        'routine_maintenance', 'routine_maintenance_photos',
    ]))
    # image numbering consistency if any photos
    assets = payload['removed_damaged_assets']
    photos = payload['removed_damaged_asset_photos']
    if photos:
        refs = []
        for row in assets:
            if row['evidence_available']:
                refs.extend([p.strip() for p in row['evidence_available'].split(',')])
        expected = [f'Refer Image No. {p["image_number"]}' for p in photos]
        ok('inventory evidence matches photos', refs == expected, f'{refs} vs {expected}')
    else:
        ok('inventory evidence matches photos', True, 'no photos')
    print(json.dumps({
        'project_details': pd,
        'overall_summary': payload['overall_summary'],
        'inventory_rows': len(assets),
        'inventory_photos': len(photos),
        'rm_rows': len(payload['routine_maintenance']),
        'rm_photos': len(payload['routine_maintenance_photos']),
    }, indent=2))
except Exception as exc:
    ok('generate succeeds', False, str(exc))

try:
    generate_asset_change_dpr(
        project_name='Totally Fake Project 999',
        reporting_engineer_name='Ram Patil',
        assessment_date='2026-08-05',
        username='ram.patil',
    )
    ok('invalid project rejected', False)
except ValidationError:
    ok('invalid project rejected', True)

failed = [c for c in checks if not c[1]]
print('\n=== SUMMARY ===')
print(f'PASS {len(checks)-len(failed)} / {len(checks)}')
for name, passed, detail in failed:
    print('FAIL', name, detail)
