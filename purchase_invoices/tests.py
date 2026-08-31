"""Tests for Purchase Invoice Management CRUD APIs."""

from decimal import Decimal

from django.test import TestCase, override_settings
from rest_framework import status
from rest_framework.test import APIClient

from authentication.jwt_utils import create_access_token
from materials.models import Material
from purchase_invoices.models import PurchaseInvoice, PurchaseInvoiceItem
from supplier.models import Supplier


def _supplier(**overrides) -> Supplier:
    payload = {
        'company_name': 'ABC Electricals Pvt Ltd',
        'contact_person': 'Rajesh Kumar',
        'mobile_number': '9876543210',
        'email': 'abc@purchase.example',
        'state': 'Maharashtra',
        'city': 'Nashik',
        'address': 'MIDC Area',
        'created_by': 'system',
        'updated_by': 'system',
    }
    payload.update(overrides)
    return Supplier.objects.create(**payload)


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
class PurchaseInvoiceAPITests(TestCase):
    def setUp(self):
        self.client_api = APIClient()
        self.token = create_access_token('ram.patil')
        self.auth = {'HTTP_AUTHORIZATION': f'Bearer {self.token}'}

        self.supplier_row = _supplier()
        self.other_supplier = _supplier(
            company_name='XYZ Supplies',
            email='xyz@purchase.example',
        )
        self.material_one = _material('Copper Wire')
        self.material_two = _material('Cement', unit='Kg', material_type='Raw Material')
        self.material_three = _material('Steel Rod', unit='Kg', material_type='Raw Material')

        self.base_payload = {
            'supplier_id': self.supplier_row.id,
            'invoice_date': '2026-08-28',
            'invoice_number': 'INV-1001',
            'products': [
                {
                    'material_id': self.material_one.id,
                    'quantity': 100,
                    'rate': '50.00',
                    'batch_number': 'B001',
                }
            ],
        }

    def _create(self, **overrides):
        payload = {**self.base_payload, **overrides}
        return self.client_api.post(
            '/api/purchase-invoices/',
            payload,
            format='json',
            **self.auth,
        )

    def test_create_requires_auth(self):
        response = self.client_api.post(
            '/api/purchase-invoices/', self.base_payload, format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_invalid_token_rejected(self):
        response = self.client_api.get(
            '/api/purchase-invoices/',
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
        self.assertEqual(data['products'][0]['material_name'], 'Copper Wire')
        self.assertEqual(data['created_by'], 'ram.patil')

    def test_create_multiple_products(self):
        response = self._create(
            invoice_number='INV-1002',
            products=[
                {
                    'material_id': self.material_one.id,
                    'quantity': 100,
                    'rate': '50.00',
                    'batch_number': 'B001',
                },
                {
                    'material_id': self.material_two.id,
                    'quantity': 200,
                    'rate': '400.00',
                    'batch_number': 'B002',
                },
                {
                    'material_id': self.material_three.id,
                    'quantity': 50,
                    'rate': '700.00',
                    'batch_number': 'B003',
                },
            ],
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        invoice_id = response.data['data']['id']
        detail = self.client_api.get(
            f'/api/purchase-invoices/{invoice_id}/', **self.auth
        )
        self.assertEqual(len(detail.data['data']['products']), 3)

    def test_create_missing_supplier(self):
        payload = {**self.base_payload}
        del payload['supplier_id']
        response = self.client_api.post(
            '/api/purchase-invoices/', payload, format='json', **self.auth
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_create_invalid_supplier(self):
        response = self._create(supplier_id=999999)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('supplier_id', response.data['error']['details'])

    def test_create_missing_invoice_date(self):
        payload = {**self.base_payload}
        del payload['invoice_date']
        response = self.client_api.post(
            '/api/purchase-invoices/', payload, format='json', **self.auth
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_create_invalid_invoice_date(self):
        response = self._create(invoice_date='not-a-date')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_create_missing_invoice_number(self):
        payload = {**self.base_payload}
        del payload['invoice_number']
        response = self.client_api.post(
            '/api/purchase-invoices/', payload, format='json', **self.auth
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_create_missing_products(self):
        payload = {**self.base_payload}
        del payload['products']
        response = self.client_api.post(
            '/api/purchase-invoices/', payload, format='json', **self.auth
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_create_empty_products(self):
        response = self._create(products=[])
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_create_invalid_material(self):
        response = self._create(
            products=[
                {
                    'material_id': 999999,
                    'quantity': 100,
                    'rate': '50.00',
                    'batch_number': 'B001',
                }
            ]
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_create_zero_quantity(self):
        response = self._create(
            products=[
                {
                    'material_id': self.material_one.id,
                    'quantity': 0,
                    'rate': '50.00',
                    'batch_number': 'B001',
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
                    'rate': '50.00',
                    'batch_number': 'B001',
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
                    'rate': '-1.00',
                    'batch_number': 'B001',
                }
            ]
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_create_missing_batch_number(self):
        response = self._create(
            products=[
                {
                    'material_id': self.material_one.id,
                    'quantity': 100,
                    'rate': '50.00',
                    'batch_number': '',
                }
            ]
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_create_duplicate_invoice_same_supplier(self):
        self.assertEqual(self._create().status_code, status.HTTP_201_CREATED)
        response = self._create(invoice_number='INV-1001')
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(response.data['error']['code'], 'DUPLICATE_PURCHASE_INVOICE')

    def test_same_invoice_number_different_supplier_allowed(self):
        self.assertEqual(self._create().status_code, status.HTTP_201_CREATED)
        response = self._create(
            supplier_id=self.other_supplier.id,
            invoice_number='INV-1001',
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_create_transaction_rollback(self):
        before_invoices = PurchaseInvoice.objects.count()
        before_items = PurchaseInvoiceItem.objects.count()
        response = self._create(
            invoice_number='INV-ROLLBACK',
            products=[
                {
                    'material_id': self.material_one.id,
                    'quantity': 100,
                    'rate': '50.00',
                    'batch_number': 'B001',
                },
                {
                    'material_id': 999999,
                    'quantity': 100,
                    'rate': '50.00',
                    'batch_number': 'B002',
                },
            ],
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(PurchaseInvoice.objects.count(), before_invoices)
        self.assertEqual(PurchaseInvoiceItem.objects.count(), before_items)

    def test_list_search_filter_sort(self):
        self._create()
        self._create(
            invoice_number='INV-2000',
            products=[
                {
                    'material_id': self.material_two.id,
                    'quantity': 10,
                    'rate': '100.00',
                    'batch_number': 'B200',
                }
            ],
        )
        search = self.client_api.get(
            '/api/purchase-invoices/?search=INV-1001', **self.auth
        )
        self.assertEqual(search.status_code, status.HTTP_200_OK)
        self.assertEqual(search.data['pagination']['total'], 1)

        supplier_search = self.client_api.get(
            '/api/purchase-invoices/?search=ABC', **self.auth
        )
        self.assertEqual(supplier_search.data['pagination']['total'], 2)

        material_search = self.client_api.get(
            '/api/purchase-invoices/?search=Copper', **self.auth
        )
        self.assertEqual(material_search.data['pagination']['total'], 1)

        filtered = self.client_api.get(
            f'/api/purchase-invoices/?supplier_id={self.supplier_row.id}&material_id={self.material_two.id}',
            **self.auth,
        )
        self.assertEqual(filtered.data['pagination']['total'], 1)

        date_filtered = self.client_api.get(
            '/api/purchase-invoices/?invoice_date=2026-08-28', **self.auth
        )
        self.assertEqual(date_filtered.data['pagination']['total'], 2)

        sorted_resp = self.client_api.get(
            '/api/purchase-invoices/?sort_by=invoice_number&sort_order=asc',
            **self.auth,
        )
        self.assertEqual(sorted_resp.status_code, status.HTTP_200_OK)

    def test_get_by_id_and_missing(self):
        invoice_id = self._create().data['data']['id']
        ok = self.client_api.get(f'/api/purchase-invoices/{invoice_id}/', **self.auth)
        self.assertEqual(ok.status_code, status.HTTP_200_OK)
        missing = self.client_api.get('/api/purchase-invoices/999999/', **self.auth)
        self.assertEqual(missing.status_code, status.HTTP_404_NOT_FOUND)

    def test_put_full_update(self):
        created = self._create()
        invoice_id = created.data['data']['id']
        old_item_id = created.data['data']['products'][0]['id']
        payload = {
            **self.base_payload,
            'invoice_date': '2026-08-29',
            'products': [
                {
                    'material_id': self.material_one.id,
                    'quantity': 150,
                    'rate': '55.00',
                    'batch_number': 'B001',
                },
                {
                    'material_id': self.material_three.id,
                    'quantity': 50,
                    'rate': '700.00',
                    'batch_number': 'B005',
                },
            ],
        }
        response = self.client_api.put(
            f'/api/purchase-invoices/{invoice_id}/',
            payload,
            format='json',
            **self.auth,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data['data']['products']), 2)
        self.assertFalse(PurchaseInvoiceItem.objects.filter(pk=old_item_id).exists())

    def test_put_duplicate_invoice_number(self):
        self._create(invoice_number='INV-KEEP')
        second = self._create(invoice_number='INV-OTHER')
        invoice_id = second.data['data']['id']
        response = self.client_api.put(
            f'/api/purchase-invoices/{invoice_id}/',
            {
                **self.base_payload,
                'invoice_number': 'INV-KEEP',
            },
            format='json',
            **self.auth,
        )
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)

    def test_patch_invoice_number_only(self):
        invoice_id = self._create().data['data']['id']
        before = self.client_api.get(
            f'/api/purchase-invoices/{invoice_id}/', **self.auth
        ).data['data']
        response = self.client_api.patch(
            f'/api/purchase-invoices/{invoice_id}/',
            {'invoice_number': 'INV-UPDATED'},
            format='json',
            **self.auth,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.data['data']
        self.assertEqual(data['invoice_number'], 'INV-UPDATED')
        self.assertEqual(data['supplier_id'], before['supplier_id'])
        self.assertEqual(data['invoice_date'], before['invoice_date'])
        self.assertEqual(len(data['products']), len(before['products']))

    def test_patch_supplier_only(self):
        invoice_id = self._create().data['data']['id']
        response = self.client_api.patch(
            f'/api/purchase-invoices/{invoice_id}/',
            {'supplier_id': self.other_supplier.id},
            format='json',
            **self.auth,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['data']['supplier_id'], self.other_supplier.id)

    def test_patch_products_upsert(self):
        created = self._create(
            invoice_number='INV-PATCH',
            products=[
                {
                    'material_id': self.material_one.id,
                    'quantity': 100,
                    'rate': '50.00',
                    'batch_number': 'B001',
                },
                {
                    'material_id': self.material_two.id,
                    'quantity': 200,
                    'rate': '400.00',
                    'batch_number': 'B002',
                },
            ],
        )
        invoice_id = created.data['data']['id']
        first_item_id = created.data['data']['products'][0]['id']
        response = self.client_api.patch(
            f'/api/purchase-invoices/{invoice_id}/',
            {
                'products': [
                    {
                        'id': first_item_id,
                        'material_id': self.material_one.id,
                        'quantity': 200,
                        'rate': '60.00',
                        'batch_number': 'B001',
                    }
                ]
            },
            format='json',
            **self.auth,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data['data']['products']), 2)
        updated = next(
            row for row in response.data['data']['products'] if row['id'] == first_item_id
        )
        self.assertEqual(Decimal(str(updated['quantity'])), Decimal('200.0000'))

    def test_delete_invoice_and_items(self):
        invoice_id = self._create().data['data']['id']
        response = self.client_api.delete(
            f'/api/purchase-invoices/{invoice_id}/', **self.auth
        )
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(PurchaseInvoice.objects.filter(pk=invoice_id).exists())
        self.assertFalse(
            PurchaseInvoiceItem.objects.filter(purchase_invoice_id=invoice_id).exists()
        )
        missing = self.client_api.get(
            f'/api/purchase-invoices/{invoice_id}/', **self.auth
        )
        self.assertEqual(missing.status_code, status.HTTP_404_NOT_FOUND)

    def test_delete_missing_invoice(self):
        response = self.client_api.delete('/api/purchase-invoices/999999/', **self.auth)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_same_material_different_batches_allowed(self):
        response = self._create(
            invoice_number='INV-MULTI-BATCH',
            products=[
                {
                    'material_id': self.material_one.id,
                    'quantity': 100,
                    'rate': '50.00',
                    'batch_number': 'B001',
                },
                {
                    'material_id': self.material_one.id,
                    'quantity': 75,
                    'rate': '55.00',
                    'batch_number': 'B002',
                },
            ],
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(len(response.data['data']['products']), 2)
