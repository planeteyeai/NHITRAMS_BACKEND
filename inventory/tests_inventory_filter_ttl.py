"""Focused tests: inventory_filter Redis TTL = 7 days (page + count + map only)."""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase, TestCase, override_settings
from rest_framework import status
from rest_framework.test import APIClient

from nhit.caching.constants import (
    CACHE_LOCK_TIMEOUT_SECONDS,
    CACHE_NS_INVENTORY_FILTER,
    CACHE_NS_INVENTORY_FILTER_MAP,
    CACHE_TTL_FILTER,
    CACHE_TTL_INVENTORY_FILTER,
)
from nhit.caching.inventory_filter_count import (
    INVENTORY_FILTER_COUNT_SCOPE,
    filter_payload_without_pagination,
    get_cached_inventory_filter_count,
)
from nhit.caching.inventory_filter_map import (
    INVENTORY_FILTER_MAP_SCOPE,
    get_cached_inventory_filter_map,
    inventory_filter_map_cache_payload,
)
from nhit.caching.inventory_summary import get_cached_inventory_summary
from nhit.caching.invalidation import invalidate_inventory_filter_cache
from nhit.caching.keys import build_cache_key, payload_digest
from nhit.caching.service import get_namespace_version


class InventoryFilterTtlConstantTests(SimpleTestCase):
    def test_ttl_constants(self):
        self.assertEqual(CACHE_TTL_FILTER, 300)
        self.assertEqual(CACHE_TTL_INVENTORY_FILTER, 604800)
        self.assertEqual(CACHE_LOCK_TIMEOUT_SECONDS, 30)


class InventoryFilterCountTtlAndKeyTests(SimpleTestCase):
    def test_count_default_timeout_is_seven_days(self):
        with patch(
            'nhit.caching.inventory_filter_count.get_or_set_cached_value',
            return_value=(1, {'cache_hit': False}),
        ) as mock_get:
            get_cached_inventory_filter_count(payload={'page': 1, 'page_size': 5, 'a': 1}, builder=lambda: 1)
        self.assertEqual(mock_get.call_args.kwargs['timeout'], 604800)
        self.assertEqual(mock_get.call_args.kwargs['namespace'], CACHE_NS_INVENTORY_FILTER)
        self.assertEqual(mock_get.call_args.kwargs['scope'], INVENTORY_FILTER_COUNT_SCOPE)
        self.assertNotIn('page', mock_get.call_args.kwargs['payload'])
        self.assertNotIn('page_size', mock_get.call_args.kwargs['payload'])

    def test_count_key_excludes_pagination_and_differs_by_filter(self):
        a = filter_payload_without_pagination(
            {'project_name': ['A'], 'page': 1, 'page_size': 5000, 'date': date(2026, 7, 28)}
        )
        b = filter_payload_without_pagination(
            {'project_name': ['A'], 'page': 9, 'page_size': 100, 'date': date(2026, 7, 28)}
        )
        c = filter_payload_without_pagination(
            {'project_name': ['B'], 'page': 1, 'page_size': 5000, 'date': date(2026, 7, 28)}
        )
        self.assertEqual(payload_digest(a), payload_digest(b))
        self.assertNotEqual(payload_digest(a), payload_digest(c))


class InventoryFilterMapTtlAndKeyTests(SimpleTestCase):
    def test_map_default_timeout_is_seven_days(self):
        with patch(
            'nhit.caching.inventory_filter_map.get_or_set_cached_value',
            return_value=({'mode': 'markers', 'markers': []}, {'cache_hit': False}),
        ) as mock_get:
            get_cached_inventory_filter_map(payload={'zoom': 10, 'viewport': {}}, builder=lambda: {})
        self.assertEqual(mock_get.call_args.kwargs['timeout'], 604800)
        self.assertEqual(mock_get.call_args.kwargs['namespace'], CACHE_NS_INVENTORY_FILTER_MAP)
        self.assertEqual(mock_get.call_args.kwargs['scope'], INVENTORY_FILTER_MAP_SCOPE)

    def test_viewport_and_zoom_change_map_cache_payload(self):
        base = {
            'project_name': ['P'],
            'asset_type': [],
            'direction': [],
            'date': date(2026, 7, 28),
            'chainage_start': 0,
            'chainage_end': 100,
            'viewport': {
                'min_lat': 15.0,
                'min_lng': 77.0,
                'max_lat': 16.0,
                'max_lng': 78.0,
            },
            'zoom': 10,
        }
        same = inventory_filter_map_cache_payload(base)
        other_vp = inventory_filter_map_cache_payload(
            {
                **base,
                'viewport': {
                    'min_lat': 15.1,
                    'min_lng': 77.0,
                    'max_lat': 16.0,
                    'max_lng': 78.0,
                },
            }
        )
        other_zoom = inventory_filter_map_cache_payload({**base, 'zoom': 11})
        self.assertNotEqual(payload_digest(same), payload_digest(other_vp))
        self.assertNotEqual(payload_digest(same), payload_digest(other_zoom))


