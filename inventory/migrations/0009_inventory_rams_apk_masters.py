# Generated manually for RAMS APK Inventory masters + report fields.

import django.db.models.deletion
from django.db import migrations, models

from inventory.seed_inventory_masters import (
    INVENTORY_CATEGORY_SEED,
    INVENTORY_SEVERITY_SEED,
    INVENTORY_SIDE_SEED,
    INVENTORY_SUBCATEGORY_SEED,
)


def seed_inventory_masters(apps, schema_editor):
    InventoryCategory = apps.get_model('inventory', 'InventoryCategory')
    InventorySubcategory = apps.get_model('inventory', 'InventorySubcategory')
    SideMaster = apps.get_model('inventory', 'SideMaster')
    SeverityMaster = apps.get_model('inventory', 'SeverityMaster')

    for order, name in INVENTORY_CATEGORY_SEED:
        InventoryCategory.objects.update_or_create(
            name=name,
            defaults={'display_order': order, 'is_active': True},
        )

    for category_name, sub_names in INVENTORY_SUBCATEGORY_SEED.items():
        category = InventoryCategory.objects.get(name=category_name)
        for order, sub_name in enumerate(sub_names, start=1):
            InventorySubcategory.objects.update_or_create(
                category=category,
                name=sub_name,
                defaults={'display_order': order, 'is_active': True},
            )

    for order, name in enumerate(INVENTORY_SIDE_SEED, start=1):
        SideMaster.objects.update_or_create(
            name=name,
            defaults={'display_order': order, 'is_active': True},
        )

    for order, name in enumerate(INVENTORY_SEVERITY_SEED, start=1):
        SeverityMaster.objects.update_or_create(
            name=name,
            defaults={'display_order': order, 'is_active': True},
        )


def unseed_inventory_masters(apps, schema_editor):
    InventoryCategory = apps.get_model('inventory', 'InventoryCategory')
    InventorySubcategory = apps.get_model('inventory', 'InventorySubcategory')
    InventorySubcategory.objects.all().delete()
    InventoryCategory.objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ('inventory', '0008_rm_report_project_id'),
    ]

    operations = [
        migrations.CreateModel(
            name='InventoryCategory',
            fields=[
                ('id', models.BigAutoField(primary_key=True, serialize=False)),
                ('name', models.CharField(max_length=255, unique=True)),
                ('display_order', models.PositiveIntegerField(db_index=True, default=0)),
                ('is_active', models.BooleanField(db_index=True, default=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'db_table': 'inventory_category',
                'ordering': ['display_order', 'name'],
            },
        ),
        migrations.CreateModel(
            name='InventorySubcategory',
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
                        to='inventory.inventorycategory',
                    ),
                ),
            ],
            options={
                'db_table': 'inventory_subcategory',
                'ordering': ['display_order', 'name'],
            },
        ),
        migrations.AddConstraint(
            model_name='inventorysubcategory',
            constraint=models.UniqueConstraint(
                fields=('category', 'name'),
                name='inv_subcat_unique_name_per_category',
            ),
        ),
        migrations.AddField(
            model_name='inventoryreport',
            name='project_id',
            field=models.IntegerField(blank=True, db_index=True, null=True),
        ),
        migrations.AddField(
            model_name='inventoryreport',
            name='project_name',
            field=models.CharField(blank=True, default='', max_length=255),
        ),
        migrations.AddField(
            model_name='inventoryreport',
            name='start_chainage',
            field=models.CharField(blank=True, default='', max_length=100),
        ),
        migrations.AddField(
            model_name='inventoryreport',
            name='end_chainage',
            field=models.CharField(blank=True, default='', max_length=100),
        ),
        migrations.AddField(
            model_name='inventoryreport',
            name='total_project_length',
            field=models.CharField(blank=True, default='', max_length=100),
        ),
        migrations.AddField(
            model_name='inventoryreport',
            name='inventory_category',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name='reports',
                to='inventory.inventorycategory',
            ),
        ),
        migrations.AddField(
            model_name='inventoryreport',
            name='inventory_subcategory',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name='reports',
                to='inventory.inventorysubcategory',
            ),
        ),
        migrations.AddField(
            model_name='inventoryreport',
            name='side',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name='inventory_reports',
                to='inventory.sidemaster',
            ),
        ),
        migrations.AddField(
            model_name='inventoryreport',
            name='severity',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name='inventory_reports',
                to='inventory.severitymaster',
            ),
        ),
        migrations.AddField(
            model_name='inventoryreport',
            name='latitude',
            field=models.FloatField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='inventoryreport',
            name='longitude',
            field=models.FloatField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='inventoryreport',
            name='chainage',
            field=models.CharField(blank=True, default='', max_length=100),
        ),
        migrations.AddField(
            model_name='inventoryreport',
            name='inspection_datetime',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='inventoryreport',
            name='description_of_work',
            field=models.TextField(blank=True, default=''),
        ),
        migrations.AddField(
            model_name='inventoryreport',
            name='nos',
            field=models.FloatField(default=0),
        ),
        migrations.AddField(
            model_name='inventoryreport',
            name='length',
            field=models.FloatField(default=0),
        ),
        migrations.AddField(
            model_name='inventoryreport',
            name='width',
            field=models.FloatField(default=0),
        ),
        migrations.AddField(
            model_name='inventoryreport',
            name='depth',
            field=models.FloatField(default=0),
        ),
        migrations.AddField(
            model_name='inventoryreport',
            name='total_quantity',
            field=models.FloatField(default=0),
        ),
        migrations.AlterField(
            model_name='inventoryreport',
            name='Project_Name',
            field=models.CharField(blank=True, default='', max_length=255),
        ),
        migrations.AlterField(
            model_name='inventoryreport',
            name='Chainage_start',
            field=models.FloatField(blank=True, default=0, null=True),
        ),
        migrations.AlterField(
            model_name='inventoryreport',
            name='Chainage_end',
            field=models.FloatField(blank=True, default=0, null=True),
        ),
        migrations.AlterField(
            model_name='inventoryreport',
            name='Direction',
            field=models.CharField(blank=True, default='', max_length=100),
        ),
        migrations.AlterField(
            model_name='inventoryreport',
            name='Asset_type',
            field=models.CharField(blank=True, default='', max_length=255),
        ),
        migrations.AlterField(
            model_name='inventoryreport',
            name='Latitude',
            field=models.FloatField(blank=True, default=0, null=True),
        ),
        migrations.AlterField(
            model_name='inventoryreport',
            name='Longitude',
            field=models.FloatField(blank=True, default=0, null=True),
        ),
        migrations.AlterField(
            model_name='inventoryreport',
            name='Date',
            field=models.CharField(blank=True, default='', max_length=50),
        ),
        migrations.RunPython(seed_inventory_masters, unseed_inventory_masters),
    ]
