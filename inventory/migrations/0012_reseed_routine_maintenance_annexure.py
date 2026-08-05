"""Widen RM category/subcategory names and reseeds from Annexure-I Excel."""

from django.db import migrations, models

from inventory.seed_routine_maintenance_masters import (
    RM_CATEGORY_SEED,
    RM_SUBCATEGORY_SEED,
)


def reseed_routine_maintenance_masters(apps, schema_editor):
    Category = apps.get_model('inventory', 'RoutineMaintenanceCategory')
    Subcategory = apps.get_model('inventory', 'RoutineMaintenanceSubcategory')

    desired_category_names = {name for _, name in RM_CATEGORY_SEED}
    desired_pairs = {
        (cat_name, sub_name)
        for cat_name, subs in RM_SUBCATEGORY_SEED.items()
        for sub_name in subs
    }

    for order, name in RM_CATEGORY_SEED:
        category, _ = Category.objects.update_or_create(
            name=name,
            defaults={'display_order': order, 'is_active': True},
        )
        for sub_order, sub_name in enumerate(RM_SUBCATEGORY_SEED.get(name, []), start=1):
            Subcategory.objects.update_or_create(
                category=category,
                name=sub_name,
                defaults={'display_order': sub_order, 'is_active': True},
            )

    # Deactivate leftover placeholder/shortened values from the initial seed.
    for category in Category.objects.all():
        if category.name not in desired_category_names:
            category.is_active = False
            category.save(update_fields=['is_active', 'updated_at'])
            Subcategory.objects.filter(category=category).update(is_active=False)
            continue

        for sub in Subcategory.objects.filter(category=category):
            if (category.name, sub.name) not in desired_pairs:
                sub.is_active = False
                sub.save(update_fields=['is_active', 'updated_at'])


def noop_reverse(apps, schema_editor):
    # Forward seed is additive/deactivating; no safe automatic restore of old short names.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('inventory', '0011_deactivate_pavement_inventory_categories'),
    ]

    operations = [
        migrations.AlterField(
            model_name='routinemaintenancecategory',
            name='name',
            field=models.CharField(max_length=500, unique=True),
        ),
        migrations.AlterField(
            model_name='routinemaintenancesubcategory',
            name='name',
            field=models.CharField(max_length=500),
        ),
        migrations.RunPython(reseed_routine_maintenance_masters, noop_reverse),
    ]
