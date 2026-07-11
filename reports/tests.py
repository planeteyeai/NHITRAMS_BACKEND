"""Tests for AI Identifier JWT authentication and report ownership."""

from datetime import datetime, timedelta, timezone

import jwt
from django.test import TestCase, override_settings
from rest_framework import status
from rest_framework.test import APIClient

from authentication.jwt_utils import create_access_token
from reports.models import AIReport


@override_settings(JWT_SECRET_KEY='test-jwt-secret')
class AuthLoginTests(TestCase):
    def setUp(self):
        self.client = APIClient()

    def test_login_returns_jwt(self):
        response = self.client.post('/api/auth/login', {'username': 'ram.patil'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data['success'])
        self.assertIn('token', response.data)
        self.assertEqual(response.data['expires_in'], 86400)

        payload = jwt.decode(response.data['token'], 'test-jwt-secret', algorithms=['HS256'])
        self.assertEqual(payload['username'], 'ram.patil')

    def test_login_requires_username(self):
        response = self.client.post('/api/auth/login', {}, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(response.data['success'])

    def test_login_rejects_blank_username(self):
        response = self.client.post('/api/auth/login', {'username': '   '}, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


@override_settings(JWT_SECRET_KEY='test-jwt-secret')
class ReportAPITests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.token = create_access_token('ram.patil')
        self.other_token = create_access_token('other.user')
        self.auth = {'HTTP_AUTHORIZATION': f'Bearer {self.token}'}
        self.other_auth = {'HTTP_AUTHORIZATION': f'Bearer {self.other_token}'}

    def test_create_report_uses_jwt_username(self):
        payload = {
            'prediction': 'alligator_crack',
            'confidence': 96.2,
            'image_path': '/uploads/img1.jpg',
            'report_json': {'severity': 'A1'},
            'username': 'attacker.user',  # must be ignored
        }
        response = self.client.post('/api/reports', payload, format='json', **self.auth)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['username'], 'ram.patil')
        self.assertEqual(AIReport.objects.get().username, 'ram.patil')

    def test_create_requires_auth(self):
        response = self.client.post(
            '/api/reports',
            {'prediction': 'pothole', 'confidence': 80.0},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_history_returns_only_own_reports(self):
        AIReport.objects.create(username='ram.patil', prediction='pothole', confidence=90.0)
        AIReport.objects.create(username='other.user', prediction='rutting', confidence=70.0)

        response = self.client.get('/api/reports/history', **self.auth)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['username'], 'ram.patil')

    def test_get_own_report(self):
        report = AIReport.objects.create(username='ram.patil', prediction='pothole', confidence=90.0)
        response = self.client.get(f'/api/reports/{report.id}', **self.auth)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['id'], report.id)

    def test_get_other_users_report_returns_403(self):
        report = AIReport.objects.create(username='other.user', prediction='rutting', confidence=70.0)
        response = self.client.get(f'/api/reports/{report.id}', **self.auth)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_delete_own_report(self):
        report = AIReport.objects.create(username='ram.patil', prediction='pothole', confidence=90.0)
        response = self.client.delete(f'/api/reports/{report.id}', **self.auth)
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(AIReport.objects.filter(pk=report.id).exists())

    def test_delete_other_users_report_returns_403(self):
        report = AIReport.objects.create(username='other.user', prediction='rutting', confidence=70.0)
        response = self.client.delete(f'/api/reports/{report.id}', **self.auth)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertTrue(AIReport.objects.filter(pk=report.id).exists())

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
            '/api/reports/history',
            HTTP_AUTHORIZATION=f'Bearer {expired}',
        )
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_invalid_token_returns_401(self):
        response = self.client.get(
            '/api/reports/history',
            HTTP_AUTHORIZATION='Bearer not-a-valid-token',
        )
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_missing_report_returns_404(self):
        response = self.client.get('/api/reports/99999', **self.auth)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
