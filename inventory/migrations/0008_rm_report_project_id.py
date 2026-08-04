from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('inventory', '0007_routine_maintenance'),
    ]

    operations = [
        migrations.AddField(
            model_name='routinemaintenancereport',
            name='project_id',
            field=models.IntegerField(blank=True, db_index=True, null=True),
        ),
    ]
