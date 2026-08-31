"""Tests for Supplier Management CRUD APIs."""

from django.test import TestCase, override_settings
from rest_framework import status
from rest_framework.test import APIClient

from authentication.jwt_utils import create_access_token
from supplier.models import Supplier


SAMPLE_PAYLOAD = {
    'company_name': 'ABC Electricals Pvt Ltd',
    'contact_person': 'Raj Patil',
    'mobile_number': '9876543210',
    'email': 'raj@abcelectricals.com',
    'state': 'Maharashtra',
    'city': 'Nashik',
    'address': 'MIDC Ambad, Nashik, Maharashtra',
}


@override_settings(JWT_SECRET_KEY='test-jwt-secret')
class SupplierAPITests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.token = create_access_token('ram.patil')
        self.auth = {'HTTP_AUTHORIZATION': f'Bearer {self.token}'}

    def _create(self, **overrides):
        payload = {**SAMPLE_PAYLOAD, **overrides}
        return self.client.post('/api/suppliers/', payload, format='json', **self.auth)

    def test_create_supplier_success(self):
        response = self._create()
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(response.data['success'])
        self.assertEqual(response.data['data']['company_name'], 'ABC Electricals Pvt Ltd')
        self.assertEqual(response.data['data']['email'], 'raj@abcelectricals.com')
        self.assertEqual(response.data['data']['created_by'], 'ram.patil')
        self.assertTrue(Supplier.objects.filter(pk=response.data['data']['id']).exists())

    def test_create_requires_auth(self):
        response = self.client.post('/api/suppliers/', SAMPLE_PAYLOAD, format='json')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_invalid_token_rejected(self):
        response = self.client.get(
            '/api/suppliers/',
            HTTP_AUTHORIZATION='Bearer invalid-token',
        )
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_create_missing_company_name(self):
        payload = {**SAMPLE_PAYLOAD}
        del payload['company_name']
        response = self.client.post('/api/suppliers/', payload, format='json', **self.auth)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(response.data['success'])
        self.assertIn('company_name', response.data['error']['details'])

    def test_create_missing_contact_person(self):
        payload = {**SAMPLE_PAYLOAD}
        del payload['contact_person']
        response = self.client.post('/api/suppliers/', payload, format='json', **self.auth)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('contact_person', response.data['error']['details'])

    def test_create_invalid_mobile(self):
        response = self._create(mobile_number='123')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('mobile_number', response.data['error']['details'])

    def test_create_invalid_email(self):
        response = self._create(email='not-an-email')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('email', response.data['error']['details'])

    def test_create_missing_state(self):
        payload = {**SAMPLE_PAYLOAD}
        del payload['state']
        response = self.client.post('/api/suppliers/', payload, format='json', **self.auth)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('state', response.data['error']['details'])

    def test_create_missing_city(self):
        payload = {**SAMPLE_PAYLOAD}
        del payload['city']
        response = self.client.post('/api/suppliers/', payload, format='json', **self.auth)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('city', response.data['error']['details'])

    def test_create_missing_address(self):
        payload = {**SAMPLE_PAYLOAD}
        del payload['address']
        response = self.client.post('/api/suppliers/', payload, format='json', **self.auth)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('address', response.data['error']['details'])

    def test_create_duplicate_company_email(self):
        self.assertEqual(self._create().status_code, status.HTTP_201_CREATED)
        response = self._create(mobile_number='9123456780')
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(response.data['error']['code'], 'DUPLICATE_SUPPLIER')

    def test_list_pagination_search_filter_sort(self):
        self._create()
        self._create(
            company_name='XYZ Supplies',
            email='info@xyz.com',
            city='Mumbai',
            state='Maharashtra',
            contact_person='Amit Sharma',
        )
        response = self.client.get(
            '/api/suppliers/?page=1&page_size=1&search=ABC&sort_by=company_name&sort_order=asc',
            **self.auth,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data['success'])
        self.assertEqual(len(response.data['data']), 1)
        self.assertEqual(response.data['pagination']['page'], 1)
        self.assertEqual(response.data['pagination']['page_size'], 1)

        filtered = self.client.get(
            '/api/suppliers/?state=Maharashtra&city=Mumbai',
            **self.auth,
        )
        self.assertEqual(filtered.status_code, status.HTTP_200_OK)
        self.assertEqual(filtered.data['pagination']['total'], 1)
        self.assertEqual(filtered.data['data'][0]['company_name'], 'XYZ Supplies')

    def test_get_existing_and_missing(self):
        created = self._create()
        supplier_id = created.data['data']['id']
        ok = self.client.get(f'/api/suppliers/{supplier_id}/', **self.auth)
        self.assertEqual(ok.status_code, status.HTTP_200_OK)
        self.assertEqual(ok.data['data']['id'], supplier_id)

        missing = self.client.get('/api/suppliers/999999/', **self.auth)
        self.assertEqual(missing.status_code, status.HTTP_404_NOT_FOUND)

    def test_put_full_update(self):
        supplier_id = self._create().data['data']['id']
        payload = {
            **SAMPLE_PAYLOAD,
            'contact_person': 'Amit Patil',
            'mobile_number': '9999999999',
            'email': 'amit@abcelectricals.com',
            'city': 'Mumbai',
            'address': 'Andheri East, Mumbai',
        }
        response = self.client.put(
            f'/api/suppliers/{supplier_id}/',
            payload,
            format='json',
            **self.auth,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['data']['contact_person'], 'Amit Patil')
        self.assertEqual(response.data['data']['city'], 'Mumbai')

    def test_put_nonexistent(self):
        response = self.client.put(
            '/api/suppliers/999999/',
            SAMPLE_PAYLOAD,
            format='json',
            **self.auth,
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_patch_partial_city_only(self):
        supplier_id = self._create().data['data']['id']
        before = self.client.get(f'/api/suppliers/{supplier_id}/', **self.auth).data['data']
        response = self.client.patch(
            f'/api/suppliers/{supplier_id}/',
            {'city': 'Pune'},
            format='json',
            **self.auth,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.data['data']
        self.assertEqual(data['city'], 'Pune')
        self.assertEqual(data['company_name'], before['company_name'])
        self.assertEqual(data['contact_person'], before['contact_person'])
        self.assertEqual(data['mobile_number'], before['mobile_number'])
        self.assertEqual(data['email'], before['email'])
        self.assertEqual(data['state'], before['state'])
        self.assertEqual(data['address'], before['address'])

    def test_patch_mobile_and_email(self):
        supplier_id = self._create(email='patch@abcelectricals.com').data['data']['id']
        response = self.client.patch(
            f'/api/suppliers/{supplier_id}/',
            {'mobile_number': '9999999999'},
            format='json',
            **self.auth,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['data']['mobile_number'], '9999999999')

        response = self.client.patch(
            f'/api/suppliers/{supplier_id}/',
            {'email': 'newemail@company.com'},
            format='json',
            **self.auth,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['data']['email'], 'newemail@company.com')

    def test_patch_invalid_mobile(self):
        supplier_id = self._create().data['data']['id']
        response = self.client.patch(
            f'/api/suppliers/{supplier_id}/',
            {'mobile_number': 'abc'},
            format='json',
            **self.auth,
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_delete_existing_and_missing(self):
        supplier_id = self._create().data['data']['id']
        response = self.client.delete(f'/api/suppliers/{supplier_id}/', **self.auth)
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Supplier.objects.filter(pk=supplier_id).exists())

        missing = self.client.delete('/api/suppliers/999999/', **self.auth)
        self.assertEqual(missing.status_code, status.HTTP_404_NOT_FOUND)

    def test_masters_endpoint(self):
        response = self.client.get('/api/suppliers/masters/', **self.auth)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('states', response.data['data'])

    def test_email_normalized_to_lowercase(self):
        response = self._create(email='Raj@ABCelectricals.COM')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['data']['email'], 'raj@abcelectricals.com')
