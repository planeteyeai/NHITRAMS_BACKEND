import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'nhit.settings')
django.setup()

from django.contrib.auth import get_user_model
from django.db import connection

User = get_user_model()
print('auth_user count', User.objects.count())
for uid in (76, 136, 129, 137, 140):
    try:
        u = User.objects.get(pk=uid)
        print(uid, '->', u.username, u.first_name, u.last_name, u.email)
    except User.DoesNotExist:
        print(uid, 'NOT FOUND')

# sample usernames that look like real names
print('sample', list(User.objects.values_list('id', 'username')[:20]))

# numeric usernames in reports
with connection.cursor() as c:
    c.execute(
        """
        SELECT username, COUNT(*) FROM inventory_reports
        GROUP BY username ORDER BY COUNT(*) DESC LIMIT 15
        """
    )
    print('inv usernames', c.fetchall())
    c.execute(
        """
        SELECT username, COUNT(*) FROM distress_reports
        GROUP BY username ORDER BY COUNT(*) DESC LIMIT 15
        """
    )
    print('distress usernames', c.fetchall())
    c.execute(
        """
        SELECT username, COUNT(*) FROM dpr_reports
        GROUP BY username ORDER BY COUNT(*) DESC LIMIT 15
        """
    )
    print('dpr usernames', c.fetchall())
    c.execute(
        """
        SELECT username, COUNT(*) FROM routine_maintenance_reports
        GROUP BY username ORDER BY COUNT(*) DESC LIMIT 15
        """
    )
    print('rm usernames', c.fetchall())
