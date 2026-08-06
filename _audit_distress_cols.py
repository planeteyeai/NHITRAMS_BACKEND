import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'nhit.settings')
django.setup()

from django.db import connection
from distress.models import DistressType

with connection.cursor() as c:
    c.execute(
        """
        SELECT column_name FROM information_schema.columns
        WHERE table_schema='public' AND table_name='distress'
        ORDER BY ordinal_position
        """
    )
    print('COLUMNS', [r[0] for r in c.fetchall()])

print('TYPES', list(DistressType.objects.values_list('distress_type_name', flat=True)))
