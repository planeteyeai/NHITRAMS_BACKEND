"""Direct-byte HIT serving for traffic_filter (TFGZ1 Phase-3 style)."""

from __future__ import annotations

import gzip
import json
import pickle
from datetime import date
from unittest.mock import patch

from django.core.cache import cache
from django.db import connection
from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings
from rest_framework import status
from rest_framework.renderers import JSONRenderer
from rest_framework.test import APIClient

from nhit.caching.accept_encoding import accepts_gzip_encoding
from nhit.caching.constants import CACHE_NS_TRAFFIC_FILTER, CACHE_TTL_FILTER
from nhit.caching.keys import build_cache_key
from nhit.caching.invalidation import invalidate_traffic_filter_cache
from nhit.caching.service import get_namespace_version, safe_cache_set
from nhit.caching.traffic_filter_cache import (
    TRAFFIC_FILTER_CACHE_MAGIC,
    TRAFFIC_FILTER_CACHE_SCOPE,
    TRAFFIC_FILTER_ENTRY_KIND,
    build_traffic_filter_data_key,
    decode_traffic_filter_cache,
    encode_traffic_filter_cache,
    extract_traffic_filter_gzip_payload,
    get_cached_traffic_filter_response,
    unwrap_traffic_filter_entry,
    wrap_traffic_filter_entry,
)
from nhit.middleware import SelectiveGZipMiddleware
from inventory.models import Project, RoadSegment
from traffic.models import Traffic
from traffic.serializers import TrafficFilterRequestSerializer

ENDPOINT = '/traffic_filter/'

PAYLOAD = {
    'chainage_start': 0,
    'chainage_end': 1381,
    'date': '2026-05-20',
    'direction': ['All'],
    'project_name': ['Abu Road to Swaroopganj'],
}

SAMPLE_GROUPS = [
    [
        {
            'date': '2026-05-20',
            'road_code': '47',
            'project_name': 'Abu Road to Swaroopganj',
            'block': 'Swaroopganj',
            'district': 'Swaroopganj',
            'year': 2026.0,
            'direction': 'Increasing',
            'length': 100.0,
            'chainage_start': 646.0,
            'chainage_end': 646.1,
            '3-wheeler/_auto': 1,
            'car/_jeep/_van/_taxi': 2,
            'standard_bus': 3,
            'lcv': 4,
            '2-axle_trucks': 5,
            '3-axle_trucks': 6,
            'mav': 7,
            'osv': 8,
            'tractor': 9,
            'others': 10,
            'aadt_in_vehicles': 100,
            'aadt_in_pcu': 38151.69921875,
            'cvd_in_vehicles': 27378.43359375,
            'longitude': 72.78508758544922,
            'latitude': 24.466760635375977,
            'toll_plaza': 'Undavariya',
        },
        {
            'date': '2026-05-20',
            'road_code': '47',
            'project_name': 'Abu Road to Swaroopganj',
            'block': 'Swaroopganj',
            'district': 'Swaroopganj',
            'year': 2026.0,
            'direction': 'Decreasing',
            'length': 100.0,
            'chainage_start': 646.0,
            'chainage_end': 646.1,
            '3-wheeler/_auto': 1,
            'car/_jeep/_van/_taxi': 2,
            'standard_bus': 3,
            'lcv': 4,
            '2-axle_trucks': 5,
            '3-axle_trucks': 6,
            'mav': 7,
            'osv': 8,
            'tractor': 9,
            'others': 10,
            'aadt_in_vehicles': 100,
            'aadt_in_pcu': 38151.69921875,
            'cvd_in_vehicles': 27378.43359375,
            'longitude': 72.78508758544922,
            'latitude': 24.466760635375977,
            'toll_plaza': 'Undavariya',
        },
    ],
]

_LOCMEM = {
    'default': {
        'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
        'LOCATION': 'traffic-tfgz1-tests',
        'KEY_PREFIX': 'traffic-tfgz1',
    }
}


