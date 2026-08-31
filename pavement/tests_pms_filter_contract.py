from datetime import date

from django.test import SimpleTestCase, TestCase
from django.test import Client


class PmsFilterRequestValidationTests(TestCase):
    def setUp(self):
        self.client = Client()

    def test_missing_required_field_returns_400(self):
        payload = {
            'chainage_start': 0,
            'chainage_end': 1381,
            'direction': ['All'],
            'project_name': ['Abu Road to Swaroopganj'],
        }
        response = self.client.post('/pms_filter/', payload, content_type='application/json')
        self.assertEqual(response.status_code, 400)


class PmsFilterUnitTests(SimpleTestCase):
    def test_sub_chainage_assignment_and_field_names(self):
        from pavement.services.pms_filter_response import (
            _assign_sub_chainages,
            group_pms_rows,
            serialize_pms_row,
        )

        base = {
            'segment_id': 1,
            'segment__direction': 'Increasing',
            'segment__chainage_start': 646.0,
            'segment__chainage_end': 646.1,
            'date_val': date(2026, 4, 20),
            'date_of_survey': date(2026, 1, 4),
            'road_id': '',
            'latitude': 24.46675,
            'longitude': 72.78407,
            'aadt': 15027.0,
            'pavement_condition_score_pcs': '',
            'international_roughness_index_iri': '',
            'iri_index': '0-2',
            'roughness_index_status': 'Off carriageway maintenance',
            'commercial_vehicle_damage_cvd_class': '',
            'temperature_c': '',
            'rainfall_mm': 0.0,
            'drainage_condition': '',
            'type_of_last_maintenance': '',
            'last_maintenance_date': None,
            'carriageway_width': 'Double Lane',
            'project__project_name': 'Abu Road to Swaroopganj',
            'project__district': None,
            'project__block_taluka': None,
            'segment__carriage_type': '',
            'sum_chainage': 0.01,
        }
        increasing = dict(base, pavement_id=1, segment__direction='Increasing')
        decreasing = dict(base, pavement_id=2, segment_id=2, segment__direction='Decreasing')
        enriched = _assign_sub_chainages([increasing, decreasing])
        inc_row = serialize_pms_row(enriched[0])
        dec_row = serialize_pms_row(enriched[1])

        self.assertEqual(inc_row['chainage_start'], 646.0)
        self.assertEqual(inc_row['chainage_end'], 646.01)
        self.assertEqual(inc_row['date_of_survey'], '04-01-2026')
        self.assertEqual(inc_row['date'], '2026-04-20')
        self.assertIsNone(inc_row['road_id'])
        self.assertEqual(inc_row['carriage_type'], '')
        self.assertIn('pavement_condition_score_(pcs):', inc_row)
        self.assertIn('temperature_(°c):', inc_row)

        groups = group_pms_rows([inc_row, dec_row])
        self.assertEqual(len(groups), 1)
        self.assertEqual([row['direction'] for row in groups[0]], ['Increasing', 'Decreasing'])
