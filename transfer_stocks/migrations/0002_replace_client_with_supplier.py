# Generated manually — replace Client with Supplier FK.

import django.db.models.deletion
from django.db import migrations, models


def clear_transfer_stocks(apps, schema_editor):
    TransferStock = apps.get_model('transfer_stocks', 'TransferStock')
    TransferStock.objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ('supplier', '0001_initial_supplier'),
        ('transfer_stocks', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(clear_transfer_stocks, migrations.RunPython.noop),
        migrations.RemoveIndex(
            model_name='transferstock',
            name='ts_date_client_idx',
        ),
        migrations.RemoveField(
            model_name='transferstock',
            name='client',
        ),
        migrations.DeleteModel(
            name='Client',
        ),
        migrations.AddField(
            model_name='transferstock',
            name='supplier',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name='transfer_stocks',
                to='supplier.supplier',
            ),
            preserve_default=False,
        ),
        migrations.AddIndex(
            model_name='transferstock',
            index=models.Index(fields=['date', 'supplier'], name='ts_date_supplier_idx'),
        ),
    ]
