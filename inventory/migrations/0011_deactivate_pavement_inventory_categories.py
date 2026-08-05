from django.db import migrations

PAVEMENT_CATEGORY_NAMES = (
    'Flexible Pavement',
    'Rigid Pavement',
)


def deactivate_pavement_categories(apps, schema_editor):
    InventoryCategory = apps.get_model('inventory', 'InventoryCategory')
    InventorySubcategory = apps.get_model('inventory', 'InventorySubcategory')

    categories = InventoryCategory.objects.filter(name__in=PAVEMENT_CATEGORY_NAMES)
    InventorySubcategory.objects.filter(category__in=categories).update(is_active=False)
    categories.update(is_active=False)


def reactivate_pavement_categories(apps, schema_editor):
    InventoryCategory = apps.get_model('inventory', 'InventoryCategory')
    InventorySubcategory = apps.get_model('inventory', 'InventorySubcategory')

    categories = InventoryCategory.objects.filter(name__in=PAVEMENT_CATEGORY_NAMES)
    categories.update(is_active=True)
    InventorySubcategory.objects.filter(category__in=categories).update(is_active=True)


class Migration(migrations.Migration):

    dependencies = [
        ('inventory', '0010_inventorywide_unmanaged_state'),
    ]

    operations = [
        migrations.RunPython(
            deactivate_pavement_categories,
            reactivate_pavement_categories,
        ),
    ]
