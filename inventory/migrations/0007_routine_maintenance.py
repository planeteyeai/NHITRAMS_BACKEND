# Generated manually for Routine Maintenance masters + reports.

from django.db import migrations, models
import django.db.models.deletion


CATEGORY_SEED = [
    (1, 'Median Maintenance'),
    (2, 'Road Side Maintenance / RoW (Avenue till boundary pillar)'),
    (3, 'Maintenance and Watering of Existing Plants'),
    (4, 'Cleaning of Road Furniture'),
    (5, 'Cleaning of Road'),
    (6, 'Cleaning of Drains'),
    (7, 'Cleaning of Toll Plaza and Booths'),
    (8, 'Cleaning of Waterway of Structures'),
    (9, 'Safety During Service'),
]

SUBCATEGORY_SEED = {
    'Median Maintenance': [
        'Trimming, Pruning of Median Plants',
        'Cleaning of Median',
        'Watering of Median Plants',
        'Replacement of Dead Plants in Median',
        'Maintenance of Median Kerbs',
    ],
    'Road Side Maintenance / RoW (Avenue till boundary pillar)': [
        'Trimming of Avenue Trees',
        'Cleaning of RoW',
        'Removal of Encroachment / Debris',
        'Maintenance of Boundary Pillars',
        'Bush Cutting along RoW',
    ],
    'Maintenance and Watering of Existing Plants': [
        'Watering of Plants',
        'Replacement of Dead Plants',
        'Manuring / Fertilizer Application',
        'Staking / Supporting of Plants',
    ],
    'Cleaning of Road Furniture': [
        'Cleaning of Sign Boards',
        'Cleaning of Crash Barriers',
        'Cleaning of KM Stones',
        'Cleaning of Delineators / Guard Posts',
        'Cleaning of Street Lights / Solar Blinkers',
    ],
    'Cleaning of Road': [
        'Sweeping of Carriageway',
        'Removal of Debris / Dead Animals',
        'Cleaning of Junctions',
        'Removal of Spillage',
    ],
    'Cleaning of Drains': [
        'Cleaning of RCC Drains',
        'Cleaning of Earthen Drains',
        'Desilting of Drains',
        'Removal of Blockages in Drains',
    ],
    'Cleaning of Toll Plaza and Booths': [
        'Cleaning of Toll Plaza Area',
        'Cleaning of Toll Booths',
        'Cleaning of Approach Roads to Toll Plaza',
        'Cleaning of Toll Plaza Drainage',
    ],
    'Cleaning of Waterway of Structures': [
        'Cleaning of Culvert Waterway',
        'Cleaning of Bridge Waterway',
        'Removal of Vegetation from Waterway',
        'Removal of Silt / Debris from Waterway',
    ],
    'Safety During Service': [
        'Placement of Traffic Cones / Barricades',
        'Deployment of Flagmen',
        'Installation of Caution Boards',
        'Temporary Diversion Arrangements',
    ],
}

SIDE_SEED = ['LHS L1', 'LHS L2', 'RHS L1', 'RHS L2']
SEVERITY_SEED = ['Low', 'Medium', 'High']


def seed_masters(apps, schema_editor):
    Category = apps.get_model('inventory', 'RoutineMaintenanceCategory')
    Subcategory = apps.get_model('inventory', 'RoutineMaintenanceSubcategory')
    Side = apps.get_model('inventory', 'SideMaster')
    Severity = apps.get_model('inventory', 'SeverityMaster')

    for order, name in CATEGORY_SEED:
        category, _ = Category.objects.get_or_create(
            name=name,
            defaults={'display_order': order, 'is_active': True},
        )
        if category.display_order != order:
            category.display_order = order
            category.save(update_fields=['display_order', 'updated_at'])

        for sub_order, sub_name in enumerate(SUBCATEGORY_SEED.get(name, []), start=1):
            Subcategory.objects.get_or_create(
                category=category,
                name=sub_name,
                defaults={'display_order': sub_order, 'is_active': True},
            )

    for order, name in enumerate(SIDE_SEED, start=1):
        Side.objects.get_or_create(
            name=name,
            defaults={'display_order': order, 'is_active': True},
        )

    for order, name in enumerate(SEVERITY_SEED, start=1):
        Severity.objects.get_or_create(
            name=name,
            defaults={'display_order': order, 'is_active': True},
        )


