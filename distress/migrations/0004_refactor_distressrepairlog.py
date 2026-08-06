# Generated manually for distress_id-based repair logs.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('distress', '0003_distressrepairlog'),
    ]

    operations = [
        migrations.RemoveIndex(
            model_name='distressrepairlog',
            name='drl_proj_type_chain_idx',
        ),
        migrations.RemoveIndex(
            model_name='distressrepairlog',
            name='drl_lat_lng_idx',
        ),
        migrations.RemoveField(
            model_name='distressrepairlog',
            name='project_name',
        ),
        migrations.RemoveField(
            model_name='distressrepairlog',
            name='chainage',
        ),
        migrations.RemoveField(
            model_name='distressrepairlog',
            name='latitude',
        ),
        migrations.RemoveField(
            model_name='distressrepairlog',
            name='longitude',
        ),
        migrations.RemoveField(
            model_name='distressrepairlog',
            name='distress_type',
        ),
        migrations.AddField(
            model_name='distressrepairlog',
            name='distress_id',
            field=models.BigIntegerField(db_index=True, default=0),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='distressrepairlog',
            name='quantity_resolved',
            field=models.FloatField(default=1.0),
        ),
        migrations.AddIndex(
            model_name='distressrepairlog',
            index=models.Index(
                fields=['distress_id', '-updated_at'],
                name='drl_distress_updated_idx',
            ),
        ),
    ]
