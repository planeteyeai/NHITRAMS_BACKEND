"""Direct-byte HIT serving for inventory_filter (Phase 3 implementation)."""

from __future__ import annotations

import gzip
import json
import pickle
from unittest.mock import patch

from django.core.cache import cache
from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings
from rest_framework import status
from rest_framework.renderers import JSONRenderer
from rest_framework.test import APIClient

from nhit.caching.accept_encoding import accepts_gzip_encoding
from nhit.caching.constants import (
    CACHE_NS_INVENTORY_FILTER,
    CACHE_NS_INVENTORY_FILTER_MAP,
    CACHE_TTL_INVENTORY_FILTER,
)
from nhit.caching.inventory_filter_count import INVENTORY_FILTER_COUNT_SCOPE
from nhit.caching.inventory_filter_map import INVENTORY_FILTER_MAP_SCOPE
from nhit.caching.inventory_filter_page import (
    INVENTORY_FILTER_PAGE_CACHE_MAGIC,
    INVENTORY_FILTER_PAGE_SCOPE,
    decode_inventory_filter_page_cache,
    encode_inventory_filter_page_cache,
    extract_inventory_filter_page_gzip_payload,
    get_cached_inventory_filter_page,
)
from nhit.caching.keys import build_cache_key
from nhit.caching.service import get_namespace_version, safe_cache_set
from nhit.middleware import SelectiveGZipMiddleware


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
        'LOCATION': 'nhit-direct-bytes-tests',
        'KEY_PREFIX': 'nhit-test-direct',
    }
}


class AcceptEncodingGzipTests(SimpleTestCase):
    def test_variants(self):
        cases = [
            (None, False),
            ('', False),
            ('   ', False),
            ('gzip', True),
            ('gzip, deflate, br', True),
            ('gzip;q=1.0', True),
            ('gzip;q=0.5', True),
            ('gzip;q=0', False),
            ('gzip;q=0.0', False),
            ('identity', False),
            ('deflate, br', False),
            ('*', True),
            ('*;q=0', False),
            ('deflate, gzip;q=0', False),
            ('deflate, gzip;q=0.1', True),
            ('GZIP', True),
        ]
        for header, expected in cases:
            with self.subTest(header=header):
                self.assertEqual(accepts_gzip_encoding(header), expected)


