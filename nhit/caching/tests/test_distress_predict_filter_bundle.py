"""Tests for distress_predict_filter combined count+page MGET (2 Redis RTTs)."""

from __future__ import annotations

import gzip
from unittest.mock import patch

from django.core.cache import cache
from django.test import SimpleTestCase, TestCase, override_settings
from rest_framework import status
from rest_framework.renderers import JSONRenderer
from rest_framework.test import APIClient

from nhit.caching.constants import CACHE_NS_DISTRESS_PREDICT_FILTER, CACHE_TTL_FILTER
from nhit.caching.distress_predict_filter_bundle import (
    get_cached_distress_predict_filter_count_and_page,
)
from nhit.caching.distress_predict_filter_count import DISTRESS_PREDICT_FILTER_COUNT_SCOPE
from nhit.caching.distress_predict_filter_page import (
    DISTRESS_PREDICT_FILTER_PAGE_CACHE_MAGIC,
    DISTRESS_PREDICT_FILTER_PAGE_SCOPE,
    encode_distress_predict_filter_page_cache,
)
from nhit.caching.keys import build_cache_key
from nhit.caching.service import get_namespace_version, safe_cache_set

SAMPLE_PAGE = [[{'distress_id': 1, 'project_name': 'Abu Road to Swaroopganj'}]]

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
        'LOCATION': 'nhit-dpgz1-bundle-tests',
        'KEY_PREFIX': 'nhit-test-bundle',
    }
}


