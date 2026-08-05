# Rename leftover single-column indexes after road_name -> project_name.

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('inventory', '0014_rename_chainage_road_name_to_project_name'),
    ]

    operations = [
        migrations.RunSQL(
            sql=[
                (
                    'ALTER INDEX IF EXISTS '
                    'chainage_reference_points_road_name_b9e4f5a7 '
                    'RENAME TO chainage_reference_points_project_name_idx'
                ),
                (
                    'ALTER INDEX IF EXISTS '
                    'chainage_reference_points_road_name_b9e4f5a7_like '
                    'RENAME TO chainage_reference_points_project_name_like_idx'
                ),
            ],
            reverse_sql=[
                (
                    'ALTER INDEX IF EXISTS '
                    'chainage_reference_points_project_name_idx '
                    'RENAME TO chainage_reference_points_road_name_b9e4f5a7'
                ),
                (
                    'ALTER INDEX IF EXISTS '
                    'chainage_reference_points_project_name_like_idx '
                    'RENAME TO chainage_reference_points_road_name_b9e4f5a7_like'
                ),
            ],
        ),
    ]
