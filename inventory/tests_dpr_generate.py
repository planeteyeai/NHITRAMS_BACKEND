"""Tests for Asset Change Detection and Monitoring DPR generate API."""

from datetime import datetime, timezone as dt_timezone
from unittest.mock import patch

from django.test import TestCase, override_settings
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIClient

from authentication.jwt_utils import create_access_token
from inventory.models import (
    InventoryCategory,
    InventoryReport,
    InventorySubcategory,
    RoutineMaintenanceCategory,
    RoutineMaintenanceReport,
    RoutineMaintenanceSubcategory,
    SeverityMaster,
    SideMaster,
)
from inventory.services.dpr_generate import (
    build_overall_summary,
    calculate_unit,
    generate_photo_references,
)
from inventory.serializers_dpr_generate import AssetChangeDPRGenerateRequestSerializer

SAMPLE_PROJECT = {
    'id': 1,
    'project_name': 'Abu Road to Swaroopganj',
    'start_chainage': '646.000',
    'end_chainage': '680.000',
    'total_project_length': '34 km',
}


@override_settings(JWT_SECRET_KEY='test-jwt-secret')
class AssetChangeDPRGenerateTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.token = create_access_token('ram.patil')
        self.auth = {'HTTP_AUTHORIZATION': f'Bearer {self.token}'}

        self.category = InventoryCategory.objects.get(name='Traffic Signs')
        self.subcategory = InventorySubcategory.objects.get(
            category=self.category, name='Informatory Signs'
        )
        self.side = SideMaster.objects.get(name='LHS L1')
        self.severity = SeverityMaster.objects.get(name='Low')

        self.rm_category = RoutineMaintenanceCategory.objects.get(
            name='Median Maintenance', is_active=True
        )
        self.rm_subcategory = RoutineMaintenanceSubcategory.objects.get(
            category=self.rm_category,
            name='Trimming, Pruning of Median plants',
            is_active=True,
        )

    def _request_body(self, **overrides):
        data = {
            'project_name': SAMPLE_PROJECT['project_name'],
            'reporting_engineer_name': 'Ram Patil',
            'assessment_date': '2026-08-05',
            'from_date': '2026-08-01',
            'to_date': '2026-08-05',
        }
        data.update(overrides)
        return data

    def _create_inventory_report(self, **overrides):
        defaults = {
            'username': 'ram.patil',
            'project_name': SAMPLE_PROJECT['project_name'],
            'project_id': 1,
            'start_chainage': SAMPLE_PROJECT['start_chainage'],
            'end_chainage': SAMPLE_PROJECT['end_chainage'],
            'total_project_length': SAMPLE_PROJECT['total_project_length'],
            'inventory_category': self.category,
            'inventory_subcategory': self.subcategory,
            'side': self.side,
            'severity': self.severity,
            'latitude': 24.5,
            'longitude': 72.8,
            'chainage': '650.200',
            'inspection_datetime': datetime(2026, 8, 5, 10, 0, tzinfo=dt_timezone.utc),
            'description_of_work': 'Damaged sign',
            'nos': 1,
            'length': 0,
            'width': 0,
            'depth': 0,
            'total_quantity': 1,
            'Image_URL': 'https://example.com/a.jpg;https://example.com/b.jpg',
        }
        defaults.update(overrides)
        return InventoryReport.objects.create(**defaults)

    def _create_rm_report(self, **overrides):
        defaults = {
            'username': 'ram.patil',
            'project_id': 1,
            'project_name': SAMPLE_PROJECT['project_name'],
            'start_chainage': SAMPLE_PROJECT['start_chainage'],
            'end_chainage': SAMPLE_PROJECT['end_chainage'],
            'total_project_length': SAMPLE_PROJECT['total_project_length'],
            'category': self.rm_category,
            'subcategory': self.rm_subcategory,
            'side': self.side,
            'severity': self.severity,
            'latitude': 24.51,
            'longitude': 72.81,
            'chainage': '651.000',
            'inspection_datetime': datetime(2026, 8, 5, 11, 0, tzinfo=dt_timezone.utc),
            'description_of_work': 'Trimmed plants',
            'nos': 0,
            'length': 10,
            'width': 0,
            'depth': 0,
            'total_quantity': 10,
            'uploaded_image_urls': ['https://example.com/rm1.jpg'],
        }
        defaults.update(overrides)
        return RoutineMaintenanceReport.objects.create(**defaults)

    def test_requires_auth(self):
        response = self.client.post(
            '/api/inventory/dpr/generate/',
            self._request_body(),
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_calculate_unit_helpers(self):
        self.assertEqual(calculate_unit(nos=2), 'Nos')
        self.assertEqual(calculate_unit(length=5), 'RMT')
        self.assertEqual(calculate_unit(length=5, width=2), 'm²')
        self.assertEqual(calculate_unit(length=5, width=2, depth=1), 'm³')
        self.assertEqual(
            generate_photo_references(2, start_at=3),
            ['Refer Image No. 3', 'Refer Image No. 4'],
        )

    def test_from_date_after_to_date_rejected(self):
        serializer = AssetChangeDPRGenerateRequestSerializer(
            data=self._request_body(from_date='2026-08-10', to_date='2026-08-01')
        )
        self.assertFalse(serializer.is_valid())
        self.assertIn('to_date', serializer.errors)

    def test_range_over_31_days_rejected(self):
        serializer = AssetChangeDPRGenerateRequestSerializer(
            data=self._request_body(from_date='2026-07-01', to_date='2026-08-05')
        )
        self.assertFalse(serializer.is_valid())
        self.assertIn('to_date', serializer.errors)

    def test_missing_from_to_rejected(self):
        response = self.client.post(
            '/api/inventory/dpr/generate/',
            {
                'project_name': SAMPLE_PROJECT['project_name'],
                'reporting_engineer_name': 'Ram Patil',
                'assessment_date': '2026-08-05',
            },
            format='json',
            **self.auth,
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('from_date', response.data['errors'])
        self.assertIn('to_date', response.data['errors'])

    @patch(
        'inventory.services.dpr_generate._matching_project_ids',
        return_value=[1],
    )
    @patch(
        'inventory.services.dpr_generate._matching_project_names',
        return_value=[SAMPLE_PROJECT['project_name']],
    )
    @patch(
        'inventory.services.dpr_generate.resolve_project_details',
        return_value=SAMPLE_PROJECT,
    )
    @patch(
        'inventory.services.dpr_generate._master_quantity_for_subcategory',
        return_value=1000.0,
    )
    def test_generate_full_payload(self, _mock_qty, _mock_project, _mock_names, _mock_ids):
        earlier = self._create_inventory_report(
            chainage='649.100',
            inspection_datetime=datetime(2026, 8, 4, 9, 0, tzinfo=dt_timezone.utc),
            Image_URL='https://example.com/only.jpg',
            total_quantity=2,
        )
        later = self._create_inventory_report(
            chainage='650.200',
            total_quantity=3,
        )
        # Outside selected period — must be excluded.
        self._create_inventory_report(
            chainage='648.000',
            inspection_datetime=datetime(2026, 7, 20, 9, 0, tzinfo=dt_timezone.utc),
            total_quantity=9,
            Image_URL='',
        )
        self._create_rm_report()

        response = self.client.post(
            '/api/inventory/dpr/generate/',
            self._request_body(),
            format='json',
            **self.auth,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data['success'])
        self.assertEqual(response.data['message'], 'DPR generated successfully.')

        data = response.data['data']
        details = data['project_details']
        self.assertEqual(details['project_name'], 'Abu Road to Swaroopganj')
        self.assertEqual(details['start_chainage'], '646.000')
        self.assertEqual(details['end_chainage'], '680.000')
        self.assertEqual(details['total_project_length'], '34 km')
        self.assertEqual(details['reporting_engineer_name'], 'Ram Patil')
        self.assertEqual(details['assessment_date'], '2026-08-05')
        self.assertEqual(details['assessment_date_display'], '05/08/2026')
        self.assertEqual(details['from_date'], '2026-08-01')
        self.assertEqual(details['to_date'], '2026-08-05')
        self.assertEqual(details['report_period_display'], '01/08/2026 - 05/08/2026')
        self.assertEqual(details['generated_by'], 'ram.patil')
        self.assertTrue(details['generation_date'])

        summary = data['overall_summary']
        self.assertEqual(len(summary), 1)
        self.assertEqual(summary[0]['asset_type'], 'Informatory Signs')
        self.assertEqual(summary[0]['total_quantity'], 1000.0)
        self.assertEqual(summary[0]['damaged_quantity'], 5.0)
        self.assertEqual(summary[0]['available_quantity'], 995.0)

        assets = data['removed_damaged_assets']
        self.assertEqual(len(assets), 2)
        self.assertEqual(assets[0]['chainage'], earlier.chainage)
        self.assertEqual(assets[1]['chainage'], later.chainage)
        self.assertEqual(assets[0]['asset_type'], 'Informatory Signs')
        self.assertEqual(assets[0]['unit'], 'Nos')
        self.assertEqual(assets[0]['evidence_available'], 'Refer Image No. 1')
        self.assertEqual(
            assets[1]['evidence_available'],
            'Refer Image No. 2, Refer Image No. 3',
        )

        photos = data['removed_damaged_asset_photos']
        self.assertEqual(len(photos), 3)
        self.assertEqual(photos[0]['image_label'], 'Image 1')
        self.assertEqual(photos[0]['photo_url'], 'https://example.com/only.jpg')
        self.assertEqual(photos[2]['image_number'], 3)

        rm = data['routine_maintenance']
        self.assertEqual(len(rm), 1)
        self.assertEqual(rm[0]['maintenance_type'], 'Trimming, Pruning of Median plants')
        self.assertEqual(rm[0]['unit'], 'RMT')
        self.assertEqual(rm[0]['evidence_available'], 'Refer Image No. 1')

        rm_photos = data['routine_maintenance_photos']
        self.assertEqual(len(rm_photos), 1)
        self.assertEqual(rm_photos[0]['image_label'], 'Image 1')

    @patch(
        'inventory.services.dpr_generate._matching_project_ids',
        return_value=[1],
    )
    @patch(
        'inventory.services.dpr_generate._matching_project_names',
        return_value=[SAMPLE_PROJECT['project_name']],
    )
    @patch(
        'inventory.services.dpr_generate.resolve_project_details',
        return_value=SAMPLE_PROJECT,
    )
    @patch(
        'inventory.services.dpr_generate._master_quantity_for_subcategory',
        return_value=1000.0,
    )
    def test_single_day_report(self, _mock_qty, _mock_project, _mock_names, _mock_ids):
        self._create_inventory_report(
            inspection_datetime=datetime(2026, 8, 5, 10, 0, tzinfo=dt_timezone.utc),
            total_quantity=2,
            Image_URL='',
        )
        self._create_inventory_report(
            inspection_datetime=datetime(2026, 8, 4, 10, 0, tzinfo=dt_timezone.utc),
            total_quantity=7,
            Image_URL='',
        )
        response = self.client.post(
            '/api/inventory/dpr/generate/',
            self._request_body(from_date='2026-08-05', to_date='2026-08-05'),
            format='json',
            **self.auth,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        details = response.data['data']['project_details']
        self.assertEqual(details['report_period_display'], '05/08/2026 - 05/08/2026')
        self.assertEqual(len(response.data['data']['removed_damaged_assets']), 1)
        self.assertEqual(response.data['data']['overall_summary'][0]['damaged_quantity'], 2.0)

    @patch(
        'inventory.services.dpr_generate._matching_project_ids',
        return_value=[1],
    )
    @patch(
        'inventory.services.dpr_generate._matching_project_names',
        return_value=[SAMPLE_PROJECT['project_name']],
    )
    @patch(
        'inventory.services.dpr_generate.resolve_project_details',
        return_value=SAMPLE_PROJECT,
    )
    def test_empty_project_returns_empty_sections(self, _mock_project, _mock_names, _mock_ids):
        response = self.client.post(
            '/api/inventory/dpr/generate/',
            self._request_body(),
            format='json',
            **self.auth,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.data['data']
        self.assertEqual(data['overall_summary'], [])
        self.assertEqual(data['removed_damaged_assets'], [])
        self.assertEqual(data['removed_damaged_asset_photos'], [])
        self.assertEqual(data['routine_maintenance'], [])
        self.assertEqual(data['routine_maintenance_photos'], [])
        self.assertEqual(data['project_details']['assessment_date_display'], '05/08/2026')
        self.assertEqual(data['project_details']['report_period_display'], '01/08/2026 - 05/08/2026')

    @patch(
        'inventory.services.dpr_generate.resolve_project_details',
        side_effect=ValidationError({'project_name': ['Invalid project selected.']}),
    )
    def test_invalid_project(self, _mock_project):
        response = self.client.post(
            '/api/inventory/dpr/generate/',
            self._request_body(project_name='Does Not Exist Project XYZ'),
            format='json',
            **self.auth,
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(response.data['success'])

    def test_missing_engineer(self):
        response = self.client.post(
            '/api/inventory/dpr/generate/',
            self._request_body(reporting_engineer_name=''),
            format='json',
            **self.auth,
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    @patch(
        'inventory.services.dpr_generate._matching_project_ids',
        return_value=[1],
    )
    @patch(
        'inventory.services.dpr_generate._matching_project_names',
        return_value=[SAMPLE_PROJECT['project_name']],
    )
    @patch(
        'inventory.services.dpr_generate.resolve_project_details',
        return_value=SAMPLE_PROJECT,
    )
    @patch(
        'inventory.services.dpr_generate._master_quantity_for_subcategory',
        return_value=1000.0,
    )
    def test_generate_includes_only_jwt_username_reports(
        self, _mock_qty, _mock_project, _mock_names, _mock_ids
    ):
        own = self._create_inventory_report(username='ram.patil', total_quantity=2, Image_URL='')
        self._create_inventory_report(username='other.user', total_quantity=99, Image_URL='')
        self._create_inventory_report(username='76', total_quantity=50, Image_URL='')
        self._create_rm_report(username='ram.patil')
        self._create_rm_report(username='other.user')

        response = self.client.post(
            '/api/inventory/dpr/generate/',
            self._request_body(),
            format='json',
            **self.auth,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.data['data']
        assets = data['removed_damaged_assets']
        self.assertEqual(len(assets), 1)
        self.assertEqual(assets[0]['chainage'], own.chainage)
        self.assertEqual(data['overall_summary'][0]['damaged_quantity'], 2.0)
        self.assertEqual(len(data['routine_maintenance']), 1)
        self.assertEqual(data['project_details']['generated_by'], 'ram.patil')

    def test_build_overall_summary_only_reported_assets(self):
        report = self._create_inventory_report(total_quantity=4)
        with patch(
            'inventory.services.dpr_generate._matching_project_ids',
            return_value=[1],
        ), patch(
            'inventory.services.dpr_generate._master_quantity_for_subcategory',
            return_value=100.0,
        ):
            summary = build_overall_summary([report], SAMPLE_PROJECT['project_name'])
        self.assertEqual(len(summary), 1)
        self.assertEqual(summary[0]['damaged_quantity'], 4.0)
        self.assertEqual(summary[0]['available_quantity'], 96.0)