def unseed_masters(apps, schema_editor):
    Category = apps.get_model('inventory', 'RoutineMaintenanceCategory')
    Side = apps.get_model('inventory', 'SideMaster')
    Severity = apps.get_model('inventory', 'SeverityMaster')
    Category.objects.filter(name__in=[name for _, name in CATEGORY_SEED]).delete()
    Side.objects.filter(name__in=SIDE_SEED).delete()
    Severity.objects.filter(name__in=SEVERITY_SEED).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('inventory', '0006_dprreport_overall_summary'),
    ]

    operations = [
        migrations.CreateModel(
            name='RoutineMaintenanceCategory',
            fields=[
                ('id', models.BigAutoField(primary_key=True, serialize=False)),
                ('name', models.CharField(max_length=255, unique=True)),
                ('display_order', models.PositiveIntegerField(db_index=True, default=0)),
                ('is_active', models.BooleanField(db_index=True, default=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'db_table': 'routine_maintenance_category',
                'ordering': ['display_order', 'name'],
            },
        ),
        migrations.CreateModel(
            name='SideMaster',
            fields=[
                ('id', models.BigAutoField(primary_key=True, serialize=False)),
                ('name', models.CharField(max_length=100, unique=True)),
                ('display_order', models.PositiveIntegerField(db_index=True, default=0)),
                ('is_active', models.BooleanField(db_index=True, default=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'db_table': 'side_master',
                'ordering': ['display_order', 'name'],
            },
        ),
        migrations.CreateModel(
            name='SeverityMaster',
            fields=[
                ('id', models.BigAutoField(primary_key=True, serialize=False)),
                ('name', models.CharField(max_length=100, unique=True)),
                ('display_order', models.PositiveIntegerField(db_index=True, default=0)),
                ('is_active', models.BooleanField(db_index=True, default=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'db_table': 'severity_master',
                'ordering': ['display_order', 'name'],
            },
        ),
        migrations.CreateModel(
            name='RoutineMaintenanceSubcategory',
            fields=[
                ('id', models.BigAutoField(primary_key=True, serialize=False)),
                ('name', models.CharField(max_length=255)),
                ('display_order', models.PositiveIntegerField(db_index=True, default=0)),
                ('is_active', models.BooleanField(db_index=True, default=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                (
                    'category',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='subcategories',
                        to='inventory.routinemaintenancecategory',
                    ),
                ),
            ],
            options={
                'db_table': 'routine_maintenance_subcategory',
                'ordering': ['display_order', 'name'],
            },
        ),
        migrations.CreateModel(
            name='RoutineMaintenanceReport',
            fields=[
                ('id', models.BigAutoField(primary_key=True, serialize=False)),
                ('username', models.CharField(db_index=True, max_length=150)),
                ('project_name', models.CharField(max_length=255)),
                ('start_chainage', models.CharField(max_length=100)),
                ('end_chainage', models.CharField(max_length=100)),
                ('total_project_length', models.CharField(max_length=100)),
                ('latitude', models.FloatField()),
                ('longitude', models.FloatField()),
                ('chainage', models.CharField(max_length=100)),
                ('inspection_datetime', models.DateTimeField()),
                ('description_of_work', models.TextField(blank=True, default='')),
                ('nos', models.FloatField(default=0)),
                ('length', models.FloatField(default=0)),
                ('width', models.FloatField(default=0)),
                ('depth', models.FloatField(default=0)),
                ('total_quantity', models.FloatField(default=0)),
                ('uploaded_image_urls', models.JSONField(blank=True, default=list)),
                (
                    'status',
                    models.CharField(
                        choices=[
                            ('draft', 'Draft'),
                            ('submitted', 'Submitted'),
                            ('approved', 'Approved'),
                            ('rejected', 'Rejected'),
                        ],
                        db_index=True,
                        default='submitted',
                        max_length=32,
                    ),
                ),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                (
                    'category',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name='reports',
                        to='inventory.routinemaintenancecategory',
                    ),
                ),
                (
                    'severity',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name='reports',
                        to='inventory.severitymaster',
                    ),
                ),
                (
                    'side',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name='reports',
                        to='inventory.sidemaster',
                    ),
                ),
                (
                    'subcategory',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name='reports',
                        to='inventory.routinemaintenancesubcategory',
                    ),
                ),
            ],
            options={
                'db_table': 'routine_maintenance_reports',
                'ordering': ['-created_at'],
            },
        ),
        migrations.AddConstraint(
            model_name='routinemaintenancesubcategory',
            constraint=models.UniqueConstraint(
                fields=('category', 'name'),
                name='rm_subcat_unique_name_per_category',
            ),
        ),
        migrations.AddIndex(
            model_name='routinemaintenancereport',
            index=models.Index(fields=['username', '-created_at'], name='rm_rpt_user_created_idx'),
        ),
        migrations.RunPython(seed_masters, unseed_masters),
    ]
