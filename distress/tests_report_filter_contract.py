"""Contract tests for POST /distress_report_filter/."""

from __future__ import annotations

from datetime import date

from django.db import connection
from django.test import TestCase, override_settings
from rest_framework import status
from rest_framework.test import APIClient

from authentication.authentication import JWTAuthentication
from authentication.jwt_utils import create_access_token
from authentication.permissions import IsJWTAuthenticated
from distress.models import Distress, DistressType, Project, RoadSegment
from distress.serializers import DistressFilterSerializer
from distress.views import DistressFullResponseView

ENDPOINT = '/distress_report_filter/'

REQUIRED_RESPONSE_KEYS = {
    'distress_id',
    'latitude',
    'longitude',
    'chainage_start',
    'chainage_end',
    'project_name',
    'distress_type',
    'direction',
    'total_distress',
    'oblique_crack',
    'pothole',
    'edge_break',
    'patching',
    'bleeding',
    'hotspots',
    'alligator_crack',
    'roughness',
    'repair',
    'block_crack',
    'rutting',
    'longitudinal_crack',
    'raveling',
    'date',
    'length',
    'area',
    'carriage_type',
    'single_discrete_crack',
    'multiple_cracks',
    'joint_crack',
    'joint_seal_defects',
    'punchout',
    'slippage',
    'heaves',
    'simple_crack',
    'transverse_crack',
    'width',
    'depth',
    'lane',
    'hairline_crack',
    'hungry_surface',
    'settlement',
    'shoving',
    'stripping',
    'faulting_(stepping)_in_cracks',
    'popout_(small_hole)',
    'single_longitudinal_crack',
    'blowup_or_bulking',
    'polished_surface/_glazing',
    'depression',
    'image_url',
}

SAMPLE_REQUEST = {
    'chainage_start': 0,
    'chainage_end': 1381,
    'date': '2026-07-20',
    'direction': ['All'],
    'project_name': ['Abu Road to Swaroopganj'],
    'distress_type': ['All'],
}


def _create_unmanaged_tables():
    with connection.schema_editor() as schema_editor:
        for model in (Project, DistressType, RoadSegment, Distress):
            try:
                schema_editor.create_model(model)
            except Exception:
                pass


@override_settings(JWT_SECRET_KEY='test-jwt-secret')
class DistressReportFilterContractTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        _create_unmanaged_tables()

    def setUp(self):
        self.client = APIClient()
        self.token = create_access_token('76')
        self.auth = {'HTTP_AUTHORIZATION': f'Bearer {self.token}'}

        self.project = Project.objects.create(project_name='Abu Road to Swaroopganj')
        self.dtype = DistressType.objects.create(distress_type_name='pothole')
        self.segment = RoadSegment.objects.create(
            project=self.project,
            chainage_start=650.0,
            chainage_end=650.5,
            direction='Increasing',
            carriage_type='Flexible',
            lane='L1',
        )
        self.prediction = Distress.objects.create(
            segment=self.segment,
            project=self.project,
            distress_type=self.dtype,
            distress_date=date(2026, 7, 20),
            latitude=24.5,
            longitude=72.8,
            count_val=1,
            length_val=1.2,
            area_val=0.5,
            width_val=0.3,
            depth_val=0.1,
        )

    def test_serializer_accepts_frontend_payload(self):
        serializer = DistressFilterSerializer(data=SAMPLE_REQUEST)
        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertEqual(serializer.validated_data['chainage_start'], 0.0)
        self.assertEqual(serializer.validated_data['chainage_end'], 1381.0)
        self.assertEqual(str(serializer.validated_data['date']), '2026-07-20')
        self.assertEqual(serializer.validated_data['direction'], ['All'])
        self.assertEqual(serializer.validated_data['distress_type'], ['All'])
        self.assertEqual(
            serializer.validated_data['project_name'],
            ['Abu Road to Swaroopganj'],
        )

    def test_invalid_payload_chainage_order(self):
        serializer = DistressFilterSerializer(
            data={**SAMPLE_REQUEST, 'chainage_start': 10, 'chainage_end': 1}
        )
        self.assertFalse(serializer.is_valid())

    def test_unauthorized(self):
        response = self.client.post(ENDPOINT, SAMPLE_REQUEST, format='json')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_uses_shared_inventory_auth(self):
        self.assertEqual(
            DistressFullResponseView.authentication_classes,
            [JWTAuthentication],
        )
        self.assertEqual(
            DistressFullResponseView.permission_classes,
            [IsJWTAuthenticated],
        )

    def test_valid_request_all_filters(self):
        response = self.client.post(
            ENDPOINT, SAMPLE_REQUEST, format='json', **self.auth
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertIsInstance(response.data, list)
        self.assertEqual(len(response.data), 1)
        rows = response.data[0]
        self.assertGreaterEqual(len(rows), 1)
        row = rows[0]
        self.assertTrue(REQUIRED_RESPONSE_KEYS.issubset(row.keys()), sorted(
            REQUIRED_RESPONSE_KEYS - set(row.keys())
        ))
        self.assertEqual(row['distress_id'], self.prediction.distress_id)
        self.assertEqual(row['project_name'], 'Abu Road to Swaroopganj')
        self.assertEqual(row['pothole'], 1.0)
        self.assertEqual(row['image_url'], '')
        self.assertIn('faulting_(stepping)_in_cracks', row)
        self.assertIn('popout_(small_hole)', row)
        self.assertIn('polished_surface/_glazing', row)

    def test_specific_distress_type_filter(self):
        response = self.client.post(
            ENDPOINT,
            {**SAMPLE_REQUEST, 'distress_type': ['pothole']},
            format='json',
            **self.auth,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        rows = response.data[0]
        self.assertTrue(all(r['distress_type'].lower() == 'pothole' for r in rows))

    def test_specific_project_filter(self):
        other = Project.objects.create(project_name='Other Project XYZ')
        other_seg = RoadSegment.objects.create(
            project=other,
            chainage_start=1,
            chainage_end=2,
            direction='Increasing',
        )
        other_type = DistressType.objects.create(distress_type_name='rutting')
        Distress.objects.create(
            segment=other_seg,
            project=other,
            distress_type=other_type,
            distress_date=date(2026, 7, 20),
            latitude=1,
            longitude=1,
            count_val=1,
            length_val=0,
            area_val=0,
            width_val=0,
            depth_val=0,
        )
        response = self.client.post(
            ENDPOINT,
            {**SAMPLE_REQUEST, 'distress_type': ['All']},
            format='json',
            **self.auth,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        names = {r['project_name'] for r in response.data[0]}
        self.assertEqual(names, {'Abu Road to Swaroopganj'})

    def test_chainage_and_date_filter(self):
        response = self.client.post(
            ENDPOINT,
            {
                **SAMPLE_REQUEST,
                'chainage_start': 600,
                'chainage_end': 700,
                'date': '2026-07-20',
            },
            format='json',
            **self.auth,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertGreaterEqual(len(response.data[0]), 1)

    def test_distress_id_present_on_every_row(self):
        response = self.client.post(
            ENDPOINT, SAMPLE_REQUEST, format='json', **self.auth
        )
        for row in response.data[0]:
            self.assertIn('distress_id', row)
            self.assertIsInstance(row['distress_id'], int)
