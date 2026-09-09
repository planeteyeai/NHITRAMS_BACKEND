"""Tests for inventory_filter legacy page Redis representation (gzip JSON bytes)."""

from __future__ import annotations

import gzip
import json
import pickle
from unittest.mock import patch

from django.core.cache import cache
from django.test import SimpleTestCase, TestCase, override_settings
from rest_framework import status
from rest_framework.renderers import JSONRenderer
from rest_framework.test import APIClient

from nhit.caching.constants import (
    CACHE_NS_INVENTORY_FILTER,
    CACHE_NS_INVENTORY_FILTER_MAP,
    CACHE_TTL_INVENTORY_FILTER,
)
from nhit.caching.inventory_filter_count import INVENTORY_FILTER_COUNT_SCOPE
from nhit.caching.inventory_filter_map import INVENTORY_FILTER_MAP_SCOPE
from nhit.caching.inventory_filter_page import (
    INVENTORY_FILTER_PAGE_SCOPE,
    decode_inventory_filter_page_cache,
    encode_inventory_filter_page_cache,
    get_cached_inventory_filter_page,
)
from nhit.caching.invalidation import invalidate_inventory_filter_cache
from nhit.caching.keys import build_cache_key
from nhit.caching.service import get_namespace_version, safe_cache_set


SAMPLE_GROUPS = [
    [
        {
            'project_name': 'Kothakota to Kurnool',
            'chainage_start': 0.0,
            'chainage_end': 1.0,
            'direction': 'Increasing',
            'asset_type': 'Trees',
            'latitude': '15.1',
            'longitude': '77.9',
            'date': '2026-07-28',
            'trees': 2,
        },
        {
            'project_name': 'Kothakota to Kurnool',
            'chainage_start': 1.0,
            'chainage_end': 2.0,
            'direction': 'Increasing',
            'asset_type': 'Trees',
            'latitude': '15.2',
            'longitude': '77.91',
            'date': '2026-07-28',
            'trees': 1,
        },
    ],
    [
        {
            'project_name': 'Kothakota to Kurnool',
            'chainage_start': 3.0,
            'chainage_end': 4.0,
            'direction': 'Decreasing',
            'asset_type': 'Sign Boards',
            'latitude': '15.3',
            'longitude': '77.92',
            'date': '2026-07-28',
        },
    ],
]

BASE_PAYLOAD = {
    'chainage_start': 0,
    'chainage_end': 99999,
    'date': '2026-07-28',
    'direction': ['All'],
    'project_name': ['Kothakota to Kurnool'],
    'asset_type': ['All'],
    'page': 1,
    'page_size': 50000,
}

_LOCMEM = {
    'default': {
        'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
        'LOCATION': 'nhit-inventory-filter-page-cache-tests',
        'KEY_PREFIX': 'nhit-test-page',
    }
}


@override_settings(NHIT_CACHE_ENABLED=True, CACHES=_LOCMEM)
class InventoryFilterPageEncodeDecodeTests(SimpleTestCase):
    def test_roundtrip_preserves_nested_structure_and_field_order(self):
        encoded = encode_inventory_filter_page_cache(SAMPLE_GROUPS)
        self.assertIsInstance(encoded, (bytes, bytearray))
        self.assertTrue(encoded.startswith(b'IFGZ1\n'))
        decoded = decode_inventory_filter_page_cache(encoded)
        self.assertEqual(decoded, SAMPLE_GROUPS)
        # Exact key order within first row
        self.assertEqual(list(decoded[0][0].keys()), list(SAMPLE_GROUPS[0][0].keys()))
        # Re-rendered JSON must match original DRF render of the Python object
        self.assertEqual(
            JSONRenderer().render(decoded),
            JSONRenderer().render(SAMPLE_GROUPS),
        )

    def test_reject_old_pickle_bytes(self):
        pickled = pickle.dumps(SAMPLE_GROUPS, protocol=pickle.HIGHEST_PROTOCOL)
        with self.assertRaises(ValueError):
            decode_inventory_filter_page_cache(pickled)

    def test_reject_python_list_object(self):
        with self.assertRaises(ValueError):
            decode_inventory_filter_page_cache(SAMPLE_GROUPS)

    def test_reject_corrupt_gzip_payload(self):
        with self.assertRaises(Exception):
            decode_inventory_filter_page_cache(b'IFGZ1\n' + b'not-gzip')

    def test_reject_gzip_without_magic(self):
        raw = gzip.compress(JSONRenderer().render(SAMPLE_GROUPS), compresslevel=6)
        with self.assertRaises(ValueError):
            decode_inventory_filter_page_cache(raw)

    def test_gzip_smaller_than_json(self):
        json_bytes = JSONRenderer().render(SAMPLE_GROUPS)
        encoded = encode_inventory_filter_page_cache(SAMPLE_GROUPS)
        self.assertLess(len(encoded), len(json_bytes))