def _create_unmanaged_tables():
    with connection.schema_editor() as schema_editor:
        for model in (Project, RoadSegment, Traffic):
            try:
                schema_editor.create_model(model)
            except Exception:
                pass


class AcceptEncodingReuseTests(SimpleTestCase):
    def test_gzip_q_variants(self):
        self.assertTrue(accepts_gzip_encoding('gzip'))
        self.assertTrue(accepts_gzip_encoding('gzip;q=0.5'))
        self.assertFalse(accepts_gzip_encoding('gzip;q=0'))
        self.assertFalse(accepts_gzip_encoding(None))
        self.assertFalse(accepts_gzip_encoding(''))


@override_settings(NHIT_CACHE_ENABLED=True, CACHES=_LOCMEM, TRAFFIC_FILTER_PERF_DEBUG=False)
class TrafficFilterCacheUnitTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_encode_decode_roundtrip_and_magic(self):
        encoded = encode_traffic_filter_cache(SAMPLE_GROUPS)
        self.assertTrue(encoded.startswith(TRAFFIC_FILTER_CACHE_MAGIC))
        gzip_payload = extract_traffic_filter_gzip_payload(encoded)
        self.assertFalse(gzip_payload.startswith(TRAFFIC_FILTER_CACHE_MAGIC))
        self.assertEqual(decode_traffic_filter_cache(encoded), SAMPLE_GROUPS)
        self.assertEqual(gzip.decompress(gzip_payload), JSONRenderer().render(SAMPLE_GROUPS))

    def test_corrupt_tfgz1_treated_as_miss_and_rebuild(self):
        calls = {'n': 0}

        def builder():
            calls['n'] += 1
            return SAMPLE_GROUPS

        key, _ = build_traffic_filter_data_key(PAYLOAD)
        safe_cache_set(
            key,
            wrap_traffic_filter_entry(ns_v=1, blob=b'TFGZ1\ncorrupt'),
            timeout=CACHE_TTL_FILTER,
        )

        value, meta = get_cached_traffic_filter_response(
            payload=PAYLOAD,
            builder=builder,
            serve_mode='python',
        )
        self.assertFalse(meta['cache_hit'])
        self.assertEqual(value, SAMPLE_GROUPS)
        self.assertEqual(calls['n'], 1)

        value2, meta2 = get_cached_traffic_filter_response(
            payload=PAYLOAD,
            builder=builder,
            serve_mode='python',
        )
        self.assertTrue(meta2['cache_hit'])
        self.assertEqual(value2, SAMPLE_GROUPS)
        self.assertEqual(calls['n'], 1)
        self.assertEqual(meta2.get('redis_round_trips'), 1)

    def test_old_pickle_format_under_data_key_rebuilds(self):
        calls = {'n': 0}

        def builder():
            calls['n'] += 1
            return SAMPLE_GROUPS

        key, _ = build_traffic_filter_data_key(PAYLOAD)
        # Simulate a mistaken/legacy Python object in Redis.
        safe_cache_set(key, SAMPLE_GROUPS, timeout=CACHE_TTL_FILTER)

        value, meta = get_cached_traffic_filter_response(
            payload=PAYLOAD,
            builder=builder,
            serve_mode='json_bytes',
        )
        self.assertFalse(meta['cache_hit'])
        self.assertEqual(calls['n'], 1)
        self.assertEqual(json.loads(value), SAMPLE_GROUPS)

        raw = cache.get(key)
        self.assertIsInstance(raw, dict)
        self.assertEqual(raw.get('kind'), TRAFFIC_FILTER_ENTRY_KIND)
        self.assertTrue(bytes(raw['blob']).startswith(TRAFFIC_FILTER_CACHE_MAGIC))

    def test_old_versioned_key_not_used_by_data_key(self):
        version = get_namespace_version(CACHE_NS_TRAFFIC_FILTER)
        old_key = build_cache_key(
            namespace=CACHE_NS_TRAFFIC_FILTER,
            version=version,
            payload=PAYLOAD,
            scope=TRAFFIC_FILTER_CACHE_SCOPE,
        )
        new_key, _ = build_traffic_filter_data_key(PAYLOAD)
        self.assertNotEqual(old_key, new_key)
        safe_cache_set(old_key, encode_traffic_filter_cache(SAMPLE_GROUPS), timeout=CACHE_TTL_FILTER)

        calls = {'n': 0}

        def builder():
            calls['n'] += 1
            return SAMPLE_GROUPS

        _, meta = get_cached_traffic_filter_response(
            payload=PAYLOAD,
            builder=builder,
            serve_mode='python',
        )
        self.assertFalse(meta['cache_hit'])
        self.assertEqual(calls['n'], 1)
        self.assertIsNotNone(cache.get(new_key))
        # Legacy versioned key remains untouched and unused.
        self.assertIsInstance(cache.get(old_key), (bytes, bytearray))

    def test_invalidation_bumps_namespace_and_forces_rebuild(self):
        calls = {'n': 0}

        def builder():
            calls['n'] += 1
            return SAMPLE_GROUPS if calls['n'] == 1 else [[{'rebuilt': True}]]

        v1, m1 = get_cached_traffic_filter_response(
            payload=PAYLOAD,
            builder=builder,
            serve_mode='python',
        )
        self.assertFalse(m1['cache_hit'])
        v2, m2 = get_cached_traffic_filter_response(
            payload=PAYLOAD,
            builder=builder,
            serve_mode='python',
        )
        self.assertTrue(m2['cache_hit'])
        self.assertEqual(v2, SAMPLE_GROUPS)

        invalidate_traffic_filter_cache()

        v3, m3 = get_cached_traffic_filter_response(
            payload=PAYLOAD,
            builder=builder,
            serve_mode='python',
        )
        self.assertFalse(m3['cache_hit'])
        self.assertEqual(v3, [[{'rebuilt': True}]])
        self.assertEqual(calls['n'], 2)

        v4, m4 = get_cached_traffic_filter_response(
            payload=PAYLOAD,
            builder=builder,
            serve_mode='python',
        )
        self.assertTrue(m4['cache_hit'])
        self.assertEqual(v4, [[{'rebuilt': True}]])

    def test_stale_ns_v_entry_not_served(self):
        key, _ = build_traffic_filter_data_key(PAYLOAD)
        blob = encode_traffic_filter_cache(SAMPLE_GROUPS)
        # Store entry stamped with an old generation while current version is higher.
        safe_cache_set(key, wrap_traffic_filter_entry(ns_v=1, blob=blob), timeout=CACHE_TTL_FILTER)
        # Force namespace version to 2.
        from nhit.caching.service import bump_namespace_version

        bump_namespace_version(CACHE_NS_TRAFFIC_FILTER)
        bump_namespace_version(CACHE_NS_TRAFFIC_FILTER)  # ensure > 1

        calls = {'n': 0}

        def builder():
            calls['n'] += 1
            return [[{'fresh': True}]]

        value, meta = get_cached_traffic_filter_response(
            payload=PAYLOAD,
            builder=builder,
            serve_mode='python',
        )
        self.assertFalse(meta['cache_hit'])
        self.assertEqual(value, [[{'fresh': True}]])
        self.assertEqual(calls['n'], 1)

    def test_ttl_unchanged_constant(self):
        self.assertEqual(CACHE_TTL_FILTER, 300)
        with patch('nhit.caching.traffic_filter_cache.safe_cache_set') as mocked_set:
            get_cached_traffic_filter_response(
                payload=PAYLOAD,
                builder=lambda: SAMPLE_GROUPS,
                timeout=CACHE_TTL_FILTER,
                serve_mode='python',
            )
            mocked_set.assert_called()
            self.assertEqual(mocked_set.call_args.kwargs['timeout'], CACHE_TTL_FILTER)

    def test_cache_key_determinism_and_payload_isolation(self):
        k1, d1 = build_traffic_filter_data_key(PAYLOAD)
        k2, d2 = build_traffic_filter_data_key(dict(PAYLOAD))
        self.assertEqual(k1, k2)
        self.assertEqual(d1, d2)
        other = {**PAYLOAD, 'project_name': ['Chittorgarh to Kota']}
        k3, d3 = build_traffic_filter_data_key(other)
        self.assertNotEqual(k1, k3)
        self.assertNotEqual(d1, d3)

    def test_hit_uses_single_redis_round_trip(self):
        get_cached_traffic_filter_response(
            payload=PAYLOAD,
            builder=lambda: SAMPLE_GROUPS,
            serve_mode='gzip_bytes',
        )
        _, meta = get_cached_traffic_filter_response(
            payload=PAYLOAD,
            builder=lambda: (_ for _ in ()).throw(RuntimeError('builder should not run')),
            serve_mode='gzip_bytes',
        )
        self.assertTrue(meta['cache_hit'])
        self.assertEqual(meta.get('redis_round_trips'), 1)

    def test_serve_modes_parity(self):
        get_cached_traffic_filter_response(
            payload=PAYLOAD,
            builder=lambda: SAMPLE_GROUPS,
            serve_mode='python',
        )
        py_val, py_meta = get_cached_traffic_filter_response(
            payload=PAYLOAD,
            builder=lambda: SAMPLE_GROUPS,
            serve_mode='python',
        )
        gz_val, gz_meta = get_cached_traffic_filter_response(
            payload=PAYLOAD,
            builder=lambda: SAMPLE_GROUPS,
            serve_mode='gzip_bytes',
        )
        js_val, js_meta = get_cached_traffic_filter_response(
            payload=PAYLOAD,
            builder=lambda: SAMPLE_GROUPS,
            serve_mode='json_bytes',
        )
        self.assertTrue(py_meta['cache_hit'])
        self.assertTrue(gz_meta['cache_hit'])
        self.assertTrue(js_meta['cache_hit'])
        expected = JSONRenderer().render(SAMPLE_GROUPS)
        self.assertEqual(py_val, SAMPLE_GROUPS)
        self.assertEqual(gzip.decompress(gz_val), expected)
        self.assertEqual(js_val, expected)


