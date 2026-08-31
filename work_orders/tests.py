"""Tests for Work Order Management CRUD APIs."""

from decimal import Decimal

from django.test import TestCase, override_settings
from rest_framework import status
from rest_framework.test import APIClient

from authentication.jwt_utils import create_access_token
from work_orders.models import WorkOrder


NORMAL_PAYLOAD = {
    'work_order_id': 'WO001',
    'type_of_work': 'Routine Maintenance',
    'other_type': '',
    'chainage_km_from': '10.500',
    'chainage_km_to': '12.750',
}

OTHER_PAYLOAD = {
    'work_order_id': 'WO002',
    'type_of_work': 'Other',
    'other_type': 'Emergency Drainage Work',
    'chainage_km_from': '15.000',
    'chainage_km_to': '16.500',
}


@override_settings(JWT_SECRET_KEY='test-jwt-secret')
class WorkOrderAPITests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.token = create_access_token('ram.patil')
        self.auth = {'HTTP_AUTHORIZATION': f'Bearer {self.token}'}

    def _create(self, **overrides):
        payload = {**NORMAL_PAYLOAD, **overrides}
        return self.client.post('/api/work-orders/', payload, format='json', **self.auth)

    def test_create_requires_auth(self):
        response = self.client.post('/api/work-orders/', NORMAL_PAYLOAD, format='json')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_invalid_token_rejected(self):
        response = self.client.get(
            '/api/work-orders/',
            HTTP_AUTHORIZATION='Bearer invalid-token',
        )
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_create_normal_work_order(self):
        response = self._create()
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        data = response.data['data']
        self.assertEqual(data['work_order_id'], 'WO001')
        self.assertEqual(data['type_of_work'], 'Routine Maintenance')
        self.assertIsNone(data['other_type'])
        self.assertEqual(data['chainage_km_from'], '10.500')
        self.assertEqual(data['chainage_km_to'], '12.750')
        self.assertEqual(data['created_by'], 'ram.patil')

    def test_create_other_work_order(self):
        response = self.client.post(
            '/api/work-orders/', OTHER_PAYLOAD, format='json', **self.auth
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        data = response.data['data']
        self.assertEqual(data['type_of_work'], 'Other')
        self.assertEqual(data['other_type'], 'Emergency Drainage Work')

    def test_missing_work_order_id(self):
        payload = {**NORMAL_PAYLOAD}
        del payload['work_order_id']
        response = self.client.post('/api/work-orders/', payload, format='json', **self.auth)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_duplicate_work_order_id(self):
        self._create()
        response = self._create()
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(response.data['error']['code'], 'DUPLICATE_WORK_ORDER')

    def test_missing_type_of_work(self):
        payload = {**NORMAL_PAYLOAD, 'work_order_id': 'WO010'}
        del payload['type_of_work']
        response = self.client.post('/api/work-orders/', payload, format='json', **self.auth)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_missing_other_type_when_other(self):
        payload = {
            **OTHER_PAYLOAD,
            'work_order_id': 'WO011',
            'other_type': '',
        }
        response = self.client.post('/api/work-orders/', payload, format='json', **self.auth)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data['error']['code'], 'OTHER_TYPE_REQUIRED')

    def test_valid_other_type(self):
        response = self.client.post(
            '/api/work-orders/',
            {**OTHER_PAYLOAD, 'work_order_id': 'WO012'},
            format='json',
            **self.auth,
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_missing_chainage_from(self):
        payload = {**NORMAL_PAYLOAD, 'work_order_id': 'WO013'}
        del payload['chainage_km_from']
        response = self.client.post('/api/work-orders/', payload, format='json', **self.auth)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_missing_chainage_to(self):
        payload = {**NORMAL_PAYLOAD, 'work_order_id': 'WO014'}
        del payload['chainage_km_to']
        response = self.client.post('/api/work-orders/', payload, format='json', **self.auth)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_negative_chainage(self):
        response = self._create(
            work_order_id='WO015',
            chainage_km_from='-1',
            chainage_km_to='10',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_invalid_chainage_range(self):
        response = self._create(
            work_order_id='WO016',
            chainage_km_from='20',
            chainage_km_to='10',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data['error']['code'], 'INVALID_CHAINAGE_RANGE')

    def test_invalid_chainage_format(self):
        response = self._create(
            work_order_id='WO017',
            chainage_km_from='abc',
            chainage_km_to='10',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_list_returns_200(self):
        self._create()
        response = self.client.get('/api/work-orders/', **self.auth)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data['success'])

    def test_list_pagination(self):
        self._create(work_order_id='WO020')
        self._create(work_order_id='WO021')
        response = self.client.get('/api/work-orders/?page=1&page_size=1', **self.auth)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data['data']), 1)

    def test_search_by_work_order_id(self):
        self._create()
        response = self.client.get('/api/work-orders/?search=WO001', **self.auth)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertGreaterEqual(len(response.data['data']), 1)

    def test_search_by_type(self):
        self._create()
        response = self.client.get('/api/work-orders/?search=Maintenance', **self.auth)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertGreaterEqual(len(response.data['data']), 1)

    def test_search_by_other_type(self):
        self.client.post('/api/work-orders/', OTHER_PAYLOAD, format='json', **self.auth)
        response = self.client.get('/api/work-orders/?search=Drainage', **self.auth)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertGreaterEqual(len(response.data['data']), 1)

    def test_filter_by_type(self):
        self._create()
        response = self.client.get(
            '/api/work-orders/?type_of_work=Routine Maintenance',
            **self.auth,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertGreaterEqual(len(response.data['data']), 1)

    def test_filter_by_work_order_id(self):
        self._create()
        response = self.client.get('/api/work-orders/?work_order_id=WO001', **self.auth)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['data'][0]['work_order_id'], 'WO001')

    def test_sort(self):
        self._create()
        response = self.client.get(
            '/api/work-orders/?sort_by=work_order_id&sort_order=asc',
            **self.auth,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_get_by_id(self):
        created = self._create().data['data']
        response = self.client.get(f"/api/work-orders/{created['id']}/", **self.auth)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['data']['work_order_id'], 'WO001')

    def test_get_invalid_id_returns_404(self):
        response = self.client.get('/api/work-orders/999999/', **self.auth)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(response.data['error']['code'], 'WORK_ORDER_NOT_FOUND')

    def test_put_complete_update(self):
        created = self._create().data['data']
        response = self.client.put(
            f"/api/work-orders/{created['id']}/",
            {
                'work_order_id': 'WO001',
                'type_of_work': 'Special Repair',
                'other_type': '',
                'chainage_km_from': '11.000',
                'chainage_km_to': '13.000',
            },
            format='json',
            **self.auth,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.data['data']
        self.assertEqual(data['type_of_work'], 'Special Repair')
        self.assertIsNone(data['other_type'])

    def test_put_change_normal_to_other(self):
        created = self._create().data['data']
        response = self.client.put(
            f"/api/work-orders/{created['id']}/",
            {
                'work_order_id': 'WO001',
                'type_of_work': 'Other',
                'other_type': 'Bridge Repair',
                'chainage_km_from': '10.500',
                'chainage_km_to': '12.750',
            },
            format='json',
            **self.auth,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['data']['other_type'], 'Bridge Repair')

    def test_put_change_other_to_normal(self):
        created = self.client.post(
            '/api/work-orders/', OTHER_PAYLOAD, format='json', **self.auth
        ).data['data']
        response = self.client.put(
            f"/api/work-orders/{created['id']}/",
            {
                'work_order_id': 'WO002',
                'type_of_work': 'Routine Maintenance',
                'other_type': 'Should Be Cleared',
                'chainage_km_from': '15.000',
                'chainage_km_to': '16.500',
            },
            format='json',
            **self.auth,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsNone(response.data['data']['other_type'])

    def test_put_invalid_chainage_range(self):
        created = self._create().data['data']
        response = self.client.put(
            f"/api/work-orders/{created['id']}/",
            {
                'work_order_id': 'WO001',
                'type_of_work': 'Routine Maintenance',
                'other_type': '',
                'chainage_km_from': '20',
                'chainage_km_to': '10',
            },
            format='json',
            **self.auth,
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data['error']['code'], 'INVALID_CHAINAGE_RANGE')

    def test_put_duplicate_work_order_id(self):
        self._create()
        second = self._create(work_order_id='WO030')
        second_id = second.data['data']['id']
        response = self.client.put(
            f'/api/work-orders/{second_id}/',
            {
                'work_order_id': 'WO001',
                'type_of_work': 'Routine Maintenance',
                'other_type': '',
                'chainage_km_from': '10.500',
                'chainage_km_to': '12.750',
            },
            format='json',
            **self.auth,
        )
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)

    def test_patch_update_one_field(self):
        created = self._create().data['data']
        response = self.client.patch(
            f"/api/work-orders/{created['id']}/",
            {'chainage_km_from': '11.000'},
            format='json',
            **self.auth,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['data']['chainage_km_from'], '11.000')
        self.assertEqual(response.data['data']['chainage_km_to'], '12.750')

    def test_patch_update_chainage(self):
        created = self._create().data['data']
        response = self.client.patch(
            f"/api/work-orders/{created['id']}/",
            {'chainage_km_to': '14.000'},
            format='json',
            **self.auth,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['data']['chainage_km_to'], '14.000')

    def test_patch_update_type(self):
        created = self._create().data['data']
        response = self.client.patch(
            f"/api/work-orders/{created['id']}/",
            {'type_of_work': 'New Construction'},
            format='json',
            **self.auth,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['data']['type_of_work'], 'New Construction')

    def test_patch_change_normal_to_other(self):
        created = self._create().data['data']
        response = self.client.patch(
            f"/api/work-orders/{created['id']}/",
            {'type_of_work': 'Other', 'other_type': 'Emergency Work'},
            format='json',
            **self.auth,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['data']['other_type'], 'Emergency Work')

    def test_patch_change_other_to_normal(self):
        created = self.client.post(
            '/api/work-orders/', OTHER_PAYLOAD, format='json', **self.auth
        ).data['data']
        response = self.client.patch(
            f"/api/work-orders/{created['id']}/",
            {'type_of_work': 'Routine Maintenance'},
            format='json',
            **self.auth,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsNone(response.data['data']['other_type'])

    def test_patch_other_requires_other_type(self):
        created = self._create().data['data']
        response = self.client.patch(
            f"/api/work-orders/{created['id']}/",
            {'type_of_work': 'Other'},
            format='json',
            **self.auth,
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data['error']['code'], 'OTHER_TYPE_REQUIRED')

    def test_patch_final_chainage_range_validation(self):
        created = self._create().data['data']
        response = self.client.patch(
            f"/api/work-orders/{created['id']}/",
            {'chainage_km_from': '25.000'},
            format='json',
            **self.auth,
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data['error']['code'], 'INVALID_CHAINAGE_RANGE')

    def test_patch_untouched_fields_unchanged(self):
        created = self._create().data['data']
        response = self.client.patch(
            f"/api/work-orders/{created['id']}/",
            {'chainage_km_from': '11.000'},
            format='json',
            **self.auth,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.data['data']
        self.assertEqual(data['work_order_id'], 'WO001')
        self.assertEqual(data['type_of_work'], 'Routine Maintenance')

    def test_delete_returns_204(self):
        created = self._create().data['data']
        response = self.client.delete(f"/api/work-orders/{created['id']}/", **self.auth)
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(WorkOrder.objects.filter(pk=created['id']).exists())

    def test_get_deleted_returns_404(self):
        created = self._create().data['data']
        self.client.delete(f"/api/work-orders/{created['id']}/", **self.auth)
        response = self.client.get(f"/api/work-orders/{created['id']}/", **self.auth)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_delete_nonexistent_returns_404(self):
        response = self.client.delete('/api/work-orders/999999/', **self.auth)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_accepts_frontend_type_values(self):
        for idx, work_type in enumerate(
            ['Routine Maintenance', 'Special Repair', 'New Construction', 'Other'],
            start=1,
        ):
            payload = {
                'work_order_id': f'WO-TYPE-{idx}',
                'type_of_work': work_type,
                'other_type': 'Custom Work' if work_type == 'Other' else '',
                'chainage_km_from': '1.000',
                'chainage_km_to': '2.000',
            }
            response = self.client.post('/api/work-orders/', payload, format='json', **self.auth)
            self.assertEqual(response.status_code, status.HTTP_201_CREATED, work_type)
