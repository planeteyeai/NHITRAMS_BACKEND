"""Tests for Material Management CRUD APIs."""

from django.test import TestCase, override_settings
from rest_framework import status
from rest_framework.test import APIClient

from authentication.jwt_utils import create_access_token
from materials.models import Material


NORMAL_PAYLOAD = {
    'material_name': 'Copper Wire',
    'material_unit': 'Meter',
    'material_type': 'Electrical',
    'other_type': '',
    'material_description': 'Copper electrical wire.',
}

OTHER_PAYLOAD = {
    'material_name': 'Special Composite',
    'material_unit': 'Kg',
    'material_type': 'Other',
    'other_type': 'Heat Resistant Composite',
    'material_description': 'Special industrial material.',
}


@override_settings(JWT_SECRET_KEY='test-jwt-secret')
class MaterialAPITests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.token = create_access_token('ram.patil')
        self.auth = {'HTTP_AUTHORIZATION': f'Bearer {self.token}'}

    def _create(self, **overrides):
        payload = {**NORMAL_PAYLOAD, **overrides}
        return self.client.post('/api/materials/', payload, format='json', **self.auth)

    def test_masters_endpoint_not_available(self):
        response = self.client.get('/api/materials/masters/', **self.auth)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_create_normal_material(self):
        response = self._create()
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(response.data['success'])
        self.assertIsNone(response.data['data']['other_type'])
        self.assertEqual(response.data['data']['created_by'], 'ram.patil')

    def test_create_other_material(self):
        response = self.client.post(
            '/api/materials/', OTHER_PAYLOAD, format='json', **self.auth
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['data']['material_type'], 'Other')
        self.assertEqual(
            response.data['data']['other_type'], 'Heat Resistant Composite'
        )

    def test_create_requires_auth(self):
        response = self.client.post('/api/materials/', NORMAL_PAYLOAD, format='json')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_invalid_token_rejected(self):
        response = self.client.get(
            '/api/materials/',
            HTTP_AUTHORIZATION='Bearer invalid-token',
        )
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_create_missing_material_name(self):
        payload = {**NORMAL_PAYLOAD}
        del payload['material_name']
        response = self.client.post('/api/materials/', payload, format='json', **self.auth)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('material_name', response.data['error']['details'])

    def test_create_missing_material_unit(self):
        payload = {**NORMAL_PAYLOAD}
        del payload['material_unit']
        response = self.client.post('/api/materials/', payload, format='json', **self.auth)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('material_unit', response.data['error']['details'])

    def test_create_missing_material_type(self):
        payload = {**NORMAL_PAYLOAD}
        del payload['material_type']
        response = self.client.post('/api/materials/', payload, format='json', **self.auth)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('material_type', response.data['error']['details'])

    def test_create_empty_material_unit(self):
        response = self._create(material_unit='')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('material_unit', response.data['error']['details'])

    def test_create_empty_material_type(self):
        response = self._create(material_type='')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('material_type', response.data['error']['details'])

    def test_create_accepts_frontend_dropdown_values_as_is(self):
        response = self._create(
            material_name='Custom Unit Material',
            material_unit='KG',
            material_type='Custom Frontend Type',
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        data = response.data['data']
        self.assertEqual(data['material_unit'], 'KG')
        self.assertEqual(data['material_type'], 'Custom Frontend Type')

    def test_create_other_without_other_type(self):
        payload = {
            **OTHER_PAYLOAD,
            'material_name': 'Missing Other Type',
            'other_type': '',
        }
        del payload['other_type']
        response = self.client.post('/api/materials/', payload, format='json', **self.auth)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('other_type', response.data['error']['details'])
        self.assertIn(
            'Other Type is required when Material Type is Other.',
            str(response.data['error']['details']['other_type']),
        )

    def test_create_duplicate(self):
        self.assertEqual(self._create().status_code, status.HTTP_201_CREATED)
        response = self._create(material_description='Duplicate attempt')
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(response.data['error']['code'], 'DUPLICATE_MATERIAL')

    def test_list_search_filter_sort(self):
        self._create()
        self._create(
            material_name='Steel Rod',
            material_unit='Kg',
            material_type='Raw Material',
            material_description='Construction steel',
        )
        response = self.client.get(
            '/api/materials/?search=Copper&sort_by=material_name&sort_order=asc',
            **self.auth,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['pagination']['total'], 1)

        filtered = self.client.get(
            '/api/materials/?material_type=Raw Material&material_unit=Kg',
            **self.auth,
        )
        self.assertEqual(filtered.status_code, status.HTTP_200_OK)
        self.assertEqual(filtered.data['pagination']['total'], 1)

    def test_get_by_id_and_missing(self):
        material_id = self._create().data['data']['id']
        ok = self.client.get(f'/api/materials/{material_id}/', **self.auth)
        self.assertEqual(ok.status_code, status.HTTP_200_OK)
        missing = self.client.get('/api/materials/999999/', **self.auth)
        self.assertEqual(missing.status_code, status.HTTP_404_NOT_FOUND)

    def test_put_full_update(self):
        material_id = self._create().data['data']['id']
        payload = {
            **NORMAL_PAYLOAD,
            'material_name': 'Copper Wire Premium',
            'material_description': 'Premium copper wire.',
        }
        response = self.client.put(
            f'/api/materials/{material_id}/',
            payload,
            format='json',
            **self.auth,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['data']['material_name'], 'Copper Wire Premium')

    def test_patch_description_only(self):
        material_id = self._create().data['data']['id']
        before = self.client.get(f'/api/materials/{material_id}/', **self.auth).data['data']
        response = self.client.patch(
            f'/api/materials/{material_id}/',
            {'material_description': 'Updated description.'},
            format='json',
            **self.auth,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.data['data']
        self.assertEqual(data['material_description'], 'Updated description.')
        self.assertEqual(data['material_name'], before['material_name'])
        self.assertEqual(data['material_unit'], before['material_unit'])

    def test_patch_electrical_to_other(self):
        material_id = self._create().data['data']['id']
        response = self.client.patch(
            f'/api/materials/{material_id}/',
            {
                'material_type': 'Other',
                'other_type': 'Special Industrial Material',
            },
            format='json',
            **self.auth,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['data']['material_type'], 'Other')
        self.assertEqual(
            response.data['data']['other_type'], 'Special Industrial Material'
        )

    def test_patch_other_without_other_type_fails(self):
        material_id = self._create().data['data']['id']
        response = self.client.patch(
            f'/api/materials/{material_id}/',
            {'material_type': 'Other'},
            format='json',
            **self.auth,
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('other_type', response.data['error']['details'])

    def test_patch_other_to_electrical_clears_other_type(self):
        created = self.client.post(
            '/api/materials/', OTHER_PAYLOAD, format='json', **self.auth
        )
        material_id = created.data['data']['id']
        response = self.client.patch(
            f'/api/materials/{material_id}/',
            {'material_type': 'Electrical'},
            format='json',
            **self.auth,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['data']['material_type'], 'Electrical')
        self.assertIsNone(response.data['data']['other_type'])
        row = Material.objects.get(pk=material_id)
        self.assertEqual(row.other_type, '')

    def test_delete_existing_and_missing(self):
        material_id = self._create().data['data']['id']
        response = self.client.delete(f'/api/materials/{material_id}/', **self.auth)
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Material.objects.filter(pk=material_id).exists())
        missing = self.client.delete('/api/materials/999999/', **self.auth)
        self.assertEqual(missing.status_code, status.HTTP_404_NOT_FOUND)
