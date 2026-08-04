"""Tests for Routine Maintenance APIs."""

from unittest.mock import patch

from django.test import TestCase, override_settings
from rest_framework import status
from rest_framework.test import APIClient

from authentication.jwt_utils import create_access_token
from inventory.models import (
    RoutineMaintenanceCategory,
    RoutineMaintenanceReport,
    RoutineMaintenanceSubcategory,
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
class RoutineMaintenanceAPITests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.token = create_access_token('ram.patil')
        self.other_token = create_access_token('other.user')
        self.auth = {'HTTP_AUTHORIZATION': f'Bearer {self.token}'}
        self.other_auth = {'HTTP_AUTHORIZATION': f'Bearer {self.other_token}'}

        self.category = RoutineMaintenanceCategory.objects.get(name='Median Maintenance')
        self.subcategory = RoutineMaintenanceSubcategory.objects.filter(
            category=self.category
        ).first()
        self.side = SideMaster.objects.get(name='LHS L1')
        self.severity = SeverityMaster.objects.get(name='Low')

    def _payload(self, **overrides):
        data = {
            'project_id': SAMPLE_PROJECT['id'],
            'category_id': self.category.id,
            'subcategory_id': self.subcategory.id,
            'side_id': self.side.id,
            'severity_id': self.severity.id,
            'description_of_work': 'Trimmed median plants',
            'nos': 2,
            'length': 1.5,
            'width': 0.5,
            'depth': 0,
            'total_quantity': 2,
            'Latitude': 24.5,
            'Longitude': 72.8,
            'chainage': '650.200',
        }
        data.update(overrides)
        return data

    def test_master_requires_auth(self):
        response = self.client.get('/api/inventory/routine-maintenance/master')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    @patch(
        'inventory.services.routine_maintenance.list_inventory_projects',
        return_value=[SAMPLE_PROJECT],
    )
    def test_master_returns_nested_dropdowns(self, _mock_projects):
        response = self.client.get('/api/inventory/routine-maintenance/master', **self.auth)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data['success'])
        data = response.data['data']
        self.assertIn('projects', data)
        self.assertIn('categories', data)
        self.assertIn('side_master', data)
        self.assertIn('severity_master', data)
        self.assertEqual(data['projects'][0]['project_name'], 'Abu Road to Swaroopganj')
        self.assertTrue(any(c['name'] == 'Median Maintenance' for c in data['categories']))
        median = next(c for c in data['categories'] if c['name'] == 'Median Maintenance')
        self.assertTrue(median['subcategories'])
        self.assertTrue(any(s['name'] == 'LHS L1' for s in data['side_master']))
        self.assertTrue(any(s['name'] == 'Low' for s in data['severity_master']))

    @patch(
        'inventory.views_routine_maintenance.list_inventory_projects',
        return_value=[SAMPLE_PROJECT],
    )
    def test_projects_list_endpoint(self, _mock_projects):
        response = self.client.get('/api/inventory/projects', **self.auth)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data['success'])
        self.assertEqual(response.data['data'][0]['id'], 1)

    @patch(
        'inventory.serializers_routine_maintenance.resolve_project_details',
        return_value=SAMPLE_PROJECT,
    )
    @patch('inventory.services.routine_maintenance.upload_dpr_images', return_value=[
        'https://nhitbucket.s3.ap-south-1.amazonaws.com/Damaged+Assets/rm.jpg',
    ])
    def test_create_report_uses_jwt_and_project_id(self, mock_upload, _mock_project):
        from django.core.files.uploadedfile import SimpleUploadedFile

        payload = self._payload()
        payload['username'] = 'attacker'
        payload['project_name'] = 'Fake'
        response = self.client.post(
            '/api/inventory/routine-maintenance/report',
            payload,
            format='multipart',
            **self.auth,
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

        payload = self._payload()
        payload['images'] = SimpleUploadedFile('rm.jpg', b'img', content_type='image/jpeg')
        response = self.client.post(
            '/api/inventory/routine-maintenance/report',
            payload,
            format='multipart',
            **self.auth,
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(response.data['success'])
        data = response.data['data']
        self.assertEqual(data['username'], 'ram.patil')
        self.assertEqual(data['project']['project_name'], 'Abu Road to Swaroopganj')
        self.assertEqual(data['project']['id'], 1)
        self.assertEqual(data['category']['name'], 'Median Maintenance')
        self.assertEqual(data['side']['name'], 'LHS L1')
        self.assertEqual(
            data['uploaded_image_urls'],
            ['https://nhitbucket.s3.ap-south-1.amazonaws.com/Damaged+Assets/rm.jpg'],
        )
        mock_upload.assert_called_once()

        report = RoutineMaintenanceReport.objects.get()
        self.assertEqual(report.username, 'ram.patil')
        self.assertEqual(report.project_id, 1)
        self.assertEqual(report.latitude, 24.5)

    @patch(
        'inventory.serializers_routine_maintenance.resolve_project_details',
        return_value=SAMPLE_PROJECT,
    )
    def test_create_rejects_project_name_body_fields(self, _mock):
        payload = self._payload()
        payload['project_name'] = 'Hacked'
        response = self.client.post(
            '/api/inventory/routine-maintenance/report',
            payload,
            format='multipart',
            **self.auth,
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('project_name', response.data['errors'])

    def test_create_rejects_invalid_project_id(self):
        from rest_framework.exceptions import ValidationError as DRFValidationError

        def _raise_invalid(*_args, **_kwargs):
            raise DRFValidationError({'project_id': ['Invalid project selected.']})

        with patch(
            'inventory.serializers_routine_maintenance.resolve_project_details',
            side_effect=_raise_invalid,
        ):
            response = self.client.post(
                '/api/inventory/routine-maintenance/report',
                self._payload(project_id=99999),
                format='multipart',
                **self.auth,
            )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('project_id', response.data['errors'])

    def test_create_rejects_subcategory_from_other_category(self):
        other_cat = RoutineMaintenanceCategory.objects.exclude(pk=self.category.id).first()
        other_sub = RoutineMaintenanceSubcategory.objects.filter(category=other_cat).first()
        with patch(
            'inventory.serializers_routine_maintenance.resolve_project_details',
            return_value=SAMPLE_PROJECT,
        ):
            response = self.client.post(
                '/api/inventory/routine-maintenance/report',
                self._payload(subcategory_id=other_sub.id),
                format='multipart',
                **self.auth,
            )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('subcategory_id', response.data['errors'])

    def test_create_rejects_invalid_lat_long(self):
        with patch(
            'inventory.serializers_routine_maintenance.resolve_project_details',
            return_value=SAMPLE_PROJECT,
        ):
            response = self.client.post(
                '/api/inventory/routine-maintenance/report',
                self._payload(Latitude=120),
                format='multipart',
                **self.auth,
            )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('latitude', response.data['errors'])

    def test_create_without_project_id_returns_400(self):
        payload = self._payload()
        del payload['project_id']
        response = self.client.post(
            '/api/inventory/routine-maintenance/report',
            payload,
            format='multipart',
            **self.auth,
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('project_id', response.data['errors'])

    def test_history_returns_only_own_reports(self):
        own = RoutineMaintenanceReport.objects.create(
            username='ram.patil',
            project_id=1,
            project_name='Abu Road to Swaroopganj',
            start_chainage='646.000',
            end_chainage='680.000',
            total_project_length='34 km',
            category=self.category,
            subcategory=self.subcategory,
            side=self.side,
            severity=self.severity,
            latitude=24.5,
            longitude=72.8,
            chainage='650.200',
            inspection_datetime='2026-08-04T10:00:00Z',
            description_of_work='Own work',
        )
        RoutineMaintenanceReport.objects.create(
            username='other.user',
            project_id=2,
            project_name='Other',
            start_chainage='0',
            end_chainage='1',
            total_project_length='1 km',
            category=self.category,
            subcategory=self.subcategory,
            side=self.side,
            severity=self.severity,
            latitude=1,
            longitude=1,
            chainage='0.1',
            inspection_datetime='2026-08-01T10:00:00Z',
            description_of_work='Other work',
        )

        response = self.client.get('/api/inventory/routine-maintenance/history', **self.auth)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data['data']), 1)
        self.assertEqual(response.data['data'][0]['report_id'], own.id)
        self.assertEqual(response.data['data'][0]['description_of_work'], 'Own work')
        self.assertEqual(
            response.data['data'][0]['project']['project_name'],
            'Abu Road to Swaroopganj',
        )

    def _create_report(self, **overrides):
        data = {
            'username': 'ram.patil',
            'project_id': 1,
            'project_name': 'Abu Road to Swaroopganj',
            'start_chainage': '646.000',
            'end_chainage': '680.000',
            'total_project_length': '34 km',
            'category': self.category,
            'subcategory': self.subcategory,
            'side': self.side,
            'severity': self.severity,
            'latitude': 24.5,
            'longitude': 72.8,
            'chainage': '650.200',
            'inspection_datetime': '2026-08-04T10:00:00Z',
            'description_of_work': 'Trimmed median plants',
            'nos': 2,
            'length': 1.5,
            'width': 0.5,
            'depth': 0,
            'total_quantity': 2,
            'uploaded_image_urls': [
                'https://nhitbucket.s3.ap-south-1.amazonaws.com/Damaged+Assets/rm.jpg',
            ],
            'status': RoutineMaintenanceReport.STATUS_SUBMITTED,
        }
        data.update(overrides)
        return RoutineMaintenanceReport.objects.create(**data)

    @patch('inventory.services.routine_maintenance.upload_dpr_images', return_value=[
        'https://nhitbucket.s3.ap-south-1.amazonaws.com/Damaged+Assets/rm2.jpg',
    ])
    def test_patch_partial_update_and_appends_images(self, mock_upload):
        from django.core.files.uploadedfile import SimpleUploadedFile

        report = self._create_report()
        created_at = report.created_at
        payload = {
            'description_of_work': 'Updated work notes',
            'status': 'draft',
            'images': SimpleUploadedFile('rm2.jpg', b'img', content_type='image/jpeg'),
        }
        response = self.client.patch(
            f'/api/inventory/routine-maintenance/report/{report.id}',
            payload,
            format='multipart',
            **self.auth,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data['success'])
        data = response.data['data']
        self.assertEqual(data['description_of_work'], 'Updated work notes')
        self.assertEqual(data['status'], 'draft')
        self.assertEqual(data['chainage'], '650.200')
        self.assertEqual(data['username'], 'ram.patil')
        self.assertEqual(data['project']['project_name'], 'Abu Road to Swaroopganj')
        self.assertEqual(
            data['uploaded_image_urls'],
            [
                'https://nhitbucket.s3.ap-south-1.amazonaws.com/Damaged+Assets/rm.jpg',
                'https://nhitbucket.s3.ap-south-1.amazonaws.com/Damaged+Assets/rm2.jpg',
            ],
        )
        mock_upload.assert_called_once()
        report.refresh_from_db()
        self.assertEqual(report.created_at, created_at)

    @patch('inventory.services.routine_maintenance.upload_dpr_images', return_value=[
        'https://nhitbucket.s3.ap-south-1.amazonaws.com/Damaged+Assets/rm3.jpg',
    ])
    def test_patch_retain_image_urls_and_new_images(self, mock_upload):
        from django.core.files.uploadedfile import SimpleUploadedFile
        import json

        keep = 'https://nhitbucket.s3.ap-south-1.amazonaws.com/Damaged+Assets/rm.jpg'
        drop = 'https://nhitbucket.s3.ap-south-1.amazonaws.com/Damaged+Assets/drop.jpg'
        report = self._create_report(uploaded_image_urls=[keep, drop])
        payload = {
            'retain_image_urls': json.dumps([keep]),
            'new_images': SimpleUploadedFile('rm3.jpg', b'img', content_type='image/jpeg'),
            'severity_id': self.severity.id,
        }
        response = self.client.patch(
            f'/api/inventory/routine-maintenance/report/{report.id}/',
            payload,
            format='multipart',
            **self.auth,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        report.refresh_from_db()
        self.assertEqual(
            report.uploaded_image_urls,
            [
                keep,
                'https://nhitbucket.s3.ap-south-1.amazonaws.com/Damaged+Assets/rm3.jpg',
            ],
        )
        mock_upload.assert_called_once()

    def test_patch_remove_images(self):
        import json

        keep = 'https://nhitbucket.s3.ap-south-1.amazonaws.com/Damaged+Assets/rm.jpg'
        drop = 'https://nhitbucket.s3.ap-south-1.amazonaws.com/Damaged+Assets/drop.jpg'
        report = self._create_report(uploaded_image_urls=[keep, drop])
        response = self.client.patch(
            f'/api/inventory/routine-maintenance/report/{report.id}',
            {'remove_images': json.dumps([drop])},
            format='multipart',
            **self.auth,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        report.refresh_from_db()
        self.assertEqual(report.uploaded_image_urls, [keep])

    def test_patch_other_users_report_returns_403(self):
        report = self._create_report(username='other.user', project_name='Other')
        response = self.client.patch(
            f'/api/inventory/routine-maintenance/report/{report.id}',
            {'description_of_work': 'hack'},
            format='multipart',
            **self.auth,
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_patch_rejects_server_owned_fields(self):
        report = self._create_report()
        response = self.client.patch(
            f'/api/inventory/routine-maintenance/report/{report.id}',
            {'project_name': 'Hacked', 'description_of_work': 'x'},
            format='multipart',
            **self.auth,
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(response.data['success'])

    def test_patch_rejects_invalid_lat(self):
        report = self._create_report()
        response = self.client.patch(
            f'/api/inventory/routine-maintenance/report/{report.id}',
            {'Latitude': 999},
            format='multipart',
            **self.auth,
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('latitude', response.data['errors'])

    def test_patch_rejects_subcategory_from_other_category(self):
        report = self._create_report()
        other_cat = RoutineMaintenanceCategory.objects.exclude(pk=self.category.id).first()
        other_sub = RoutineMaintenanceSubcategory.objects.filter(category=other_cat).first()
        response = self.client.patch(
            f'/api/inventory/routine-maintenance/report/{report.id}',
            {'subcategory_id': other_sub.id},
            format='multipart',
            **self.auth,
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('subcategory_id', response.data['errors'])

    def test_patch_missing_report_returns_404(self):
        response = self.client.patch(
            '/api/inventory/routine-maintenance/report/99999',
            {'description_of_work': 'x'},
            format='multipart',
            **self.auth,
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_history_reflects_patched_report(self):
        report = self._create_report()
        response = self.client.patch(
            f'/api/inventory/routine-maintenance/report/{report.id}',
            {'description_of_work': 'History sees this'},
            format='multipart',
            **self.auth,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        history = self.client.get('/api/inventory/routine-maintenance/history', **self.auth)
        self.assertEqual(history.status_code, status.HTTP_200_OK)
        self.assertEqual(history.data['data'][0]['description_of_work'], 'History sees this')
