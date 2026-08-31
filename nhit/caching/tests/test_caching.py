"""Tests for NHIT Redis caching layer."""

from __future__ import annotations

from datetime import date
from unittest.mock import patch

from django.core.cache import cache
from django.test import SimpleTestCase, TestCase, override_settings
from rest_framework import status
from rest_framework.test import APIClient

from nhit.caching.constants import CACHE_NS_AIS_FILTER, CACHE_TTL_FILTER
from nhit.caching.invalidation import bump_namespace_version, invalidate_ais_filter_cache
from nhit.caching.keys import normalize_payload, payload_digest
from nhit.caching.service import get_or_set_cached_value, is_cache_usable


AIS_PAYLOAD = {
    'chainage_start': 0,
    'chainage_end': 1381,
    'date': '2025-06-20',
    'direction': ['All'],
    'project_name': ['Abu Road to Swaroopganj'],
}


@override_settings(
    NHIT_CACHE_ENABLED=True,
    CACHES={
        'default': {
            'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
            'LOCATION': 'nhit-cache-tests',
            'KEY_PREFIX': 'nhit-test',
        }
    },
)
class CacheKeyTests(SimpleTestCase):
    def test_equivalent_payloads_share_digest(self):
        a = {'project_name': ['A', 'B'], 'date': date(2026, 4, 20)}
        b = {'date': date(2026, 4, 20), 'project_name': ['B', 'A']}
        self.assertEqual(payload_digest(a), payload_digest(b))

    def test_different_payloads_have_different_digests(self):
        a = {'direction': ['All']}
        b = {'direction': ['Increasing']}
        self.assertNotEqual(payload_digest(a), payload_digest(b))

    def test_normalize_payload_sorts_scalar_lists(self):
        normalized = normalize_payload({'direction': ['Decreasing', 'All']})
        self.assertEqual(normalized['direction'], ['All', 'Decreasing'])


@override_settings(
    NHIT_CACHE_ENABLED=True,
    CACHES={
        'default': {
            'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
            'LOCATION': 'nhit-cache-service-tests',
            'KEY_PREFIX': 'nhit-test',
        }
    },
)
class CacheServiceTests(SimpleTestCase):
    def setUp(self):
        cache.clear()

    def test_miss_then_hit(self):
        calls = {'count': 0}

        def builder():
            calls['count'] += 1
            return {'value': 42}

        value1, meta1 = get_or_set_cached_value(
            namespace='test_ns',
            payload={'a': 1},
            builder=builder,
            timeout=CACHE_TTL_FILTER,
        )
        value2, meta2 = get_or_set_cached_value(
            namespace='test_ns',
            payload={'a': 1},
            builder=builder,
            timeout=CACHE_TTL_FILTER,
        )

        self.assertEqual(value1, {'value': 42})
        self.assertEqual(value2, {'value': 42})
        self.assertFalse(meta1['cache_hit'])
        self.assertTrue(meta2['cache_hit'])
        self.assertEqual(calls['count'], 1)

    def test_version_bump_invalidates_previous_entries(self):
        calls = {'count': 0}

        def builder():
            calls['count'] += 1
            return calls['count']

        get_or_set_cached_value(
            namespace='version_ns',
            payload={'x': 1},
            builder=builder,
            timeout=CACHE_TTL_FILTER,
        )
        bump_namespace_version('version_ns')
        _, meta = get_or_set_cached_value(
            namespace='version_ns',
            payload={'x': 1},
            builder=builder,
            timeout=CACHE_TTL_FILTER,
        )
        self.assertFalse(meta['cache_hit'])
        self.assertEqual(calls['count'], 2)

    @patch('nhit.caching.service.cache.get', side_effect=ConnectionError('redis down'))
    @patch('nhit.caching.service.cache.set', side_effect=ConnectionError('redis down'))
    def test_redis_failure_falls_back_to_builder(self, *_mocks):
        value, meta = get_or_set_cached_value(
            namespace='fail_ns',
            payload={'z': 1},
            builder=lambda: ['ok'],
            timeout=CACHE_TTL_FILTER,
        )
        self.assertEqual(value, ['ok'])
        self.assertFalse(meta['cache_hit'])


@override_settings(
    NHIT_CACHE_ENABLED=False,
    CACHES={'default': {'BACKEND': 'django.core.cache.backends.dummy.DummyCache'}},
)
class CacheDisabledTests(SimpleTestCase):
    def test_cache_disabled_skips_storage(self):
        calls = {'count': 0}

        def builder():
            calls['count'] += 1
            return [1]

        value, meta = get_or_set_cached_value(
            namespace='disabled_ns',
            payload={'a': 1},
            builder=builder,
            timeout=CACHE_TTL_FILTER,
        )
        self.assertEqual(value, [1])
        self.assertFalse(meta['cache_enabled'])
        self.assertEqual(calls['count'], 1)
        self.assertFalse(is_cache_usable())


@override_settings(
    NHIT_CACHE_ENABLED=True,
    CACHES={
        'default': {
            'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
            'LOCATION': 'nhit-cache-ais-tests',
            'KEY_PREFIX': 'nhit-test',
        }
    },
)
class AisFilterCacheIntegrationTests(TestCase):
    databases = {'default'}

    def setUp(self):
        cache.clear()
        self.client = APIClient()

    @patch('ais.views.build_ais_filter_groups', return_value=[[{'date': '2025-06-20'}]])
    def test_second_identical_request_hits_cache(self, mocked_builder):
        for _ in range(2):
            response = self.client.post('/ais_filter/', {
                'chainage_start': 0,
                'chainage_end': 1381,
                'date': '2025-06-20',
                'direction': ['All'],
                'project_name': ['Abu Road to Swaroopganj'],
            }, format='json')
            self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.assertEqual(mocked_builder.call_count, 1)
        self.assertEqual(response.json(), [[{'date': '2025-06-20'}]])

    @patch('ais.views.build_ais_filter_groups', return_value=[[{'date': '2025-06-20'}]])
    def test_invalidation_forces_rebuild(self, mocked_builder):
        self.client.post('/ais_filter/', AIS_PAYLOAD, format='json')
        invalidate_ais_filter_cache()
        self.client.post('/ais_filter/', AIS_PAYLOAD, format='json')
        self.assertEqual(mocked_builder.call_count, 2)

    @patch('ais.views.build_ais_filter_groups', side_effect=ConnectionError('db down'))
    @patch('nhit.caching.service.safe_cache_get', return_value=(True, [[{'date': 'cached'}]]))
    def test_cached_response_returned_when_db_would_fail(self, *_mocks):
        response = self.client.post('/ais_filter/', AIS_PAYLOAD, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json(), [[{'date': 'cached'}]])
