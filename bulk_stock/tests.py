"""Tests for Bulk Inward and Bulk Outward Management APIs."""

from decimal import Decimal
from threading import Barrier, Thread

from django.db import connections
from django.test import TestCase, TransactionTestCase, override_settings
from rest_framework import status
from rest_framework.test import APIClient

from authentication.jwt_utils import create_access_token
from bulk_stock.models import BulkStockTransaction, BulkStockTransactionItem, MaterialStock
from bulk_stock import services
from bulk_stock.serializers import BulkStockWriteSerializer
from materials.models import Material


def _material(name: str, *, unit='Meter', material_type='Electrical') -> Material:
    return Material.objects.create(
        material_name=name,
        material_unit=unit,
        material_type=material_type,
        other_type='',
        material_description='',
        created_by='system',
        updated_by='system',
    )


@override_settings(JWT_SECRET_KEY='test-jwt-secret')
class BulkInwardAPITests(TestCase):
    def setUp(self):
        self.client_api = APIClient()
        self.token = create_access_token('ram.patil')
        self.auth = {'HTTP_AUTHORIZATION': f'Bearer {self.token}'}
        self.material_one = _material('Copper Wire')
        self.material_two = _material('Cement', unit='Kg', material_type='Raw Material')
        self.base_payload = {
            'products': [
                {'material_id': self.material_one.id, 'quantity': 100},
            ]
        }

    def _create_inward(self, **overrides):
        payload = {**self.base_payload, **overrides}
        return self.client_api.post(
            '/api/bulk-inward/',
            payload,
            format='json',
            **self.auth,
        )

    def _stock(self, material_id: int) -> Decimal:
        return services.get_material_stock(material_id)

    def test_create_requires_auth(self):
        response = self.client_api.post(
            '/api/bulk-inward/', self.base_payload, format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_invalid_token_rejected(self):
        response = self.client_api.get(
            '/api/bulk-inward/',
            HTTP_AUTHORIZATION='Bearer invalid-token',
        )
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_create_one_product(self):
        response = self._create_inward()
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        data = response.data['data']
        self.assertEqual(data['transaction_type'], 'INWARD')
        self.assertEqual(len(data['products']), 1)
        self.assertEqual(data['products'][0]['material_id'], self.material_one.id)
        self.assertEqual(data['products'][0]['material_name'], 'Copper Wire')
        self.assertEqual(data['products'][0]['quantity'], '100.0000')
        self.assertEqual(data['created_by'], 'ram.patil')

    def test_create_multiple_products(self):
        response = self._create_inward(
            products=[
                {'material_id': self.material_one.id, 'quantity': 100},
                {'material_id': self.material_two.id, 'quantity': 50},
            ]
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(len(response.data['data']['products']), 2)

    def test_create_increases_stock(self):
        self._create_inward()
        self.assertEqual(self._stock(self.material_one.id), Decimal('100.0000'))
        self._create_inward(products=[{'material_id': self.material_one.id, 'quantity': 25}])
        self.assertEqual(self._stock(self.material_one.id), Decimal('125.0000'))

    def test_invalid_material_rejected(self):
        response = self._create_inward(
            products=[{'material_id': 999999, 'quantity': 10}]
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_missing_quantity_rejected(self):
        response = self._create_inward(
            products=[{'material_id': self.material_one.id}]
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_zero_quantity_rejected(self):
        response = self._create_inward(
            products=[{'material_id': self.material_one.id, 'quantity': 0}]
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_negative_quantity_rejected(self):
        response = self._create_inward(
            products=[{'material_id': self.material_one.id, 'quantity': -5}]
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_empty_products_rejected(self):
        response = self._create_inward(products=[])
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_duplicate_material_rejected(self):
        response = self._create_inward(
            products=[
                {'material_id': self.material_one.id, 'quantity': 10},
                {'material_id': self.material_one.id, 'quantity': 20},
            ]
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data['error']['code'], 'DUPLICATE_MATERIAL')

    def test_list_returns_200(self):
        self._create_inward()
        response = self.client_api.get('/api/bulk-inward/', **self.auth)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data['success'])
        self.assertGreaterEqual(len(response.data['data']), 1)

    def test_list_pagination(self):
        for qty in (10, 20, 30):
            self._create_inward(products=[{'material_id': self.material_one.id, 'quantity': qty}])
        response = self.client_api.get(
            '/api/bulk-inward/?page=1&page_size=2',
            **self.auth,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data['data']), 2)
        self.assertEqual(response.data['pagination']['page_size'], 2)

    def test_list_search_by_material_name(self):
        self._create_inward()
        response = self.client_api.get('/api/bulk-inward/?search=Copper', **self.auth)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertGreaterEqual(len(response.data['data']), 1)

    def test_list_filter_by_material_id(self):
        self._create_inward()
        response = self.client_api.get(
            f'/api/bulk-inward/?material_id={self.material_one.id}',
            **self.auth,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertGreaterEqual(len(response.data['data']), 1)

    def test_list_sort(self):
        self._create_inward()
        response = self.client_api.get(
            '/api/bulk-inward/?sort_by=created_at&sort_order=asc',
            **self.auth,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_get_by_id(self):
        created = self._create_inward().data['data']
        response = self.client_api.get(f"/api/bulk-inward/{created['id']}/", **self.auth)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['data']['id'], created['id'])

    def test_get_invalid_id_returns_404(self):
        response = self.client_api.get('/api/bulk-inward/999999/', **self.auth)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_put_complete_update(self):
        created = self._create_inward().data['data']
        response = self.client_api.put(
            f"/api/bulk-inward/{created['id']}/",
            {
                'products': [
                    {'material_id': self.material_one.id, 'quantity': 200},
                    {'material_id': self.material_two.id, 'quantity': 100},
                ]
            },
            format='json',
            **self.auth,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data['data']['products']), 2)
        self.assertEqual(self._stock(self.material_one.id), Decimal('200.0000'))
        self.assertEqual(self._stock(self.material_two.id), Decimal('100.0000'))

    def test_put_replaces_products_and_adjusts_stock(self):
        created = self._create_inward().data['data']
        self.assertEqual(self._stock(self.material_one.id), Decimal('100.0000'))
        response = self.client_api.put(
            f"/api/bulk-inward/{created['id']}/",
            {'products': [{'material_id': self.material_one.id, 'quantity': 50}]},
            format='json',
            **self.auth,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(self._stock(self.material_one.id), Decimal('50.0000'))

    def test_patch_partial_update(self):
        created = self._create_inward().data['data']
        item_id = created['products'][0]['id']
        response = self.client_api.patch(
            f"/api/bulk-inward/{created['id']}/",
            {
                'products': [
                    {'id': item_id, 'material_id': self.material_one.id, 'quantity': 150}
                ]
            },
            format='json',
            **self.auth,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(self._stock(self.material_one.id), Decimal('150.0000'))

    def test_delete_reverses_stock(self):
        created = self._create_inward().data['data']
        self.assertEqual(self._stock(self.material_one.id), Decimal('100.0000'))
        response = self.client_api.delete(
            f"/api/bulk-inward/{created['id']}/",
            **self.auth,
        )
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertEqual(self._stock(self.material_one.id), Decimal('0'))
        get_response = self.client_api.get(
            f"/api/bulk-inward/{created['id']}/",
            **self.auth,
        )
        self.assertEqual(get_response.status_code, status.HTTP_404_NOT_FOUND)


@override_settings(JWT_SECRET_KEY='test-jwt-secret')
class BulkOutwardAPITests(TestCase):
    def setUp(self):
        self.client_api = APIClient()
        self.token = create_access_token('ram.patil')
        self.auth = {'HTTP_AUTHORIZATION': f'Bearer {self.token}'}
        self.material_one = _material('Copper Wire')
        self.material_two = _material('Cement', unit='Kg', material_type='Raw Material')

        self.client_api.post(
            '/api/bulk-inward/',
            {
                'products': [
                    {'material_id': self.material_one.id, 'quantity': 100},
                    {'material_id': self.material_two.id, 'quantity': 50},
                ]
            },
            format='json',
            **self.auth,
        )

        self.base_payload = {
            'products': [
                {'material_id': self.material_one.id, 'quantity': 20},
            ]
        }

    def _create_outward(self, **overrides):
        payload = {**self.base_payload, **overrides}
        return self.client_api.post(
            '/api/bulk-outward/',
            payload,
            format='json',
            **self.auth,
        )

    def _stock(self, material_id: int) -> Decimal:
        return services.get_material_stock(material_id)

    def test_create_requires_auth(self):
        response = self.client_api.post(
            '/api/bulk-outward/', self.base_payload, format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_invalid_token_rejected(self):
        response = self.client_api.get(
            '/api/bulk-outward/',
            HTTP_AUTHORIZATION='Bearer invalid-token',
        )
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_create_one_product(self):
        response = self._create_outward()
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        data = response.data['data']
        self.assertEqual(data['transaction_type'], 'OUTWARD')
        self.assertEqual(data['products'][0]['quantity'], '20.0000')

    def test_create_multiple_products(self):
        response = self._create_outward(
            products=[
                {'material_id': self.material_one.id, 'quantity': 10},
                {'material_id': self.material_two.id, 'quantity': 5},
            ]
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(len(response.data['data']['products']), 2)

    def test_create_decreases_stock(self):
        self._create_outward()
        self.assertEqual(self._stock(self.material_one.id), Decimal('80.0000'))

    def test_insufficient_stock_rejected(self):
        response = self._create_outward(
            products=[{'material_id': self.material_one.id, 'quantity': 1000}]
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data['error']['code'], 'INSUFFICIENT_STOCK')
        self.assertEqual(self._stock(self.material_one.id), Decimal('100.0000'))

    def test_zero_quantity_rejected(self):
        response = self._create_outward(
            products=[{'material_id': self.material_one.id, 'quantity': 0}]
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_negative_quantity_rejected(self):
        response = self._create_outward(
            products=[{'material_id': self.material_one.id, 'quantity': -1}]
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_invalid_material_rejected(self):
        response = self._create_outward(
            products=[{'material_id': 999999, 'quantity': 1}]
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_empty_products_rejected(self):
        response = self._create_outward(products=[])
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_multi_product_rollback_on_insufficient_stock(self):
        before_a = self._stock(self.material_one.id)
        before_b = self._stock(self.material_two.id)
        response = self._create_outward(
            products=[
                {'material_id': self.material_one.id, 'quantity': 10},
                {'material_id': self.material_two.id, 'quantity': 999999},
            ]
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(self._stock(self.material_one.id), before_a)
        self.assertEqual(self._stock(self.material_two.id), before_b)
        self.assertEqual(
            BulkStockTransaction.objects.filter(transaction_type='OUTWARD').count(),
            0,
        )
        self.assertEqual(
            BulkStockTransactionItem.objects.filter(
                transaction__transaction_type='OUTWARD'
            ).count(),
            0,
        )

    def test_list_returns_200(self):
        self._create_outward()
        response = self.client_api.get('/api/bulk-outward/', **self.auth)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_list_pagination(self):
        self._create_outward()
        response = self.client_api.get('/api/bulk-outward/?page=1&page_size=1', **self.auth)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_list_search(self):
        self._create_outward()
        response = self.client_api.get('/api/bulk-outward/?search=Copper', **self.auth)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_list_filter(self):
        response = self.client_api.get(
            f'/api/bulk-outward/?material_id={self.material_one.id}',
            **self.auth,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_list_sort(self):
        response = self.client_api.get(
            '/api/bulk-outward/?sort_by=created_at&sort_order=desc',
            **self.auth,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_get_by_id(self):
        created = self._create_outward().data['data']
        response = self.client_api.get(f"/api/bulk-outward/{created['id']}/", **self.auth)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_get_invalid_id_returns_404(self):
        response = self.client_api.get('/api/bulk-outward/999999/', **self.auth)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_put_updates_and_adjusts_stock(self):
        created = self._create_outward().data['data']
        self.assertEqual(self._stock(self.material_one.id), Decimal('80.0000'))
        response = self.client_api.put(
            f"/api/bulk-outward/{created['id']}/",
            {'products': [{'material_id': self.material_one.id, 'quantity': 30}]},
            format='json',
            **self.auth,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(self._stock(self.material_one.id), Decimal('70.0000'))

    def test_patch_partial_update(self):
        created = self._create_outward().data['data']
        item_id = created['products'][0]['id']
        response = self.client_api.patch(
            f"/api/bulk-outward/{created['id']}/",
            {
                'products': [
                    {'id': item_id, 'material_id': self.material_one.id, 'quantity': 40}
                ]
            },
            format='json',
            **self.auth,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(self._stock(self.material_one.id), Decimal('60.0000'))

    def test_delete_restores_stock(self):
        created = self._create_outward().data['data']
        self.assertEqual(self._stock(self.material_one.id), Decimal('80.0000'))
        response = self.client_api.delete(
            f"/api/bulk-outward/{created['id']}/",
            **self.auth,
        )
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertEqual(self._stock(self.material_one.id), Decimal('100.0000'))
        get_response = self.client_api.get(
            f"/api/bulk-outward/{created['id']}/",
            **self.auth,
        )
        self.assertEqual(get_response.status_code, status.HTTP_404_NOT_FOUND)


@override_settings(JWT_SECRET_KEY='test-jwt-secret')
class BulkOutwardConcurrencyTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.client_api = APIClient()
        self.token = create_access_token('ram.patil')
        self.auth = {'HTTP_AUTHORIZATION': f'Bearer {self.token}'}
        self.material = _material('Copper Wire')
        self.client_api.post(
            '/api/bulk-inward/',
            {'products': [{'material_id': self.material.id, 'quantity': 100}]},
            format='json',
            **self.auth,
        )

    def test_concurrent_outward_requests_do_not_oversell(self):
        barrier = Barrier(2)
        results: list[int] = []

        def worker(quantity: int):
            connections.close_all()
            client = APIClient()
            barrier.wait()
            response = client.post(
                '/api/bulk-outward/',
                {'products': [{'material_id': self.material.id, 'quantity': quantity}]},
                format='json',
                HTTP_AUTHORIZATION=f'Bearer {self.token}',
            )
            results.append(response.status_code)

        thread_a = Thread(target=worker, args=(70,))
        thread_b = Thread(target=worker, args=(50,))
        thread_a.start()
        thread_b.start()
        thread_a.join()
        thread_b.join()

        self.assertEqual(sorted(results), [201, 400])
        stock = MaterialStock.objects.get(material_id=self.material.id).quantity
        self.assertIn(stock, {Decimal('30.0000'), Decimal('50.0000')})
        self.assertGreaterEqual(stock, Decimal('0'))


@override_settings(JWT_SECRET_KEY='test-jwt-secret')
class BulkStockMaterialValidationQueryTests(TestCase):
    def setUp(self):
        self.materials = [_material(f'Batch Material {index}') for index in range(10)]

    def test_serializer_validates_ten_products_with_single_material_query(self):
        serializer = BulkStockWriteSerializer(
            data={
                'products': [
                    {'material_id': material.id, 'quantity': '1.0000'}
                    for material in self.materials
                ]
            }
        )
        with self.assertNumQueries(1):
            self.assertTrue(serializer.is_valid(), serializer.errors)