class InventorySummaryStillUsesShortTtlTests(SimpleTestCase):
    def test_summary_default_timeout_remains_300(self):
        with patch(
            'nhit.caching.inventory_summary.get_or_set_cached_value',
            return_value=({}, {'cache_hit': False}),
        ) as mock_get:
            get_cached_inventory_summary(payload={'page': 1, 'page_size': 5}, builder=dict)
        self.assertEqual(mock_get.call_args.kwargs['timeout'], CACHE_TTL_FILTER)
        self.assertEqual(CACHE_TTL_FILTER, 300)


@override_settings(
    NHIT_CACHE_ENABLED=True,
    CACHES={
        'default': {
            'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
            'LOCATION': 'nhit-inventory-filter-ttl-tests',
            'KEY_PREFIX': 'nhit-inv-ttl',
        }
    },
)
class InventoryFilterSevenDayCacheIntegrationTests(TestCase):
    def setUp(self):
        from django.core.cache import cache

        cache.clear()
        self.client = APIClient()
        self.base_payload = {
            'chainage_start': 0,
            'chainage_end': 99999,
            'date': '2026-04-20',
            'direction': ['All'],
            'project_name': ['Agra Bypass Road'],
            'asset_type': ['All'],
            'page': 1,
            'page_size': 5000,
        }
        self.mock_groups = [[{'project_name': 'Agra Bypass Road', 'chainage_end': 1.0}]]

    @patch('inventory.views.get_or_set_cached_value')
    @patch('inventory.views.get_cached_inventory_filter_count')
    @patch('inventory.views.build_inventory_filter_queryset')
    def test_legacy_page_cache_passes_604800_timeout(
        self, mock_qs, mock_count, mock_page_cache
    ):
        mock_qs.return_value = MagicMock()
        mock_count.return_value = (10, {'cache_hit': False})
        mock_page_cache.return_value = (self.mock_groups, {'cache_hit': False, 'build_ms': 1})
        response = self.client.post('/inventory_filter/', self.base_payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(mock_page_cache.call_args.kwargs['timeout'], 604800)
        self.assertEqual(mock_page_cache.call_args.kwargs['namespace'], CACHE_NS_INVENTORY_FILTER)

    @patch('inventory.views.build_inventory_filter_groups')
    @patch('inventory.views.build_inventory_filter_queryset')
    def test_legacy_miss_then_hit(self, mock_build_qs, mock_build_groups):
        mock_qs = mock_build_qs.return_value
        mock_qs.count.return_value = 12000
        mock_build_groups.return_value = self.mock_groups

        first = self.client.post('/inventory_filter/', self.base_payload, format='json')
        second = self.client.post('/inventory_filter/', self.base_payload, format='json')
        self.assertEqual(first.status_code, status.HTTP_200_OK)
        self.assertEqual(second.status_code, status.HTTP_200_OK)
        self.assertEqual(first.data, second.data)
        self.assertEqual(mock_build_groups.call_count, 1)
        self.assertEqual(mock_qs.count.call_count, 1)

    @patch('inventory.views.build_inventory_filter_groups')
    @patch('inventory.views.build_inventory_filter_queryset')
    def test_different_page_page_size_filters_different_page_keys(
        self, mock_build_qs, mock_build_groups
    ):
        mock_qs = mock_build_qs.return_value
        mock_qs.count.return_value = 12000
        mock_build_groups.return_value = self.mock_groups

        self.client.post('/inventory_filter/', self.base_payload, format='json')
        self.client.post(
            '/inventory_filter/',
            {**self.base_payload, 'page': 2},
            format='json',
        )
        self.client.post(
            '/inventory_filter/',
            {**self.base_payload, 'page_size': 100},
            format='json',
        )
        self.client.post(
            '/inventory_filter/',
            {**self.base_payload, 'project_name': ['Other Project']},
            format='json',
        )
        # Same filters share count (1); page cache rebuilds for page/page_size/filter changes.
        self.assertEqual(mock_qs.count.call_count, 2)
        self.assertEqual(mock_build_groups.call_count, 4)

    @patch('inventory.views.build_inventory_filter_map_payload')
    def test_map_hit_and_key_isolation(self, mock_build):
        mock_build.return_value = {
            'mode': 'markers',
            'markers': [],
            'meta': {'version': 1, 'zoom': 10, 'marker_count': 0, 'cluster_count': 0},
        }
        map_payload = {
            **{k: v for k, v in self.base_payload.items() if k not in ('page', 'page_size')},
            'viewport': {
                'min_lat': 15.2,
                'min_lng': 77.8,
                'max_lat': 16.4,
                'max_lng': 78.1,
            },
            'zoom': 10,
        }
        a = self.client.post('/inventory_filter/', map_payload, format='json')
        b = self.client.post('/inventory_filter/', map_payload, format='json')
        self.assertEqual(a.data, b.data)
        self.assertEqual(mock_build.call_count, 1)

        other_vp = {
            **map_payload,
            'viewport': {
                'min_lat': 15.3,
                'min_lng': 77.8,
                'max_lat': 16.4,
                'max_lng': 78.1,
            },
        }
        other_zoom = {**map_payload, 'zoom': 12}
        self.client.post('/inventory_filter/', other_vp, format='json')
        self.client.post('/inventory_filter/', other_zoom, format='json')
        self.assertEqual(mock_build.call_count, 3)

        # Explicit digest isolation
        from inventory.serializers import InventoryFilterSerializer

        ser = InventoryFilterSerializer(data=map_payload)
        self.assertTrue(ser.is_valid(), ser.errors)
        base_key_payload = inventory_filter_map_cache_payload(ser.validated_data)
        ser_vp = InventoryFilterSerializer(data=other_vp)
        self.assertTrue(ser_vp.is_valid(), ser_vp.errors)
        ser_z = InventoryFilterSerializer(data=other_zoom)
        self.assertTrue(ser_z.is_valid(), ser_z.errors)
        self.assertNotEqual(
            payload_digest(base_key_payload),
            payload_digest(inventory_filter_map_cache_payload(ser_vp.validated_data)),
        )
        self.assertNotEqual(
            payload_digest(base_key_payload),
            payload_digest(inventory_filter_map_cache_payload(ser_z.validated_data)),
        )

    @patch('inventory.views.build_inventory_filter_map_payload')
    @patch('inventory.views.build_inventory_filter_groups')
    @patch('inventory.views.build_inventory_filter_queryset')
    def test_invalidate_bumps_legacy_and_map(
        self, mock_build_qs, mock_build_groups, mock_map_build
    ):
        mock_qs = mock_build_qs.return_value
        mock_qs.count.return_value = 100
        mock_build_groups.return_value = self.mock_groups
        mock_map_build.return_value = {
            'mode': 'markers',
            'markers': [],
            'meta': {'version': 1, 'zoom': 10, 'marker_count': 0, 'cluster_count': 0},
        }

        legacy_v0 = get_namespace_version(CACHE_NS_INVENTORY_FILTER)
        map_v0 = get_namespace_version(CACHE_NS_INVENTORY_FILTER_MAP)

        self.client.post('/inventory_filter/', self.base_payload, format='json')
        map_payload = {
            'chainage_start': 0,
            'chainage_end': 99999,
            'date': '2026-04-20',
            'direction': ['All'],
            'project_name': ['Agra Bypass Road'],
            'asset_type': ['All'],
            'viewport': {
                'min_lat': 15.2,
                'min_lng': 77.8,
                'max_lat': 16.4,
                'max_lng': 78.1,
            },
            'zoom': 10,
        }
        self.client.post('/inventory_filter/', map_payload, format='json')
        self.assertEqual(mock_build_groups.call_count, 1)
        self.assertEqual(mock_map_build.call_count, 1)

        invalidate_inventory_filter_cache()
        self.assertGreater(get_namespace_version(CACHE_NS_INVENTORY_FILTER), legacy_v0)
        self.assertGreater(get_namespace_version(CACHE_NS_INVENTORY_FILTER_MAP), map_v0)

        self.client.post('/inventory_filter/', self.base_payload, format='json')
        self.client.post('/inventory_filter/', map_payload, format='json')
        self.assertEqual(mock_build_groups.call_count, 2)
        self.assertEqual(mock_qs.count.call_count, 2)
        self.assertEqual(mock_map_build.call_count, 2)

    @patch('inventory.views.build_inventory_filter_groups')
    @patch('inventory.views.build_inventory_filter_queryset')
    @override_settings(NHIT_CACHE_ENABLED=False)
    def test_cache_disabled_still_works(self, mock_build_qs, mock_build_groups):
        mock_qs = mock_build_qs.return_value
        mock_qs.count.return_value = 7
        mock_build_groups.return_value = self.mock_groups
        response = self.client.post('/inventory_filter/', self.base_payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response['X-Inventory-Row-Count'], '7')