@override_settings(NHIT_CACHE_ENABLED=True, CACHES=_LOCMEM)
class InventoryFilterPageCacheServiceTests(SimpleTestCase):
    def setUp(self):
        cache.clear()

    def test_miss_then_hit_identical_value(self):
        calls = {'n': 0}

        def builder():
            calls['n'] += 1
            return SAMPLE_GROUPS

        v1, m1 = get_cached_inventory_filter_page(payload=BASE_PAYLOAD, builder=builder)
        v2, m2 = get_cached_inventory_filter_page(payload=BASE_PAYLOAD, builder=builder)
        self.assertEqual(v1, SAMPLE_GROUPS)
        self.assertEqual(v2, SAMPLE_GROUPS)
        self.assertFalse(m1['cache_hit'])
        self.assertTrue(m2['cache_hit'])
        self.assertEqual(calls['n'], 1)
        self.assertEqual(m1['cache_repr'], INVENTORY_FILTER_PAGE_SCOPE)
        self.assertEqual(m1.get('redis_payload_bytes'), len(encode_inventory_filter_page_cache(SAMPLE_GROUPS)))

    def test_page_and_page_size_are_part_of_cache_key(self):
        calls = {'n': 0}

        def builder():
            calls['n'] += 1
            return [[{'page_marker': calls['n']}]]

        get_cached_inventory_filter_page(payload=BASE_PAYLOAD, builder=builder)
        get_cached_inventory_filter_page(
            payload={**BASE_PAYLOAD, 'page': 2},
            builder=builder,
        )
        get_cached_inventory_filter_page(
            payload={**BASE_PAYLOAD, 'page_size': 5000},
            builder=builder,
        )
        self.assertEqual(calls['n'], 3)

        # Hits for each distinct key
        _, m_p1 = get_cached_inventory_filter_page(payload=BASE_PAYLOAD, builder=builder)
        _, m_p2 = get_cached_inventory_filter_page(
            payload={**BASE_PAYLOAD, 'page': 2},
            builder=builder,
        )
        _, m_ps = get_cached_inventory_filter_page(
            payload={**BASE_PAYLOAD, 'page_size': 5000},
            builder=builder,
        )
        self.assertTrue(m_p1['cache_hit'])
        self.assertTrue(m_p2['cache_hit'])
        self.assertTrue(m_ps['cache_hit'])
        self.assertEqual(calls['n'], 3)

    def test_page_scope_differs_from_count_and_map_and_legacy_empty_scope(self):
        version = get_namespace_version(CACHE_NS_INVENTORY_FILTER)
        page_key = build_cache_key(
            namespace=CACHE_NS_INVENTORY_FILTER,
            version=version,
            payload=BASE_PAYLOAD,
            scope=INVENTORY_FILTER_PAGE_SCOPE,
        )
        empty_scope_key = build_cache_key(
            namespace=CACHE_NS_INVENTORY_FILTER,
            version=version,
            payload=BASE_PAYLOAD,
            scope='',
        )
        count_key = build_cache_key(
            namespace=CACHE_NS_INVENTORY_FILTER,
            version=version,
            payload={k: v for k, v in BASE_PAYLOAD.items() if k not in ('page', 'page_size')},
            scope=INVENTORY_FILTER_COUNT_SCOPE,
        )
        map_key = build_cache_key(
            namespace=CACHE_NS_INVENTORY_FILTER_MAP,
            version=get_namespace_version(CACHE_NS_INVENTORY_FILTER_MAP),
            payload=BASE_PAYLOAD,
            scope=INVENTORY_FILTER_MAP_SCOPE,
        )
        self.assertNotEqual(page_key, empty_scope_key)
        self.assertNotEqual(page_key, count_key)
        self.assertNotEqual(page_key, map_key)
        self.assertTrue(INVENTORY_FILTER_PAGE_SCOPE)
        self.assertEqual(CACHE_TTL_INVENTORY_FILTER, 604800)

    def test_corrupt_cached_payload_falls_back_to_builder(self):
        calls = {'n': 0}

        def builder():
            calls['n'] += 1
            return SAMPLE_GROUPS

        # Warm key path once so we know the key, then overwrite with garbage.
        get_cached_inventory_filter_page(payload=BASE_PAYLOAD, builder=builder)
        version = get_namespace_version(CACHE_NS_INVENTORY_FILTER)
        cache_key = build_cache_key(
            namespace=CACHE_NS_INVENTORY_FILTER,
            version=version,
            payload=BASE_PAYLOAD,
            scope=INVENTORY_FILTER_PAGE_SCOPE,
        )
        safe_cache_set(cache_key, b'IFGZ1\ncorrupt', timeout=CACHE_TTL_INVENTORY_FILTER)

        value, meta = get_cached_inventory_filter_page(payload=BASE_PAYLOAD, builder=builder)
        self.assertEqual(value, SAMPLE_GROUPS)
        self.assertFalse(meta['cache_hit'])
        self.assertEqual(calls['n'], 2)

        # Replacement write should make subsequent HIT succeed
        value2, meta2 = get_cached_inventory_filter_page(payload=BASE_PAYLOAD, builder=builder)
        self.assertEqual(value2, SAMPLE_GROUPS)
        self.assertTrue(meta2['cache_hit'])
        self.assertEqual(calls['n'], 2)

    def test_old_pickle_object_in_new_key_falls_back(self):
        calls = {'n': 0}

        def builder():
            calls['n'] += 1
            return SAMPLE_GROUPS

        version = get_namespace_version(CACHE_NS_INVENTORY_FILTER)
        cache_key = build_cache_key(
            namespace=CACHE_NS_INVENTORY_FILTER,
            version=version,
            payload=BASE_PAYLOAD,
            scope=INVENTORY_FILTER_PAGE_SCOPE,
        )
        # Simulate accidentally finding a legacy Python object under the key.
        safe_cache_set(cache_key, SAMPLE_GROUPS, timeout=CACHE_TTL_INVENTORY_FILTER)

        value, meta = get_cached_inventory_filter_page(payload=BASE_PAYLOAD, builder=builder)
        self.assertEqual(value, SAMPLE_GROUPS)
        self.assertFalse(meta['cache_hit'])
        self.assertEqual(calls['n'], 1)

    @patch('nhit.caching.inventory_filter_page.safe_cache_get', return_value=(False, None))
    @patch('nhit.caching.inventory_filter_page.safe_cache_set', return_value=False)
    def test_redis_failure_falls_back_to_builder(self, *_mocks):
        value, meta = get_cached_inventory_filter_page(
            payload=BASE_PAYLOAD,
            builder=lambda: SAMPLE_GROUPS,
        )
        self.assertEqual(value, SAMPLE_GROUPS)
        self.assertFalse(meta['cache_hit'])

    def test_invalidation_bumps_page_cache(self):
        calls = {'n': 0}

        def builder():
            calls['n'] += 1
            return SAMPLE_GROUPS

        get_cached_inventory_filter_page(payload=BASE_PAYLOAD, builder=builder)
        invalidate_inventory_filter_cache()
        _, meta = get_cached_inventory_filter_page(payload=BASE_PAYLOAD, builder=builder)
        self.assertFalse(meta['cache_hit'])
        self.assertEqual(calls['n'], 2)