@override_settings(NHIT_CACHE_ENABLED=True, CACHES=_LOCMEM)
class DistressPredictBundleHelperTests(SimpleTestCase):
    def setUp(self):
        cache.clear()

    def test_full_hit_uses_two_round_trips_and_skips_builders(self):
        calls = {'count': 0, 'page': 0}

        def count_builder():
            calls['count'] += 1
            return 10

        def page_builder():
            calls['page'] += 1
            return SAMPLE_PAGE

        # Warm via normal path
        get_cached_distress_predict_filter_count_and_page(
            payload=BASE_PAYLOAD,
            count_builder=count_builder,
            page_builder=page_builder,
            serve_mode='gzip_bytes',
        )
        self.assertEqual(calls['count'], 1)
        self.assertEqual(calls['page'], 1)

        total, body, cmeta, pmeta = get_cached_distress_predict_filter_count_and_page(
            payload=BASE_PAYLOAD,
            count_builder=count_builder,
            page_builder=page_builder,
            serve_mode='gzip_bytes',
        )
        self.assertEqual(total, 10)
        self.assertTrue(cmeta['cache_hit'])
        self.assertTrue(pmeta['cache_hit'])
        self.assertEqual(pmeta['lookup_path'], 'mget_hit')
        self.assertEqual(pmeta['redis_round_trips'], 2)
        self.assertEqual(pmeta['response_path'], 'direct_gzip_bytes')
        self.assertEqual(calls['count'], 1)
        self.assertEqual(calls['page'], 1)
        self.assertEqual(gzip.decompress(body), JSONRenderer().render(SAMPLE_PAGE))

    def test_missing_page_rebuilds_page_only(self):
        version = get_namespace_version(CACHE_NS_DISTRESS_PREDICT_FILTER)
        count_key = build_cache_key(
            namespace=CACHE_NS_DISTRESS_PREDICT_FILTER,
            version=version,
            payload={k: v for k, v in BASE_PAYLOAD.items() if k not in ('page', 'page_size')},
            scope=DISTRESS_PREDICT_FILTER_COUNT_SCOPE,
        )
        safe_cache_set(count_key, 5, timeout=CACHE_TTL_FILTER)

        calls = {'count': 0, 'page': 0}

        def count_builder():
            calls['count'] += 1
            return 5

        def page_builder():
            calls['page'] += 1
            return SAMPLE_PAGE

        total, body, cmeta, pmeta = get_cached_distress_predict_filter_count_and_page(
            payload=BASE_PAYLOAD,
            count_builder=count_builder,
            page_builder=page_builder,
            serve_mode='json_bytes',
        )
        self.assertEqual(total, 5)
        self.assertTrue(cmeta['cache_hit'])
        self.assertEqual(calls['count'], 0)
        self.assertEqual(calls['page'], 1)
        self.assertEqual(pmeta['lookup_path'], 'mget_partial_rebuild_page')
        self.assertEqual(body, JSONRenderer().render(SAMPLE_PAGE))

    def test_corrupt_page_falls_back(self):
        version = get_namespace_version(CACHE_NS_DISTRESS_PREDICT_FILTER)
        count_key = build_cache_key(
            namespace=CACHE_NS_DISTRESS_PREDICT_FILTER,
            version=version,
            payload={k: v for k, v in BASE_PAYLOAD.items() if k not in ('page', 'page_size')},
            scope=DISTRESS_PREDICT_FILTER_COUNT_SCOPE,
        )
        page_key = build_cache_key(
            namespace=CACHE_NS_DISTRESS_PREDICT_FILTER,
            version=version,
            payload=BASE_PAYLOAD,
            scope=DISTRESS_PREDICT_FILTER_PAGE_SCOPE,
        )
        safe_cache_set(count_key, 3, timeout=CACHE_TTL_FILTER)
        safe_cache_set(page_key, b'NOTDPGZ1', timeout=CACHE_TTL_FILTER)

        calls = {'page': 0}

        def page_builder():
            calls['page'] += 1
            return SAMPLE_PAGE

        total, body, cmeta, pmeta = get_cached_distress_predict_filter_count_and_page(
            payload=BASE_PAYLOAD,
            count_builder=lambda: 3,
            page_builder=page_builder,
            serve_mode='gzip_bytes',
        )
        self.assertEqual(total, 3)
        self.assertEqual(calls['page'], 1)
        self.assertFalse(bytes(body).startswith(DISTRESS_PREDICT_FILTER_PAGE_CACHE_MAGIC))
        self.assertEqual(gzip.decompress(body), JSONRenderer().render(SAMPLE_PAGE))

    def test_corrupt_count_falls_back(self):
        version = get_namespace_version(CACHE_NS_DISTRESS_PREDICT_FILTER)
        count_key = build_cache_key(
            namespace=CACHE_NS_DISTRESS_PREDICT_FILTER,
            version=version,
            payload={k: v for k, v in BASE_PAYLOAD.items() if k not in ('page', 'page_size')},
            scope=DISTRESS_PREDICT_FILTER_COUNT_SCOPE,
        )
        page_key = build_cache_key(
            namespace=CACHE_NS_DISTRESS_PREDICT_FILTER,
            version=version,
            payload=BASE_PAYLOAD,
            scope=DISTRESS_PREDICT_FILTER_PAGE_SCOPE,
        )
        safe_cache_set(count_key, object(), timeout=CACHE_TTL_FILTER)
        safe_cache_set(
            page_key,
            encode_distress_predict_filter_page_cache(SAMPLE_PAGE),
            timeout=CACHE_TTL_FILTER,
        )

        total, body, cmeta, pmeta = get_cached_distress_predict_filter_count_and_page(
            payload=BASE_PAYLOAD,
            count_builder=lambda: 9,
            page_builder=lambda: SAMPLE_PAGE,
            serve_mode='gzip_bytes',
        )
        self.assertEqual(total, 9)
        self.assertFalse(cmeta['cache_hit'])
        self.assertTrue(pmeta['cache_hit'])

    def test_beyond_last_page_skips_page_rebuild(self):
        calls = {'page': 0}

        def page_builder():
            calls['page'] += 1
            return SAMPLE_PAGE

        total, body, cmeta, pmeta = get_cached_distress_predict_filter_count_and_page(
            payload={**BASE_PAYLOAD, 'page': 99},
            count_builder=lambda: 1,
            page_builder=page_builder,
            serve_mode='gzip_bytes',
            offset=98 * 200000,
        )
        self.assertEqual(total, 1)
        self.assertIsNone(body)
        self.assertEqual(calls['page'], 0)
        self.assertEqual(pmeta['lookup_path'], 'skipped_beyond_last_page')

    def test_concurrent_miss_only_one_page_builder(self):
        import threading

        barrier = threading.Barrier(4)
        builders = {'count': 0, 'page': 0}
        lock = threading.Lock()

        def count_builder():
            with lock:
                builders['count'] += 1
            return 7

        def page_builder():
            with lock:
                builders['page'] += 1
            return SAMPLE_PAGE

        results = []

        def worker():
            barrier.wait(timeout=5)
            results.append(
                get_cached_distress_predict_filter_count_and_page(
                    payload=BASE_PAYLOAD,
                    count_builder=count_builder,
                    page_builder=page_builder,
                    serve_mode='gzip_bytes',
                )
            )

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        self.assertEqual(len(results), 4)
        self.assertTrue(all(r[0] == 7 for r in results))
        # Stampede lock should keep rebuilds low (exactly 1 under locmem).
        self.assertEqual(builders['page'], 1)
        self.assertEqual(builders['count'], 1)


@override_settings(
    NHIT_CACHE_ENABLED=True,
    CACHES=_LOCMEM,
)
class DistressPredictBundleHttpTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.payload = dict(BASE_PAYLOAD)

    @patch('distress.views.build_distress_predict_filter_page')
    @patch('distress.views.build_distress_predict_filter_queryset')
    def test_http_hit_after_warm(self, mock_qs, mock_page):
        mock_qs.return_value.count.return_value = 1
        mock_page.return_value = SAMPLE_PAGE
        warm = self.client.post(
            '/distress_predict_filter/', self.payload, format='json'
        )
        self.assertEqual(warm.status_code, status.HTTP_200_OK)
        mock_page.reset_mock()
        hit = self.client.post(
            '/distress_predict_filter/',
            self.payload,
            format='json',
            HTTP_ACCEPT_ENCODING='gzip',
        )
        self.assertEqual(hit.status_code, status.HTTP_200_OK)
        self.assertEqual(hit.get('Content-Encoding'), 'gzip')
        self.assertEqual(hit['X-Distress-Row-Count'], '1')
        self.assertEqual(mock_page.call_count, 0)
