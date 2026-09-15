from pathlib import Path
import os

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / '.env', override=True)
os.environ['DJANGO_ENV'] = 'production'
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'nhit.settings')

import django

django.setup()

from django.conf import settings
from django.db import connection
from distress.models import Distress

print('model', Distress.__name__)
print('db_table', Distress._meta.db_table)
host = settings.DATABASES['default'].get('HOST') or ''
print('host_class', 'neon' if 'neon.tech' in host else 'other')

with connection.cursor() as c:
    c.execute('SELECT COUNT(*) FROM distress')
    print('distress_count', c.fetchone()[0])
    c.execute(
        'SELECT COUNT(*) FROM distress '
        'WHERE latitude IS NOT NULL AND longitude IS NOT NULL AND project_id IS NOT NULL'
    )
    print('distress_filterable_count', c.fetchone()[0])