@override_settings(
    NHIT_CACHE_ENABLED=False,
    CACHES={'default': {'BACKEND': 'django.core.cache.backends.dummy.DummyCache'}},
)
class InventoryFilterPageCacheDisabledTests(SimpleTestCase):
    def test_disabled_skips_redis(self):
        calls = {'n': 0}

        def builder():
            calls['n'] += 1
            return SAMPLE_GROUPS

        v1, m1 = get_cached_inventory_filter_page(payload=BASE_PAYLOAD, builder=builder)
        v2, m2 = get_cached_inventory_filter_page(payload=BASE_PAYLOAD, builder=builder)
        self.assertEqual(v1, SAMPLE_GROUPS)
        self.assertEqual(v2, SAMPLE_GROUPS)
        self.assertFalse(m1['cache_enabled'])
        self.assertEqual(calls['n'], 2)


@override_settings(NHIT_CACHE_ENABLED=True, CACHES=_LOCMEM)
class InventoryFilterPageHttpIntegrationTests(TestCase):
    databases = {'default'}

    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.payload = {
            'chainage_start': 0,
            'chainage_end': 99999,
            'date': '2026-07-28',
            'direction': ['All'],
            'project_name': ['Kothakota to Kurnool'],
            'asset_type': ['All'],
            'page': 1,
            'page_size': 5000,
        }
        self.mock_groups = SAMPLE_GROUPS

    @patch('inventory.views.build_inventory_filter_groups')
    @patch('inventory.views.build_inventory_filter_queryset')
    def test_miss_hit_exact_json_and_headers(self, mock_build_qs, mock_build_groups):
        mock_qs = mock_build_qs.return_value
        mock_qs.count.return_value = 230523
        mock_build_groups.return_value = self.mock_groups

        miss = self.client.post('/inventory_filter/', self.payload, format='json')
        hit = self.client.post('/inventory_filter/', self.payload, format='json')

        self.assertEqual(miss.status_code, status.HTTP_200_OK)
        self.assertEqual(hit.status_code, status.HTTP_200_OK)
        self.assertEqual(miss.content, hit.content)
        self.assertEqual(json.loads(miss.content), self.mock_groups)
        for header in (
            'X-Inventory-Paginated',
            'X-Inventory-Page',
            'X-Inventory-Page-Size',
            'X-Inventory-Row-Count',
            'X-Inventory-Has-More',
        ):
            self.assertEqual(miss[header], hit[header])
        self.assertEqual(miss['X-Inventory-Paginated'], 'true')
        self.assertEqual(miss['X-Inventory-Page'], '1')
        self.assertEqual(miss['X-Inventory-Page-Size'], '5000')
        self.assertEqual(miss['X-Inventory-Row-Count'], '230523')
        self.assertEqual(miss['X-Inventory-Has-More'], 'true')
        self.assertEqual(mock_build_groups.call_count, 1)

    @patch('inventory.views.build_inventory_filter_groups')
    @patch('inventory.views.build_inventory_filter_queryset')
    def test_gzip_http_body_unchanged(self, mock_build_qs, mock_build_groups):
        mock_qs = mock_build_qs.return_value
        mock_qs.count.return_value = 10000
        mock_build_groups.return_value = self.mock_groups

        # Warm cache
        self.client.post('/inventory_filter/', self.payload, format='json')

        plain = self.client.post('/inventory_filter/', self.payload, format='json')
        compressed = self.client.post(
            '/inventory_filter/',
            self.payload,
            format='json',
            HTTP_ACCEPT_ENCODING='gzip',
        )
        self.assertEqual(compressed.get('Content-Encoding'), 'gzip')
        self.assertEqual(gzip.decompress(compressed.content), plain.content)

    @patch('inventory.views.build_inventory_filter_groups')
    @patch('inventory.views.build_inventory_filter_queryset')
    def test_no_match_unchanged(self, mock_build_qs, mock_build_groups):
        mock_qs = mock_build_qs.return_value
        mock_qs.count.return_value = 0
        response = self.client.post('/inventory_filter/', self.payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data, {'message': 'No match'})
        mock_build_groups.assert_not_called()

    @patch('inventory.views.build_inventory_filter_groups')
    @patch('inventory.views.build_inventory_filter_queryset')
    def test_stores_gzip_json_bytes_not_python_list(self, mock_build_qs, mock_build_groups):
        mock_qs = mock_build_qs.return_value
        mock_qs.count.return_value = 10
        mock_build_groups.return_value = self.mock_groups

        self.client.post('/inventory_filter/', self.payload, format='json')

        version = get_namespace_version(CACHE_NS_INVENTORY_FILTER)
        # Serializer validates/coerces payload; rebuild key from normalized request body
        # by reading whatever was written under page scope for this version.
        # Use the validated shape from the view: same keys as payload after serializer.
        from inventory.serializers import InventoryFilterSerializer

        ser = InventoryFilterSerializer(data=self.payload)
        self.assertTrue(ser.is_valid(), ser.errors)
        cache_key = build_cache_key(
            namespace=CACHE_NS_INVENTORY_FILTER,
            version=version,
            payload=ser.validated_data,
            scope=INVENTORY_FILTER_PAGE_SCOPE,
        )
        raw = cache.get(cache_key)
        self.assertIsInstance(raw, (bytes, bytearray))
        self.assertTrue(bytes(raw).startswith(b'IFGZ1\n'))
        self.assertEqual(decode_inventory_filter_page_cache(raw), self.mock_groups)
