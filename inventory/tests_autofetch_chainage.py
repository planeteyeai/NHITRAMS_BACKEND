"""Tests for auto-fetch chainage from GPS."""

from django.test import TestCase, override_settings
from rest_framework import status
from rest_framework.test import APIClient

from authentication.jwt_utils import create_access_token
from inventory.models import ChainageReferencePoint
from inventory.services.chainage_lookup import find_nearest_chainage, haversine_meters


@override_settings(JWT_SECRET_KEY='test-jwt-secret')
class AutoFetchChainageAPITests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.token = create_access_token('ram.patil')
        self.auth = {'HTTP_AUTHORIZATION': f'Bearer {self.token}'}

        # Synthetic corridor around the first KML point.
        ChainageReferencePoint.objects.bulk_create(
            [
                ChainageReferencePoint(
                    project_name='Borkhedi to Wadner',
                    chainage_meters=36600.0,
                    chainage_km=36.600,
                    latitude=20.8462077,
                    longitude=78.9606274,
                ),
                ChainageReferencePoint(
                    project_name='Borkhedi to Wadner',
                    chainage_meters=36601.0,
                    chainage_km=36.601,
                    latitude=20.846198705,
                    longitude=78.9606265,
                ),
                ChainageReferencePoint(
                    project_name='Other Project',
                    chainage_meters=1000.0,
                    chainage_km=1.000,
                    latitude=21.0,
                    longitude=79.0,
                ),
            ]
        )

    def test_requires_auth(self):
        response = self.client.post(
            '/api/inventory/autofetch-chainage/',
            {'latitude': 20.8462077, 'longitude': 78.9606274},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_post_returns_nearest_chainage(self):
        response = self.client.post(
            '/api/inventory/autofetch-chainage/',
            {'latitude': 20.8462077, 'longitude': 78.9606274},
            format='json',
            **self.auth,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data['success'])
        data = response.data['data']
        self.assertEqual(data['chainage_display'], 'Km 36.600')
        self.assertEqual(data['chainage_meters'], 36600.0)
        self.assertEqual(data['project_name'], 'Borkhedi to Wadner')
        self.assertNotIn('road_name', data)
        self.assertNotIn('chainage_km', data)
        self.assertLessEqual(data['distance_from_road_meters'], 1.0)

    def test_get_query_params(self):
        response = self.client.get(
            '/api/inventory/autofetch-chainage/',
            {'latitude': 20.846198705, 'longitude': 78.9606265},
            **self.auth,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['data']['chainage_display'], 'Km 36.601')
        self.assertEqual(response.data['data']['project_name'], 'Borkhedi to Wadner')

    def test_project_name_filter(self):
        response = self.client.post(
            '/api/inventory/autofetch-chainage/',
            {
                'project_name': 'Borkhedi to Wadner',
                'latitude': 20.8462077,
                'longitude': 78.9606274,
            },
            format='json',
            **self.auth,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['data']['project_name'], 'Borkhedi to Wadner')

    def test_legacy_road_name_still_accepted(self):
        response = self.client.post(
            '/api/inventory/autofetch-chainage/',
            {
                'road_name': 'Borkhedi to Wadner',
                'latitude': 20.8462077,
                'longitude': 78.9606274,
            },
            format='json',
            **self.auth,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.data['data']
        self.assertEqual(data['project_name'], 'Borkhedi to Wadner')
        self.assertNotIn('road_name', data)

    def test_project_name_takes_priority_over_road_name(self):
        response = self.client.post(
            '/api/inventory/autofetch-chainage/',
            {
                'project_name': 'Borkhedi to Wadner',
                'road_name': 'Other Project',
                'latitude': 20.8462077,
                'longitude': 78.9606274,
            },
            format='json',
            **self.auth,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['data']['project_name'], 'Borkhedi to Wadner')

    def test_far_away_returns_404(self):
        response = self.client.post(
            '/api/inventory/autofetch-chainage/',
            {'latitude': 0.0, 'longitude': 0.0},
            format='json',
            **self.auth,
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertFalse(response.data['success'])

    def test_invalid_latitude(self):
        response = self.client.post(
            '/api/inventory/autofetch-chainage/',
            {'latitude': 120.0, 'longitude': 78.9},
            format='json',
            **self.auth,
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_haversine_zero_for_same_point(self):
        self.assertEqual(haversine_meters(20.0, 78.0, 20.0, 78.0), 0.0)

    def test_service_prefers_closer_point(self):
        result = find_nearest_chainage(20.846198705, 78.9606265)
        self.assertEqual(result['chainage_meters'], 36601.0)
        self.assertEqual(result['chainage_display'], 'Km 36.601')
        self.assertEqual(result['project_name'], 'Borkhedi to Wadner')
