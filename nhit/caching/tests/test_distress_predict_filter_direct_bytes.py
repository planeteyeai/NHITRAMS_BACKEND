"""Direct-byte HIT serving for distress_predict_filter (DPGZ1)."""

from __future__ import annotations

import gzip
import json
import pickle
from unittest.mock import MagicMock, patch

from django.core.cache import cache
from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings
from rest_framework import status
from rest_framework.renderers import JSONRenderer
from rest_framework.test import APIClient

from nhit.caching.constants import CACHE_NS_DISTRESS_PREDICT_FILTER, CACHE_TTL_FILTER
from nhit.caching.distress_predict_filter_count import DISTRESS_PREDICT_FILTER_COUNT_SCOPE
from nhit.caching.distress_predict_filter_page import (
    DISTRESS_PREDICT_FILTER_PAGE_CACHE_MAGIC,
    DISTRESS_PREDICT_FILTER_PAGE_SCOPE,
    decode_distress_predict_filter_page_cache,
    encode_distress_predict_filter_page_cache,
    extract_distress_predict_filter_page_gzip_payload,
    get_cached_distress_predict_filter_page,
)
from nhit.caching.keys import build_cache_key
from nhit.caching.service import get_namespace_version, safe_cache_set
from nhit.middleware import SelectiveGZipMiddleware

SAMPLE_PAGE = [
    [
        {
            'distress_id': 1,
            'latitude': 24.5,
            'longitude': 72.8,
            'project_name': 'Abu Road to Swaroopganj',
            'chainage_start': 650.0,
            'chainage_end': 650.5,
            'total_distress': 3.0,
            'predicted_quantity': 3.0,
            'resolved_quantity': 0.0,
            'remaining_quantity': 3.0,
            'distress_type': 'Pothole',
            'direction': 'lhs',
            'lane': 'L1',
            'date': '2026-08-01',
            'carriage_type': 'Flexible',
            'pothole': 3.0,
            'alligator_crack': 0,
        }
    ]
]

BASE_PAYLOAD = {
    'project_name': ['Abu Road to Swaroopganj'],
    'direction': ['All'],
    'distress_type': ['All'],
    'page': 1,
    'page_size': 200000,
}

_LOCMEM = {
    'default': {
        'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
        'LOCATION': 'nhit-dpgz1-tests',
        'KEY_PREFIX': 'nhit-test-dpgz1',
    }
}


