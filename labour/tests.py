"""Tests for Labour Management CRUD APIs."""

from datetime import date, timedelta

from django.test import TestCase, override_settings
from rest_framework import status
from rest_framework.test import APIClient

from authentication.jwt_utils import create_access_token
from labour.models import Labour


SAMPLE_PAYLOAD = {
    'first_name': 'Raj',
    'last_name': 'Patil',
    'date_of_birth': '1995-05-15',
    'labour_type': 'Skilled',
    'crew_id': 'CRW001',
    'gender': 'Male',
    'contact_number': '9876543210',
    'address': 'Nashik, Maharashtra',
}


@override_settings(JWT_SECRET_KEY='test-jwt-secret')
class LabourAPITests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.token = create_access_token('ram.patil')
        self.auth = {'HTTP_AUTHORIZATION': f'Bearer {self.token}'}

    def _create(self, **overrides):
        payload = {**SAMPLE_PAYLOAD, **overrides}
        return self.client.post('/api/labours/', payload, format='json', **self.auth)

    def test_create_labour_success(self):
        response = self._create()
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(response.data['success'])
        self.assertEqual(response.data['data']['first_name'], 'Raj')
        self.assertEqual(response.data['data']['created_by'], 'ram.patil')
        self.assertTrue(Labour.objects.filter(pk=response.data['data']['id']).exists())

    def test_create_requires_auth(self):
        response = self.client.post('/api/labours/', SAMPLE_PAYLOAD, format='json')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_create_missing_first_name(self):
        payload = {**SAMPLE_PAYLOAD}
        del payload['first_name']
        response = self.client.post('/api/labours/', payload, format='json', **self.auth)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(response.data['success'])
        self.assertIn('first_name', response.data['error']['details'])

    def test_create_missing_last_name(self):
        payload = {**SAMPLE_PAYLOAD}
        del payload['last_name']
        response = self.client.post('/api/labours/', payload, format='json', **self.auth)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('last_name', response.data['error']['details'])

    def test_create_future_dob(self):
        future = (date.today() + timedelta(days=2)).isoformat()
        response = self._create(date_of_birth=future)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('date_of_birth', response.data['error']['details'])

    def test_create_invalid_labour_type(self):
        response = self._create(labour_type='Expert')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('labour_type', response.data['error']['details'])

    def test_create_invalid_gender(self):
        response = self._create(gender='Unknown')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('gender', response.data['error']['details'])

    def test_create_invalid_contact(self):
        response = self._create(contact_number='12345')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('contact_number', response.data['error']['details'])

    def test_create_missing_address(self):
        payload = {**SAMPLE_PAYLOAD}
        del payload['address']
        response = self.client.post('/api/labours/', payload, format='json', **self.auth)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('address', response.data['error']['details'])

    def test_create_duplicate_crew_name_dob(self):
        self.assertEqual(self._create().status_code, status.HTTP_201_CREATED)
        response = self._create(contact_number='9876543211')
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(response.data['error']['code'], 'DUPLICATE_LABOUR')

    def test_list_pagination_search_filter_sort(self):
        self._create()
        self._create(
            first_name='Sita',
            last_name='Sharma',
            crew_id='CRW002',
            contact_number='9123456780',
            labour_type='Unskilled',
            gender='Female',
        )
        response = self.client.get(
            '/api/labours/?page=1&page_size=1&search=Raj&sort_by=first_name&sort_order=asc',
            **self.auth,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data['success'])
        self.assertEqual(len(response.data['data']), 1)
        self.assertEqual(response.data['pagination']['page'], 1)
        self.assertEqual(response.data['pagination']['page_size'], 1)
        self.assertEqual(response.data['pagination']['total'], 1)

        filtered = self.client.get(
            '/api/labours/?labour_type=Unskilled&gender=Female',
            **self.auth,
        )
        self.assertEqual(filtered.status_code, status.HTTP_200_OK)
        self.assertEqual(filtered.data['pagination']['total'], 1)
        self.assertEqual(filtered.data['data'][0]['first_name'], 'Sita')

    def test_get_existing_and_missing(self):
        created = self._create()
        labour_id = created.data['data']['id']
        ok = self.client.get(f'/api/labours/{labour_id}/', **self.auth)
        self.assertEqual(ok.status_code, status.HTTP_200_OK)
        self.assertEqual(ok.data['data']['id'], labour_id)

        missing = self.client.get('/api/labours/999999/', **self.auth)
        self.assertEqual(missing.status_code, status.HTTP_404_NOT_FOUND)

    def test_put_full_update(self):
        labour_id = self._create().data['data']['id']
        payload = {
            **SAMPLE_PAYLOAD,
            'crew_id': 'CRW002',
            'contact_number': '9876543211',
            'address': 'Pune, Maharashtra',
        }
        response = self.client.put(
            f'/api/labours/{labour_id}/',
            payload,
            format='json',
            **self.auth,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['data']['crew_id'], 'CRW002')
        self.assertEqual(response.data['data']['address'], 'Pune, Maharashtra')

    def test_put_nonexistent(self):
        response = self.client.put(
            '/api/labours/999999/',
            SAMPLE_PAYLOAD,
            format='json',
            **self.auth,
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_patch_partial(self):
        labour_id = self._create().data['data']['id']
        response = self.client.patch(
            f'/api/labours/{labour_id}/',
            {'contact_number': '9999999999'},
            format='json',
            **self.auth,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['data']['contact_number'], '9999999999')
        self.assertEqual(response.data['data']['first_name'], 'Raj')

    def test_patch_invalid_contact(self):
        labour_id = self._create().data['data']['id']
        response = self.client.patch(
            f'/api/labours/{labour_id}/',
            {'contact_number': 'abc'},
            format='json',
            **self.auth,
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_delete_existing_and_missing(self):
        labour_id = self._create().data['data']['id']
        response = self.client.delete(f'/api/labours/{labour_id}/', **self.auth)
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Labour.objects.filter(pk=labour_id).exists())

        missing = self.client.delete('/api/labours/999999/', **self.auth)
        self.assertEqual(missing.status_code, status.HTTP_404_NOT_FOUND)

    def test_masters_endpoint(self):
        response = self.client.get('/api/labours/masters/', **self.auth)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('labour_types', response.data['data'])
        self.assertIn('genders', response.data['data'])
