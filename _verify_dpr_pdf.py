import os
from pathlib import Path

import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'nhit.settings')
django.setup()

from inventory.services.dpr_generate import DPRGenerationService
from inventory.services.dpr_generate_pdf import build_dpr_pdf_filename, render_asset_change_dpr_pdf
from pdf.utils import TempWorkspace
from rest_framework.exceptions import ValidationError

checks = []

def ok(name, cond, detail=''):
    checks.append((name, bool(cond), detail))
    print(('PASS' if cond else 'FAIL'), '-', name, detail)

# Auth/service reuse
payload = DPRGenerationService.generate(
    project_name='Abu Road to Swaroopganj',
    reporting_engineer_name='Ram Patil',
    assessment_date='2026-08-05',
    username='ram.patil',
)
ok('service generate', isinstance(payload, dict) and 'project_details' in payload)
ok('project details', bool(payload['project_details'].get('project_name')))
ok('overall summary key', 'overall_summary' in payload)
ok('removed assets key', 'removed_damaged_assets' in payload)
ok('rm key', 'routine_maintenance' in payload)

name = build_dpr_pdf_filename(payload['project_details']['project_name'], payload['project_details']['assessment_date'])
ok('filename pattern', name.startswith('DPR_') and name.endswith('.pdf'), name)

with TempWorkspace() as ws:
    pdf_path = ws.make_pdf_path('live_')
    render_asset_change_dpr_pdf(payload, pdf_path, temp_dir=ws.dir)
    data = Path(pdf_path).read_bytes()
    ok('pdf magic', data.startswith(b'%PDF'))
    ok('pdf size', len(data) > 1000, str(len(data)))
    # copy out for manual inspection
    out = Path('DPR_live_verify.pdf')
    out.write_bytes(data)
    ok('wrote sample', out.exists(), str(out.resolve()))

# empty-ish still works (same payload with cleared lists)
empty = dict(payload)
empty['overall_summary'] = []
empty['removed_damaged_assets'] = []
empty['removed_damaged_asset_photos'] = []
empty['routine_maintenance'] = []
empty['routine_maintenance_photos'] = []
with TempWorkspace() as ws:
    pdf_path = ws.make_pdf_path('empty_')
    render_asset_change_dpr_pdf(empty, pdf_path, temp_dir=ws.dir)
    ok('empty project pdf', Path(pdf_path).read_bytes().startswith(b'%PDF'))

try:
    DPRGenerationService.generate(
        project_name='Totally Fake Project XYZ',
        reporting_engineer_name='Ram Patil',
        assessment_date='2026-08-05',
        username='ram.patil',
    )
    ok('invalid project rejected', False)
except ValidationError:
    ok('invalid project rejected', True)

failed = [c for c in checks if not c[1]]
print('\nSUMMARY', f'{len(checks)-len(failed)}/{len(checks)} PASS')
for n, p, d in failed:
    print('FAIL', n, d)
