"""Tests for PostgreSQL-backed distress report APIs (Excel replacement)."""

from unittest.mock import patch

from django.test import TestCase, override_settings
from rest_framework import status
from rest_framework.test import APIClient

from authentication.jwt_utils import create_access_token
from distress.models import DistressReport


SAMPLE_PAYLOAD = {
    'Latitude': 19.076,
    'Longitude': 72.877,
    'Chainage_Start': 10.0,
    'Chainage_End': 10.5,
    'Project_Name': 'NH-48',
    'Distress_Type': 'Pothole',
    'Direction': 'LHS',
    'Date': '2026-07-11',
    'Length': 1.2,
    'Carriage_Type': 'Flexible',
    'Width': 0.5,
    'Depth': 0.1,
    'Lane': 'L1',
    'No_of_Distress': 1,
    'Image_URL': 'https://example.com/img.jpg',
}


@override_settings(JWT_SECRET_KEY='test-jwt-secret')
class AppendDistressReportedExcelTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.token = create_access_token('ram.patil')
        self.auth = {'HTTP_AUTHORIZATION': f'Bearer {self.token}'}

    def test_append_saves_to_postgres_with_jwt_username(self):
        payload = {**SAMPLE_PAYLOAD, 'username': 'attacker.user'}
        response = self.client.post(
            '/append_distressReported_excel/',
            payload,
            format='json',
            **self.auth,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['message'], 'Data appended successfully')

        report = DistressReport.objects.get()
        self.assertEqual(report.username, 'ram.patil')
        self.assertEqual(report.Project_Name, 'NH-48')
        self.assertEqual(report.Distress_Type, 'Pothole')
        self.assertEqual(report.Latitude, 19.076)
        self.assertEqual(report.No_of_Distress, 1)
        self.assertEqual(report.Carriage_Type, 'Flexible')

    def test_append_requires_carriage_type(self):
        payload = {k: v for k, v in SAMPLE_PAYLOAD.items() if k != 'Carriage_Type'}
        response = self.client.post(
            '/append_distressReported_excel/',
            payload,
            format='json',
            **self.auth,
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('Carriage_Type', response.data)

    def test_append_rejects_invalid_carriage_type(self):
        payload = {**SAMPLE_PAYLOAD, 'Carriage_Type': 'Concrete'}
        response = self.client.post(
            '/append_distressReported_excel/',
            payload,
            format='json',
            **self.auth,
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('Carriage_Type', response.data)

    def test_append_accepts_carriage_type_alias(self):
        payload = {k: v for k, v in SAMPLE_PAYLOAD.items() if k != 'Carriage_Type'}
        payload['carriage_type'] = 'rigid'
        response = self.client.post(
            '/append_distressReported_excel/',
            payload,
            format='json',
            **self.auth,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(DistressReport.objects.get().Carriage_Type, 'Rigid')

    def test_append_requires_auth(self):
        response = self.client.post(
            '/append_distressReported_excel/',
            SAMPLE_PAYLOAD,
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_append_validates_required_fields(self):
        response = self.client.post(
            '/append_distressReported_excel/',
            {'Latitude': 1.0},
            format='json',
            **self.auth,
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('Project_Name', response.data)
        self.assertIn('Distress_Type', response.data)

    def test_append_rejects_invalid_chainage(self):
        payload = {**SAMPLE_PAYLOAD, 'Chainage_Start': 20.0, 'Chainage_End': 10.0}
        response = self.client.post(
            '/append_distressReported_excel/',
            payload,
            format='json',
            **self.auth,
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_put_updates_owned_report_via_append_url(self):
        report = DistressReport.objects.create(username='ram.patil', **SAMPLE_PAYLOAD)
        payload = {
            **SAMPLE_PAYLOAD,
            'id': report.id,
            'Distress_Type': 'Crack',
            'username': 'attacker',
        }
        response = self.client.put(
            '/append_distressReported_excel/',
            payload,
            format='json',
            **self.auth,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['message'], 'Data updated successfully')
        self.assertEqual(response.data['data']['Distress_Type'], 'Crack')
        report.refresh_from_db()
        self.assertEqual(report.Distress_Type, 'Crack')
        self.assertEqual(report.username, 'ram.patil')

    def test_patch_updates_partial_fields_via_append_url(self):
        report = DistressReport.objects.create(username='ram.patil', **SAMPLE_PAYLOAD)
        response = self.client.patch(
            '/append_distressReported_excel/',
            {'id': report.id, 'Lane': 'L2'},
            format='json',
            **self.auth,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        report.refresh_from_db()
        self.assertEqual(report.Lane, 'L2')
        self.assertEqual(report.Distress_Type, 'Pothole')

    def test_patch_requires_id(self):
        response = self.client.patch(
            '/append_distressReported_excel/',
            {'Lane': 'L2'},
            format='json',
            **self.auth,
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('id', response.data)

    @patch(
        'inventory.utils.upload_file_to_s3',
        return_value='https://nhitbucket.s3.ap-south-1.amazonaws.com/Damaged%20Assets/distress.jpg',
    )
    def test_patch_uploads_image_file(self, mock_upload):
        from django.core.files.uploadedfile import SimpleUploadedFile

        report = DistressReport.objects.create(username='ram.patil', **SAMPLE_PAYLOAD)
        response = self.client.patch(
            '/append_distressReported_excel/',
            {
                'id': report.id,
                'image': SimpleUploadedFile(
                    'photo.jpg', b'img', content_type='image/jpeg'
                ),
            },
            format='multipart',
            **self.auth,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        report.refresh_from_db()
        self.assertEqual(
            report.Image_URL,
            'https://nhitbucket.s3.ap-south-1.amazonaws.com/Damaged%20Assets/distress.jpg',
        )
        mock_upload.assert_called_once()


@override_settings(JWT_SECRET_KEY='test-jwt-secret')
class DistressReportCRUDTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.token = create_access_token('ram.patil')
        self.other_token = create_access_token('other.user')
        self.auth = {'HTTP_AUTHORIZATION': f'Bearer {self.token}'}
        self.other_auth = {'HTTP_AUTHORIZATION': f'Bearer {self.other_token}'}

    def _create(self, username='ram.patil', **overrides):
        data = {**SAMPLE_PAYLOAD, **overrides}
        return DistressReport.objects.create(username=username, **data)

    def test_history_returns_only_own_reports(self):
        self._create(username='ram.patil')
        self._create(username='other.user', Project_Name='Other')

        response = self.client.get('/distress/history/', **self.auth)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['username'], 'ram.patil')
        self.assertEqual(response.data[0]['Project_Name'], 'NH-48')

    def test_get_own_report(self):
        report = self._create()
        response = self.client.get(f'/distress/{report.id}/', **self.auth)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['id'], report.id)
        self.assertEqual(response.data['Distress_Type'], 'Pothole')

    def test_get_other_users_report_returns_403(self):
        report = self._create(username='other.user')
        response = self.client.get(f'/distress/{report.id}/', **self.auth)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_delete_own_report(self):
        report = self._create()
        response = self.client.delete(f'/distress/{report.id}/', **self.auth)
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(DistressReport.objects.filter(pk=report.id).exists())

    def test_delete_other_users_report_returns_403(self):
        report = self._create(username='other.user')
        response = self.client.delete(f'/distress/{report.id}/', **self.auth)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertTrue(DistressReport.objects.filter(pk=report.id).exists())

    def test_missing_report_returns_404(self):
        response = self.client.get('/distress/99999/', **self.auth)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_put_via_detail_url(self):
        report = self._create()
        payload = {**SAMPLE_PAYLOAD, 'Distress_Type': 'Rutting'}
        response = self.client.put(
            f'/distress/{report.id}/',
            payload,
            format='json',
            **self.auth,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        report.refresh_from_db()
        self.assertEqual(report.Distress_Type, 'Rutting')

    def test_patch_other_users_report_returns_403(self):
        report = self._create(username='other.user')
        response = self.client.patch(
            f'/distress/{report.id}/',
            {'Lane': 'hack'},
            format='json',
            **self.auth,
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
