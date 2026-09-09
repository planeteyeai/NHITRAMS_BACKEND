"""Tests for map branch of POST /inventory_filter/ (viewport + zoom)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase, TestCase, override_settings
from rest_framework import status
from rest_framework.test import APIClient

from inventory.services.inventory_filter_map import (
    MAP_CLUSTER_HARD_MAX,
    MAP_MARKER_HARD_MAX,
    MAP_MARKER_SOFT_MAX,
    apply_map_viewport_filter,
    build_inventory_filter_map_payload,
    get_grid_size_for_zoom,
    resolve_grid_size,
)
from inventory.serializers import (
    InventoryFilterSerializer,
    is_inventory_filter_map_request,
)
from nhit.caching.constants import CACHE_NS_INVENTORY_FILTER, CACHE_NS_INVENTORY_FILTER_MAP
from nhit.caching.inventory_filter_map import inventory_filter_map_cache_payload
from nhit.caching.keys import build_cache_key
from nhit.caching.service import get_namespace_version


def _base_legacy_payload(**overrides):
    payload = {
        'chainage_start': 0,
        'chainage_end': 99999,
        'date': '2026-07-28',
        'direction': ['All'],
        'project_name': ['Kothakota to Kurnool'],
        'asset_type': ['All'],
        'page': 1,
        'page_size': 5000,
    }
    payload.update(overrides)
    return payload


def _base_map_payload(**overrides):
    payload = {
        'chainage_start': 0,
        'chainage_end': 99999,
        'date': '2026-07-28',
        'direction': ['All'],
        'project_name': ['Kothakota to Kurnool'],
        'asset_type': ['All'],
        'viewport': {
            'min_lat': 15.20,
            'min_lng': 77.80,
            'max_lat': 16.40,
            'max_lng': 78.10,
        },
        'zoom': 10,
    }
    payload.update(overrides)
    return payload


class InventoryFilterMapDetectionSerializerTests(SimpleTestCase):
    def test_legacy_without_viewport_zoom(self):
        ser = InventoryFilterSerializer(data=_base_legacy_payload())
        self.assertTrue(ser.is_valid(), ser.errors)
        self.assertFalse(is_inventory_filter_map_request(ser.validated_data))

    def test_valid_viewport_and_zoom_is_map(self):
        ser = InventoryFilterSerializer(data=_base_map_payload())
        self.assertTrue(ser.is_valid(), ser.errors)
        self.assertTrue(is_inventory_filter_map_request(ser.validated_data))
        self.assertEqual(ser.validated_data['direction'], [])
        self.assertEqual(ser.validated_data['asset_type'], [])

    def test_viewport_without_zoom_invalid(self):
        payload = _base_map_payload()
        del payload['zoom']
        ser = InventoryFilterSerializer(data=payload)
        self.assertFalse(ser.is_valid())
        self.assertIn('zoom', ser.errors)

    def test_zoom_without_viewport_invalid(self):
        payload = _base_legacy_payload(zoom=15)
        ser = InventoryFilterSerializer(data=payload)
        self.assertFalse(ser.is_valid())
        self.assertIn('viewport', ser.errors)

    def test_invalid_latitude(self):
        ser = InventoryFilterSerializer(
            data=_base_map_payload(
                viewport={
                    'min_lat': -91,
                    'min_lng': 77.8,
                    'max_lat': 16.4,
                    'max_lng': 78.1,
                }
            )
        )
        self.assertFalse(ser.is_valid())

    def test_invalid_longitude(self):
        ser = InventoryFilterSerializer(
            data=_base_map_payload(
                viewport={
                    'min_lat': 15.2,
                    'min_lng': -181,
                    'max_lat': 16.4,
                    'max_lng': 78.1,
                }
            )
        )
        self.assertFalse(ser.is_valid())

    def test_min_lat_not_less_than_max_lat(self):
        ser = InventoryFilterSerializer(
            data=_base_map_payload(
                viewport={
                    'min_lat': 16.4,
                    'min_lng': 77.8,
                    'max_lat': 15.2,
                    'max_lng': 78.1,
                }
            )
        )
        self.assertFalse(ser.is_valid())

    def test_invalid_zoom(self):
        self.assertFalse(InventoryFilterSerializer(data=_base_map_payload(zoom=23)).is_valid())
        self.assertFalse(InventoryFilterSerializer(data=_base_map_payload(zoom=-1)).is_valid())

    def test_all_behavior_unchanged(self):
        ser = InventoryFilterSerializer(
            data=_base_map_payload(direction=['ALL'], asset_type=['all'])
        )
        self.assertTrue(ser.is_valid(), ser.errors)
        self.assertEqual(ser.validated_data['direction'], [])
        self.assertEqual(ser.validated_data['asset_type'], [])

    def test_no_map_mode_field(self):
        ser = InventoryFilterSerializer(data=_base_map_payload(**{'map_mode': True}))
        # Extra fields are ignored by DRF; map still detected via viewport+zoom.
        self.assertTrue(ser.is_valid(), ser.errors)
        self.assertNotIn('map_mode', ser.validated_data)
        self.assertTrue(is_inventory_filter_map_request(ser.validated_data))


class InventoryFilterMapGridTests(SimpleTestCase):
    def test_zoom_increases_shrink_grid(self):
        self.assertGreater(get_grid_size_for_zoom(4), get_grid_size_for_zoom(10))
        self.assertGreater(get_grid_size_for_zoom(10), get_grid_size_for_zoom(16))

    def test_resolve_grid_size_inflates_for_huge_viewport(self):
        viewport = {
            'min_lat': -90.0,
            'max_lat': 90.0,
            'min_lng': -180.0,
            'max_lng': 180.0,
        }
        grid = resolve_grid_size(18, viewport)
        import math

        est = math.ceil(180.0 / grid) * math.ceil(360.0 / grid)
        self.assertLessEqual(est, MAP_CLUSTER_HARD_MAX)


class InventoryFilterMapViewportHelperTests(SimpleTestCase):
    def test_apply_map_viewport_filter_adds_lat_lng_bounds(self):
        qs = MagicMock()
        qs.filter.return_value = qs
        qs.exclude.return_value = qs
        viewport = {
            'min_lat': 15.0,
            'max_lat': 16.0,
            'min_lng': 77.0,
            'max_lng': 78.0,
        }
        result = apply_map_viewport_filter(qs, viewport)
        kwargs = qs.filter.call_args.kwargs
        self.assertEqual(kwargs['latitude__gte'], 15.0)
        self.assertEqual(kwargs['longitude__lte'], 78.0)
        self.assertIs(result, qs)


@override_settings(NHIT_CACHE_ENABLED=False)
class InventoryFilterIntegratedMapViewTests(TestCase):
    def setUp(self):
        self.client = APIClient()

    def test_map_url_removed(self):
        response = self.client.post('/inventory_filter/map/', _base_map_payload(), format='json')
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_viewport_without_zoom_400(self):
        payload = _base_map_payload()
        del payload['zoom']
        response = self.client.post('/inventory_filter/', payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('zoom', response.data)

    def test_zoom_without_viewport_400(self):
        response = self.client.post(
            '/inventory_filter/',
            _base_legacy_payload(zoom=15),
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('viewport', response.data)

    @patch('inventory.views.build_inventory_filter_map_payload')
    def test_empty_markers_response(self, mock_build):
        mock_build.return_value = {
            'mode': 'markers',
            'markers': [],
            'meta': {'version': 1, 'zoom': 10, 'marker_count': 0, 'cluster_count': 0},
        }
        response = self.client.post('/inventory_filter/', _base_map_payload(), format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['mode'], 'markers')
        self.assertEqual(response.data['markers'], [])
        self.assertNotEqual(response.data, {'message': 'No match'})
        self.assertNotIn('X-Inventory-Paginated', response)
        self.assertNotIn('X-Inventory-Page', response)
        self.assertNotIn('X-Inventory-Page-Size', response)
        self.assertNotIn('X-Inventory-Has-More', response)

    @patch('inventory.views.build_inventory_filter_map_payload')
    def test_markers_payload_shape(self, mock_build):
        mock_build.return_value = {
            'mode': 'markers',
            'markers': [
                {
                    'id': 12345,
                    'latitude': 15.3123,
                    'longitude': 78.2145,
                    'asset_type': 'Crash Barrier',
                    'direction': 'Increasing',
                }
            ],
            'meta': {'version': 1, 'zoom': 12, 'marker_count': 1, 'cluster_count': 0},
        }
        response = self.client.post(
            '/inventory_filter/',
            _base_map_payload(zoom=12),
            format='json',
        )
        marker = response.data['markers'][0]
        self.assertEqual(
            set(marker.keys()),
            {'id', 'latitude', 'longitude', 'asset_type', 'direction'},
        )
        self.assertIsInstance(marker['latitude'], float)
        self.assertEqual(marker['id'], 12345)

    @patch('inventory.views.build_inventory_filter_map_payload')
    def test_clusters_payload_and_ignores_page_size(self, mock_build):
        mock_build.return_value = {
            'mode': 'clusters',
            'clusters': [
                {'latitude': 15.312, 'longitude': 78.214, 'count': 427, 'cell_id': '1:2'},
            ],
            'meta': {
                'version': 1,
                'zoom': 8,
                'marker_count': 0,
                'cluster_count': 1,
                'grid_size_deg': 0.2,
            },
        }
        response = self.client.post(
            '/inventory_filter/',
            _base_map_payload(zoom=8, page=3, page_size=50),
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['mode'], 'clusters')
        self.assertNotIn('X-Inventory-Page', response)
        # Builder receives validated data including page fields, but map path ignores them.
        called_data = mock_build.call_args.args[0]
        self.assertIn('viewport', called_data)
        self.assertEqual(called_data['zoom'], 8)

    @patch('inventory.views.build_inventory_filter_queryset')
    @patch('inventory.views.get_cached_inventory_filter_count')
    @patch('inventory.views.get_or_set_cached_value')
    def test_legacy_path_still_returns_headers(
        self, mock_page_cache, mock_count, mock_qs
    ):
        mock_qs.return_value = MagicMock()
        mock_count.return_value = (100, {'cache_hit': False})
        mock_page_cache.return_value = (
            [[{'project_name': 'X', 'asset_type': 'Trees', 'latitude': '1', 'longitude': '2'}]],
            {'cache_hit': False, 'build_ms': 1},
        )
        response = self.client.post(
            '/inventory_filter/',
            _base_legacy_payload(page=1, page_size=15),
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response['X-Inventory-Paginated'], 'true')
        self.assertEqual(response['X-Inventory-Page'], '1')
        self.assertEqual(response['X-Inventory-Page-Size'], '15')
        self.assertEqual(response['X-Inventory-Row-Count'], '100')
        self.assertEqual(response['X-Inventory-Has-More'], 'true')
        self.assertIsInstance(response.data, list)

    @patch('inventory.views.build_inventory_filter_queryset')
    @patch('inventory.views.get_cached_inventory_filter_count')
    @patch('inventory.views.build_inventory_filter_map_payload')
    def test_legacy_does_not_call_map_builder(self, mock_map, mock_count, mock_qs):
        mock_qs.return_value = MagicMock()
        mock_count.return_value = (0, {'cache_hit': False})
        response = self.client.post('/inventory_filter/', _base_legacy_payload(), format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data, {'message': 'No match'})
        mock_map.assert_not_called()


class InventoryFilterMapPayloadLogicTests(SimpleTestCase):
    def _fake_qs(self, rows):
        class _ValuesQS:
            def __init__(self, data):
                self._data = data

            def __getitem__(self, item):
                if isinstance(item, slice):
                    return list(self._data[item])
                return self._data[item]

        class _QS:
            def __init__(self, data):
                self._data = list(data)

            def filter(self, *args, **kwargs):
                return self

            def exclude(self, *args, **kwargs):
                self._data = [
                    r
                    for r in self._data
                    if not (r.get('latitude') == 0.0 and r.get('longitude') == 0.0)
                ]
                return self

            def order_by(self, *args, **kwargs):
                return self

            def values(self, *fields):
                return _ValuesQS([{k: r[k] for k in fields} for r in self._data])

        return _QS(rows)

    @patch('inventory.services.inventory_filter_map.build_inventory_filter_queryset')
    @patch('inventory.services.inventory_filter_map._run_grid_cluster_query')
    def test_soft_max_or_fewer_returns_markers(self, mock_cluster, mock_build_qs):
        rows = [
            {
                'inventory_id': i,
                'latitude': 15.3 + i * 0.0001,
                'longitude': 78.2,
                'asset_type': 'Trees',
                'direction': 'Increasing',
            }
            for i in range(50)
        ]
        mock_build_qs.return_value = self._fake_qs(rows)
        ser = InventoryFilterSerializer(data=_base_map_payload())
        self.assertTrue(ser.is_valid(), ser.errors)
        payload = build_inventory_filter_map_payload(ser.validated_data)
        self.assertEqual(payload['mode'], 'markers')
        self.assertEqual(len(payload['markers']), 50)
        self.assertEqual(payload['markers'][0]['id'], 0)
        mock_cluster.assert_not_called()

    @patch('inventory.services.inventory_filter_map.build_inventory_filter_queryset')
    @patch('inventory.services.inventory_filter_map._fetch_marker_probe')
    @patch('inventory.services.inventory_filter_map._run_grid_cluster_query')
    def test_above_soft_max_returns_clusters(
        self, mock_cluster, mock_probe, mock_build_qs
    ):
        mock_build_qs.return_value = MagicMock()
        mock_probe.return_value = [
            {
                'id': i,
                'latitude': 15.0,
                'longitude': 78.0,
                'asset_type': 'Kerb',
                'direction': 'Increasing',
            }
            for i in range(MAP_MARKER_SOFT_MAX + 1)
        ]
        mock_cluster.return_value = [
            {'latitude': 15.1, 'longitude': 78.1, 'count': 1500, 'cell_id': '0:0'},
            {'latitude': 15.2, 'longitude': 78.2, 'count': 500, 'cell_id': '1:1'},
        ]
        ser = InventoryFilterSerializer(data=_base_map_payload())
        self.assertTrue(ser.is_valid(), ser.errors)
        payload = build_inventory_filter_map_payload(ser.validated_data)
        self.assertEqual(payload['mode'], 'clusters')
        self.assertEqual(sum(c['count'] for c in payload['clusters']), 2000)

    @patch('inventory.services.inventory_filter_map.build_inventory_filter_queryset')
    @patch('inventory.services.inventory_filter_map._fetch_marker_probe')
    @patch('inventory.services.inventory_filter_map._run_grid_cluster_query')
    def test_full_project_never_returns_raw_markers(
        self, mock_cluster, mock_probe, mock_build_qs
    ):
        mock_build_qs.return_value = MagicMock()
        mock_probe.return_value = [
            {'id': i, 'latitude': 15.0, 'longitude': 78.0, 'asset_type': 'Kerb', 'direction': 'X'}
            for i in range(MAP_MARKER_HARD_MAX + 1)
        ]
        mock_cluster.return_value = [
            {'latitude': 15.5, 'longitude': 78.0, 'count': 230523, 'cell_id': '5:5'},
        ]
        ser = InventoryFilterSerializer(data=_base_map_payload())
        self.assertTrue(ser.is_valid(), ser.errors)
        payload = build_inventory_filter_map_payload(ser.validated_data)
        self.assertEqual(payload['mode'], 'clusters')
        self.assertEqual(payload['clusters'][0]['count'], 230523)

    def test_marker_thresholds_configured(self):
        self.assertEqual(MAP_MARKER_SOFT_MAX, 1000)
        self.assertEqual(MAP_MARKER_HARD_MAX, 2000)


@override_settings(
    NHIT_CACHE_ENABLED=True,
    CACHES={
        'default': {
            'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
            'LOCATION': 'nhit-inventory-map-integrated-tests',
            'KEY_PREFIX': 'nhit-map-int-test',
        }
    },
)
class InventoryFilterMapCacheTests(TestCase):
    def setUp(self):
        from django.core.cache import cache

        cache.clear()
        self.client = APIClient()

    @patch('inventory.views.build_inventory_filter_map_payload')
    def test_second_map_request_is_cache_hit(self, mock_build):
        mock_build.return_value = {
            'mode': 'markers',
            'markers': [],
            'meta': {'version': 1, 'zoom': 10, 'marker_count': 0, 'cluster_count': 0},
        }
        first = self.client.post('/inventory_filter/', _base_map_payload(), format='json')
        second = self.client.post('/inventory_filter/', _base_map_payload(), format='json')
        self.assertEqual(first.data, second.data)
        self.assertEqual(mock_build.call_count, 1)

    def test_map_and_legacy_cache_namespaces_do_not_collide(self):
        self.assertNotEqual(CACHE_NS_INVENTORY_FILTER, CACHE_NS_INVENTORY_FILTER_MAP)
        legacy_payload = {
            'project_name': ['Kothakota to Kurnool'],
            'date': '2026-07-28',
            'page': 1,
            'page_size': 5000,
            'chainage_start': 0,
            'chainage_end': 99999,
            'direction': [],
            'asset_type': [],
        }
        map_body = _base_map_payload(zoom=10)
        ser = InventoryFilterSerializer(data=map_body)
        self.assertTrue(ser.is_valid(), ser.errors)
        map_cache_payload = inventory_filter_map_cache_payload(ser.validated_data)

        legacy_key = build_cache_key(
            namespace=CACHE_NS_INVENTORY_FILTER,
            version=get_namespace_version(CACHE_NS_INVENTORY_FILTER),
            payload=legacy_payload,
            scope='',
        )
        map_key = build_cache_key(
            namespace=CACHE_NS_INVENTORY_FILTER_MAP,
            version=get_namespace_version(CACHE_NS_INVENTORY_FILTER_MAP),
            payload=map_cache_payload,
            scope='map_v1',
        )
        self.assertTrue(legacy_key.startswith('inventory_filter:'))
        self.assertTrue(map_key.startswith('inventory_filter_map:'))
        self.assertNotEqual(legacy_key, map_key)
