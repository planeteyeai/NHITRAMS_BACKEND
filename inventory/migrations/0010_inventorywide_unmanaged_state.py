# State-only: InventoryWide already exists as an unmanaged table.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('inventory', '0009_inventory_rams_apk_masters'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.CreateModel(
                    name='InventoryWide',
                    fields=[
                        ('inventory_id', models.BigIntegerField(primary_key=True, serialize=False)),
                        ('project_name', models.TextField(blank=True, db_column='Project Name', null=True)),
                        ('chainage_start', models.FloatField(blank=True, db_column='Chainage start', null=True)),
                        ('chainage_end', models.FloatField(blank=True, db_column='Chainage end', null=True)),
                        ('direction', models.TextField(blank=True, db_column='Direction', null=True)),
                        ('asset_type', models.TextField(blank=True, db_column='Asset type', null=True)),
                        ('trees', models.FloatField(db_column='Trees', default=0)),
                        ('culvert', models.FloatField(db_column='Culvert', default=0)),
                        ('street_lights', models.FloatField(db_column='Street Lights', default=0)),
                        ('bridges', models.FloatField(db_column='Bridges', default=0)),
                        ('traffic_signals', models.FloatField(db_column='Traffic Signals', default=0)),
                        ('bus_stop', models.FloatField(db_column='Bus Stop', default=0)),
                        ('truck_layby', models.FloatField(db_column='Truck LayBy', default=0)),
                        ('toll_plaza', models.FloatField(db_column='Toll Plaza', default=0)),
                        ('adjacent_road', models.FloatField(db_column='Adjacent Road', default=0)),
                        ('toilet_blocks', models.FloatField(db_column='Toilet Blocks', default=0)),
                        ('rest_area', models.FloatField(db_column='Rest Area', default=0)),
                        ('rcc_drain', models.FloatField(db_column='RCC Drain', default=0)),
                        ('fuel_station', models.FloatField(db_column='Fuel Station', default=0)),
                        ('emergency_call_box', models.FloatField(db_column='Emergency Call Box', default=0)),
                        ('tunnels', models.FloatField(db_column='Tunnels', default=0)),
                        ('footpath', models.FloatField(db_column='Footpath', default=0)),
                        ('junction', models.FloatField(db_column='Junction', default=0)),
                        ('sign_boards', models.FloatField(db_column='Sign Boards', default=0)),
                        ('solar_blinker', models.FloatField(db_column='Solar Blinker', default=0)),
                        ('median_plants', models.FloatField(db_column='Median Plants', default=0)),
                        ('service_road', models.FloatField(db_column='Service Road', default=0)),
                        ('km_stones', models.FloatField(db_column='KM Stones', default=0)),
                        ('crash_barrier', models.FloatField(db_column='Crash Barrier', default=0)),
                        ('median_opening', models.FloatField(db_column='Median Opening', default=0)),
                        ('latitude', models.FloatField(blank=True, db_column='Latitude', null=True)),
                        ('longitude', models.FloatField(blank=True, db_column='Longitude', null=True)),
                        ('survey_date', models.DateField(blank=True, db_column='Date', null=True)),
                        ('sub_asset_type', models.TextField(blank=True, db_column='Sub Asset Type', null=True)),
                        ('carriage_type', models.TextField(blank=True, db_column='Carriage Type', null=True)),
                        ('lane', models.TextField(blank=True, db_column='Lane', null=True)),
                        ('kerb', models.FloatField(db_column='Kerb', default=0)),
                        ('guard_post', models.FloatField(db_column='Guard Post', default=0)),
                        ('delineators', models.FloatField(db_column='Delineators', default=0)),
                        ('pedestrian_guard_rail', models.FloatField(db_column='Pedestrian Guard Rail', default=0)),
                        ('slip_roads', models.FloatField(db_column='Slip Roads', default=0)),
                        ('bus_bays', models.FloatField(db_column='Bus Bays', default=0)),
                        ('image_url', models.TextField(blank=True, db_column='Image URL', null=True)),
                        ('source', models.TextField(blank=True, db_column='Source', null=True)),
                    ],
                    options={
                        'db_table': 'inventory_wide',
                        'managed': False,
                    },
                ),
            ],
            database_operations=[],
        ),
    ]
