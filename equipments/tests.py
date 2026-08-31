"""Tests for Equipment Management CRUD APIs."""

from datetime import date
from decimal import Decimal

from django.test import TestCase, override_settings
from rest_framework import status
from rest_framework.test import APIClient

from authentication.jwt_utils import create_access_token
from equipments.models import Equipment


SAMPLE_PAYLOAD = {
    'item_name': 'JCB',
    'year_of_make': 2022,
    'make_and_model': 'JCB 3DX',
    'equipment_code': 'EQP001',
    'estimate_cost': '250000.00',
    'status': 'Working',
    'remark': 'Equipment available at Nashik site',
}


@override_settings(JWT_SECRET_KEY='test-jwt-secret')
class EquipmentAPITests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.token = create_access_token('ram.patil')
        self.auth = {'HTTP_AUTHORIZATION': f'Bearer {self.token}'}

    def _create(self, **overrides):
        payload = {**SAMPLE_PAYLOAD, **overrides}
        return self.client.post('/api/equipments/', payload, format='json', **self.auth)

    def test_create_requires_auth(self):
        response = self.client.post('/api/equipments/', SAMPLE_PAYLOAD, format='json')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_invalid_token_rejected(self):
        response = self.client.get(
            '/api/equipments/',
            HTTP_AUTHORIZATION='Bearer invalid-token',
        )
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_create_valid_equipment(self):
        response = self._create()
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(response.data['success'])
        data = response.data['data']
        self.assertEqual(data['equipment_code'], 'EQP001')
        self.assertEqual(data['created_by'], 'ram.patil')
        self.assertEqual(data['estimate_cost'], '250000.00')

    def test_create_missing_item_name(self):
        payload = {**SAMPLE_PAYLOAD}
        del payload['item_name']
        response = self.client.post('/api/equipments/', payload, format='json', **self.auth)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('item_name', response.data['error']['details'])

    def test_create_missing_year_of_make(self):
        payload = {**SAMPLE_PAYLOAD}
        del payload['year_of_make']
        response = self.client.post('/api/equipments/', payload, format='json', **self.auth)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('year_of_make', response.data['error']['details'])

    def test_create_missing_make_and_model(self):
        payload = {**SAMPLE_PAYLOAD}
        del payload['make_and_model']
        response = self.client.post('/api/equipments/', payload, format='json', **self.auth)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('make_and_model', response.data['error']['details'])

    def test_create_missing_equipment_code(self):
        payload = {**SAMPLE_PAYLOAD}
        del payload['equipment_code']
        response = self.client.post('/api/equipments/', payload, format='json', **self.auth)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('equipment_code', response.data['error']['details'])

    def test_create_missing_estimate_cost(self):
        payload = {**SAMPLE_PAYLOAD}
        del payload['estimate_cost']
        response = self.client.post('/api/equipments/', payload, format='json', **self.auth)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('estimate_cost', response.data['error']['details'])

    def test_create_missing_status(self):
        payload = {**SAMPLE_PAYLOAD}
        del payload['status']
        response = self.client.post('/api/equipments/', payload, format='json', **self.auth)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('status', response.data['error']['details'])

    def test_create_missing_remark(self):
        payload = {**SAMPLE_PAYLOAD}
        del payload['remark']
        response = self.client.post('/api/equipments/', payload, format='json', **self.auth)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('remark', response.data['error']['details'])

    def test_create_duplicate_equipment_code(self):
        self.assertEqual(self._create().status_code, status.HTTP_201_CREATED)
        response = self._create(
            item_name='Another JCB',
            make_and_model='JCB 4DX',
            remark='Duplicate code test',
        )
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(response.data['error']['code'], 'DUPLICATE_EQUIPMENT')
        self.assertIn('equipment_code', response.data['error']['details'])

    def test_create_negative_estimate_cost(self):
        response = self._create(estimate_cost='-100.00')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_create_invalid_year(self):
        response = self._create(year_of_make='invalid')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_create_future_year(self):
        response = self._create(year_of_make=date.today().year + 1)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_create_whitespace_only_required_fields(self):
        response = self._create(item_name='   ', remark='   ')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_create_accepts_year_from_date_string(self):
        response = self._create(
            equipment_code='EQP-DATE',
            year_of_make='2021-06-15',
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['data']['year_of_make'], 2021)

    def test_list_pagination_search_filter_sort(self):
        self._create()
        self._create(
            equipment_code='EQP002',
            item_name='Tata Truck',
            make_and_model='Tata 407',
            status='Not-Working',
            year_of_make=2020,
            remark='Parked at depot',
        )
        listing = self.client.get('/api/equipments/?page=1&page_size=1', **self.auth)
        self.assertEqual(listing.status_code, status.HTTP_200_OK)
        self.assertIn('pagination', listing.data)
        self.assertEqual(listing.data['pagination']['page_size'], 1)

        search = self.client.get('/api/equipments/?search=JCB', **self.auth)
        self.assertEqual(search.status_code, status.HTTP_200_OK)
        self.assertEqual(search.data['pagination']['total'], 1)

        code_search = self.client.get('/api/equipments/?search=EQP002', **self.auth)
        self.assertEqual(code_search.data['pagination']['total'], 1)

        model_search = self.client.get('/api/equipments/?search=Tata 407', **self.auth)
        self.assertEqual(model_search.data['pagination']['total'], 1)

        filtered = self.client.get('/api/equipments/?status=Not-Working', **self.auth)
        self.assertEqual(filtered.status_code, status.HTTP_200_OK)
        self.assertEqual(filtered.data['pagination']['total'], 1)

        year_filtered = self.client.get('/api/equipments/?year_of_make=2020', **self.auth)
        self.assertEqual(year_filtered.data['pagination']['total'], 1)

        sorted_resp = self.client.get(
            '/api/equipments/?sort_by=item_name&sort_order=asc',
            **self.auth,
        )
        self.assertEqual(sorted_resp.status_code, status.HTTP_200_OK)
        names = [row['item_name'] for row in sorted_resp.data['data']]
        self.assertEqual(names, sorted(names, key=str.casefold))

    def test_get_by_id_and_missing(self):
        equipment_id = self._create().data['data']['id']
        ok = self.client.get(f'/api/equipments/{equipment_id}/', **self.auth)
        self.assertEqual(ok.status_code, status.HTTP_200_OK)
        missing = self.client.get('/api/equipments/999999/', **self.auth)
        self.assertEqual(missing.status_code, status.HTTP_404_NOT_FOUND)

    def test_put_full_update(self):
        equipment_id = self._create().data['data']['id']
        payload = {
            **SAMPLE_PAYLOAD,
            'year_of_make': 2023,
            'estimate_cost': '275000.00',
            'remark': 'Updated equipment information',
        }
        response = self.client.put(
            f'/api/equipments/{equipment_id}/',
            payload,
            format='json',
            **self.auth,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['data']['estimate_cost'], '275000.00')

    def test_put_duplicate_equipment_code(self):
        self._create(equipment_code='EQP-A')
        second = self._create(
            equipment_code='EQP-B',
            item_name='Generator',
            make_and_model='Kirloskar',
            remark='Second equipment',
        )
        second_id = second.data['data']['id']
        response = self.client.put(
            f'/api/equipments/{second_id}/',
            {**SAMPLE_PAYLOAD, 'equipment_code': 'EQP-A'},
            format='json',
            **self.auth,
        )
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)

    def test_put_invalid_data(self):
        equipment_id = self._create().data['data']['id']
        response = self.client.put(
            f'/api/equipments/{equipment_id}/',
            {**SAMPLE_PAYLOAD, 'estimate_cost': '-1.00'},
            format='json',
            **self.auth,
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_patch_status_only(self):
        equipment_id = self._create().data['data']['id']
        before = self.client.get(f'/api/equipments/{equipment_id}/', **self.auth).data['data']
        response = self.client.patch(
            f'/api/equipments/{equipment_id}/',
            {'status': 'Under-repair'},
            format='json',
            **self.auth,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.data['data']
        self.assertEqual(data['status'], 'Under-repair')
        self.assertEqual(data['item_name'], before['item_name'])
        self.assertEqual(data['estimate_cost'], before['estimate_cost'])

    def test_patch_estimate_cost_only(self):
        equipment_id = self._create().data['data']['id']
        response = self.client.patch(
            f'/api/equipments/{equipment_id}/',
            {'estimate_cost': '300000.00'},
            format='json',
            **self.auth,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['data']['estimate_cost'], '300000.00')

    def test_patch_remark_only(self):
        equipment_id = self._create().data['data']['id']
        response = self.client.patch(
            f'/api/equipments/{equipment_id}/',
            {'remark': 'Updated remark only'},
            format='json',
            **self.auth,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['data']['remark'], 'Updated remark only')

    def test_patch_multiple_fields(self):
        equipment_id = self._create().data['data']['id']
        response = self.client.patch(
            f'/api/equipments/{equipment_id}/',
            {
                'make_and_model': 'JCB 3DX Super',
                'status': 'Not-Working',
            },
            format='json',
            **self.auth,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.data['data']
        self.assertEqual(data['make_and_model'], 'JCB 3DX Super')
        self.assertEqual(data['status'], 'Not-Working')

    def test_patch_duplicate_equipment_code(self):
        self._create(equipment_code='EQP-KEEP')
        second = self._create(
            equipment_code='EQP-OTHER',
            item_name='Loader',
            make_and_model='Loader X',
            remark='Other equipment',
        )
        second_id = second.data['data']['id']
        response = self.client.patch(
            f'/api/equipments/{second_id}/',
            {'equipment_code': 'EQP-KEEP'},
            format='json',
            **self.auth,
        )
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)

    def test_delete_equipment(self):
        equipment_id = self._create().data['data']['id']
        response = self.client.delete(f'/api/equipments/{equipment_id}/', **self.auth)
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Equipment.objects.filter(pk=equipment_id).exists())
        missing = self.client.get(f'/api/equipments/{equipment_id}/', **self.auth)
        self.assertEqual(missing.status_code, status.HTTP_404_NOT_FOUND)

    def test_delete_missing_equipment(self):
        response = self.client.delete('/api/equipments/999999/', **self.auth)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_status_values_accepted_from_frontend(self):
        for idx, status_value in enumerate(['Working', 'Not-Working', 'Under-repair'], start=1):
            response = self._create(
                equipment_code=f'EQP-STATUS-{idx}',
                status=status_value,
                remark=f'Status test {status_value}',
            )
            self.assertEqual(response.status_code, status.HTTP_201_CREATED)
            self.assertEqual(response.data['data']['status'], status_value)

    def test_masters_endpoint_not_available(self):
        response = self.client.get('/api/equipments/masters/', **self.auth)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