@override_settings(NHIT_CACHE_ENABLED=True, CACHES=_LOCMEM, JWT_SECRET_KEY='test-jwt-secret')
class DistressPredictDirectGzipTests(TestCase):
    databases = {'default'}

    def setUp(self):
        cache.clear()
        self.client = APIClient()
        from authentication.jwt_utils import create_access_token

        self.auth = {'HTTP_AUTHORIZATION': f'Bearer {create_access_token("cache.tester")}'}
        self.payload = dict(BASE_PAYLOAD)

    def _mock_qs(self, total=1):
        qs = MagicMock()
        qs.count.return_value = total
        return qs

    def _warm(self, mock_build_qs, mock_build_page, *, total=1):
        mock_build_qs.return_value = self._mock_qs(total=total)
        mock_build_page.return_value = SAMPLE_PAGE
        warm = self.client.post(
            '/distress_predict_filter/', self.payload, format='json', **self.auth
        )
        self.assertEqual(warm.status_code, status.HTTP_200_OK, warm.content)
        return warm

    def _cached_raw(self):
        from distress.serializers import DistressFilterSerializer

        ser = DistressFilterSerializer(data=self.payload)
        self.assertTrue(ser.is_valid(), ser.errors)
        key = build_cache_key(
            namespace=CACHE_NS_DISTRESS_PREDICT_FILTER,
            version=get_namespace_version(CACHE_NS_DISTRESS_PREDICT_FILTER),
            payload=ser.validated_data,
            scope=DISTRESS_PREDICT_FILTER_PAGE_SCOPE,
        )
        raw = cache.get(key)
        self.assertIsInstance(raw, (bytes, bytearray))
        return bytes(raw), key

    @patch('distress.views.build_distress_predict_filter_page')
    @patch('distress.views.build_distress_predict_filter_queryset')
    def test_direct_gzip_hit_headers_and_body(self, mock_build_qs, mock_build_page):
        self._warm(mock_build_qs, mock_build_page)
        raw, _ = self._cached_raw()
        gzip_payload = extract_distress_predict_filter_page_gzip_payload(raw)

        response = self.client.post(
            '/distress_predict_filter/',
            self.payload,
            format='json',
            HTTP_ACCEPT_ENCODING='gzip',
            **self.auth,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response['Content-Type'].startswith('application/json'))
        self.assertEqual(response.get('Content-Encoding'), 'gzip')
        self.assertEqual(response.get('Content-Length'), str(len(gzip_payload)))
        self.assertIn('Accept-Encoding', response.get('Vary', ''))
        self.assertEqual(response['X-Distress-Paginated'], 'true')
        self.assertEqual(response['X-Distress-Page'], '1')
        self.assertEqual(response['X-Distress-Page-Size'], '200000')
        self.assertEqual(response['X-Distress-Row-Count'], '1')
        self.assertEqual(response['X-Distress-Has-More'], 'false')
        self.assertEqual(bytes(response.content), gzip_payload)
        self.assertFalse(bytes(response.content).startswith(DISTRESS_PREDICT_FILTER_PAGE_CACHE_MAGIC))
        self.assertEqual(mock_build_page.call_count, 1)

    @patch('distress.views.build_distress_predict_filter_page')
    @patch('distress.views.build_distress_predict_filter_queryset')
    def test_no_db_on_hit(self, mock_build_qs, mock_build_page):
        self._warm(mock_build_qs, mock_build_page)
        before_page = mock_build_page.call_count
        before_count = mock_build_qs.return_value.count.call_count
        self.client.post(
            '/distress_predict_filter/',
            self.payload,
            format='json',
            HTTP_ACCEPT_ENCODING='gzip',
            **self.auth,
        )
        self.assertEqual(mock_build_page.call_count, before_page)
        # Count may still be a Redis HIT; queryset.count must not run again on page HIT
        # after warm (count was cached separately).
        self.assertEqual(mock_build_qs.return_value.count.call_count, before_count)

    @patch('distress.views.build_distress_predict_filter_page')
    @patch('distress.views.build_distress_predict_filter_queryset')
    def test_decompression_parity(self, mock_build_qs, mock_build_page):
        self._warm(mock_build_qs, mock_build_page)
        plain = self.client.post(
            '/distress_predict_filter/', self.payload, format='json', **self.auth
        )
        gzipped = self.client.post(
            '/distress_predict_filter/',
            self.payload,
            format='json',
            HTTP_ACCEPT_ENCODING='gzip',
            **self.auth,
        )
        self.assertIsNone(plain.get('Content-Encoding'))
        self.assertEqual(gzip.decompress(gzipped.content), plain.content)
        self.assertEqual(json.loads(plain.content), SAMPLE_PAGE)

    @patch('distress.views.build_distress_predict_filter_page')
    @patch('distress.views.build_distress_predict_filter_queryset')
    def test_no_double_gzip_through_middleware(self, mock_build_qs, mock_build_page):
        self._warm(mock_build_qs, mock_build_page)
        raw, _ = self._cached_raw()
        gzip_payload = extract_distress_predict_filter_page_gzip_payload(raw)

        api = self.client.post(
            '/distress_predict_filter/',
            self.payload,
            format='json',
            HTTP_ACCEPT_ENCODING='gzip',
            **self.auth,
        )
        factory = RequestFactory()
        req = factory.post('/distress_predict_filter/', data=b'{}', content_type='application/json')
        req.META['HTTP_ACCEPT_ENCODING'] = 'gzip'
        resp = HttpResponse(gzip_payload, content_type='application/json', status=200)
        resp['Content-Encoding'] = 'gzip'
        resp['Content-Length'] = str(len(gzip_payload))
        mw = SelectiveGZipMiddleware(get_response=lambda r: HttpResponse(b'x'))
        out = mw.process_response(req, resp)
        self.assertEqual(out.get('Content-Encoding'), 'gzip')
        self.assertEqual(bytes(out.content), gzip_payload)
        self.assertEqual(bytes(api.content), gzip_payload)

    @patch('distress.views.build_distress_predict_filter_page')
    @patch('distress.views.build_distress_predict_filter_queryset')
    def test_accept_encoding_q0(self, mock_build_qs, mock_build_page):
        self._warm(mock_build_qs, mock_build_page)
        raw, _ = self._cached_raw()
        gzip_payload = extract_distress_predict_filter_page_gzip_payload(raw)
        expected_json = JSONRenderer().render(SAMPLE_PAGE)

        r = self.client.post(
            '/distress_predict_filter/',
            self.payload,
            format='json',
            HTTP_ACCEPT_ENCODING='gzip;q=0',
            **self.auth,
        )
        self.assertNotEqual(bytes(r.content), gzip_payload)
        if r.get('Content-Encoding') == 'gzip':
            self.assertEqual(gzip.decompress(r.content), expected_json)
        else:
            self.assertEqual(bytes(r.content), expected_json)

    @patch('distress.views.build_distress_predict_filter_page')
    @patch('distress.views.build_distress_predict_filter_queryset')
    def test_corrupt_cache_falls_back(self, mock_build_qs, mock_build_page):
        mock_build_qs.return_value = self._mock_qs(total=1)
        mock_build_page.return_value = SAMPLE_PAGE
        expected = JSONRenderer().render(SAMPLE_PAGE)

        bad_values = [
            b'WRONGMAGIC\n' + b'\x1f\x8b' + b'xxxx',
            DISTRESS_PREDICT_FILTER_PAGE_CACHE_MAGIC,
            DISTRESS_PREDICT_FILTER_PAGE_CACHE_MAGIC + b'not-gzip',
            pickle.dumps(SAMPLE_PAGE),
            SAMPLE_PAGE,
        ]
        for bad in bad_values:
            with self.subTest(bad=repr(bad)[:40]):
                cache.clear()
                mock_build_page.reset_mock()
                self.client.post(
                    '/distress_predict_filter/', self.payload, format='json', **self.auth
                )
                _, key = self._cached_raw()
                safe_cache_set(key, bad, timeout=CACHE_TTL_FILTER)
                before = mock_build_page.call_count
                r = self.client.post(
                    '/distress_predict_filter/',
                    self.payload,
                    format='json',
                    HTTP_ACCEPT_ENCODING='gzip',
                    **self.auth,
                )
                self.assertEqual(r.status_code, status.HTTP_200_OK)
                self.assertEqual(gzip.decompress(r.content), expected)
                self.assertGreater(mock_build_page.call_count, before)

    def test_cache_key_scopes_isolated(self):
        version = get_namespace_version(CACHE_NS_DISTRESS_PREDICT_FILTER)
        page_key = build_cache_key(
            namespace=CACHE_NS_DISTRESS_PREDICT_FILTER,
            version=version,
            payload=BASE_PAYLOAD,
            scope=DISTRESS_PREDICT_FILTER_PAGE_SCOPE,
        )
        count_key = build_cache_key(
            namespace=CACHE_NS_DISTRESS_PREDICT_FILTER,
            version=version,
            payload={k: v for k, v in BASE_PAYLOAD.items() if k not in ('page', 'page_size')},
            scope=DISTRESS_PREDICT_FILTER_COUNT_SCOPE,
        )
        self.assertNotEqual(page_key, count_key)

    def test_magic_is_dpgz1(self):
        encoded = encode_distress_predict_filter_page_cache(SAMPLE_PAGE)
        self.assertTrue(encoded.startswith(b'DPGZ1\n'))
        self.assertEqual(decode_distress_predict_filter_page_cache(encoded), SAMPLE_PAGE)


@override_settings(NHIT_CACHE_ENABLED=True, CACHES=_LOCMEM)
class DistressPredictServeModeHelperTests(SimpleTestCase):
    def setUp(self):
        cache.clear()

    def test_gzip_and_json_modes_on_hit(self):
        calls = {'n': 0}

        def builder():
            calls['n'] += 1
            return SAMPLE_PAGE

        get_cached_distress_predict_filter_page(
            payload=BASE_PAYLOAD, builder=builder, serve_mode='python'
        )
        gz, m1 = get_cached_distress_predict_filter_page(
            payload=BASE_PAYLOAD, builder=builder, serve_mode='gzip_bytes'
        )
        js, m2 = get_cached_distress_predict_filter_page(
            payload=BASE_PAYLOAD, builder=builder, serve_mode='json_bytes'
        )
        self.assertTrue(m1['cache_hit'])
        self.assertTrue(m2['cache_hit'])
        self.assertEqual(m1['response_path'], 'direct_gzip_bytes')
        self.assertEqual(m2['response_path'], 'direct_json_bytes')
        self.assertEqual(gzip.decompress(gz), js)
        self.assertEqual(calls['n'], 1)
