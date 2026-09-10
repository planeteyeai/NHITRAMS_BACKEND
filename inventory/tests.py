"""Tests for PostgreSQL-backed inventory report APIs (Excel replacement).

Legacy PascalCase append tests were replaced by RAMS APK coverage in
`tests_inventory_rams.py`.
"""

from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch
import json
import unittest

import jwt
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase, override_settings
from rest_framework import status
from rest_framework.test import APIClient

from authentication.jwt_utils import create_access_token
from inventory.models import DPRReport, InventoryReport, UserProjectAssignment
from inventory.serializers import InventoryReportWriteSerializer
from inventory.services import ProjectDetailsNotFound, get_project_details


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


@unittest.skip('Superseded by inventory.tests_inventory_rams (RAMS APK flow)')
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

    def test_put_updates_owned_report_via_append_url(self):
        report = InventoryReport.objects.create(username='ram.patil', **SAMPLE_PAYLOAD)
        payload = {
            **SAMPLE_PAYLOAD,
            'id': report.id,
            'Asset_type': 'Guard Rails',
            'username': 'attacker',
        }
        response = self.client.put(
            '/append_inventory_excel/',
            payload,
            format='json',
            **self.auth,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['message'], 'Data updated successfully')
        self.assertEqual(response.data['data']['Asset_type'], 'Guard Rails')
        report.refresh_from_db()
        self.assertEqual(report.Asset_type, 'Guard Rails')
        self.assertEqual(report.username, 'ram.patil')

    def test_patch_updates_partial_fields_via_append_url(self):
        report = InventoryReport.objects.create(username='ram.patil', **SAMPLE_PAYLOAD)
        response = self.client.patch(
            '/append_inventory_excel/',
            {'id': report.id, 'Lane': 'L2'},
            format='json',
            **self.auth,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        report.refresh_from_db()
        self.assertEqual(report.Lane, 'L2')
        self.assertEqual(report.Asset_type, 'Sign Boards')

    def test_patch_requires_id(self):
        response = self.client.patch(
            '/append_inventory_excel/',
            {'Lane': 'L2'},
            format='json',
            **self.auth,
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('id', response.data)

    @patch(
        'inventory.utils.upload_file_to_s3',
        side_effect=[
            'https://nhitbucket.s3.ap-south-1.amazonaws.com/Damaged%20Assets/img.jpg',
            'https://nhitbucket.s3.ap-south-1.amazonaws.com/Damaged%20Assets/vid.mp4',
        ],
    )
    def test_patch_uploads_image_and_video_files(self, mock_upload):
        from django.core.files.uploadedfile import SimpleUploadedFile

        report = InventoryReport.objects.create(username='ram.patil', **SAMPLE_PAYLOAD)
        response = self.client.patch(
            '/append_inventory_excel/',
            {
                'id': report.id,
                'image': SimpleUploadedFile(
                    'photo.jpg', b'img', content_type='image/jpeg'
                ),
                'video': SimpleUploadedFile(
                    'clip.mp4', b'vid', content_type='video/mp4'
                ),
            },
            format='multipart',
            **self.auth,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        report.refresh_from_db()
        self.assertEqual(
            report.Image_URL,
            'https://nhitbucket.s3.ap-south-1.amazonaws.com/Damaged%20Assets/img.jpg',
        )
        self.assertEqual(
            report.Video_URL,
            'https://nhitbucket.s3.ap-south-1.amazonaws.com/Damaged%20Assets/vid.mp4',
        )
        self.assertEqual(mock_upload.call_count, 2)

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
                'Source',
            ],
        )


@unittest.skip('Superseded by inventory.tests_inventory_rams (RAMS APK flow)')
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

    def test_put_via_detail_url(self):
        report = self._create()
        payload = {**SAMPLE_PAYLOAD, 'Asset_type': 'KM Stones'}
        response = self.client.put(
            f'/inventory/{report.id}/',
            payload,
            format='json',
            **self.auth,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        report.refresh_from_db()
        self.assertEqual(report.Asset_type, 'KM Stones')

    def test_patch_other_users_report_returns_403(self):
        report = self._create(username='other.user')
        response = self.client.patch(
            f'/inventory/{report.id}/',
            {'Lane': 'hack'},
            format='json',
            **self.auth,
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

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


@override_settings(JWT_SECRET_KEY='test-jwt-secret')
class DPRReportAPITests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.token = create_access_token('ram.patil')
        self.other_token = create_access_token('other.user')
        self.auth = {'HTTP_AUTHORIZATION': f'Bearer {self.token}'}
        self.other_auth = {'HTTP_AUTHORIZATION': f'Bearer {self.other_token}'}
        UserProjectAssignment.objects.create(
            username='ram.patil',
            project_name='Abu Road to Swaroopganj',
            start_chainage='646.000',
            end_chainage='680.000',
            total_project_length='34.000 km',
        )

    def _payload(self, **overrides):
        data = {
            'report_date': '2026-08-04',
            'reporting_engineer': 'Ram Patil',
            'assessment_date': '2026-08-03',
            'remarks': 'Work in progress',
            'asset_details': '{"sign_boards": 2}',
        }
        data.update(overrides)
        return data

    @patch('inventory.views.upload_dpr_images')
    def test_create_dpr_uses_jwt_username_and_project_lookup(self, mock_upload):
        mock_upload.return_value = [
            'https://nhitbucket.s3.ap-south-1.amazonaws.com/Damaged+Assets/evidence.jpg',
        ]
        image = SimpleUploadedFile('evidence.jpg', b'fake-image-bytes', content_type='image/jpeg')
        payload = self._payload()
        payload['images'] = image
        # Attempted identity spoofing must be ignored / rejected
        payload['username'] = 'attacker.user'
        payload['project_name'] = 'Fake Project'

        response = self.client.post(
            '/api/inventory/dpr/report',
            payload,
            format='multipart',
            **self.auth,
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(response.data['success'])

        # Retry without forbidden fields
        payload = self._payload()
        payload['images'] = SimpleUploadedFile(
            'evidence.jpg', b'fake-image-bytes', content_type='image/jpeg'
        )
        response = self.client.post(
            '/api/inventory/dpr/report',
            payload,
            format='multipart',
            **self.auth,
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(response.data['success'])
        data = response.data['data']
        self.assertEqual(data['username'], 'ram.patil')
        self.assertEqual(data['project_name'], 'Abu Road to Swaroopganj')
        self.assertEqual(data['start_chainage'], '646.000')
        self.assertEqual(data['end_chainage'], '680.000')
        self.assertEqual(data['total_project_length'], '34.000 km')
        self.assertEqual(
            data['uploaded_images'],
            ['https://nhitbucket.s3.ap-south-1.amazonaws.com/Damaged+Assets/evidence.jpg'],
        )
        self.assertEqual(data['overall_summary'], {})

        report = DPRReport.objects.get()
        self.assertEqual(report.username, 'ram.patil')
        self.assertEqual(report.reporting_engineer, 'Ram Patil')
        self.assertEqual(report.project_name, 'Abu Road to Swaroopganj')

    @patch('inventory.views.upload_dpr_images', return_value=[])
    def test_create_dpr_calculates_overall_summary_available_quantity(self, _mock_upload):
        summary = {
            'sign_board': {'total_quantity': 1000, 'damaged_quantity': 1},
            'streetlight': {'total_quantity': 250, 'damaged_quantity': 3},
        }
        payload = self._payload()
        payload['overall_summary'] = json.dumps(summary)

        response = self.client.post(
            '/api/inventory/dpr/report',
            payload,
            format='multipart',
            **self.auth,
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        expected = {
            'sign_board': {
                'total_quantity': 1000,
                'damaged_quantity': 1,
                'available_quantity': 999,
            },
            'streetlight': {
                'total_quantity': 250,
                'damaged_quantity': 3,
                'available_quantity': 247,
            },
        }
        self.assertEqual(response.data['data']['overall_summary'], expected)
        self.assertEqual(DPRReport.objects.get().overall_summary, expected)

    @patch('inventory.views.upload_dpr_images', return_value=[])
    def test_create_dpr_rejects_invalid_overall_summary(self, _mock_upload):
        payload = self._payload()
        payload['overall_summary'] = json.dumps({
            'sign_board': {'total_quantity': 10, 'damaged_quantity': 11},
        })
        response = self.client.post(
            '/api/inventory/dpr/report',
            payload,
            format='multipart',
            **self.auth,
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(response.data['success'])
        self.assertIn('overall_summary', response.data['errors'])

        payload = self._payload()
        payload['overall_summary'] = '{not-json'
        response = self.client.post(
            '/api/inventory/dpr/report',
            payload,
            format='multipart',
            **self.auth,
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('overall_summary', response.data['errors'])

    def test_create_requires_auth(self):
        response = self.client.post(
            '/api/inventory/dpr/report',
            self._payload(),
            format='multipart',
        )
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_create_without_project_assignment_returns_404(self):
        response = self.client.post(
            '/api/inventory/dpr/report',
            self._payload(),
            format='multipart',
            **self.other_auth,
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertFalse(response.data['success'])

    @patch('inventory.views.upload_dpr_images', return_value=[])
    def test_history_returns_only_own_reports(self, _mock_upload):
        DPRReport.objects.create(
            username='ram.patil',
            project_name='Abu Road to Swaroopganj',
            start_chainage='646.000',
            end_chainage='680.000',
            total_project_length='34.000 km',
            report_date='2026-08-04',
            reporting_engineer='Ram Patil',
            uploaded_images=['https://nhitbucket.s3.ap-south-1.amazonaws.com/Damaged+Assets/a.jpg'],
            overall_summary={
                'sign_board': {
                    'total_quantity': 1000,
                    'damaged_quantity': 1,
                    'available_quantity': 999,
                },
            },
        )
        DPRReport.objects.create(
            username='other.user',
            project_name='Other Project',
            start_chainage='0',
            end_chainage='10',
            total_project_length='10 km',
            report_date='2026-08-01',
            reporting_engineer='Other',
        )

        response = self.client.get('/api/inventory/dpr/history', **self.auth)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data['success'])
        self.assertEqual(len(response.data['data']), 1)
        self.assertEqual(response.data['data'][0]['username'], 'ram.patil')
        self.assertEqual(
            response.data['data'][0]['uploaded_images'],
            ['https://nhitbucket.s3.ap-south-1.amazonaws.com/Damaged+Assets/a.jpg'],
        )
        self.assertEqual(
            response.data['data'][0]['overall_summary']['sign_board']['available_quantity'],
            999,
        )

    def test_history_ignores_username_query_param(self):
        DPRReport.objects.create(
            username='ram.patil',
            project_name='Abu Road to Swaroopganj',
            start_chainage='646.000',
            end_chainage='680.000',
            total_project_length='34.000 km',
            report_date='2026-08-04',
            reporting_engineer='Ram Patil',
        )
        DPRReport.objects.create(
            username='other.user',
            project_name='Other Project',
            start_chainage='0',
            end_chainage='10',
            total_project_length='10 km',
            report_date='2026-08-01',
            reporting_engineer='Other',
        )

        response = self.client.get(
            '/api/inventory/dpr/history?username=other.user',
            **self.auth,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data['data']), 1)
        self.assertEqual(response.data['data'][0]['username'], 'ram.patil')

    def test_get_project_details_helper(self):
        details = get_project_details('ram.patil')
        self.assertEqual(details['project_name'], 'Abu Road to Swaroopganj')
        with self.assertRaises(ProjectDetailsNotFound):
            get_project_details('unknown.user')

    def _create_report(self, username='ram.patil', **overrides):
        data = {
            'username': username,
            'project_name': 'Abu Road to Swaroopganj',
            'start_chainage': '646.000',
            'end_chainage': '680.000',
            'total_project_length': '34.000 km',
            'report_date': '2026-08-04',
            'reporting_engineer': 'Ram Patil',
            'remarks': 'Work in progress',
            'overall_summary': {
                'sign_board': {
                    'total_quantity': 1000,
                    'damaged_quantity': 1,
                    'available_quantity': 999,
                },
            },
            'uploaded_images': [
                'https://nhitbucket.s3.ap-south-1.amazonaws.com/Damaged+Assets/a.jpg',
            ],
        }
        data.update(overrides)
        return DPRReport.objects.create(**data)

    @patch('inventory.views.upload_dpr_images', return_value=[])
    def test_put_updates_full_dpr_report(self, _mock_upload):
        report = self._create_report()
        payload = self._payload(
            report_date='2026-08-05',
            reporting_engineer='Updated Engineer',
            remarks='Updated remarks',
            overall_summary=json.dumps({
                'sign_board': {'total_quantity': 1000, 'damaged_quantity': 5},
            }),
        )
        response = self.client.put(
            f'/api/inventory/dpr/report/{report.id}',
            payload,
            format='multipart',
            **self.auth,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data['success'])
        self.assertEqual(response.data['message'], 'Report updated successfully.')
        data = response.data['data']
        self.assertEqual(data['report_id'], report.id)
        self.assertEqual(data['username'], 'ram.patil')
        self.assertEqual(data['project_name'], 'Abu Road to Swaroopganj')
        self.assertEqual(
            data['overall_summary']['sign_board']['available_quantity'],
            995,
        )

        report.refresh_from_db()
        self.assertEqual(report.reporting_engineer, 'Updated Engineer')
        self.assertEqual(str(report.report_date), '2026-08-05')
        self.assertEqual(report.remarks, 'Updated remarks')

    @patch('inventory.views.upload_dpr_images', return_value=[
        'https://nhitbucket.s3.ap-south-1.amazonaws.com/Damaged+Assets/b.jpg',
    ])
    def test_patch_partial_update_and_appends_images(self, mock_upload):
        report = self._create_report()
        payload = {
            'remarks': 'Patched only',
            'images': SimpleUploadedFile('b.jpg', b'img', content_type='image/jpeg'),
        }
        response = self.client.patch(
            f'/api/inventory/dpr/report/{report.id}/',
            payload,
            format='multipart',
            **self.auth,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data['success'])
        self.assertEqual(response.data['message'], 'Report updated successfully.')
        report.refresh_from_db()
        self.assertEqual(report.remarks, 'Patched only')
        self.assertEqual(report.reporting_engineer, 'Ram Patil')  # unchanged
        self.assertEqual(
            report.uploaded_images,
            [
                'https://nhitbucket.s3.ap-south-1.amazonaws.com/Damaged+Assets/a.jpg',
                'https://nhitbucket.s3.ap-south-1.amazonaws.com/Damaged+Assets/b.jpg',
            ],
        )
        mock_upload.assert_called_once()

    @patch('inventory.views.upload_dpr_images', return_value=[
        'https://nhitbucket.s3.ap-south-1.amazonaws.com/Damaged+Assets/retain-new.jpg',
    ])
    def test_patch_retain_image_urls_plus_new_images(self, mock_upload):
        keep = 'https://nhitbucket.s3.ap-south-1.amazonaws.com/Damaged+Assets/a.jpg'
        drop = 'https://nhitbucket.s3.ap-south-1.amazonaws.com/Damaged+Assets/drop.jpg'
        report = self._create_report(uploaded_images=[keep, drop])
        payload = {
            'retain_image_urls': json.dumps([keep]),
            'new_images': SimpleUploadedFile('n.jpg', b'img', content_type='image/jpeg'),
        }
        response = self.client.patch(
            f'/api/inventory/dpr/report/{report.id}',
            payload,
            format='multipart',
            **self.auth,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        report.refresh_from_db()
        self.assertEqual(
            report.uploaded_images,
            [
                keep,
                'https://nhitbucket.s3.ap-south-1.amazonaws.com/Damaged+Assets/retain-new.jpg',
            ],
        )
        mock_upload.assert_called_once()

    @patch('inventory.views.upload_dpr_images', return_value=[
        'https://nhitbucket.s3.ap-south-1.amazonaws.com/Damaged+Assets/new.jpg',
    ])
    def test_put_replaces_images_when_new_files_sent(self, mock_upload):
        report = self._create_report()
        payload = self._payload()
        payload['images'] = SimpleUploadedFile('new.jpg', b'img', content_type='image/jpeg')
        response = self.client.put(
            f'/api/inventory/dpr/report/{report.id}',
            payload,
            format='multipart',
            **self.auth,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        report.refresh_from_db()
        self.assertEqual(
            report.uploaded_images,
            ['https://nhitbucket.s3.ap-south-1.amazonaws.com/Damaged+Assets/new.jpg'],
        )
        mock_upload.assert_called_once()

    @patch('inventory.views.upload_dpr_images', return_value=[
        'https://nhitbucket.s3.ap-south-1.amazonaws.com/Damaged+Assets/c.jpg',
    ])
    def test_put_keep_images_plus_new_uploads(self, _mock_upload):
        old = 'https://nhitbucket.s3.ap-south-1.amazonaws.com/Damaged+Assets/a.jpg'
        report = self._create_report(uploaded_images=[old, 'https://example.com/drop.jpg'])
        payload = self._payload()
        payload['keep_images'] = json.dumps([old])
        payload['images'] = SimpleUploadedFile('c.jpg', b'img', content_type='image/jpeg')
        response = self.client.put(
            f'/api/inventory/dpr/report/{report.id}',
            payload,
            format='multipart',
            **self.auth,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        report.refresh_from_db()
        self.assertEqual(
            report.uploaded_images,
            [
                old,
                'https://nhitbucket.s3.ap-south-1.amazonaws.com/Damaged+Assets/c.jpg',
            ],
        )

    def test_patch_remove_images(self):
        keep = 'https://nhitbucket.s3.ap-south-1.amazonaws.com/Damaged+Assets/a.jpg'
        drop = 'https://nhitbucket.s3.ap-south-1.amazonaws.com/Damaged+Assets/drop.jpg'
        report = self._create_report(uploaded_images=[keep, drop])
        response = self.client.patch(
            f'/api/inventory/dpr/report/{report.id}',
            {'remove_images': json.dumps([drop])},
            format='multipart',
            **self.auth,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        report.refresh_from_db()
        self.assertEqual(report.uploaded_images, [keep])

    def test_patch_clear_images(self):
        report = self._create_report()
        response = self.client.patch(
            f'/api/inventory/dpr/report/{report.id}',
            {'clear_images': 'true'},
            format='multipart',
            **self.auth,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        report.refresh_from_db()
        self.assertEqual(report.uploaded_images, [])

    @patch('inventory.views.upload_dpr_images', return_value=[])
    def test_put_without_image_fields_keeps_existing_images(self, _mock_upload):
        report = self._create_report()
        response = self.client.put(
            f'/api/inventory/dpr/report/{report.id}',
            self._payload(),
            format='multipart',
            **self.auth,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        report.refresh_from_db()
        self.assertEqual(
            report.uploaded_images,
            ['https://nhitbucket.s3.ap-south-1.amazonaws.com/Damaged+Assets/a.jpg'],
        )

    def test_put_other_users_report_returns_403(self):
        report = self._create_report(username='other.user', project_name='Other')
        response = self.client.put(
            f'/api/inventory/dpr/report/{report.id}',
            self._payload(),
            format='multipart',
            **self.auth,
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertFalse(response.data['success'])
        self.assertEqual(
            response.data['message'],
            'You are not authorized to update this report.',
        )

    def test_patch_missing_report_returns_404(self):
        response = self.client.patch(
            '/api/inventory/dpr/report/99999',
            {'remarks': 'x'},
            format='multipart',
            **self.auth,
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_put_rejects_server_owned_fields(self):
        report = self._create_report()
        payload = self._payload(project_name='Hacked')
        response = self.client.put(
            f'/api/inventory/dpr/report/{report.id}',
            payload,
            format='multipart',
            **self.auth,
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(response.data['success'])


class InventoryFilterSerializerTests(TestCase):
    """inventory_filter accepts All/all for direction and asset_type."""

    def _base_payload(self, **overrides):
        payload = {
            'chainage_start': 0,
            'chainage_end': 100,
            'date': '2026-04-20',
            'direction': ['Decreasing'],
            'project_name': ['Agra Bypass Road'],
            'asset_type': ['Culvert'],
        }
        payload.update(overrides)
        return payload

    def test_all_keyword_case_insensitive_for_direction_and_asset_type(self):
        from inventory.serializers import InventoryFilterSerializer

        for direction in ('all', 'All', 'ALL'):
            for asset_type in ('all', 'All', 'ALL'):
                serializer = InventoryFilterSerializer(
                    data=self._base_payload(direction=[direction], asset_type=[asset_type])
                )
                self.assertTrue(serializer.is_valid(), serializer.errors)
                self.assertEqual(serializer.validated_data['direction'], [])
                self.assertEqual(serializer.validated_data['asset_type'], [])

    def test_specific_filters_unaffected_when_all_not_present(self):
        from inventory.serializers import InventoryFilterSerializer

        serializer = InventoryFilterSerializer(data=self._base_payload())
        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertEqual(serializer.validated_data['direction'], ['Decreasing'])
        self.assertEqual(serializer.validated_data['asset_type'], ['Culvert'])

    def test_all_in_either_list_clears_that_filter_only(self):
        from inventory.serializers import InventoryFilterSerializer

        serializer = InventoryFilterSerializer(
            data=self._base_payload(direction=['All'], asset_type=['Culvert'])
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertEqual(serializer.validated_data['direction'], [])
        self.assertEqual(serializer.validated_data['asset_type'], ['Culvert'])


class InventoryFilterResponseTests(TestCase):
    """NHIT-compatible sparse serialization for inventory_filter."""

    def test_sparse_row_omits_zero_asset_fields_and_median_lane_rules(self):
        from datetime import date
        from types import SimpleNamespace

        from inventory.services.inventory_filter_response import serialize_inventory_wide_row

        item = SimpleNamespace(
            project_name='Agra Bypass Road',
            chainage_start=32.8,
            chainage_end=32.9,
            direction='Median',
            asset_type='Street Lights',
            trees=0.0,
            culvert=0.0,
            street_lights=1.0,
            bridges=0.0,
            traffic_signals=0.0,
            bus_stop=0.0,
            truck_layby=0.0,
            toll_plaza=0.0,
            adjacent_road=0.0,
            toilet_blocks=0.0,
            rest_area=0.0,
            rcc_drain=0.0,
            fuel_station=0.0,
            emergency_call_box=0.0,
            tunnels=0.0,
            footpath=0.0,
            junction=0.0,
            sign_boards=0.0,
            solar_blinker=0.0,
            median_plants=0.0,
            service_road=0.0,
            km_stones=0.0,
            crash_barrier=0.0,
            median_opening=0.0,
            kerb=0.0,
            guard_post=0.0,
            delineators=0.0,
            pedestrian_guard_rail=0.0,
            slip_roads=0.0,
            bus_bays=0.0,
            latitude=27.06559,
            longitude=77.983,
            survey_date=date(2026, 4, 20),
            sub_asset_type='MBCB',
            carriage_type='Flexible',
            lane='L3',
            image_url='http://example.com/x.jpg',
            source='excel_import',
        )

        row = serialize_inventory_wide_row(item)

        self.assertEqual(row['asset_type'], 'Street Lights')
        self.assertEqual(row['street_lights'], 1)
        self.assertEqual(row['latitude'], '27.06559')
        self.assertEqual(row['longitude'], '77.983')
        self.assertEqual(row['source'], 'SAR')
        self.assertEqual(row['sub_asset_type'], 'MBCB')
        self.assertNotIn('trees', row)
        self.assertNotIn('carriage_type', row)
        self.assertNotIn('lane', row)
        self.assertNotIn('image_url', row)

    def test_sparse_row_omits_blank_sub_asset_type(self):
        from datetime import date
        from types import SimpleNamespace

        from inventory.services.inventory_filter_response import serialize_inventory_wide_row

        item = SimpleNamespace(
            project_name='Agra Bypass Road',
            chainage_start=1.0,
            chainage_end=1.1,
            direction='Increasing',
            asset_type='Culvert',
            trees=0.0,
            culvert=1.0,
            street_lights=0.0,
            bridges=0.0,
            traffic_signals=0.0,
            bus_stop=0.0,
            truck_layby=0.0,
            toll_plaza=0.0,
            adjacent_road=0.0,
            toilet_blocks=0.0,
            rest_area=0.0,
            rcc_drain=0.0,
            fuel_station=0.0,
            emergency_call_box=0.0,
            tunnels=0.0,
            footpath=0.0,
            junction=0.0,
            sign_boards=0.0,
            solar_blinker=0.0,
            median_plants=0.0,
            service_road=0.0,
            km_stones=0.0,
            crash_barrier=0.0,
            median_opening=0.0,
            kerb=0.0,
            guard_post=0.0,
            delineators=0.0,
            pedestrian_guard_rail=0.0,
            slip_roads=0.0,
            bus_bays=0.0,
            latitude=27.0,
            longitude=77.0,
            survey_date=date(2026, 4, 20),
            sub_asset_type='  ',
            carriage_type='Flexible',
            lane='L1',
            image_url='',
            source=None,
        )

        row = serialize_inventory_wide_row(item)
        self.assertNotIn('sub_asset_type', row)

    def test_omits_chainage_start_for_first_segment(self):
        from datetime import date
        from types import SimpleNamespace

        from inventory.services.inventory_filter_response import serialize_inventory_wide_row

        item = SimpleNamespace(
            project_name='Agra Bypass Road',
            chainage_start=0.0,
            chainage_end=0.1,
            direction='Increasing',
            asset_type='Culvert',
            trees=0.0,
            culvert=1.0,
            bridges=0.0,
            traffic_signals=0.0,
            bus_stop=0.0,
            truck_layby=0.0,
            toll_plaza=0.0,
            adjacent_road=0.0,
            toilet_blocks=0.0,
            rest_area=0.0,
            rcc_drain=0.0,
            fuel_station=0.0,
            emergency_call_box=0.0,
            tunnels=0.0,
            footpath=0.0,
            junction=0.0,
            street_lights=0.0,
            sign_boards=0.0,
            solar_blinker=0.0,
            median_plants=0.0,
            service_road=0.0,
            km_stones=0.0,
            crash_barrier=0.0,
            median_opening=0.0,
            kerb=0.0,
            guard_post=0.0,
            delineators=0.0,
            pedestrian_guard_rail=0.0,
            slip_roads=0.0,
            bus_bays=0.0,
            latitude=27.26094,
            longitude=77.81572,
            survey_date=date(2026, 4, 20),
            sub_asset_type='',
            carriage_type='Flexible',
            lane='L3',
            image_url='',
            source='excel_import',
        )

        row = serialize_inventory_wide_row(item)

        self.assertNotIn('chainage_start', row)
        self.assertEqual(row['chainage_end'], 0.1)
        self.assertEqual(row['culvert'], 1)

    def test_uses_dms_override_for_sign_boards_with_zero_coordinates(self):
        from datetime import date
        from types import SimpleNamespace

        from inventory.services.inventory_filter_response import serialize_inventory_wide_row

        item = SimpleNamespace(
            project_name='Agra Bypass Road',
            chainage_start=1.0,
            chainage_end=1.1,
            direction='Increasing',
            asset_type='Sign Boards',
            trees=0.0,
            culvert=0.0,
            street_lights=0.0,
            bridges=0.0,
            traffic_signals=0.0,
            bus_stop=0.0,
            truck_layby=0.0,
            toll_plaza=0.0,
            adjacent_road=0.0,
            toilet_blocks=0.0,
            rest_area=0.0,
            rcc_drain=0.0,
            fuel_station=0.0,
            emergency_call_box=0.0,
            tunnels=0.0,
            footpath=0.0,
            junction=0.0,
            sign_boards=1.0,
            solar_blinker=0.0,
            median_plants=0.0,
            service_road=0.0,
            km_stones=0.0,
            crash_barrier=0.0,
            median_opening=0.0,
            kerb=0.0,
            guard_post=0.0,
            delineators=0.0,
            pedestrian_guard_rail=0.0,
            slip_roads=0.0,
            bus_bays=0.0,
            latitude=0.0,
            longitude=0.0,
            survey_date=date(2026, 4, 20),
            sub_asset_type='',
            carriage_type='Flexible',
            lane='L2',
            image_url='',
            source='excel_import',
        )

        row = serialize_inventory_wide_row(item)

        self.assertEqual(row['latitude'], '27°15\'12.24"N')
        self.assertEqual(row['longitude'], '77°48\'36.01"E')
        self.assertEqual(row['sign_boards'], 1)

    def test_groups_consecutive_rows_with_same_coordinates(self):
        from inventory.services.inventory_filter_response import group_serialized_rows

        rows = [
            {'latitude': '27.1', 'longitude': '77.1', 'direction': 'Median'},
            {'latitude': '27.1', 'longitude': '77.1', 'direction': 'Increasing'},
            {'latitude': '27.2', 'longitude': '77.2', 'direction': 'Decreasing'},
        ]

        groups = group_serialized_rows(rows)

        self.assertEqual(groups, [rows[:2], [rows[2]]])


class InventoryFilterGzipMiddlewareTests(TestCase):
    """GZipMiddleware transparently compresses inventory_filter without changing JSON."""

    def setUp(self):
        self.client = APIClient()
        self.payload = {
            'chainage_start': 0,
            'chainage_end': 1381,
            'date': '2026-04-20',
            'direction': ['All'],
            'project_name': ['Agra Bypass Road'],
            'asset_type': ['All'],
            'page': 1,
            'page_size': 5000,
        }
        # Large enough payload for GZipMiddleware (>200 bytes) without hitting inventory_wide.
        self.mock_response = [
            [
                {
                    'project_name': 'Agra Bypass Road',
                    'chainage_start': float(i),
                    'chainage_end': float(i) + 0.1,
                    'direction': 'Increasing',
                    'asset_type': 'Trees',
                    'latitude': '27.1',
                    'longitude': '77.1',
                    'date': '2026-04-20',
                    'trees': 1,
                }
                for i in range(100)
            ]
        ]

    @patch('inventory.views.build_inventory_filter_groups')
    @patch('inventory.views.build_inventory_filter_queryset')
    @override_settings(NHIT_CACHE_ENABLED=False)
    def test_gzip_response_matches_uncompressed_body(self, mock_build_qs, mock_build_groups):
        import gzip

        mock_qs = mock_build_qs.return_value
        mock_qs.count.return_value = 10000
        mock_build_groups.return_value = self.mock_response

        plain = self.client.post(
            '/inventory_filter/',
            self.payload,
            content_type='application/json',
        )
        compressed = self.client.post(
            '/inventory_filter/',
            self.payload,
            content_type='application/json',
            HTTP_ACCEPT_ENCODING='gzip',
        )

        self.assertEqual(plain.status_code, status.HTTP_200_OK)
        self.assertEqual(compressed.status_code, status.HTTP_200_OK)
        self.assertEqual(compressed.get('Content-Encoding'), 'gzip')

        decompressed = gzip.decompress(compressed.content)
        self.assertEqual(json.loads(decompressed), json.loads(plain.content))

    def test_error_response_not_gzip_corrupted(self):
        bad_payload = dict(self.payload)
        bad_payload.pop('date')
        response = self.client.post(
            '/inventory_filter/',
            bad_payload,
            content_type='application/json',
            HTTP_ACCEPT_ENCODING='gzip',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertNotEqual(response.get('Content-Encoding'), 'gzip')


class InventoryFilterPaginationTests(TestCase):
    """NHIT-compatible pagination headers and page slicing for inventory_filter."""

    def setUp(self):
        self.client = APIClient()
        self.payload = {
            'chainage_start': 0,
            'chainage_end': 99999,
            'date': '2026-04-20',
            'direction': ['All'],
            'project_name': ['Agra Bypass Road'],
            'asset_type': ['All'],
            'page': 1,
            'page_size': 5000,
        }
        self.mock_groups = [[{'project_name': 'Agra Bypass Road', 'chainage_end': 1.0}]]

    @patch('inventory.views.build_inventory_filter_groups')
    @patch('inventory.views.build_inventory_filter_queryset')
    @override_settings(NHIT_CACHE_ENABLED=False)
    def test_page_one_headers_and_slice(self, mock_build_qs, mock_build_groups):
        mock_qs = mock_build_qs.return_value
        mock_qs.count.return_value = 12000
        mock_build_groups.return_value = self.mock_groups

        response = self.client.post('/inventory_filter/', self.payload, format='json')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(json.loads(response.content), self.mock_groups)
        self.assertEqual(response['X-Inventory-Paginated'], 'true')
        self.assertEqual(response['X-Inventory-Page'], '1')
        self.assertEqual(response['X-Inventory-Page-Size'], '5000')
        self.assertEqual(response['X-Inventory-Row-Count'], '12000')
        self.assertEqual(response['X-Inventory-Has-More'], 'true')
        mock_build_groups.assert_called_once()
        _args, kwargs = mock_build_groups.call_args
        self.assertIs(_args[0], mock_qs)
        self.assertEqual(kwargs.get('offset'), 0)
        self.assertEqual(kwargs.get('limit'), 5000)

    @patch('inventory.views.build_inventory_filter_groups')
    @patch('inventory.views.build_inventory_filter_queryset')
    @override_settings(NHIT_CACHE_ENABLED=False)
    def test_page_size_100000_headers_and_body_contract(self, mock_build_qs, mock_build_groups):
        """page_size=100000 is accepted; body + pagination headers stay NHIT-shaped."""
        mock_qs = mock_build_qs.return_value
        mock_qs.count.return_value = 250000
        mock_build_groups.return_value = self.mock_groups

        payload = {**self.payload, 'page': 1, 'page_size': 100000}
        mock_build_groups.reset_mock()
        response = self.client.post('/inventory_filter/', payload, format='json')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        body = json.loads(response.content)
        self.assertEqual(body, self.mock_groups)
        self.assertIsInstance(body, list)
        self.assertEqual(response['X-Inventory-Paginated'], 'true')
        self.assertEqual(response['X-Inventory-Page'], '1')
        self.assertEqual(response['X-Inventory-Page-Size'], '100000')
        self.assertEqual(response['X-Inventory-Row-Count'], '250000')
        self.assertEqual(response['X-Inventory-Has-More'], 'true')
        matching = [
            call
            for call in mock_build_groups.call_args_list
            if call.kwargs.get('limit') == 100000 and call.kwargs.get('offset') == 0
        ]
        self.assertEqual(len(matching), 1)

    @patch('inventory.views.build_inventory_filter_groups')
    @patch('inventory.views.build_inventory_filter_queryset')
    @override_settings(NHIT_CACHE_ENABLED=False)
    def test_middle_page_same_total_count(self, mock_build_qs, mock_build_groups):
        mock_qs = mock_build_qs.return_value
        mock_qs.count.return_value = 12000
        mock_build_groups.return_value = self.mock_groups

        payload = {**self.payload, 'page': 2}
        response = self.client.post('/inventory_filter/', payload, format='json')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response['X-Inventory-Page'], '2')
        self.assertEqual(response['X-Inventory-Row-Count'], '12000')
        self.assertEqual(response['X-Inventory-Has-More'], 'true')
        mock_build_groups.assert_called_once()
        _args, kwargs = mock_build_groups.call_args
        self.assertIs(_args[0], mock_qs)
        self.assertEqual(kwargs.get('offset'), 5000)
        self.assertEqual(kwargs.get('limit'), 5000)

    @patch('inventory.views.build_inventory_filter_groups')
    @patch('inventory.views.build_inventory_filter_queryset')
    @override_settings(NHIT_CACHE_ENABLED=False)
    def test_final_page_has_more_false(self, mock_build_qs, mock_build_groups):
        mock_qs = mock_build_qs.return_value
        mock_qs.count.return_value = 9000
        mock_build_groups.return_value = self.mock_groups

        payload = {**self.payload, 'page': 2}
        response = self.client.post('/inventory_filter/', payload, format='json')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response['X-Inventory-Has-More'], 'false')
        mock_build_groups.assert_called_once()
        _args, kwargs = mock_build_groups.call_args
        self.assertIs(_args[0], mock_qs)
        self.assertEqual(kwargs.get('offset'), 5000)
        self.assertEqual(kwargs.get('limit'), 5000)

    @patch('inventory.views.build_inventory_filter_groups')
    @patch('inventory.views.build_inventory_filter_queryset')
    @override_settings(NHIT_CACHE_ENABLED=False)
    def test_beyond_final_page_returns_no_match(self, mock_build_qs, mock_build_groups):
        mock_qs = mock_build_qs.return_value
        mock_qs.count.return_value = 9000

        payload = {**self.payload, 'page': 3}
        response = self.client.post('/inventory_filter/', payload, format='json')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data, {'message': 'No match'})
        mock_build_groups.assert_not_called()

    @patch('inventory.views.build_inventory_filter_groups')
    @patch('inventory.views.build_inventory_filter_queryset')
    @override_settings(NHIT_CACHE_ENABLED=False)
    def test_empty_dataset_page_one_returns_no_match(self, mock_build_qs, mock_build_groups):
        mock_qs = mock_build_qs.return_value
        mock_qs.count.return_value = 0

        response = self.client.post('/inventory_filter/', self.payload, format='json')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data, {'message': 'No match'})
        mock_build_groups.assert_not_called()

    def test_default_page_size_matches_nhit(self):
        from inventory.serializers import InventoryFilterSerializer

        serializer = InventoryFilterSerializer(
            data={
                'chainage_start': 0,
                'chainage_end': 100,
                'date': '2026-04-20',
                'direction': ['All'],
                'project_name': ['Agra Bypass Road'],
                'asset_type': ['All'],
            }
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertEqual(serializer.validated_data['page'], 1)
        self.assertEqual(serializer.validated_data['page_size'], 15000)

    def test_page_size_accepts_5000_through_100000(self):
        from inventory.serializers import InventoryFilterSerializer

        base = {
            'chainage_start': 0,
            'chainage_end': 100,
            'date': '2026-04-20',
            'direction': ['All'],
            'project_name': ['Agra Bypass Road'],
            'asset_type': ['All'],
        }
        for page_size in (5000, 15000, 25000, 50000, 100000):
            serializer = InventoryFilterSerializer(data={**base, 'page_size': page_size})
            self.assertTrue(serializer.is_valid(), serializer.errors)
            self.assertEqual(serializer.validated_data['page_size'], page_size)

    def test_page_size_rejects_above_100000(self):
        from inventory.serializers import InventoryFilterSerializer

        serializer = InventoryFilterSerializer(
            data={
                'chainage_start': 0,
                'chainage_end': 100,
                'date': '2026-04-20',
                'direction': ['All'],
                'project_name': ['Agra Bypass Road'],
                'asset_type': ['All'],
                'page_size': 100001,
            }
        )
        self.assertFalse(serializer.is_valid())
        self.assertIn('page_size', serializer.errors)

    def test_page_size_zero_and_negative_rejected(self):
        from inventory.serializers import InventoryFilterSerializer

        base = {
            'chainage_start': 0,
            'chainage_end': 100,
            'date': '2026-04-20',
            'direction': ['All'],
            'project_name': ['Agra Bypass Road'],
            'asset_type': ['All'],
        }
        for page_size in (0, -1):
            serializer = InventoryFilterSerializer(data={**base, 'page_size': page_size})
            self.assertFalse(serializer.is_valid())
            self.assertIn('page_size', serializer.errors)

    def test_values_path_matches_model_instance_serialization(self):
        """Dict rows from values()+quantity_value must match model-style rows."""
        from datetime import date
        from types import SimpleNamespace

        from inventory.services.inventory_filter_response import (
            ASSET_TYPE_FIELD_MAP,
            serialize_inventory_wide_row,
        )

        model_like = SimpleNamespace(
            project_name='Agra Bypass Road',
            chainage_start=10.0,
            chainage_end=10.1,
            direction='Increasing',
            asset_type='Culvert',
            sub_asset_type='',
            latitude=19.0,
            longitude=72.0,
            survey_date=date(2026, 4, 20),
            carriage_type='Flexible',
            lane='L1',
            source='excel_import',
            **{field: 0.0 for field in ASSET_TYPE_FIELD_MAP.values()},
        )
        model_like.culvert = 2.0

        values_like = {
            'project_name': 'Agra Bypass Road',
            'chainage_start': 10.0,
            'chainage_end': 10.1,
            'direction': 'Increasing',
            'asset_type': 'Culvert',
            'sub_asset_type': '',
            'latitude': 19.0,
            'longitude': 72.0,
            'survey_date': date(2026, 4, 20),
            'carriage_type': 'Flexible',
            'lane': 'L1',
            'source': 'excel_import',
            'quantity_value': 2.0,
        }

        self.assertEqual(
            serialize_inventory_wide_row(model_like),
            serialize_inventory_wide_row(values_like),
        )


@override_settings(
    NHIT_CACHE_ENABLED=True,
    CACHES={
        'default': {
            'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
            'LOCATION': 'nhit-inventory-filter-count-tests',
            'KEY_PREFIX': 'nhit-test',
        }
    },
)
class InventoryFilterCountCacheTests(TestCase):
    """Separate COUNT cache must ignore page/page_size and hit across pages."""

    def setUp(self):
        from django.core.cache import cache

        cache.clear()
        self.client = APIClient()
        self.base_payload = {
            'chainage_start': 0,
            'chainage_end': 99999,
            'date': '2026-04-20',
            'direction': ['All'],
            'project_name': ['Agra Bypass Road'],
            'asset_type': ['All'],
            'page': 1,
            'page_size': 5000,
        }
        self.mock_groups = [[{'project_name': 'Agra Bypass Road', 'chainage_end': 1.0}]]

    @patch('inventory.views.build_inventory_filter_groups')
    @patch('inventory.views.build_inventory_filter_queryset')
    def test_second_page_reuses_count_cache(self, mock_build_qs, mock_build_groups):
        mock_qs = mock_build_qs.return_value
        mock_qs.count.return_value = 12000
        mock_build_groups.return_value = self.mock_groups

        first = self.client.post('/inventory_filter/', self.base_payload, format='json')
        second = self.client.post(
            '/inventory_filter/',
            {**self.base_payload, 'page': 2},
            format='json',
        )

        self.assertEqual(first.status_code, status.HTTP_200_OK)
        self.assertEqual(second.status_code, status.HTTP_200_OK)
        self.assertEqual(first['X-Inventory-Row-Count'], '12000')
        self.assertEqual(second['X-Inventory-Row-Count'], '12000')
        self.assertEqual(mock_qs.count.call_count, 1)
        self.assertEqual(mock_build_groups.call_count, 2)

    @patch('inventory.views.build_inventory_filter_groups')
    @patch('inventory.views.build_inventory_filter_queryset')
    def test_different_page_size_reuses_count_cache(self, mock_build_qs, mock_build_groups):
        mock_qs = mock_build_qs.return_value
        mock_qs.count.return_value = 12000
        mock_build_groups.return_value = self.mock_groups

        self.client.post('/inventory_filter/', self.base_payload, format='json')
        self.client.post(
            '/inventory_filter/',
            {**self.base_payload, 'page_size': 7500},
            format='json',
        )
        self.assertEqual(mock_qs.count.call_count, 1)

    @patch('inventory.views.build_inventory_filter_groups')
    @patch('inventory.views.build_inventory_filter_queryset')
    def test_different_filter_misses_count_cache(self, mock_build_qs, mock_build_groups):
        mock_qs = mock_build_qs.return_value
        mock_qs.count.return_value = 12000
        mock_build_groups.return_value = self.mock_groups

        self.client.post('/inventory_filter/', self.base_payload, format='json')
        other = {**self.base_payload, 'project_name': ['Other Project']}
        self.client.post('/inventory_filter/', other, format='json')
        self.assertEqual(mock_qs.count.call_count, 2)

    @patch('inventory.views.build_inventory_filter_groups')
    @patch('inventory.views.build_inventory_filter_queryset')
    def test_page_cache_hit_skips_builder(self, mock_build_qs, mock_build_groups):
        mock_qs = mock_build_qs.return_value
        mock_qs.count.return_value = 12000
        mock_build_groups.return_value = self.mock_groups

        self.client.post('/inventory_filter/', self.base_payload, format='json')
        self.client.post('/inventory_filter/', self.base_payload, format='json')
        self.assertEqual(mock_qs.count.call_count, 1)
        self.assertEqual(mock_build_groups.call_count, 1)

    @patch('inventory.views.build_inventory_filter_groups')
    @patch('inventory.views.build_inventory_filter_queryset')
    @override_settings(NHIT_CACHE_ENABLED=False)
    def test_redis_disabled_still_counts_from_db(self, mock_build_qs, mock_build_groups):
        mock_qs = mock_build_qs.return_value
        mock_qs.count.return_value = 42
        mock_build_groups.return_value = self.mock_groups

        response = self.client.post('/inventory_filter/', self.base_payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response['X-Inventory-Row-Count'], '42')
        self.assertEqual(mock_qs.count.call_count, 1)

    @patch('inventory.views.should_log_inventory_filter_perf', return_value=True)
    @patch('inventory.views.log_inventory_filter_performance')
    @patch('inventory.views.build_inventory_filter_groups')
    @patch('inventory.views.build_inventory_filter_queryset')
    def test_observability_log_has_safe_fields(
        self,
        mock_build_qs,
        mock_build_groups,
        mock_log,
        _should_log,
    ):
        mock_qs = mock_build_qs.return_value
        mock_qs.count.return_value = 12000
        mock_build_groups.return_value = self.mock_groups

        response = self.client.post(
            '/inventory_filter/',
            self.base_payload,
            format='json',
            HTTP_X_REQUEST_ID='req-test-123',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        mock_log.assert_called_once()
        fields = mock_log.call_args.args[0]
        self.assertEqual(fields['request_id'], 'req-test-123')
        self.assertEqual(fields['endpoint'], '/inventory_filter/')
        self.assertEqual(fields['page'], 1)
        self.assertEqual(fields['page_size'], 5000)
        self.assertIn('filter_hash', fields)
        self.assertIn('cache_hit', fields)
        self.assertIn('count_cache_hit', fields)
        self.assertIn('total_ms', fields)
        self.assertIn('query_count', fields)
        self.assertIn('response_bytes', fields)
        serialized = json.dumps(fields)
        # Must never appear even if a client sent them on the request.
        self.assertNotIn('Bearer', serialized)
        self.assertNotIn('Authorization', serialized)
        self.assertNotIn('redis://', serialized.lower())
        self.assertNotIn('password', serialized.lower())
        self.assertNotIn('JWT', serialized)

        # Explicitly ensure the logger helper only serializes provided fields.
        from inventory.services.inventory_filter_perf import log_inventory_filter_performance
        with self.assertLogs('nhit.inventory_filter.perf', level='INFO') as captured:
            log_inventory_filter_performance({
                'request_id': 'x',
                'endpoint': '/inventory_filter/',
                'filter_hash': 'abc',
            })
        joined = ' '.join(captured.output)
        self.assertIn('inventory_filter_performance', joined)
        self.assertNotIn('Authorization', joined)
        self.assertNotIn('redis://', joined.lower())



class InventorySummaryServiceTests(SimpleTestCase):
    """Unit tests for summary formatting and aggregate construction."""

    def test_summary_fields_match_inventory_filter_asset_map(self):
        from inventory.services.inventory_filter_response import ASSET_TYPE_FIELD_MAP
        from inventory.services.inventory_summary import INVENTORY_SUMMARY_FIELDS

        self.assertEqual(list(INVENTORY_SUMMARY_FIELDS), list(ASSET_TYPE_FIELD_MAP.values()))

    def test_format_summary_number_preserves_int_float_zero(self):
        from inventory.services.inventory_summary import _format_summary_number

        self.assertEqual(_format_summary_number(None), 0)
        self.assertEqual(_format_summary_number(0.0), 0)
        self.assertEqual(_format_summary_number(44.0), 44)
        self.assertEqual(_format_summary_number(39.33), 39.33)

    @patch('inventory.services.inventory_summary.build_inventory_filter_queryset')
    def test_aggregate_uses_single_queryset_aggregate(self, mock_build_qs):
        from inventory.services.inventory_filter_response import ASSET_TYPE_FIELD_MAP
        from inventory.services.inventory_summary import (
            INVENTORY_SUMMARY_FIELDS,
            build_inventory_summary_aggregates,
        )

        mock_qs = mock_build_qs.return_value
        mock_qs.aggregate.return_value = {field: 0.0 for field in INVENTORY_SUMMARY_FIELDS}
        mock_qs.aggregate.return_value['trees'] = 14367.0
        mock_qs.aggregate.return_value['delineators'] = 481.0
        mock_qs.aggregate.return_value['crash_barrier'] = 62787.0
        mock_qs.aggregate.return_value['service_road'] = 21.334
        mock_qs.aggregate.return_value['toll_plaza'] = 1

        data = {
            'chainage_start': 0,
            'chainage_end': 100,
            'date': date(2026, 7, 28),
            'direction': [],
            'project_name': ['Kothakota to Kurnool'],
            'asset_type': [],
            'page': 1,
            'page_size': 5000,
        }
        summary = build_inventory_summary_aggregates(data)
        self.assertEqual(mock_qs.aggregate.call_count, 1)
        kwargs = mock_qs.aggregate.call_args.kwargs
        # Column SUMs are unrestricted (no Case/When asset_type gate) except toll_plaza.
        self.assertIn('trees', kwargs)
        self.assertIn('toll_plaza', kwargs)
        self.assertEqual(summary['trees'], 14367)
        self.assertEqual(summary['delineators'], 481)
        self.assertEqual(summary['crash_barrier'], 62787)
        self.assertEqual(summary['service_road'], 21.334)
        self.assertEqual(summary['toll_plaza'], 1)
        for field in ASSET_TYPE_FIELD_MAP.values():
            self.assertIn(field, summary)


@override_settings(NHIT_CACHE_ENABLED=False)
class InventorySummaryViewTests(TestCase):
    """HTTP contract for POST /inventory_summary/."""

    def setUp(self):
        self.client = APIClient()
        self.base_payload = {
            'chainage_start': 0,
            'chainage_end': 99999,
            'date': '2026-07-28',
            'direction': ['All'],
            'project_name': ['Kothakota to Kurnool'],
            'asset_type': ['All'],
            'page': 1,
            'page_size': 5000,
        }
        self.sample_summary = {
            field: 0
            for field in (
                'trees', 'culvert', 'street_lights', 'bridges', 'traffic_signals',
                'bus_stop', 'truck_layby', 'toll_plaza', 'adjacent_road', 'toilet_blocks',
                'rest_area', 'rcc_drain', 'fuel_station', 'emergency_call_box', 'tunnels',
                'footpath', 'junction', 'sign_boards', 'solar_blinker', 'median_plants',
                'service_road', 'km_stones', 'crash_barrier', 'median_opening', 'kerb',
                'guard_post', 'delineators', 'pedestrian_guard_rail', 'slip_roads', 'bus_bays',
            )
        }
        self.sample_summary['trees'] = 12
        self.sample_summary['service_road'] = 39.33
        self.sample_summary['crash_barrier'] = 100

    @patch('inventory.views.build_inventory_summary_aggregates')
    def test_accepts_same_body_as_inventory_filter(self, mock_agg):
        mock_agg.return_value = dict(self.sample_summary)
        response = self.client.post('/inventory_summary/', self.base_payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['trees'], 12)
        self.assertEqual(response.data['service_road'], 39.33)
        self.assertNotIn('X-Inventory-Paginated', response)
        self.assertNotIn('X-Inventory-Page', response)

    @patch('inventory.views.build_inventory_summary_aggregates')
    def test_page_size_does_not_change_summary(self, mock_agg):
        mock_agg.return_value = dict(self.sample_summary)
        a = self.client.post('/inventory_summary/', {**self.base_payload, 'page': 1, 'page_size': 100}, format='json')
        b = self.client.post('/inventory_summary/', {**self.base_payload, 'page': 10, 'page_size': 50000}, format='json')
        self.assertEqual(a.data, b.data)
        self.assertEqual(mock_agg.call_count, 2)
        # Both calls receive validated data with different page fields but same filters;
        # builder ignores page for aggregation — assert filter fields equal.
        first_data = mock_agg.call_args_list[0].args[0]
        second_data = mock_agg.call_args_list[1].args[0]
        for key in ('project_name', 'date', 'direction', 'asset_type', 'chainage_start', 'chainage_end'):
            self.assertEqual(first_data[key], second_data[key])

    @patch('inventory.views.build_inventory_summary_aggregates')
    def test_empty_result_returns_zero_filled_object(self, mock_agg):
        zeros = {k: 0 for k in self.sample_summary}
        mock_agg.return_value = zeros
        response = self.client.post('/inventory_summary/', self.base_payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data, zeros)

    def test_invalid_body_rejected(self):
        response = self.client.post('/inventory_summary/', {'project_name': ['X']}, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


@override_settings(
    NHIT_CACHE_ENABLED=True,
    CACHES={
        'default': {
            'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
            'LOCATION': 'nhit-inventory-summary-tests',
            'KEY_PREFIX': 'nhit-summary-test',
        }
    },
)
class InventorySummaryCacheTests(TestCase):
    def setUp(self):
        from django.core.cache import cache

        cache.clear()
        self.client = APIClient()
        self.base_payload = {
            'chainage_start': 0,
            'chainage_end': 99999,
            'date': '2026-07-28',
            'direction': ['All'],
            'project_name': ['Kothakota to Kurnool'],
            'asset_type': ['All'],
            'page': 1,
            'page_size': 5000,
        }
        self.sample_summary = {
            'trees': 5,
            'culvert': 0,
            'street_lights': 0,
            'bridges': 0,
            'traffic_signals': 0,
            'bus_stop': 0,
            'truck_layby': 0,
            'toll_plaza': 0,
            'adjacent_road': 0,
            'toilet_blocks': 0,
            'rest_area': 0,
            'rcc_drain': 0,
            'fuel_station': 0,
            'emergency_call_box': 0,
            'tunnels': 0,
            'footpath': 0,
            'junction': 0,
            'sign_boards': 0,
            'solar_blinker': 0,
            'median_plants': 0,
            'service_road': 1.5,
            'km_stones': 0,
            'crash_barrier': 0,
            'median_opening': 0,
            'kerb': 0,
            'guard_post': 0,
            'delineators': 0,
            'pedestrian_guard_rail': 0,
            'slip_roads': 0,
            'bus_bays': 0,
        }

    @patch('inventory.views.build_inventory_summary_aggregates')
    def test_second_request_is_cache_hit_without_rebuild(self, mock_agg):
        mock_agg.return_value = dict(self.sample_summary)
        first = self.client.post('/inventory_summary/', self.base_payload, format='json')
        second = self.client.post('/inventory_summary/', self.base_payload, format='json')
        self.assertEqual(first.status_code, status.HTTP_200_OK)
        self.assertEqual(second.status_code, status.HTTP_200_OK)
        self.assertEqual(first.data, second.data)
        self.assertEqual(mock_agg.call_count, 1)

    @patch('inventory.views.build_inventory_summary_aggregates')
    def test_page_fields_share_summary_cache_key(self, mock_agg):
        mock_agg.return_value = dict(self.sample_summary)
        self.client.post('/inventory_summary/', self.base_payload, format='json')
        self.client.post(
            '/inventory_summary/',
            {**self.base_payload, 'page': 9, 'page_size': 25000},
            format='json',
        )
        self.assertEqual(mock_agg.call_count, 1)

    @patch('inventory.views.build_inventory_summary_aggregates')
    def test_filter_change_misses_cache(self, mock_agg):
        mock_agg.return_value = dict(self.sample_summary)
        self.client.post('/inventory_summary/', self.base_payload, format='json')
        other = {**self.base_payload, 'project_name': ['Other Project']}
        self.client.post('/inventory_summary/', other, format='json')
        self.assertEqual(mock_agg.call_count, 2)


class InventorySummaryParityTests(SimpleTestCase):
    """Summary totals must match summing inventory_filter quantity columns."""

    def test_python_reference_sum_matches_unrestricted_column_sums(self):
        """Dashboard-style totals = SUM of each quantity column (not asset_type-gated)."""
        from inventory.services.inventory_filter_response import ASSET_TYPE_FIELD_MAP
        from inventory.services.inventory_summary import (
            INVENTORY_SUMMARY_FIELDS,
            _format_summary_number,
        )
        from types import SimpleNamespace
        from datetime import date as date_cls

        def blank_item(**overrides):
            base = {
                'project_name': 'P',
                'chainage_start': 1.0,
                'chainage_end': 2.0,
                'direction': 'Increasing',
                'asset_type': 'Trees',
                'latitude': 1.0,
                'longitude': 2.0,
                'survey_date': date_cls(2026, 7, 28),
                'sub_asset_type': '',
                'carriage_type': 'Flexible',
                'lane': 'L1',
                'image_url': '',
                'source': 'SAR',
            }
            for field in ASSET_TYPE_FIELD_MAP.values():
                base[field] = 0.0
            base.update(overrides)
            return SimpleNamespace(**base)

        rows = [
            blank_item(asset_type='Trees', trees=3.0),
            blank_item(asset_type='Tree', trees=7.0),  # label variant still in Trees col
            blank_item(asset_type='Delineator', delineators=481.0),
            blank_item(asset_type='Service Road', service_road=12.5),
            blank_item(asset_type='Service Road', service_road=8.834),
            blank_item(asset_type='Crash Barrier', crash_barrier=100.0),
            blank_item(asset_type='Slip Roads', crash_barrier=10.0, slip_roads=1.0),
            blank_item(asset_type='Toll Plaza', toll_plaza=1.0, latitude=10.0, longitude=20.0),
            blank_item(asset_type='Toll Plaza', toll_plaza=1.0, latitude=10.0, longitude=20.0, direction='Decreasing'),
        ]

        expected = {field: 0.0 for field in INVENTORY_SUMMARY_FIELDS}
        for item in rows:
            for field in INVENTORY_SUMMARY_FIELDS:
                if field == 'toll_plaza':
                    continue
                expected[field] += float(getattr(item, field) or 0.0)
        # Unique lat/lon for toll plaza
        expected['toll_plaza'] = 1.0

        formatted = {k: _format_summary_number(v) for k, v in expected.items()}
        self.assertEqual(formatted['trees'], 10)
        self.assertEqual(formatted['delineators'], 481)
        self.assertEqual(formatted['service_road'], 21.334)
        self.assertEqual(formatted['crash_barrier'], 110)
        self.assertEqual(formatted['slip_roads'], 1)
        self.assertEqual(formatted['toll_plaza'], 1)
        self.assertEqual(formatted['rcc_drain'], 0)
