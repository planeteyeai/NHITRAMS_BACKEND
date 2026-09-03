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


class RedisUrlResolutionTests(SimpleTestCase):
    def test_internal_url_preferred_on_railway(self):
        from nhit.caching.redis_config import resolve_redis_url

        resolved = resolve_redis_url(
            redis_internal_url='redis://internal.example:6379/0',
            redis_url='redis://public.example:6379/0',
            use_internal=True,
        )
        self.assertEqual(resolved, 'redis://internal.example:6379/0')

    def test_local_prefers_public_even_when_internal_set(self):
        from nhit.caching.redis_config import resolve_redis_url

        resolved = resolve_redis_url(
            redis_internal_url='redis://internal.example:6379/0',
            redis_url='redis://public.example:6379/0',
            use_internal=False,
        )
        self.assertEqual(resolved, 'redis://public.example:6379/0')

    def test_fallback_to_redis_url_on_railway_without_internal(self):
        from nhit.caching.redis_config import resolve_redis_url

        resolved = resolve_redis_url(
            redis_internal_url='  ',
            redis_url='redis://public.example:6379/0',
            use_internal=True,
        )
        self.assertEqual(resolved, 'redis://public.example:6379/0')

    def test_empty_when_unset(self):
        from nhit.caching.redis_config import resolve_redis_url

        self.assertEqual(
            resolve_redis_url(redis_internal_url=None, redis_url=None, use_internal=False),
            '',
        )

    def test_resolver_module_does_not_log_or_print_urls(self):
        import inspect

        from nhit.caching import redis_config

        source = inspect.getsource(redis_config)
        self.assertNotIn('logging', source)
        self.assertNotIn('print(', source)
        self.assertNotIn('logger', source)


@override_settings(
    NHIT_CACHE_ENABLED=True,
    CACHES={
        'default': {
            'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
            'LOCATION': 'nhit-count-key-tests',
            'KEY_PREFIX': 'nhit-test',
        }
    },
)
class InventoryFilterCountKeyTests(SimpleTestCase):
    def setUp(self):
        cache.clear()

    def test_page_fields_excluded_from_count_payload(self):
        from nhit.caching.inventory_filter_count import filter_payload_without_pagination
        from nhit.caching.keys import payload_digest

        base = {
            'chainage_start': 0.0,
            'chainage_end': 100.0,
            'date': date(2026, 4, 20),
            'direction': [],
            'project_name': ['A'],
            'asset_type': [],
            'page': 1,
            'page_size': 5000,
        }
        other_page = {**base, 'page': 9, 'page_size': 15000}
        self.assertEqual(
            payload_digest(filter_payload_without_pagination(base)),
            payload_digest(filter_payload_without_pagination(other_page)),
        )

    def test_distinct_filters_produce_distinct_count_keys(self):
        from nhit.caching.inventory_filter_count import filter_payload_without_pagination
        from nhit.caching.keys import build_cache_key

        def key_for(**overrides):
            payload = {
                'chainage_start': 0.0,
                'chainage_end': 100.0,
                'date': date(2026, 4, 20),
                'direction': [],
                'project_name': ['A'],
                'asset_type': [],
                'page': 1,
                'page_size': 5000,
                **overrides,
            }
            return build_cache_key(
                namespace='inventory_filter',
                version=1,
                payload=filter_payload_without_pagination(payload),
                scope='count',
            )

        keys = {
            key_for(),
            key_for(project_name=['B']),
            key_for(date=date(2026, 7, 28)),
            key_for(chainage_start=10.0),
            key_for(chainage_end=50.0),
            key_for(direction=['Increasing']),
            key_for(asset_type=['Trees']),
        }
        self.assertEqual(len(keys), 7)

    def test_count_cache_hit_skips_builder(self):
        from nhit.caching.inventory_filter_count import get_cached_inventory_filter_count

        calls = {'n': 0}

        def builder():
            calls['n'] += 1
            return 99

        payload = {
            'chainage_start': 0.0,
            'chainage_end': 1.0,
            'date': date(2026, 4, 20),
            'direction': [],
            'project_name': ['A'],
            'asset_type': [],
            'page': 1,
            'page_size': 5000,
        }
        v1, m1 = get_cached_inventory_filter_count(payload=payload, builder=builder)
        v2, m2 = get_cached_inventory_filter_count(
            payload={**payload, 'page': 2, 'page_size': 100},
            builder=builder,
        )
        self.assertEqual(v1, 99)
        self.assertEqual(v2, 99)
        self.assertFalse(m1['cache_hit'])
        self.assertTrue(m2['cache_hit'])
        self.assertEqual(calls['n'], 1)


@override_settings(
    NHIT_CACHE_ENABLED=True,
    CACHES={
        'default': {
            'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
            'LOCATION': 'nhit-stampede-tests',
            'KEY_PREFIX': 'nhit-test',
        }
    },
)
class CacheStampedeWaitTests(SimpleTestCase):
    def setUp(self):
        cache.clear()

    @patch('nhit.caching.service.time.sleep', return_value=None)
    @patch('nhit.caching.service._acquire_population_lock', return_value=False)
    @patch('nhit.caching.service.safe_cache_get')
    def test_waiter_uses_value_populated_during_backoff(self, mock_get, _lock, mock_sleep):
        calls = {'n': 0}

        def builder():
            calls['n'] += 1
            return {'rebuilt': True}

        # Initial miss, then HIT on first backoff poll — waiter must not rebuild.
        mock_get.side_effect = [
            (False, None),
            (True, {'ok': True}),
        ]
        value, meta = get_or_set_cached_value(
            namespace='stampede_ns',
            payload={'p': 1},
            builder=builder,
            timeout=CACHE_TTL_FILTER,
            use_lock=True,
        )
        self.assertEqual(value, {'ok': True})
        self.assertTrue(meta['cache_hit'])
        self.assertEqual(calls['n'], 0)
        mock_sleep.assert_called()
