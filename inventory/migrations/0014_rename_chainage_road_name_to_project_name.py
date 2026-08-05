# Rename ChainageReferencePoint.road_name -> project_name (preserve data).

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('inventory', '0013_chainage_reference_points'),
    ]

    operations = [
        migrations.RemoveIndex(
            model_name='chainagereferencepoint',
            name='crp_road_latlng_idx',
        ),
        migrations.RenameField(
            model_name='chainagereferencepoint',
            old_name='road_name',
            new_name='project_name',
        ),
        migrations.AddIndex(
            model_name='chainagereferencepoint',
            index=models.Index(
                fields=['project_name', 'latitude', 'longitude'],
                name='crp_project_latlng_idx',
            ),
        ),
    ]
