"""Isolated tests for temporary TRAFFIC_FILTER_PERF_DEBUG instrumentation."""

from __future__ import annotations

import gzip
import logging
from datetime import date
from unittest.mock import patch

from django.db import connection
from django.test import TestCase, override_settings
from rest_framework import status
from rest_framework.test import APIClient

from inventory.models import Project, RoadSegment
from traffic.models import Traffic
from traffic.services.traffic_filter_perf import format_traffic_filter_perf_log

ENDPOINT = '/traffic_filter/'

PAYLOAD = {
    'chainage_start': 0,
    'chainage_end': 1381,
    'date': '2026-05-20',
    'direction': ['All'],
    'project_name': ['Abu Road to Swaroopganj'],
}


def _create_unmanaged_tables():
    with connection.schema_editor() as schema_editor:
        for model in (Project, RoadSegment, Traffic):
            try:
                schema_editor.create_model(model)
            except Exception:
                pass


@override_settings(
    NHIT_CACHE_ENABLED=True,
    TRAFFIC_FILTER_PERF_DEBUG=False,
    CACHES={
        'default': {
            'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
            'LOCATION': 'traffic-filter-perf-tests',
            'KEY_PREFIX': 'traffic-perf-test',
        }
    },
)
class TrafficFilterPerfInstrumentationTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        _create_unmanaged_tables()

    def setUp(self):
        self.client = APIClient()
        project = Project.objects.create(
            project_name='Abu Road to Swaroopganj',
            state='Rajasthan',
            district='Swaroopganj',
            block_taluka='Swaroopganj',
            road_code='47',
            originating='Abu Road',
            terminating='Swaroopganj',
            length_km=100.0,
        )
        increasing = RoadSegment.objects.create(
            project=project,
            chainage_start=646.0,
            chainage_end=646.1,
            direction='Increasing',
            lane='L1',
            carriage_type='Flexible',
        )
        decreasing = RoadSegment.objects.create(
            project=project,
            chainage_start=646.0,
            chainage_end=646.1,
            direction='Decreasing',
            lane='L1',
            carriage_type='Flexible',
        )
        common = {
            'project': project,
            'survey_date': date(2026, 5, 20),
            'year_val': 2026.0,
            'length_val': 100.0,
            'three_wheeler_auto': 1,
            'car_jeep_van_taxi': 2,
            'standard_bus': 3,
            'lcv': 4,
            'two_axle_trucks': 5,
            'three_axle_trucks': 6,
            'mav': 7,
            'osv': 8,
            'tractor': 9,
            'others': 10,
            'aadt_vehicles': 100,
            'aadt_pcu': 38151.69921875,
            'cvd_vehicles': 27378.43359375,
            'latitude': 24.466760635375977,
            'longitude': 72.78508758544922,
            'toll_plaza': 'Undavariya',
            'source_file': 'Abu Road to Swaroopganj.xlsx',
        }
        Traffic.objects.create(segment=increasing, **common)
        Traffic.objects.create(segment=decreasing, **common)

    def _post(self, *, accept_gzip: bool = False):
        headers = {}
        if accept_gzip:
            headers['HTTP_ACCEPT_ENCODING'] = 'gzip'
        return self.client.post(ENDPOINT, PAYLOAD, format='json', **headers)

    def test_debug_disabled_no_perf_log(self):
        with self.assertLogs('nhit.traffic_filter.perf', level='INFO') as cm:
            # Force the logger to be watched; expect no TRAFFIC_FILTER_PERF lines.
            logging.getLogger('nhit.traffic_filter.perf').info('probe')
            response = self._post()
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        perf_lines = [line for line in cm.output if 'TRAFFIC_FILTER_PERF' in line]
        self.assertEqual(perf_lines, [])

    @override_settings(TRAFFIC_FILTER_PERF_DEBUG=True)
    def test_debug_enabled_emits_perf_log(self):
        with self.assertLogs('nhit.traffic_filter.perf', level='INFO') as cm:
            response = self._post(accept_gzip=True)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        joined = '\n'.join(cm.output)
        self.assertIn('TRAFFIC_FILTER_PERF', joined)
        self.assertIn('cache=', joined)
        self.assertIn('server_total_ms=', joined)
        self.assertIn('json_ms=', joined)
        self.assertIn('gzip_ms=', joined)
        # Security: never log credentials / redis / full bodies.
        lowered = joined.lower()
        for forbidden in ('password', 'redis://', 'authorization', 'secret_key'):
            self.assertNotIn(forbidden, lowered)

    def test_response_json_unchanged_with_debug_on_or_off(self):
        off = self._post()
        self.assertEqual(off.status_code, status.HTTP_200_OK)
        off_bytes = bytes(off.content)

        with override_settings(TRAFFIC_FILTER_PERF_DEBUG=True):
            with self.assertLogs('nhit.traffic_filter.perf', level='INFO'):
                on = self._post()
        self.assertEqual(on.status_code, status.HTTP_200_OK)
        self.assertEqual(bytes(on.content), off_bytes)

    def test_status_and_cache_behavior_unchanged(self):
        first = self._post()
        second = self._post()
        self.assertEqual(first.status_code, status.HTTP_200_OK)
        self.assertEqual(second.status_code, status.HTTP_200_OK)
        self.assertEqual(bytes(first.content), bytes(second.content))

        with override_settings(TRAFFIC_FILTER_PERF_DEBUG=True):
            with self.assertLogs('nhit.traffic_filter.perf', level='INFO') as cm:
                miss_or_hit = self._post()
                hit = self._post()
        self.assertEqual(miss_or_hit.status_code, status.HTTP_200_OK)
        self.assertEqual(hit.status_code, status.HTTP_200_OK)
        joined = '\n'.join(cm.output)
        self.assertIn('cache=HIT', joined)

    def test_gzip_behavior_unchanged(self):
        plain = self._post(accept_gzip=False)
        gzipped = self._post(accept_gzip=True)
        self.assertEqual(plain.status_code, status.HTTP_200_OK)
        self.assertEqual(gzipped.status_code, status.HTTP_200_OK)

        plain_body = bytes(plain.content)
        raw = bytes(gzipped.content)
        encoding = gzipped.get('Content-Encoding')
        if encoding == 'gzip':
            decompressed = gzip.decompress(raw)
        else:
            # LocMem / small payloads may skip compression under GZipMiddleware rules.
            decompressed = raw
        self.assertEqual(decompressed, plain_body)

        with override_settings(TRAFFIC_FILTER_PERF_DEBUG=True):
            with self.assertLogs('nhit.traffic_filter.perf', level='INFO'):
                plain_dbg = self._post(accept_gzip=False)
                gzip_dbg = self._post(accept_gzip=True)
        self.assertEqual(bytes(plain_dbg.content), plain_body)
        raw_dbg = bytes(gzip_dbg.content)
        enc_dbg = gzip_dbg.get('Content-Encoding')
        if enc_dbg == 'gzip':
            self.assertEqual(gzip.decompress(raw_dbg), plain_body)
        else:
            self.assertEqual(raw_dbg, plain_body)

    def test_format_log_omits_secret_like_keys(self):
        line = format_traffic_filter_perf_log({
            'request_id': 'abc',
            'cache': 'HIT',
            'redis_url': 'redis://should-not-appear',
            'password': 'nope',
            'server_total_ms': 12.3,
        })
        self.assertIn('TRAFFIC_FILTER_PERF', line)
        self.assertIn('request_id=abc', line)
        self.assertNotIn('redis://', line)
        self.assertNotIn('password', line)
        self.assertNotIn('nope', line)

    @override_settings(TRAFFIC_FILTER_PERF_DEBUG=True)
    def test_debug_enabled_still_uses_tfgz1_helper(self):
        """Perf debug must not bypass the TFGZ1 cache helper."""
        with patch(
            'traffic.views.get_cached_traffic_filter_response',
            wraps=__import__(
                'nhit.caching.traffic_filter_cache',
                fromlist=['get_cached_traffic_filter_response'],
            ).get_cached_traffic_filter_response,
        ) as mocked:
            with self.assertLogs('nhit.traffic_filter.perf', level='INFO'):
                response = self._post(accept_gzip=True)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        mocked.assert_called()
        self.assertEqual(response.get('Content-Encoding'), 'gzip')

    def test_debug_off_uses_tfgz1_helper(self):
        from nhit.caching.traffic_filter_cache import (
            get_cached_traffic_filter_response as real_helper,
        )

        with patch('traffic.views.get_cached_traffic_filter_response', wraps=real_helper) as mocked:
            response = self._post()
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        mocked.assert_called_once()
