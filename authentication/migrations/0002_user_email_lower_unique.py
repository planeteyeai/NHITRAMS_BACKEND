"""Case-insensitive unique email constraint on auth_user."""

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('authentication', '0001_initial'),
    ]

    operations = [
        migrations.RunSQL(
            sql="""
                CREATE UNIQUE INDEX auth_user_email_lower_uniq
                ON auth_user (LOWER(email))
                WHERE email <> '';
            """,
            reverse_sql='DROP INDEX IF EXISTS auth_user_email_lower_uniq;',
        ),
    ]
