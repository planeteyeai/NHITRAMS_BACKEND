"""Tests for PostgreSQL-backed inventory report APIs (Excel replacement)."""

from datetime import datetime, timedelta, timezone

import jwt
from django.test import TestCase, override_settings
from rest_framework import status
from rest_framework.test import APIClient

from authentication.jwt_utils import create_access_token
from inventory.models import InventoryReport
from inventory.serializers import InventoryReportWriteSerializer


SAMPLE_PAYLOAD = {
    'Project_Name': 'NH-48',
    'Chainage_start': 10.0,
    'Chainage_end': 10.5,
    'Direction': 'LHS',
    'Asset_type': 'Sign Boards',
    'Latitude': 19.076,
    'Longitude': 72.877,
    'Date': '2026-07-11',
    'Sub_Asset_Type': 'Gantry',
    'Carriage_Type': 'Flexible',
    'Lane': 'L1',
    'No_of_inventories': 2,
    'Image_URL': 'https://example.com/img.jpg',
    'Video_URL': 'https://example.com/video.mp4',
}


@override_settings(JWT_SECRET_KEY='test-jwt-secret')
class AppendInventoryExcelTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.token = create_access_token('ram.patil')
        self.auth = {'HTTP_AUTHORIZATION': f'Bearer {self.token}'}

    def test_append_saves_to_postgres_with_jwt_username(self):
        payload = {**SAMPLE_PAYLOAD, 'username': 'attacker.user'}
        response = self.client.post(
            '/append_inventory_excel/',
            payload,
            format='json',
            **self.auth,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['message'], 'Data appended successfully')

        report = InventoryReport.objects.get()
        self.assertEqual(report.username, 'ram.patil')
        self.assertEqual(report.Project_Name, 'NH-48')
        self.assertEqual(report.Asset_type, 'Sign Boards')
        self.assertEqual(report.Latitude, 19.076)
        self.assertEqual(report.No_of_inventories, 2)
        self.assertEqual(report.Video_URL, 'https://example.com/video.mp4')

    def test_append_requires_auth(self):
        response = self.client.post(
            '/append_inventory_excel/',
            SAMPLE_PAYLOAD,
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_append_validates_required_fields(self):
        response = self.client.post(
            '/append_inventory_excel/',
            {'Latitude': 1.0},
            format='json',
            **self.auth,
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('Project_Name', response.data)
        self.assertIn('Asset_type', response.data)

    def test_append_rejects_invalid_chainage(self):
        payload = {**SAMPLE_PAYLOAD, 'Chainage_start': 20.0, 'Chainage_end': 10.0}
        response = self.client.post(
            '/append_inventory_excel/',
            payload,
            format='json',
            **self.auth,
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_swagger_serializer_includes_video_url(self):
        fields = InventoryReportWriteSerializer().fields
        self.assertIn('Video_URL', fields)
        self.assertEqual(
            list(InventoryReportWriteSerializer.Meta.fields),
            [
                'Project_Name',
                'Chainage_start',
                'Chainage_end',
                'Direction',
                'Asset_type',
                'Latitude',
                'Longitude',
                'Date',
                'Sub_Asset_Type',
                'Carriage_Type',
                'Lane',
                'No_of_inventories',
                'Image_URL',
                'Video_URL',
            ],
        )


@override_settings(JWT_SECRET_KEY='test-jwt-secret')
class InventoryReportCRUDTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.token = create_access_token('ram.patil')
        self.other_token = create_access_token('other.user')
        self.auth = {'HTTP_AUTHORIZATION': f'Bearer {self.token}'}
        self.other_auth = {'HTTP_AUTHORIZATION': f'Bearer {self.other_token}'}

    def _create(self, username='ram.patil', **overrides):
        data = {**SAMPLE_PAYLOAD, **overrides}
        return InventoryReport.objects.create(username=username, **data)

    def test_history_returns_only_own_reports(self):
        self._create(username='ram.patil')
        self._create(username='other.user', Project_Name='Other')

        response = self.client.get('/inventory/history/', **self.auth)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['username'], 'ram.patil')
        self.assertEqual(response.data[0]['Project_Name'], 'NH-48')
        self.assertEqual(response.data[0]['Video_URL'], 'https://example.com/video.mp4')

    def test_history_requires_auth(self):
        response = self.client.get('/inventory/history/')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_get_own_report(self):
        report = self._create()
        response = self.client.get(f'/inventory/{report.id}/', **self.auth)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['id'], report.id)
        self.assertEqual(response.data['Asset_type'], 'Sign Boards')
        self.assertEqual(response.data['Video_URL'], 'https://example.com/video.mp4')

    def test_get_other_users_report_returns_403(self):
        report = self._create(username='other.user')
        response = self.client.get(f'/inventory/{report.id}/', **self.auth)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_delete_own_report(self):
        report = self._create()
        response = self.client.delete(f'/inventory/{report.id}/', **self.auth)
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(InventoryReport.objects.filter(pk=report.id).exists())

    def test_delete_other_users_report_returns_403(self):
        report = self._create(username='other.user')
        response = self.client.delete(f'/inventory/{report.id}/', **self.auth)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertTrue(InventoryReport.objects.filter(pk=report.id).exists())

    def test_missing_report_returns_404(self):
        response = self.client.get('/inventory/99999/', **self.auth)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_expired_token_returns_401(self):
        expired = jwt.encode(
            {
                'username': 'ram.patil',
                'exp': datetime.now(timezone.utc) - timedelta(hours=1),
            },
            'test-jwt-secret',
            algorithm='HS256',
        )
        response = self.client.get(
            '/inventory/history/',
            HTTP_AUTHORIZATION=f'Bearer {expired}',
        )
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_invalid_token_returns_401(self):
        response = self.client.get(
            '/inventory/history/',
            HTTP_AUTHORIZATION='Bearer not-a-valid-token',
        )
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
