"""Tests for Transfer Stock Management CRUD APIs."""

from decimal import Decimal

from django.test import TestCase, override_settings
from rest_framework import status
from rest_framework.test import APIClient

from authentication.jwt_utils import create_access_token
from materials.models import Material
from supplier.models import Supplier
from transfer_stocks.models import Batch, TransferStock, TransferStockItem


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


def _supplier(**overrides) -> Supplier:
    payload = {
        'company_name': 'ABC Electricals Pvt Ltd',
        'contact_person': 'Rajesh Kumar',
        'mobile_number': '9876543210',
        'email': 'abc@electricals.example',
        'state': 'Maharashtra',
        'city': 'Nashik',
        'address': 'MIDC Area',
        'created_by': 'system',
        'updated_by': 'system',
    }
    payload.update(overrides)
    return Supplier.objects.create(**payload)


@override_settings(JWT_SECRET_KEY='test-jwt-secret')
class TransferStockAPITests(TestCase):
    def setUp(self):
        self.client_api = APIClient()
        self.token = create_access_token('ram.patil')
        self.auth = {'HTTP_AUTHORIZATION': f'Bearer {self.token}'}

        self.supplier_row = _supplier()
        self.batch_one = Batch.objects.create(batch_number='BATCH001')
        self.batch_two = Batch.objects.create(batch_number='BATCH002')
        self.batch_three = Batch.objects.create(batch_number='BATCH003')
        self.material_one = _material('Copper Wire')
        self.material_two = _material('Cement', unit='Kg', material_type='Raw Material')
        self.material_three = _material('Steel Rod', unit='Kg', material_type='Raw Material')

        self.base_payload = {
            'supplier_id': self.supplier_row.id,
            'date': '2026-08-28',
            'location': 'Nashik Warehouse',
            'remark': 'Stock transfer',
            'products': [
                {
                    'material_id': self.material_one.id,
                    'quantity': 100,
                    'rate': 50,
                    'batch_id': self.batch_one.id,
                }
            ],
        }

    def _create(self, **overrides):
        payload = {**self.base_payload, **overrides}
        return self.client_api.post(
            '/api/transfer-stocks/',
            payload,
            format='json',
            **self.auth,
        )

    def test_create_requires_auth(self):
        response = self.client_api.post(
            '/api/transfer-stocks/', self.base_payload, format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_invalid_token_rejected(self):
        response = self.client_api.get(
            '/api/transfer-stocks/',
            HTTP_AUTHORIZATION='Bearer invalid-token',
        )
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_create_one_product(self):
        response = self._create()
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        data = response.data['data']
        self.assertEqual(data['supplier_id'], self.supplier_row.id)
        self.assertEqual(data['supplier_name'], 'ABC Electricals Pvt Ltd')
        self.assertEqual(len(data['products']), 1)
        self.assertEqual(data['products'][0]['material_id'], self.material_one.id)
        self.assertEqual(data['products'][0]['material_name'], 'Copper Wire')
        self.assertEqual(data['products'][0]['batch_number'], 'BATCH001')
        self.assertEqual(data['created_by'], 'ram.patil')

    def test_create_multiple_products(self):
        payload = {
            **self.base_payload,
            'remark': 'Multiple product transfer',
            'products': [
                {
                    'material_id': self.material_one.id,
                    'quantity': 100,
                    'rate': 50,
                    'batch_id': self.batch_one.id,
                },
                {
                    'material_id': self.material_two.id,
                    'quantity': 200,
                    'rate': 400,
                    'batch_id': self.batch_two.id,
                },
                {
                    'material_id': self.material_three.id,
                    'quantity': 50,
                    'rate': 700,
                    'batch_id': self.batch_three.id,
                },
            ],
        }
        response = self.client_api.post(
            '/api/transfer-stocks/', payload, format='json', **self.auth
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        transfer_id = response.data['data']['id']
        detail = self.client_api.get(
            f'/api/transfer-stocks/{transfer_id}/', **self.auth
        )
        self.assertEqual(len(detail.data['data']['products']), 3)

    def test_create_missing_supplier(self):
        payload = {**self.base_payload}
        del payload['supplier_id']
        response = self.client_api.post(
            '/api/transfer-stocks/', payload, format='json', **self.auth
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('supplier_id', response.data['error']['details'])

    def test_create_invalid_supplier_id(self):
        response = self._create(supplier_id=999999)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('supplier_id', response.data['error']['details'])

    def test_create_missing_date(self):
        payload = {**self.base_payload}
        del payload['date']
        response = self.client_api.post(
            '/api/transfer-stocks/', payload, format='json', **self.auth
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('date', response.data['error']['details'])

    def test_create_missing_location(self):
        payload = {**self.base_payload}
        del payload['location']
        response = self.client_api.post(
            '/api/transfer-stocks/', payload, format='json', **self.auth
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('location', response.data['error']['details'])

    def test_create_missing_products(self):
        payload = {**self.base_payload}
        del payload['products']
        response = self.client_api.post(
            '/api/transfer-stocks/', payload, format='json', **self.auth
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('products', response.data['error']['details'])

    def test_create_empty_products(self):
        response = self._create(products=[])
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('products', response.data['error']['details'])

    def test_create_invalid_material_id(self):
        response = self._create(
            products=[
                {
                    'material_id': 999999,
                    'quantity': 100,
                    'rate': 50,
                    'batch_id': self.batch_one.id,
                }
            ]
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('material_id', response.data['error']['details']['products'][0])

    def test_create_invalid_batch_id(self):
        response = self._create(
            products=[
                {
                    'material_id': self.material_one.id,
                    'quantity': 100,
                    'rate': 50,
                    'batch_id': 999999,
                }
            ]
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('batch_id', response.data['error']['details']['products'][0])

    def test_create_zero_quantity(self):
        response = self._create(
            products=[
                {
                    'material_id': self.material_one.id,
                    'quantity': 0,
                    'rate': 50,
                    'batch_id': self.batch_one.id,
                }
            ]
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_create_negative_quantity(self):
        response = self._create(
            products=[
                {
                    'material_id': self.material_one.id,
                    'quantity': -5,
                    'rate': 50,
                    'batch_id': self.batch_one.id,
                }
            ]
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_create_negative_rate(self):
        response = self._create(
            products=[
                {
                    'material_id': self.material_one.id,
                    'quantity': 100,
                    'rate': -1,
                    'batch_id': self.batch_one.id,
                }
            ]
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_create_duplicate_material_batch_in_payload(self):
        duplicate_line = {
            'material_id': self.material_one.id,
            'quantity': 100,
            'rate': 50,
            'batch_id': self.batch_one.id,
        }
        response = self._create(products=[duplicate_line, duplicate_line])
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)

    def test_create_transaction_rollback_on_invalid_child(self):
        before_count = TransferStock.objects.count()
        before_items = TransferStockItem.objects.count()
        response = self._create(
            products=[
                {
                    'material_id': self.material_one.id,
                    'quantity': 100,
                    'rate': 50,
                    'batch_id': self.batch_one.id,
                },
                {
                    'material_id': 999999,
                    'quantity': 100,
                    'rate': 50,
                    'batch_id': self.batch_one.id,
                },
            ]
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(TransferStock.objects.count(), before_count)
        self.assertEqual(TransferStockItem.objects.count(), before_items)

    def test_list_search_filter_sort(self):
        self._create(location='Nashik Warehouse')
        self._create(
            location='Pune Warehouse',
            remark='Different site',
            products=[
                {
                    'material_id': self.material_two.id,
                    'quantity': 10,
                    'rate': 100,
                    'batch_id': self.batch_two.id,
                }
            ],
        )
        response = self.client_api.get(
            '/api/transfer-stocks/?search=Nashik&sort_by=date&sort_order=desc',
            **self.auth,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['pagination']['total'], 1)

        filtered = self.client_api.get(
            f'/api/transfer-stocks/?supplier_id={self.supplier_row.id}&material_id={self.material_two.id}',
            **self.auth,
        )
        self.assertEqual(filtered.status_code, status.HTTP_200_OK)
        self.assertEqual(filtered.data['pagination']['total'], 1)

    def test_get_by_id_and_missing(self):
        transfer_id = self._create().data['data']['id']
        ok = self.client_api.get(f'/api/transfer-stocks/{transfer_id}/', **self.auth)
        self.assertEqual(ok.status_code, status.HTTP_200_OK)
        missing = self.client_api.get('/api/transfer-stocks/999999/', **self.auth)
        self.assertEqual(missing.status_code, status.HTTP_404_NOT_FOUND)

    def test_put_full_update_replaces_products(self):
        created = self._create()
        transfer_id = created.data['data']['id']
        item_id = created.data['data']['products'][0]['id']
        payload = {
            **self.base_payload,
            'date': '2026-08-29',
            'location': 'Pune Warehouse',
            'remark': 'Updated transfer',
            'products': [
                {
                    'material_id': self.material_one.id,
                    'quantity': 150,
                    'rate': 55,
                    'batch_id': self.batch_one.id,
                },
                {
                    'material_id': self.material_three.id,
                    'quantity': 50,
                    'rate': 700,
                    'batch_id': self.batch_three.id,
                },
            ],
        }
        response = self.client_api.put(
            f'/api/transfer-stocks/{transfer_id}/',
            payload,
            format='json',
            **self.auth,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.data['data']
        self.assertEqual(data['location'], 'Pune Warehouse')
        self.assertEqual(len(data['products']), 2)
        self.assertFalse(TransferStockItem.objects.filter(pk=item_id).exists())

    def test_patch_remark_only(self):
        created = self._create()
        transfer_id = created.data['data']['id']
        before = self.client_api.get(
            f'/api/transfer-stocks/{transfer_id}/', **self.auth
        ).data['data']
        response = self.client_api.patch(
            f'/api/transfer-stocks/{transfer_id}/',
            {'remark': 'Updated transfer remark'},
            format='json',
            **self.auth,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.data['data']
        self.assertEqual(data['remark'], 'Updated transfer remark')
        self.assertEqual(data['location'], before['location'])
        self.assertEqual(data['date'], before['date'])
        self.assertEqual(data['supplier_id'], before['supplier_id'])
        self.assertEqual(len(data['products']), len(before['products']))

    def test_patch_products_upsert_without_deleting_omitted(self):
        created = self.client_api.post(
            '/api/transfer-stocks/',
            {
                **self.base_payload,
                'products': [
                    {
                        'material_id': self.material_one.id,
                        'quantity': 100,
                        'rate': 50,
                        'batch_id': self.batch_one.id,
                    },
                    {
                        'material_id': self.material_two.id,
                        'quantity': 200,
                        'rate': 400,
                        'batch_id': self.batch_two.id,
                    },
                ],
            },
            format='json',
            **self.auth,
        )
        transfer_id = created.data['data']['id']
        first_item_id = created.data['data']['products'][0]['id']
        response = self.client_api.patch(
            f'/api/transfer-stocks/{transfer_id}/',
            {
                'products': [
                    {
                        'id': first_item_id,
                        'material_id': self.material_one.id,
                        'quantity': 200,
                        'rate': 60,
                        'batch_id': self.batch_one.id,
                    }
                ]
            },
            format='json',
            **self.auth,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.data['data']
        self.assertEqual(len(data['products']), 2)
        updated = next(row for row in data['products'] if row['id'] == first_item_id)
        self.assertEqual(Decimal(str(updated['quantity'])), Decimal('200.0000'))

    def test_delete_transfer_stock_and_items(self):
        transfer_id = self._create().data['data']['id']
        item_id = self._create().data['data']['products'][0]['id']
        response = self.client_api.delete(
            f'/api/transfer-stocks/{transfer_id}/', **self.auth
        )
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(TransferStock.objects.filter(pk=transfer_id).exists())
        self.assertFalse(TransferStockItem.objects.filter(transfer_stock_id=transfer_id).exists())
        missing = self.client_api.get(
            f'/api/transfer-stocks/{transfer_id}/', **self.auth
        )
        self.assertEqual(missing.status_code, status.HTTP_404_NOT_FOUND)
        self.assertTrue(TransferStockItem.objects.filter(pk=item_id).exists())

    def test_same_material_different_batches_allowed(self):
        response = self._create(
            products=[
                {
                    'material_id': self.material_one.id,
                    'quantity': 100,
                    'rate': 50,
                    'batch_id': self.batch_one.id,
                },
                {
                    'material_id': self.material_one.id,
                    'quantity': 75,
                    'rate': 55,
                    'batch_id': self.batch_two.id,
                },
            ]
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(len(response.data['data']['products']), 2)