@override_settings(NHIT_CACHE_ENABLED=True, CACHES=_LOCMEM, TRAFFIC_FILTER_PERF_DEBUG=False)
class TrafficFilterDirectBytesHttpTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        _create_unmanaged_tables()

    def setUp(self):
        cache.clear()
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

    def _validated_payload(self):
        ser = TrafficFilterRequestSerializer(data=PAYLOAD)
        self.assertTrue(ser.is_valid(), ser.errors)
        return ser.validated_data

    def _cached_raw(self):
        key, _ = build_traffic_filter_data_key(self._validated_payload())
        entry = cache.get(key)
        self.assertIsInstance(entry, dict)
        raw = entry.get('blob')
        self.assertIsInstance(raw, (bytes, bytearray))
        return bytes(raw), key

    def _warm(self):
        warm = self.client.post(ENDPOINT, PAYLOAD, format='json')
        self.assertEqual(warm.status_code, status.HTTP_200_OK)
        return warm

    def test_gzip_cache_hit_headers_and_body(self):
        self._warm()
        raw, _ = self._cached_raw()
        gzip_payload = extract_traffic_filter_gzip_payload(raw)

        response = self.client.post(
            ENDPOINT,
            PAYLOAD,
            format='json',
            HTTP_ACCEPT_ENCODING='gzip',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response['Content-Type'].startswith('application/json'))
        self.assertEqual(response.get('Content-Encoding'), 'gzip')
        self.assertEqual(response.get('Content-Length'), str(len(gzip_payload)))
        self.assertIn('Accept-Encoding', response.get('Vary', ''))
        self.assertEqual(bytes(response.content), gzip_payload)
        self.assertFalse(bytes(response.content).startswith(TRAFFIC_FILTER_CACHE_MAGIC))

    def test_non_gzip_cache_hit(self):
        self._warm()
        response = self.client.post(ENDPOINT, PAYLOAD, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertNotEqual(response.get('Content-Encoding'), 'gzip')
        self.assertIn('Accept-Encoding', response.get('Vary', ''))
        body = bytes(response.content)
        self.assertTrue(body.startswith(b'['))
        self.assertEqual(response.get('Content-Length'), str(len(body)))

    def test_accept_encoding_variants(self):
        self._warm()
        raw, _ = self._cached_raw()
        gzip_payload = extract_traffic_filter_gzip_payload(raw)
        expected_json = gzip.decompress(gzip_payload)

        for header in ('gzip', 'gzip;q=1.0', 'gzip;q=0.5', 'gzip, deflate, br'):
            with self.subTest(header=header):
                r = self.client.post(
                    ENDPOINT,
                    PAYLOAD,
                    format='json',
                    HTTP_ACCEPT_ENCODING=header,
                )
                self.assertEqual(r.get('Content-Encoding'), 'gzip')
                self.assertEqual(bytes(r.content), gzip_payload)

        for header in ('gzip;q=0', 'identity'):
            with self.subTest(header=header):
                r = self.client.post(
                    ENDPOINT,
                    PAYLOAD,
                    format='json',
                    HTTP_ACCEPT_ENCODING=header,
                )
                self.assertNotEqual(bytes(r.content), gzip_payload)
                if header == 'gzip;q=0' and r.get('Content-Encoding') == 'gzip':
                    # Pre-existing GZipMiddleware may still compress when header matches \\bgzip\\b.
                    self.assertEqual(gzip.decompress(r.content), expected_json)
                else:
                    self.assertNotEqual(r.get('Content-Encoding'), 'gzip')
                    self.assertEqual(bytes(r.content), expected_json)

        missing = self.client.post(ENDPOINT, PAYLOAD, format='json')
        self.assertNotEqual(missing.get('Content-Encoding'), 'gzip')
        self.assertEqual(bytes(missing.content), expected_json)

    def test_decompression_parity_gzip_vs_plain(self):
        self._warm()
        plain = self.client.post(ENDPOINT, PAYLOAD, format='json')
        gzipped = self.client.post(
            ENDPOINT,
            PAYLOAD,
            format='json',
            HTTP_ACCEPT_ENCODING='gzip',
        )
        self.assertEqual(gzip.decompress(gzipped.content), plain.content)
        parsed = json.loads(plain.content)
        self.assertEqual(len(parsed), 1)
        self.assertEqual(len(parsed[0]), 2)
        self.assertEqual(parsed[0][0]['direction'], 'Increasing')
        self.assertEqual(parsed[0][1]['direction'], 'Decreasing')
        self.assertEqual(list(parsed[0][0].keys()), list(SAMPLE_GROUPS[0][0].keys()))

    def test_no_double_gzip_through_middleware(self):
        self._warm()
        raw, _ = self._cached_raw()
        gzip_payload = extract_traffic_filter_gzip_payload(raw)

        api = self.client.post(
            ENDPOINT,
            PAYLOAD,
            format='json',
            HTTP_ACCEPT_ENCODING='gzip',
        )
        factory = RequestFactory()
        req = factory.post(ENDPOINT, data=b'{}', content_type='application/json')
        req.META['HTTP_ACCEPT_ENCODING'] = 'gzip'
        resp = HttpResponse(gzip_payload, content_type='application/json', status=200)
        resp['Content-Encoding'] = 'gzip'
        resp['Content-Length'] = str(len(gzip_payload))
        mw = SelectiveGZipMiddleware(get_response=lambda r: HttpResponse(b'x'))
        out = mw.process_response(req, resp)
        self.assertEqual(out.get('Content-Encoding'), 'gzip')
        self.assertEqual(bytes(out.content), gzip_payload)
        self.assertEqual(bytes(api.content), gzip_payload)

    def test_cache_miss_then_hit(self):
        from traffic.services.traffic_filter_response import build_traffic_filter_groups as real_build

        with patch('traffic.views.build_traffic_filter_groups', wraps=real_build) as mocked:
            miss = self.client.post(ENDPOINT, PAYLOAD, format='json')
            hit = self.client.post(
                ENDPOINT,
                PAYLOAD,
                format='json',
                HTTP_ACCEPT_ENCODING='gzip',
            )
        self.assertEqual(miss.status_code, status.HTTP_200_OK)
        self.assertEqual(hit.status_code, status.HTTP_200_OK)
        self.assertEqual(mocked.call_count, 1)
        self.assertEqual(hit.get('Content-Encoding'), 'gzip')

    def test_corrupt_cache_rebuild_via_http(self):
        self._warm()
        raw, key = self._cached_raw()
        cache.set(key, wrap_traffic_filter_entry(ns_v=1, blob=b'TFGZ1\n' + b'not-gzip'))
        response = self.client.post(ENDPOINT, PAYLOAD, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        new_entry = cache.get(key)
        self.assertIsInstance(new_entry, dict)
        self.assertTrue(bytes(new_entry['blob']).startswith(TRAFFIC_FILTER_CACHE_MAGIC))
        self.assertNotEqual(bytes(new_entry['blob']), b'TFGZ1\n' + b'not-gzip')

    def test_old_path_vs_optimized_path_byte_identical_json(self):
        """Parity: DRF JSONRenderer of Python groups vs TFGZ1 gunzip bytes."""
        from traffic.services.traffic_filter_response import build_traffic_filter_groups
        from traffic.models import Traffic as TrafficModel
        from django.db.models import Q

        data = self._validated_payload()
        queryset = TrafficModel.objects.select_related('project', 'segment')
        queryset = queryset.filter(
            segment__chainage_start__gte=data['chainage_start'],
            segment__chainage_end__lte=data['chainage_end'],
            survey_date=data['date'],
        )
        q_objects = Q()
        for name in data['project_name']:
            q_objects |= Q(project__project_name__iexact=name)
        queryset = queryset.filter(q_objects)
        old_python = build_traffic_filter_groups(queryset)
        old_json = JSONRenderer().render(old_python)

        encoded = encode_traffic_filter_cache(old_python)
        new_json = gzip.decompress(extract_traffic_filter_gzip_payload(encoded))
        self.assertEqual(new_json, old_json)

        # HTTP optimized plain HIT must match old JSON bytes.
        self._warm()
        http = self.client.post(ENDPOINT, PAYLOAD, format='json')
        self.assertEqual(bytes(http.content), old_json)

    def test_stores_tfgz1_not_python_list(self):
        self._warm()
        raw, key = self._cached_raw()
        self.assertIsInstance(raw, (bytes, bytearray))
        self.assertTrue(raw.startswith(TRAFFIC_FILTER_CACHE_MAGIC))
        entry = cache.get(key)
        self.assertEqual(entry.get('kind'), TRAFFIC_FILTER_ENTRY_KIND)
        # Must not be a pickle of a list under this key.
        try:
            loaded = pickle.loads(raw)
        except Exception:
            loaded = None
        self.assertFalse(isinstance(loaded, list))

    def test_http_invalidation_clears_stale_hit(self):
        self._warm()
        hit = self.client.post(ENDPOINT, PAYLOAD, format='json', HTTP_ACCEPT_ENCODING='gzip')
        self.assertEqual(hit.status_code, status.HTTP_200_OK)
        self.assertEqual(hit.get('Content-Encoding'), 'gzip')
        invalidate_traffic_filter_cache()
        # Next request rebuilds; still valid JSON after decompress.
        rebuilt = self.client.post(ENDPOINT, PAYLOAD, format='json', HTTP_ACCEPT_ENCODING='gzip')
        self.assertEqual(rebuilt.status_code, status.HTTP_200_OK)
        self.assertEqual(hit.get('Content-Encoding'), 'gzip')
        self.assertEqual(gzip.decompress(rebuilt.content), gzip.decompress(hit.content))