@override_settings(NHIT_CACHE_ENABLED=True, CACHES=_LOCMEM)
class DirectGzipHttpIntegrationTests(TestCase):
    databases = {'default'}

    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.payload = {**BASE_PAYLOAD, 'page_size': 5000}

    def _warm(self, mock_build_qs, mock_build_groups, *, total=230523):
        mock_qs = mock_build_qs.return_value
        mock_qs.count.return_value = total
        mock_build_groups.return_value = SAMPLE_GROUPS
        # Warm without forcing Accept-Encoding.
        warm = self.client.post('/inventory_filter/', self.payload, format='json')
        self.assertEqual(warm.status_code, status.HTTP_200_OK)
        return mock_qs

    def _cached_raw(self):
        from inventory.serializers import InventoryFilterSerializer

        ser = InventoryFilterSerializer(data=self.payload)
        self.assertTrue(ser.is_valid(), ser.errors)
        key = build_cache_key(
            namespace=CACHE_NS_INVENTORY_FILTER,
            version=get_namespace_version(CACHE_NS_INVENTORY_FILTER),
            payload=ser.validated_data,
            scope=INVENTORY_FILTER_PAGE_SCOPE,
        )
        raw = cache.get(key)
        self.assertIsInstance(raw, (bytes, bytearray))
        return bytes(raw), key

    @patch('inventory.views.build_inventory_filter_groups')
    @patch('inventory.views.build_inventory_filter_queryset')
    def test_direct_gzip_hit_headers_and_body(self, mock_build_qs, mock_build_groups):
        self._warm(mock_build_qs, mock_build_groups)
        raw, _ = self._cached_raw()
        gzip_payload = extract_inventory_filter_page_gzip_payload(raw)

        response = self.client.post(
            '/inventory_filter/',
            self.payload,
            format='json',
            HTTP_ACCEPT_ENCODING='gzip',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response['Content-Type'].startswith('application/json'))
        self.assertEqual(response.get('Content-Encoding'), 'gzip')
        self.assertEqual(response.get('Content-Length'), str(len(gzip_payload)))
        self.assertIn('Accept-Encoding', response.get('Vary', ''))
        self.assertEqual(response['X-Inventory-Paginated'], 'true')
        self.assertEqual(response['X-Inventory-Page'], '1')
        self.assertEqual(response['X-Inventory-Page-Size'], '5000')
        self.assertEqual(response['X-Inventory-Row-Count'], '230523')
        self.assertEqual(response['X-Inventory-Has-More'], 'true')
        self.assertEqual(bytes(response.content), gzip_payload)
        self.assertFalse(bytes(response.content).startswith(INVENTORY_FILTER_PAGE_CACHE_MAGIC))
        self.assertEqual(mock_build_groups.call_count, 1)

    @patch('inventory.views.build_inventory_filter_groups')
    @patch('inventory.views.build_inventory_filter_queryset')
    def test_decompression_parity_with_no_gzip_path(self, mock_build_qs, mock_build_groups):
        self._warm(mock_build_qs, mock_build_groups)
        plain = self.client.post('/inventory_filter/', self.payload, format='json')
        gzipped = self.client.post(
            '/inventory_filter/',
            self.payload,
            format='json',
            HTTP_ACCEPT_ENCODING='gzip',
        )
        self.assertIsNone(plain.get('Content-Encoding'))
        self.assertEqual(gzip.decompress(gzipped.content), plain.content)
        self.assertEqual(json.loads(plain.content), SAMPLE_GROUPS)
        # Field order preserved
        self.assertEqual(
            list(json.loads(plain.content)[0][0].keys()),
            list(SAMPLE_GROUPS[0][0].keys()),
        )

    @patch('inventory.views.build_inventory_filter_groups')
    @patch('inventory.views.build_inventory_filter_queryset')
    def test_no_double_gzip_through_middleware(self, mock_build_qs, mock_build_groups):
        self._warm(mock_build_qs, mock_build_groups)
        raw, _ = self._cached_raw()
        gzip_payload = extract_inventory_filter_page_gzip_payload(raw)

        api = self.client.post(
            '/inventory_filter/',
            self.payload,
            format='json',
            HTTP_ACCEPT_ENCODING='gzip',
        )
        # Re-run SelectiveGZipMiddleware explicitly on an equivalent response.
        factory = RequestFactory()
        req = factory.post('/inventory_filter/', data=b'{}', content_type='application/json')
        req.META['HTTP_ACCEPT_ENCODING'] = 'gzip'
        resp = HttpResponse(gzip_payload, content_type='application/json', status=200)
        resp['Content-Encoding'] = 'gzip'
        resp['Content-Length'] = str(len(gzip_payload))
        mw = SelectiveGZipMiddleware(get_response=lambda r: HttpResponse(b'x'))
        out = mw.process_response(req, resp)
        self.assertEqual(out.get('Content-Encoding'), 'gzip')
        self.assertEqual(bytes(out.content), gzip_payload)
        self.assertEqual(gzip.decompress(bytes(out.content)), JSONRenderer().render(SAMPLE_GROUPS))
        self.assertEqual(bytes(api.content), gzip_payload)

    @patch('inventory.views.build_inventory_filter_groups')
    @patch('inventory.views.build_inventory_filter_queryset')
    def test_accept_encoding_variants(self, mock_build_qs, mock_build_groups):
        self._warm(mock_build_qs, mock_build_groups)
        raw, _ = self._cached_raw()
        gzip_payload = extract_inventory_filter_page_gzip_payload(raw)
        expected_json = JSONRenderer().render(SAMPLE_GROUPS)

        gzip_ok = [
            'gzip',
            'gzip, deflate, br',
            'gzip;q=1.0',
            'gzip;q=0.5',
        ]
        for header in gzip_ok:
            with self.subTest(header=header):
                r = self.client.post(
                    '/inventory_filter/',
                    self.payload,
                    format='json',
                    HTTP_ACCEPT_ENCODING=header,
                )
                self.assertEqual(r.get('Content-Encoding'), 'gzip')
                self.assertEqual(bytes(r.content), gzip_payload)

        gzip_no = [
            'gzip;q=0',
            'identity',
            '',
        ]
        for header in gzip_no:
            with self.subTest(header=header):
                kwargs = {'format': 'json'}
                if header != '':
                    kwargs['HTTP_ACCEPT_ENCODING'] = header
                r = self.client.post('/inventory_filter/', self.payload, **kwargs)
                # Direct Redis gzip payload must never be used when gzip is not acceptable.
                self.assertNotEqual(bytes(r.content), gzip_payload)
                if header == 'gzip;q=0' and r.get('Content-Encoding') == 'gzip':
                    # Django GZipMiddleware matches \\bgzip\\b and may still compress;
                    # that is pre-existing middleware behavior, not direct-byte serving.
                    self.assertEqual(gzip.decompress(r.content), expected_json)
                else:
                    self.assertNotEqual(r.get('Content-Encoding'), 'gzip')
                    self.assertEqual(bytes(r.content), expected_json)

        # Missing Accept-Encoding
        r = self.client.post('/inventory_filter/', self.payload, format='json')
        self.assertNotEqual(r.get('Content-Encoding'), 'gzip')
        self.assertEqual(bytes(r.content), expected_json)

    @patch('inventory.views.build_inventory_filter_groups')
    @patch('inventory.views.build_inventory_filter_queryset')
    def test_corrupt_cache_falls_back(self, mock_build_qs, mock_build_groups):
        mock_qs = mock_build_qs.return_value
        mock_qs.count.return_value = 230523
        mock_build_groups.return_value = SAMPLE_GROUPS
        expected = JSONRenderer().render(SAMPLE_GROUPS)

        bad_values = [
            b'WRONGMAGIC\n' + b'\x1f\x8b' + b'xxxx',
            INVENTORY_FILTER_PAGE_CACHE_MAGIC,
            INVENTORY_FILTER_PAGE_CACHE_MAGIC + b'not-gzip',
            pickle.dumps(SAMPLE_GROUPS),
            SAMPLE_GROUPS,
        ]
        for bad in bad_values:
            with self.subTest(bad=repr(bad)[:40]):
                cache.clear()
                mock_build_groups.reset_mock()
                # Seed a valid entry then overwrite with corrupt payload.
                self.client.post('/inventory_filter/', self.payload, format='json')
                _, key = self._cached_raw()
                safe_cache_set(key, bad, timeout=CACHE_TTL_INVENTORY_FILTER)
                before = mock_build_groups.call_count
                r = self.client.post(
                    '/inventory_filter/',
                    self.payload,
                    format='json',
                    HTTP_ACCEPT_ENCODING='gzip',
                )
                self.assertEqual(r.status_code, status.HTTP_200_OK)
                self.assertEqual(gzip.decompress(r.content), expected)
                self.assertGreater(mock_build_groups.call_count, before)

    @patch('inventory.views.build_inventory_filter_groups')
    @patch('inventory.views.build_inventory_filter_queryset')
    @patch('nhit.caching.inventory_filter_page.safe_cache_get', return_value=(False, None))
    def test_redis_get_failure_falls_back(self, _mock_get, mock_build_qs, mock_build_groups):
        mock_qs = mock_build_qs.return_value
        mock_qs.count.return_value = 10
        mock_build_groups.return_value = SAMPLE_GROUPS
        r = self.client.post(
            '/inventory_filter/',
            self.payload,
            format='json',
            HTTP_ACCEPT_ENCODING='gzip',
        )
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertEqual(r.get('Content-Encoding'), 'gzip')
        self.assertEqual(gzip.decompress(r.content), JSONRenderer().render(SAMPLE_GROUPS))
        self.assertEqual(mock_build_groups.call_count, 1)

    @patch('inventory.views.build_inventory_filter_groups')
    @patch('inventory.views.build_inventory_filter_queryset')
    def test_page_page_size_isolation(self, mock_build_qs, mock_build_groups):
        mock_qs = mock_build_qs.return_value
        mock_qs.count.return_value = 100000
        mock_build_groups.return_value = SAMPLE_GROUPS

        self.client.post('/inventory_filter/', self.payload, format='json')
        self.client.post(
            '/inventory_filter/',
            {**self.payload, 'page': 2},
            format='json',
        )
        self.client.post(
            '/inventory_filter/',
            {**self.payload, 'page_size': 50000},
            format='json',
        )
        self.assertEqual(mock_build_groups.call_count, 3)

        # Hits
        self.client.post(
            '/inventory_filter/',
            self.payload,
            format='json',
            HTTP_ACCEPT_ENCODING='gzip',
        )
        self.assertEqual(mock_build_groups.call_count, 3)

    def test_cache_key_scopes_isolated(self):
        version = get_namespace_version(CACHE_NS_INVENTORY_FILTER)
        page_key = build_cache_key(
            namespace=CACHE_NS_INVENTORY_FILTER,
            version=version,
            payload=BASE_PAYLOAD,
            scope=INVENTORY_FILTER_PAGE_SCOPE,
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
        self.assertNotEqual(page_key, count_key)
        self.assertNotEqual(page_key, map_key)

    @patch('inventory.views.build_inventory_filter_groups')
    @patch('inventory.views.build_inventory_filter_queryset')
    def test_legacy_compat_python_mode_vs_direct_gzip(self, mock_build_qs, mock_build_groups):
        """A: python decode path vs B: direct gzip — decompressed body equal."""
        mock_qs = mock_build_qs.return_value
        mock_qs.count.return_value = 10
        mock_build_groups.return_value = SAMPLE_GROUPS

        # Populate cache
        get_cached_inventory_filter_page(
            payload=self.payload,
            builder=lambda: SAMPLE_GROUPS,
            serve_mode='python',
        )
        py_val, py_meta = get_cached_inventory_filter_page(
            payload=self.payload,
            builder=lambda: SAMPLE_GROUPS,
            serve_mode='python',
        )
        gz_val, gz_meta = get_cached_inventory_filter_page(
            payload=self.payload,
            builder=lambda: SAMPLE_GROUPS,
            serve_mode='gzip_bytes',
        )
        self.assertTrue(py_meta['cache_hit'])
        self.assertTrue(gz_meta['cache_hit'])
        self.assertEqual(gz_meta['response_path'], 'direct_gzip_bytes')
        self.assertEqual(JSONRenderer().render(py_val), gzip.decompress(gz_val))
        self.assertEqual(decode_inventory_filter_page_cache(
            INVENTORY_FILTER_PAGE_CACHE_MAGIC + gz_val
        ), py_val)


@override_settings(NHIT_CACHE_ENABLED=True, CACHES=_LOCMEM)
class ServeModeHelperTests(SimpleTestCase):
    def setUp(self):
        cache.clear()

    def test_gzip_and_json_modes_on_hit(self):
        calls = {'n': 0}

        def builder():
            calls['n'] += 1
            return SAMPLE_GROUPS

        get_cached_inventory_filter_page(payload=BASE_PAYLOAD, builder=builder, serve_mode='python')
        gz, m1 = get_cached_inventory_filter_page(
            payload=BASE_PAYLOAD, builder=builder, serve_mode='gzip_bytes'
        )
        js, m2 = get_cached_inventory_filter_page(
            payload=BASE_PAYLOAD, builder=builder, serve_mode='json_bytes'
        )
        self.assertTrue(m1['cache_hit'])
        self.assertTrue(m2['cache_hit'])
        self.assertEqual(m1['response_path'], 'direct_gzip_bytes')
        self.assertEqual(m2['response_path'], 'direct_json_bytes')
        self.assertEqual(gzip.decompress(gz), js)
        self.assertEqual(calls['n'], 1)
