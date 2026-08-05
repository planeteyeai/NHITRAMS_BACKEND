"""Tests for RAMS APK Inventory Report APIs."""

from unittest.mock import patch

from django.test import TestCase, override_settings
from rest_framework import status
from rest_framework.test import APIClient

from authentication.jwt_utils import create_access_token
from inventory.models import (
    InventoryCategory,
    InventoryReport,
    InventorySubcategory,
    SeverityMaster,
    SideMaster,
)

SAMPLE_PROJECT = {
    'id': 1,
    'project_name': 'Abu Road to Swaroopganj',
    'start_chainage': '646.000',
    'end_chainage': '680.000',
    'total_project_length': '34 km',
}


@override_settings(JWT_SECRET_KEY='test-jwt-secret')
class InventoryRamsAPITests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.token = create_access_token('ram.patil')
        self.auth = {'HTTP_AUTHORIZATION': f'Bearer {self.token}'}
        self.other_auth = {
            'HTTP_AUTHORIZATION': f'Bearer {create_access_token("other.user")}'
        }

        self.category = InventoryCategory.objects.get(name='Road Furniture')
        self.subcategory = InventorySubcategory.objects.filter(
            category=self.category, name='Kilometer Stones'
        ).first()
        self.side = SideMaster.objects.get(name='LHS L1')
        self.severity = SeverityMaster.objects.get(name='Low')

    def _payload(self, **overrides):
        data = {
            'project_name': SAMPLE_PROJECT['project_name'],
            'inventory_category_id': self.category.id,
            'inventory_subcategory_id': self.subcategory.id,
            'side_id': self.side.id,
            'severity_id': self.severity.id,
            'latitude': 24.5,
            'longitude': 72.8,
            'chainage': '650.200',
            'description_of_work': 'KM stone missing',
            'nos': 1,
            'length': 0,
            'width': 0,
            'depth': 0,
            'total_quantity': 1,
        }
        data.update(overrides)
        return data

    def test_master_requires_auth(self):
        response = self.client.get('/api/inventory/master')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    @patch(
        'inventory.services.inventory_report.list_inventory_projects',
        return_value=[{'project_name': SAMPLE_PROJECT['project_name']}],
    )
    def test_master_returns_nested_dropdowns(self, _mock):
        response = self.client.get('/api/inventory/master', **self.auth)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data['success'])
        data = response.data['data']
        self.assertIn('projects', data)
        self.assertIn('inventory_categories', data)
        self.assertIn('side_master', data)
        self.assertIn('severity_master', data)
        road = next(c for c in data['inventory_categories'] if c['name'] == 'Road Furniture')
        self.assertTrue(any(s['name'] == 'Kilometer Stones' for s in road['subcategories']))
        self.assertTrue(any(s['name'] == 'LHS L1' for s in data['side_master']))
        self.assertTrue(any(s['name'] == 'Low' for s in data['severity_master']))

    @patch(
        'inventory.serializers_inventory_report.resolve_project_details',
        return_value=SAMPLE_PROJECT,
    )
    def test_create_report(self, _mock):
        response = self.client.post(
            '/append_inventory_excel/',
            self._payload(username='attacker'),
            format='json',
            **self.auth,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data['success'])
        data = response.data['data']
        self.assertEqual(data['username'], 'ram.patil')
        self.assertEqual(data['project']['project_name'], 'Abu Road to Swaroopganj')
        self.assertEqual(data['inventory_category']['name'], 'Road Furniture')
        self.assertEqual(data['inventory_subcategory']['name'], 'Kilometer Stones')
        self.assertEqual(data['side']['name'], 'LHS L1')
        self.assertEqual(data['severity']['name'], 'Low')
        self.assertEqual(data['latitude'], 24.5)
        self.assertEqual(data['nos'], 1.0)

        report = InventoryReport.objects.get()
        self.assertEqual(report.username, 'ram.patil')
        self.assertEqual(report.Asset_type, 'Road Furniture')
        self.assertEqual(report.Sub_Asset_Type, 'Kilometer Stones')

    @patch(
        'inventory.serializers_inventory_report.resolve_project_details',
        return_value=SAMPLE_PROJECT,
    )
    def test_create_rejects_mismatched_subcategory(self, _mock):
        other = InventorySubcategory.objects.exclude(category=self.category).first()
        response = self.client.post(
            '/append_inventory_excel/',
            self._payload(inventory_subcategory_id=other.id),
            format='json',
            **self.auth,
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('inventory_subcategory_id', response.data['errors'])

    @patch(
        'inventory.serializers_inventory_report.resolve_project_details',
        return_value=SAMPLE_PROJECT,
    )
    @patch(
        'inventory.utils.upload_file_to_s3',
        side_effect=[
            'https://nhitbucket.s3.ap-south-1.amazonaws.com/Damaged%20Assets/a.jpg',
            'https://nhitbucket.s3.ap-south-1.amazonaws.com/Damaged%20Assets/b.mp4',
        ],
    )
    def test_create_with_image_and_video(self, _upload, _project):
        from django.core.files.uploadedfile import SimpleUploadedFile

        payload = self._payload()
        payload['image'] = SimpleUploadedFile('a.jpg', b'img', content_type='image/jpeg')
        payload['video'] = SimpleUploadedFile('b.mp4', b'vid', content_type='video/mp4')
        response = self.client.post(
            '/append_inventory_excel/',
            payload,
            format='multipart',
            **self.auth,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        report = InventoryReport.objects.get()
        self.assertIn('a.jpg', report.Image_URL)
        self.assertIn('b.mp4', report.Video_URL)

    @patch(
        'inventory.serializers_inventory_report.resolve_project_details',
        return_value=SAMPLE_PROJECT,
    )
    def test_history_and_detail(self, _mock):
        create = self.client.post(
            '/append_inventory_excel/',
            self._payload(),
            format='json',
            **self.auth,
        )
        report_id = create.data['data']['report_id']

        history = self.client.get('/inventory/history/', **self.auth)
        self.assertEqual(history.status_code, status.HTTP_200_OK)
        self.assertEqual(len(history.data['data']), 1)

        detail = self.client.get(f'/inventory/{report_id}/', **self.auth)
        self.assertEqual(detail.status_code, status.HTTP_200_OK)
        self.assertEqual(detail.data['data']['report_id'], report_id)

    @patch(
        'inventory.serializers_inventory_report.resolve_project_details',
        return_value=SAMPLE_PROJECT,
    )
    def test_patch_and_put(self, _mock):
        create = self.client.post(
            '/append_inventory_excel/',
            self._payload(),
            format='json',
            **self.auth,
        )
        report_id = create.data['data']['report_id']

        patch = self.client.patch(
            '/append_inventory_excel/',
            {'id': report_id, 'description_of_work': 'Updated', 'nos': 3},
            format='json',
            **self.auth,
        )
        self.assertEqual(patch.status_code, status.HTTP_200_OK)
        self.assertEqual(patch.data['data']['description_of_work'], 'Updated')
        self.assertEqual(patch.data['data']['nos'], 3.0)
        # project unchanged
        self.assertEqual(
            patch.data['data']['project']['project_name'],
            'Abu Road to Swaroopganj',
        )

        put = self.client.put(
            f'/inventory/{report_id}/',
            self._payload(description_of_work='Full replace', nos=2),
            format='json',
            **self.auth,
        )
        self.assertEqual(put.status_code, status.HTTP_200_OK)
        self.assertEqual(put.data['data']['description_of_work'], 'Full replace')

    @patch(
        'inventory.serializers_inventory_report.resolve_project_details',
        return_value=SAMPLE_PROJECT,
    )
    def test_ownership(self, _mock):
        create = self.client.post(
            '/append_inventory_excel/',
            self._payload(),
            format='json',
            **self.auth,
        )
        report_id = create.data['data']['report_id']
        response = self.client.patch(
            f'/inventory/{report_id}/',
            {'description_of_work': 'hack'},
            format='json',
            **self.other_auth,
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
