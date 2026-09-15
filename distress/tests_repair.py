"""Tests for PATCH /api/distress/repair (predicted_distress_id-based repair status)."""

from __future__ import annotations

from datetime import date

from django.db import connection
from django.test import TestCase, override_settings
from rest_framework import status
from rest_framework.test import APIClient

from authentication.jwt_utils import create_access_token
from distress.models import DistressRepairLog, PredictedDistress

ENDPOINT = '/api/distress/repair'


def _create_unmanaged_tables():
    try:
        with connection.schema_editor() as schema_editor:
            schema_editor.create_model(PredictedDistress)
    except Exception:
        pass


@override_settings(JWT_SECRET_KEY='test-jwt-secret')
class DistressRepairAPITests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        _create_unmanaged_tables()

    def setUp(self):
        self.client = APIClient()
        self.token = create_access_token('field.engineer')
        self.auth = {'HTTP_AUTHORIZATION': f'Bearer {self.token}'}

        self.prediction = PredictedDistress.objects.create(
            latitude=24.50012,
            longitude=72.80125,
            project_name='Abu Road to Swaroopganj',
            chainage_start=650.0,
            chainage_end=650.5,
            total_distress=3,
            distress_type='Pothole',
            pothole=3,
            direction='LHS',
            lane='L1',
            distress_date=date(2026, 8, 1),
            carriage_type='Flexible',
        )
        self.payload = {
            'distress_id': self.prediction.predicted_distress_id,
            'status': 'REPAIRED',
        }

    def test_valid_repair_defaults_quantity_1(self):
        response = self.client.patch(ENDPOINT, self.payload, format='json', **self.auth)
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertTrue(response.data['success'])
        data = response.data['data']
        self.assertEqual(data['distress_id'], self.prediction.predicted_distress_id)
        self.assertEqual(data['predicted_quantity'], 3.0)
        self.assertEqual(data['resolved_quantity'], 1.0)
        self.assertEqual(data['remaining_quantity'], 2.0)
        self.assertEqual(data['status'], 'REPAIRED')

        log = DistressRepairLog.objects.get()
        self.assertEqual(log.updated_by, 'field.engineer')
        self.assertEqual(log.distress_id, self.prediction.predicted_distress_id)
        self.assertEqual(log.quantity_resolved, 1.0)
        self.prediction.refresh_from_db()
        self.assertEqual(self.prediction.total_distress, 3)

    def test_not_present(self):
        payload = {**self.payload, 'status': 'NOT_PRESENT'}
        response = self.client.patch(ENDPOINT, payload, format='json', **self.auth)
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(response.data['data']['status'], 'NOT_PRESENT')
        self.assertEqual(DistressRepairLog.objects.get().status, 'NOT_PRESENT')

    def test_partial_repairs_and_remaining_decreases(self):
        r1 = self.client.patch(
            ENDPOINT,
            {**self.payload, 'quantity_resolved': 1},
            format='json',
            **self.auth,
        )
        r2 = self.client.patch(
            ENDPOINT,
            {**self.payload, 'status': 'NOT_PRESENT', 'quantity_resolved': 1},
            format='json',
            **self.auth,
        )
        self.assertEqual(r1.data['data']['remaining_quantity'], 2.0)
        self.assertEqual(r2.data['data']['remaining_quantity'], 1.0)
        self.assertEqual(r2.data['data']['resolved_quantity'], 2.0)
        self.assertEqual(DistressRepairLog.objects.count(), 2)

    def test_full_resolve_then_409(self):
        response = self.client.patch(
            ENDPOINT,
            {**self.payload, 'quantity_resolved': 3},
            format='json',
            **self.auth,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(response.data['data']['remaining_quantity'], 0.0)

        conflict = self.client.patch(ENDPOINT, self.payload, format='json', **self.auth)
        self.assertEqual(conflict.status_code, status.HTTP_409_CONFLICT)
        self.assertFalse(conflict.data['success'])
        self.assertIn('already been resolved', conflict.data['message'])
        self.assertEqual(DistressRepairLog.objects.count(), 1)
        self.assertEqual(PredictedDistress.objects.get().total_distress, 3)

    def test_quantity_overshoot_returns_400(self):
        response = self.client.patch(
            ENDPOINT,
            {**self.payload, 'quantity_resolved': 5},
            format='json',
            **self.auth,
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('quantity_resolved', response.data)

    def test_negative_quantity_returns_400(self):
        response = self.client.patch(
            ENDPOINT,
            {**self.payload, 'quantity_resolved': -1},
            format='json',
            **self.auth,
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_invalid_status(self):
        response = self.client.patch(
            ENDPOINT,
            {**self.payload, 'status': 'FIXED'},
            format='json',
            **self.auth,
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('status', response.data)

    def test_authentication_required(self):
        response = self.client.patch(ENDPOINT, self.payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_audit_log_created(self):
        response = self.client.patch(ENDPOINT, self.payload, format='json', **self.auth)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        log = DistressRepairLog.objects.get()
        self.assertEqual(log.updated_by, 'field.engineer')
        self.assertIsNotNone(log.updated_at)
        self.assertEqual(log.distress_id, self.prediction.predicted_distress_id)

    def test_prediction_table_not_modified(self):
        before = list(
            PredictedDistress.objects.values(
                'predicted_distress_id',
                'total_distress',
                'latitude',
                'longitude',
                'distress_date',
            )
        )
        self.client.patch(ENDPOINT, self.payload, format='json', **self.auth)
        after = list(
            PredictedDistress.objects.values(
                'predicted_distress_id',
                'total_distress',
                'latitude',
                'longitude',
                'distress_date',
            )
        )
        self.assertEqual(before, after)

    def test_unknown_distress_id_returns_404(self):
        response = self.client.patch(
            ENDPOINT,
            {'distress_id': 99999999, 'status': 'REPAIRED'},
            format='json',
            **self.auth,
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertFalse(response.data['success'])

    def test_null_total_distress_returns_400(self):
        self.prediction.total_distress = None
        self.prediction.save(update_fields=['total_distress'])
        response = self.client.patch(ENDPOINT, self.payload, format='json', **self.auth)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('distress_id', response.data)
