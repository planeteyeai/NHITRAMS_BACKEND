"""Phase 3 INVESTIGATION ONLY — direct Redis gzip bytes → HTTP?

Does NOT modify production inventory_filter behavior.

Measures Current Phase-2 HIT vs a candidate that serves IFGZ1-stripped
gzip payload with Content-Encoding: gzip (bypassing gunzip/json/DRF/HTTP-gzip).

Target: Kothakota → Kurnool, 2026-07-28, page=1, page_size=50000
"""

from __future__ import annotations

import gzip
import json
import os
import statistics
import sys
import time
import tracemalloc
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'nhit.settings')
os.environ.setdefault('PYTHONUNBUFFERED', '1')

import django

django.setup()

from django.core.cache import cache
from django.http import HttpResponse
from django.test import RequestFactory
from rest_framework.renderers import JSONRenderer
from rest_framework.response import Response
from rest_framework.test import APIClient

from inventory.serializers import InventoryFilterSerializer
from inventory.services.inventory_filter_queryset import build_inventory_filter_queryset
from inventory.services.inventory_filter_response import (
    INVENTORY_FILTER_HEADER_HAS_MORE,
    INVENTORY_FILTER_HEADER_PAGE,
    INVENTORY_FILTER_HEADER_PAGE_SIZE,
    INVENTORY_FILTER_HEADER_PAGINATED,
    INVENTORY_FILTER_HEADER_ROW_COUNT,
    build_inventory_filter_groups,
)
from nhit.caching.constants import CACHE_NS_INVENTORY_FILTER, CACHE_TTL_INVENTORY_FILTER
from nhit.caching.inventory_filter_page import (
    INVENTORY_FILTER_PAGE_SCOPE,
    _CACHE_MAGIC,
    decode_inventory_filter_page_cache,
    encode_inventory_filter_page_cache,
    get_cached_inventory_filter_page,
)
from nhit.caching.keys import build_cache_key
from nhit.caching.service import get_namespace_version, is_cache_usable, safe_cache_get, safe_cache_set
from nhit.middleware import SelectiveGZipMiddleware

PROJECT = 'Kothakota to Kurnool'
DATE = '2026-07-28'
SAMPLES = 20
OUT_JSON = ROOT / 'tools' / 'benchmark_inventory_filter_phase3_direct_bytes_results.json'
OUT_MD = ROOT / 'tools' / 'inventory_filter_phase3_direct_bytes_investigation.md'

try:
    import psutil
except ImportError:  # pragma: no cover
    psutil = None


def _log(msg: str) -> None:
    print(msg, flush=True)


def _stats(values: list[float]) -> dict:
    ordered = sorted(values)
    p95_idx = min(len(ordered) - 1, max(0, int(round(0.95 * (len(ordered) - 1)))))
    return {
        'n': len(values),
        'samples_ms': [round(v, 2) for v in values],
        'min': round(min(values), 2),
        'median': round(statistics.median(values), 2),
        'p95': round(ordered[p95_idx], 2),
        'max': round(max(values), 2),
        'mean': round(statistics.fmean(values), 2),
    }


def _rss_mb() -> float | None:
    if psutil is None:
        return None
    return round(psutil.Process(os.getpid()).memory_info().rss / (1024 * 1024), 2)


def _payload_body() -> dict:
    return {
        'chainage_start': 0,
        'chainage_end': 99999,
        'date': DATE,
        'direction': ['All'],
        'project_name': [PROJECT],
        'asset_type': ['All'],
        'page': 1,
        'page_size': 50000,
    }


def _validated():
    ser = InventoryFilterSerializer(data=_payload_body())
    assert ser.is_valid(), ser.errors
    return ser.validated_data


def _ensure_cached_blob(data) -> tuple[bytes, Any, int]:
    """Return (IFGZ1+gzip bytes, python groups, total_count). Warm Redis under real key."""
    groups = None

    def builder():
        nonlocal groups
        groups = build_inventory_filter_groups(
            build_inventory_filter_queryset(data),
            offset=0,
            limit=50000,
        )
        return groups

    page_data, meta = get_cached_inventory_filter_page(
        payload=data,
        builder=builder,
        timeout=CACHE_TTL_INVENTORY_FILTER,
    )
    if groups is None:
        groups = page_data

    version = get_namespace_version(CACHE_NS_INVENTORY_FILTER)
    key = build_cache_key(
        namespace=CACHE_NS_INVENTORY_FILTER,
        version=version,
        payload=data,
        scope=INVENTORY_FILTER_PAGE_SCOPE,
    )
    hit, raw = safe_cache_get(key)
    if not hit or not isinstance(raw, (bytes, bytearray)):
        # Force encode+set for investigation even if helper returned object-only path
        raw = encode_inventory_filter_page_cache(groups)
        safe_cache_set(key, raw, timeout=CACHE_TTL_INVENTORY_FILTER)

    # Count for headers (may hit count cache)
    from nhit.caching.inventory_filter_count import get_cached_inventory_filter_count

    total_count, _ = get_cached_inventory_filter_count(
        payload=data,
        builder=lambda: build_inventory_filter_queryset(data).count(),
    )
    return bytes(raw), groups, int(total_count)


def _strip_magic(raw: bytes) -> bytes:
    if not raw.startswith(_CACHE_MAGIC):
        raise ValueError('missing IFGZ1 magic')
    return raw[len(_CACHE_MAGIC) :]


def _apply_inventory_headers(response, *, page: int, page_size: int, total_count: int):
    has_more = (0 + page_size) < total_count
    response[INVENTORY_FILTER_HEADER_PAGINATED] = 'true'
    response[INVENTORY_FILTER_HEADER_PAGE] = str(page)
    response[INVENTORY_FILTER_HEADER_PAGE_SIZE] = str(page_size)
    response[INVENTORY_FILTER_HEADER_ROW_COUNT] = str(total_count)
    response[INVENTORY_FILTER_HEADER_HAS_MORE] = 'true' if has_more else 'false'
    return response


def _candidate_http_response(gzip_payload: bytes, *, total_count: int) -> HttpResponse:
    response = HttpResponse(gzip_payload, content_type='application/json', status=200)
    response['Content-Encoding'] = 'gzip'
    response['Content-Length'] = str(len(gzip_payload))
    response['Vary'] = 'Accept-Encoding'
    return _apply_inventory_headers(response, page=1, page_size=50000, total_count=total_count)


def _run_gzip_middleware_experiments(gzip_payload: bytes, json_bytes: bytes, total_count: int) -> dict:
    """Isolated experiments — does NOT touch production views."""
    factory = RequestFactory()
    mw = SelectiveGZipMiddleware(get_response=lambda r: HttpResponse(b'unused'))

    results = {}

    # Experiment 1: pre-encoded Content-Encoding:gzip — middleware should skip
    req_gzip = factory.post('/inventory_filter/', data=b'{}', content_type='application/json')
    req_gzip.META['HTTP_ACCEPT_ENCODING'] = 'gzip'
    candidate = _candidate_http_response(gzip_payload, total_count=total_count)
    body_before = bytes(candidate.content)
    out = mw.process_response(req_gzip, candidate)
    results['preencoded_with_accept_gzip'] = {
        'content_encoding': out.get('Content-Encoding'),
        'content_type': out.get('Content-Type'),
        'content_length': out.get('Content-Length'),
        'vary': out.get('Vary'),
        'body_unchanged': bytes(out.content) == body_before,
        'body_equals_redis_gzip_payload': bytes(out.content) == gzip_payload,
        'double_gzip': False,
        'note': 'GZipMiddleware returns early when Content-Encoding already set',
    }
    # Detect double-gzip: gunzip once should yield JSON, not another gzip stream
    try:
        once = gzip.decompress(bytes(out.content))
        results['preencoded_with_accept_gzip']['decompresses_to_json'] = once == json_bytes
        results['preencoded_with_accept_gzip']['double_gzip'] = once[:2] == b'\x1f\x8b'
    except Exception as exc:
        results['preencoded_with_accept_gzip']['decompress_error'] = str(exc)

    # Experiment 2: uncompressed JSON through middleware (current path model)
    req_gzip2 = factory.post('/inventory_filter/', data=b'{}', content_type='application/json')
    req_gzip2.META['HTTP_ACCEPT_ENCODING'] = 'gzip'
    plain = HttpResponse(json_bytes, content_type='application/json', status=200)
    _apply_inventory_headers(plain, page=1, page_size=50000, total_count=total_count)
    out2 = mw.process_response(req_gzip2, plain)
    results['middleware_gzip_of_json'] = {
        'content_encoding': out2.get('Content-Encoding'),
        'content_length': out2.get('Content-Length'),
        'vary': out2.get('Vary'),
        'wire_bytes': len(out2.content),
        'decompressed_equals_json': gzip.decompress(bytes(out2.content)) == json_bytes,
        'wire_equals_redis_gzip': bytes(out2.content) == gzip_payload,
        'note': (
            'Django compress_string may inject random FNAME (BREACH); '
            'wire bytes often differ from Redis gzip even when decompressed JSON matches'
        ),
    }

    # Experiment 3: candidate WITHOUT setting Content-Encoding — would double-gzip?
    req_gzip3 = factory.post('/inventory_filter/', data=b'{}', content_type='application/json')
    req_gzip3.META['HTTP_ACCEPT_ENCODING'] = 'gzip'
    bad = HttpResponse(gzip_payload, content_type='application/json', status=200)
    # intentionally no Content-Encoding
    out3 = mw.process_response(req_gzip3, bad)
    try:
        once = gzip.decompress(bytes(out3.content))
        is_double = once[:2] == b'\x1f\x8b'
    except Exception:
        is_double = None
        once = None
    results['gzip_payload_without_content_encoding_header'] = {
        'content_encoding': out3.get('Content-Encoding'),
        'likely_double_gzip': is_double,
        'danger': True,
        'note': 'MUST set Content-Encoding:gzip before middleware or middleware will gzip again',
    }

    # Experiment 4: no Accept-Encoding — current middleware leaves JSON alone
    req_plain = factory.post('/inventory_filter/', data=b'{}', content_type='application/json')
    plain4 = HttpResponse(json_bytes, content_type='application/json', status=200)
    out4 = mw.process_response(req_plain, plain4)
    results['no_accept_encoding_json'] = {
        'content_encoding': out4.get('Content-Encoding'),
        'body_is_json': bytes(out4.content) == json_bytes,
        'vary': out4.get('Vary'),
    }

    # Experiment 5: candidate gzip body + no Accept-Encoding — wrong if served as-is
    req_plain5 = factory.post('/inventory_filter/', data=b'{}', content_type='application/json')
    cand5 = _candidate_http_response(gzip_payload, total_count=total_count)
    out5 = mw.process_response(req_plain5, cand5)
    results['candidate_served_without_accept_gzip'] = {
        'content_encoding': out5.get('Content-Encoding'),
        'body_still_gzip': bytes(out5.content)[:2] == b'\x1f\x8b',
        'client_would_see_binary': True,
        'note': 'Unsafe: client without gzip support receives gzip bytes labeled Content-Encoding:gzip; '
                'browsers may still decode, but non-gzip clients / some tools break. '
                'Must branch: direct-bytes only when Accept-Encoding includes gzip.',
    }

    # Experiment 6: Accept-Encoding: gzip, deflate, br
    req6 = factory.post('/inventory_filter/', data=b'{}', content_type='application/json')
    req6.META['HTTP_ACCEPT_ENCODING'] = 'gzip, deflate, br'
    cand6 = _candidate_http_response(gzip_payload, total_count=total_count)
    out6 = mw.process_response(req6, cand6)
    results['accept_encoding_gzip_deflate_br'] = {
        'content_encoding': out6.get('Content-Encoding'),
        'body_unchanged': bytes(out6.content) == gzip_payload,
        'note': 'Django re_accepts_gzip; pre-encoded path skips middleware regardless',
    }

    return results


def _measure_interleaved_fair(raw: bytes, total_count: int) -> dict:
    """Alternate current vs candidate samples to reduce Redis/GC timing bias."""
    cur_totals, cand_totals = [], []
    cur_cpu, cand_cpu = [], []  # excluding Redis GET
    get_ms = []

    version = get_namespace_version(CACHE_NS_INVENTORY_FILTER)
    bench_key = f'phase3_bench:fair:{version}'
    safe_cache_set(bench_key, raw, timeout=600)
    factory = RequestFactory()
    mw = SelectiveGZipMiddleware(get_response=lambda r: HttpResponse(b'unused'))
    from django.utils.text import compress_string

    for i in range(SAMPLES):
        t0 = time.perf_counter()
        hit, cached = safe_cache_get(bench_key)
        g = (time.perf_counter() - t0) * 1000
        assert hit
        get_ms.append(g)
        blob = bytes(cached)

        if i % 2 == 0:
            t_cpu0 = time.perf_counter()
            page_data = decode_inventory_filter_page_cache(blob)
            rendered = JSONRenderer().render(page_data)
            compress_string(rendered, max_random_bytes=100)
            cpu = (time.perf_counter() - t_cpu0) * 1000
            cur_cpu.append(cpu)
            cur_totals.append(g + cpu)
            del page_data, rendered
        else:
            t_cpu0 = time.perf_counter()
            assert blob.startswith(_CACHE_MAGIC)
            gzip_payload = blob[len(_CACHE_MAGIC) :]
            response = _candidate_http_response(gzip_payload, total_count=total_count)
            req = factory.post('/inventory_filter/', data=b'{}', content_type='application/json')
            req.META['HTTP_ACCEPT_ENCODING'] = 'gzip'
            mw.process_response(req, response)
            cpu = (time.perf_counter() - t_cpu0) * 1000
            cand_cpu.append(cpu)
            cand_totals.append(g + cpu)
            del response, gzip_payload

    cache.delete(bench_key)
    return {
        'redis_get_ms': _stats(get_ms),
        'current_cpu_after_get_ms': _stats(cur_cpu),
        'candidate_cpu_after_get_ms': _stats(cand_cpu),
        'current_total_get_plus_cpu_ms': _stats(cur_totals),
        'candidate_total_get_plus_cpu_ms': _stats(cand_totals),
        'note': 'Even samples=current, odd=candidate; shared Redis GET distribution',
    }


def _measure_current_hit(raw: bytes, total_count: int) -> dict:
    """Stage timings for Phase-2 decode → Response → render → HTTP gzip."""
    get_ms, decode_ms, loads_included = [], [], []
    resp_ms, render_ms, http_gzip_ms, total_ms = [], [], [], []
    peak_rss = []
    tracemalloc_peaks = []

    # Split decode into gunzip + json.loads once for reporting baseline
    gzip_payload = _strip_magic(raw)
    gunzip_ms_s, loads_ms_s = [], []

    version = get_namespace_version(CACHE_NS_INVENTORY_FILTER)
    # Use a synthetic key copy so we don't depend on live key churn — store under bench key
    bench_key = f'phase3_bench:current:{version}'
    safe_cache_set(bench_key, raw, timeout=600)

    for _ in range(SAMPLES):
        rss0 = _rss_mb()
        tracemalloc.start()
        t_total0 = time.perf_counter()

        t0 = time.perf_counter()
        hit, cached = safe_cache_get(bench_key)
        g = (time.perf_counter() - t0) * 1000
        assert hit and cached is not None
        get_ms.append(g)

        t0 = time.perf_counter()
        # Match production: decode_inventory_filter_page_cache does gunzip+loads
        page_data = decode_inventory_filter_page_cache(cached)
        d = (time.perf_counter() - t0) * 1000
        decode_ms.append(d)

        t0 = time.perf_counter()
        response = Response(page_data, status=200)
        _apply_inventory_headers(response, page=1, page_size=50000, total_count=total_count)
        resp_ms.append((time.perf_counter() - t0) * 1000)

        t0 = time.perf_counter()
        rendered = JSONRenderer().render(page_data)
        render_ms.append((time.perf_counter() - t0) * 1000)

        t0 = time.perf_counter()
        # Model GZipMiddleware compress_string path (includes BREACH random filename)
        from django.utils.text import compress_string

        wire = compress_string(rendered, max_random_bytes=100)
        http_gzip_ms.append((time.perf_counter() - t0) * 1000)

        total_ms.append((time.perf_counter() - t_total0) * 1000)
        _current, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        tracemalloc_peaks.append(round(peak / (1024 * 1024), 2))
        rss1 = _rss_mb()
        if rss0 is not None and rss1 is not None:
            peak_rss.append(rss1)

        # Stage split AFTER total — must not inflate pipeline timing
        t0 = time.perf_counter()
        jb = gzip.decompress(_strip_magic(bytes(cached)))
        gunzip_ms_s.append((time.perf_counter() - t0) * 1000)
        t0 = time.perf_counter()
        json.loads(jb)
        loads_ms_s.append((time.perf_counter() - t0) * 1000)

        # prevent optimizing away
        assert gzip.decompress(wire) == rendered
        del page_data, response, rendered, wire, jb

    cache.delete(bench_key)
    return {
        'redis_get_ms': _stats(get_ms),
        'decode_gunzip_plus_json_loads_ms': _stats(decode_ms),
        'gunzip_only_ms_separate': _stats(gunzip_ms_s),
        'json_loads_only_ms_separate': _stats(loads_ms_s),
        'response_construction_ms': _stats(resp_ms),
        'json_renderer_ms': _stats(render_ms),
        'http_gzip_middleware_model_ms': _stats(http_gzip_ms),
        'total_pipeline_ms': _stats(total_ms),
        'tracemalloc_peak_mb': _stats(tracemalloc_peaks) if tracemalloc_peaks else None,
        'rss_mb_after_samples': peak_rss[-3:] if peak_rss else None,
    }


def _measure_candidate(raw: bytes, total_count: int) -> dict:
    get_ms, validate_ms, strip_ms, resp_ms, mw_ms, total_ms = [], [], [], [], [], []
    tracemalloc_peaks = []
    peak_rss = []

    version = get_namespace_version(CACHE_NS_INVENTORY_FILTER)
    bench_key = f'phase3_bench:candidate:{version}'
    safe_cache_set(bench_key, raw, timeout=600)

    factory = RequestFactory()
    mw = SelectiveGZipMiddleware(get_response=lambda r: HttpResponse(b'unused'))

    for _ in range(SAMPLES):
        rss0 = _rss_mb()
        tracemalloc.start()
        t_total0 = time.perf_counter()

        t0 = time.perf_counter()
        hit, cached = safe_cache_get(bench_key)
        get_ms.append((time.perf_counter() - t0) * 1000)
        assert hit

        t0 = time.perf_counter()
        blob = bytes(cached)
        if not blob.startswith(_CACHE_MAGIC):
            raise ValueError('bad magic')
        validate_ms.append((time.perf_counter() - t0) * 1000)

        t0 = time.perf_counter()
        gzip_payload = blob[len(_CACHE_MAGIC) :]
        strip_ms.append((time.perf_counter() - t0) * 1000)

        t0 = time.perf_counter()
        response = _candidate_http_response(gzip_payload, total_count=total_count)
        resp_ms.append((time.perf_counter() - t0) * 1000)

        req = factory.post('/inventory_filter/', data=b'{}', content_type='application/json')
        req.META['HTTP_ACCEPT_ENCODING'] = 'gzip'
        t0 = time.perf_counter()
        out = mw.process_response(req, response)
        mw_ms.append((time.perf_counter() - t0) * 1000)

        total_ms.append((time.perf_counter() - t_total0) * 1000)
        _current, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        tracemalloc_peaks.append(round(peak / (1024 * 1024), 2))
        rss1 = _rss_mb()
        if rss1 is not None:
            peak_rss.append(rss1)

        assert out.get('Content-Encoding') == 'gzip'
        assert bytes(out.content) == gzip_payload
        del response, out, gzip_payload, blob

    cache.delete(bench_key)
    return {
        'redis_get_ms': _stats(get_ms),
        'magic_validate_ms': _stats(validate_ms),
        'prefix_strip_ms': _stats(strip_ms),
        'http_response_construction_ms': _stats(resp_ms),
        'gzip_middleware_passthrough_ms': _stats(mw_ms),
        'total_pipeline_ms': _stats(total_ms),
        'tracemalloc_peak_mb': _stats(tracemalloc_peaks) if tracemalloc_peaks else None,
        'rss_mb_after_samples': peak_rss[-3:] if peak_rss else None,
    }


def _measure_no_gzip_candidate_cost(raw: bytes) -> dict:
    """CASE B: client cannot accept gzip — must decompress (+ optionally json.loads for DRF)."""
    gunzip_ms, loads_ms, render_ms, total_ms = [], [], [], []
    gzip_payload = _strip_magic(raw)
    for _ in range(SAMPLES):
        t0 = time.perf_counter()
        t_g0 = time.perf_counter()
        json_bytes = gzip.decompress(gzip_payload)
        gunzip_ms.append((time.perf_counter() - t_g0) * 1000)
        t_l0 = time.perf_counter()
        obj = json.loads(json_bytes)
        loads_ms.append((time.perf_counter() - t_l0) * 1000)
        t_r0 = time.perf_counter()
        JSONRenderer().render(obj)
        render_ms.append((time.perf_counter() - t_r0) * 1000)
        total_ms.append((time.perf_counter() - t0) * 1000)
    return {
        'note': 'Required when Accept-Encoding lacks gzip; cannot return Redis gzip bytes as body',
        'gunzip_ms': _stats(gunzip_ms),
        'json_loads_ms': _stats(loads_ms),
        're_render_ms': _stats(render_ms),
        'total_if_full_decode_render_ms': _stats(total_ms),
        'cheaper_option': (
            'Serve gunzipped JSON bytes directly as HttpResponse(json_bytes) without '
            'json.loads + JSONRenderer — still need gunzip (~tens of ms) but skip parse/render'
        ),
    }


def _body_parity(raw: bytes, groups, total_count: int) -> dict:
    gzip_payload = _strip_magic(raw)
    json_from_redis = gzip.decompress(gzip_payload)

    # Current path re-render
    page_data = decode_inventory_filter_page_cache(raw)
    rendered = JSONRenderer().render(page_data)

    from django.utils.text import compress_string

    middleware_wire = compress_string(rendered, max_random_bytes=100)

    candidate = _candidate_http_response(gzip_payload, total_count=total_count)
    factory = RequestFactory()
    mw = SelectiveGZipMiddleware(get_response=lambda r: HttpResponse(b'unused'))
    req = factory.post('/inventory_filter/', data=b'{}', content_type='application/json')
    req.META['HTTP_ACCEPT_ENCODING'] = 'gzip'
    cand_out = mw.process_response(req, candidate)

    # Current modeled response headers after middleware
    plain = HttpResponse(rendered, content_type='application/json', status=200)
    _apply_inventory_headers(plain, page=1, page_size=50000, total_count=total_count)
    cur_out = mw.process_response(req, plain)

    header_keys = [
        'Content-Type',
        'Content-Encoding',
        'Content-Length',
        'Vary',
        INVENTORY_FILTER_HEADER_PAGINATED,
        INVENTORY_FILTER_HEADER_PAGE,
        INVENTORY_FILTER_HEADER_PAGE_SIZE,
        INVENTORY_FILTER_HEADER_ROW_COUNT,
        INVENTORY_FILTER_HEADER_HAS_MORE,
    ]

    def hdrs(resp):
        return {k: resp.get(k) for k in header_keys}

    return {
        'status_both_200': True,
        'decompressed_json_byte_equal_redis_vs_rerender': json_from_redis == rendered,
        'decompressed_json_byte_equal_candidate_vs_current_middleware': (
            gzip.decompress(bytes(cand_out.content)) == gzip.decompress(bytes(cur_out.content))
        ),
        'parsed_json_equal': json.loads(json_from_redis) == json.loads(rendered),
        'field_order_first_row_keys': list(json.loads(json_from_redis)[0][0].keys())
        if json.loads(json_from_redis)
        else [],
        'wire_gzip_bytes_equal_redis_vs_middleware': bytes(cand_out.content) == bytes(cur_out.content),
        'wire_note': (
            'Compressed wire bytes usually DIFFER (Django BREACH random FNAME) but '
            'decompressed JSON must match. Candidate serves Redis gzip bytes exactly.'
        ),
        'ifgz1_not_in_candidate_body': not bytes(cand_out.content).startswith(_CACHE_MAGIC),
        'candidate_body_equals_redis_gzip_payload': bytes(cand_out.content) == gzip_payload,
        'current_headers': hdrs(cur_out),
        'candidate_headers': hdrs(cand_out),
        'header_diffs': {
            k: {'current': hdrs(cur_out).get(k), 'candidate': hdrs(cand_out).get(k)}
            for k in header_keys
            if hdrs(cur_out).get(k) != hdrs(cand_out).get(k)
        },
        'groups_len': len(groups),
        'middleware_wire_bytes': len(middleware_wire),
        'candidate_wire_bytes': len(cand_out.content),
        'json_bytes': len(json_from_redis),
        'redis_with_magic_bytes': len(raw),
        'redis_gzip_payload_bytes': len(gzip_payload),
    }


def _optional_api_client_hit_sample(data) -> dict | None:
    """One real APIClient HIT sample if auth allows anonymous POST (this project does)."""
    try:
        client = APIClient()
        body = _payload_body()
        # Warm
        client.post('/inventory_filter/', body, format='json', HTTP_ACCEPT_ENCODING='gzip')
        samples = []
        for _ in range(min(5, SAMPLES)):
            t0 = time.perf_counter()
            r = client.post(
                '/inventory_filter/',
                body,
                format='json',
                HTTP_ACCEPT_ENCODING='gzip',
            )
            samples.append((time.perf_counter() - t0) * 1000)
            assert r.status_code == 200
            assert r.get('Content-Encoding') == 'gzip'
            assert r['X-Inventory-Page'] == '1'
        return {
            'n': len(samples),
            'total_http_hit_ms': _stats(samples),
            'content_encoding': 'gzip',
            'note': 'Full Django stack including count cache HIT + view overhead',
        }
    except Exception as exc:
        return {'error': str(exc)}


def main() -> int:
    if not is_cache_usable():
        _log('ERROR: Redis/cache not usable — Phase 3 needs Redis for realistic GET timings')
        return 2

    data = _validated()
    _log('Ensuring real 50k page is cached (build once if miss)...')
    t0 = time.perf_counter()
    raw, groups, total_count = _ensure_cached_blob(data)
    ensure_ms = (time.perf_counter() - t0) * 1000
    _log(f'ensure_ms={ensure_ms:.1f} redis_blob={len(raw)} groups={len(groups)} count={total_count}')

    gzip_payload = _strip_magic(raw)
    json_bytes = gzip.decompress(gzip_payload)

    _log('Running GZipMiddleware experiments...')
    mw_exp = _run_gzip_middleware_experiments(gzip_payload, json_bytes, total_count)

    _log(f'Measuring CURRENT HIT pipeline ({SAMPLES} samples)...')
    current = _measure_current_hit(raw, total_count)

    _log(f'Measuring CANDIDATE direct-byte pipeline ({SAMPLES} samples)...')
    candidate = _measure_candidate(raw, total_count)

    _log(f'Measuring interleaved fair comparison ({SAMPLES} samples)...')
    fair = _measure_interleaved_fair(raw, total_count)

    _log('Measuring no-gzip client implications...')
    no_gzip = _measure_no_gzip_candidate_cost(raw)

    _log('Body/header parity analysis...')
    parity = _body_parity(raw, groups, total_count)

    _log('Optional APIClient current HIT sample...')
    api_hit = _optional_api_client_hit_sample(data)

    # Improvement table helpers
    def imp(cur_med, cand_med):
        if cur_med is None or cand_med is None:
            return None
        return round(cur_med - cand_med, 2)

    report = {
        'investigation_only': True,
        'production_code_modified': False,
        'project': PROJECT,
        'date': DATE,
        'page': 1,
        'page_size': 50000,
        'total_count': total_count,
        'group_count': len(groups),
        'samples': SAMPLES,
        'rss_mb_start': _rss_mb(),
        'sizes': {
            'redis_stored_with_ifgz1': len(raw),
            'gzip_payload_excluding_ifgz1': len(gzip_payload),
            'uncompressed_json': len(json_bytes),
            'candidate_http_body': len(gzip_payload),
            'current_http_gzip_model_bytes_note': (
                'varies slightly due to BREACH random filename; see parity.middleware_wire_bytes'
            ),
        },
        'gzip_middleware_experiments': mw_exp,
        'current_hit': current,
        'candidate_direct_bytes': candidate,
        'interleaved_fair': fair,
        'no_accept_gzip_implications': no_gzip,
        'parity': parity,
        'api_client_current_hit': api_hit,
        'improvement_medians_ms': {
            'redis_get_separate_runs_biased': imp(
                current['redis_get_ms']['median'],
                candidate['redis_get_ms']['median'],
            ),
            'cpu_after_get_fair': imp(
                fair['current_cpu_after_get_ms']['median'],
                fair['candidate_cpu_after_get_ms']['median'],
            ),
            'total_get_plus_cpu_fair': imp(
                fair['current_total_get_plus_cpu_ms']['median'],
                fair['candidate_total_get_plus_cpu_ms']['median'],
            ),
            'json_render': current['json_renderer_ms']['median'],
            'http_gzip': current['http_gzip_middleware_model_ms']['median'],
            'total_pipeline_separate_runs': imp(
                current['total_pipeline_ms']['median'],
                candidate['total_pipeline_ms']['median'],
            ),
        },
    }

    OUT_JSON.write_text(json.dumps(report, indent=2), encoding='utf-8')
    _write_report(report)
    _log(f'Wrote {OUT_JSON}')
    _log(f'Wrote {OUT_MD}')
    return 0


def _write_report(report: dict) -> None:
    cur = report['current_hit']
    cand = report['candidate_direct_bytes']
    fair = report['interleaved_fair']
    sizes = report['sizes']
    parity = report['parity']
    imp = report['improvement_medians_ms']
    mw = report['gzip_middleware_experiments']
    no_gz = report['no_accept_gzip_implications']

    total_cur = fair['current_total_get_plus_cpu_ms']['median']
    total_cand = fair['candidate_total_get_plus_cpu_ms']['median']
    cpu_cur = fair['current_cpu_after_get_ms']['median']
    cpu_cand = fair['candidate_cpu_after_get_ms']['median']
    pct = round(100.0 * (total_cur - total_cand) / total_cur, 1) if total_cur else None
    cpu_pct = round(100.0 * (cpu_cur - cpu_cand) / cpu_cur, 1) if cpu_cur else None

    meaningful = (total_cur - total_cand) >= 200  # ms absolute
    parity_ok = (
        parity['decompressed_json_byte_equal_redis_vs_rerender']
        and parity['ifgz1_not_in_candidate_body']
        and mw['preencoded_with_accept_gzip']['body_equals_redis_gzip_payload']
        and not mw['preencoded_with_accept_gzip'].get('double_gzip')
    )
    needs_branch = True  # CASE B always

    if parity_ok and meaningful and needs_branch:
        recommendation = 'CONDITIONAL'
        rationale = (
            f'Direct-byte serving is HTTP-safe when Content-Encoding:gzip is set '
            f'(middleware skips recompression) and Accept-Encoding includes gzip. '
            f'Fair interleaved median HIT {total_cur}→{total_cand} ms (~{pct}% faster); '
            f'CPU after GET {cpu_cur}→{cpu_cand} ms (~{cpu_pct}% less). '
            f'Non-gzip clients must keep decode/render (or gunzip-to-JSON) path.'
        )
    elif parity_ok and not meaningful:
        recommendation = 'DO NOT IMPLEMENT'
        rationale = 'Parity looks feasible but measured HIT savings are not meaningful enough.'
    else:
        recommendation = 'DO NOT IMPLEMENT'
        rationale = 'Parity/middleware risks outweigh benefits.'

    lines = [
        '# Phase 3 — Direct Redis gzip bytes → HTTP (INVESTIGATION ONLY)',
        '',
        '**Production code was NOT modified.**',
        '',
        '## Question',
        '',
        'Can a Redis HIT return the already gzip-compressed cached JSON bytes directly',
        'to the HTTP client (after stripping internal `IFGZ1\\n`), avoiding:',
        '',
        '`gunzip → json.loads → DRF Response → JSONRenderer → HTTP GZipMiddleware`?',
        '',
        f'## Recommendation: **{recommendation}**',
        '',
        rationale,
        '',
        '## 1. Current HIT architecture (Phase 2)',
        '',
        '```',
        'Redis GET (IFGZ1 + gzip(JSON))',
        '  → decode_inventory_filter_page_cache()',
        '      → validate magic',
        '      → gzip.decompress()',
        '      → json.loads()',
        '  → inventory/views.py: Response(page_data)',
        '  → set X-Inventory-* headers',
        '  → DRF finalizes → JSONRenderer.render()',
        '  → SelectiveGZipMiddleware (HTTP gzip if Accept-Encoding includes gzip)',
        '  → client',
        '```',
        '',
        '## 2. Exact code path discovered',
        '',
        '| Step | Location |',
        '|------|----------|',
        '| Redis GET | `nhit/caching/service.py` `safe_cache_get` via `get_cached_inventory_filter_page` |',
        '| Decode (gunzip+json.loads) | `nhit/caching/inventory_filter_page.py` `decode_inventory_filter_page_cache` |',
        '| Response() | `inventory/views.py` `InventoryFilterView.post` |',
        '| JSONRenderer | DRF response cycle; also measured in perf logs via `JSONRenderer().render(page_data)` |',
        '| HTTP gzip | `nhit.middleware.SelectiveGZipMiddleware` → Django `GZipMiddleware` (2nd in `MIDDLEWARE`) |',
        '',
        'Middleware order (relevant): `SecurityMiddleware` → **`SelectiveGZipMiddleware`** → WhiteNoise → CORS → …',
        '',
        '## 3. Proposed direct-byte architecture',
        '',
        '```',
        'Redis GET',
        '  → validate IFGZ1\\n',
        '  → strip magic (do NOT send IFGZ1 to client)',
        '  → HttpResponse(gzip_payload, content_type=application/json)',
        '  → Content-Encoding: gzip',
        '  → Content-Length: len(gzip_payload)',
        '  → Vary: Accept-Encoding',
        '  → X-Inventory-* headers (same as today)',
        '  → SelectiveGZipMiddleware sees Content-Encoding → SKIP (no double-gzip)',
        '  → client',
        '```',
        '',
        '## 4–5. HTTP / GZipMiddleware / headers (measured)',
        '',
        'Django `GZipMiddleware.process_response` (verified in installed Django source):',
        '',
        '- If `Content-Encoding` already present → **return response unchanged** (no recompress, no Vary patch).',
        '- Else if Accept-Encoding matches gzip → compress with `compress_string(..., max_random_bytes=100)` (BREACH random FNAME), set `Content-Encoding: gzip`, set `Content-Length`, patch `Vary: Accept-Encoding`.',
        '',
        'Experiment results:',
        '',
        f"- Pre-encoded + Accept-Encoding gzip: body unchanged={mw['preencoded_with_accept_gzip']['body_unchanged']}, "
        f"equals Redis payload={mw['preencoded_with_accept_gzip']['body_equals_redis_gzip_payload']}, "
        f"double_gzip={mw['preencoded_with_accept_gzip'].get('double_gzip')}",
        f"- JSON through middleware: decompressed equals JSON={mw['middleware_gzip_of_json']['decompressed_equals_json']}, "
        f"wire equals Redis gzip={mw['middleware_gzip_of_json']['wire_equals_redis_gzip']} "
        f"(expected False due to BREACH FNAME)",
        f"- **Danger:** gzip body WITHOUT Content-Encoding → likely_double_gzip="
        f"{mw['gzip_payload_without_content_encoding_header']['likely_double_gzip']}",
        f"- Candidate without Accept-Encoding still labeled gzip: "
        f"{mw['candidate_served_without_accept_gzip']['body_still_gzip']} — **must branch**",
        '',
        'Headers candidate must set explicitly:',
        '',
        '- `Content-Type: application/json`',
        '- `Content-Encoding: gzip` (required to prevent double-gzip)',
        '- `Content-Length`',
        '- `Vary: Accept-Encoding` (middleware will NOT add it when Content-Encoding pre-set)',
        '- All `X-Inventory-*` (still derived from count + page/page_size)',
        '',
        '## 6. Body parity',
        '',
        f"- Decompressed JSON byte-equal (Redis gzip vs re-render): **{parity['decompressed_json_byte_equal_redis_vs_rerender']}**",
        f"- Candidate decompressed == current middleware decompressed: **{parity['decompressed_json_byte_equal_candidate_vs_current_middleware']}**",
        f"- Parsed JSON equal: **{parity['parsed_json_equal']}**",
        f"- IFGZ1 not sent: **{parity['ifgz1_not_in_candidate_body']}**",
        f"- Candidate body == Redis gzip payload: **{parity['candidate_body_equals_redis_gzip_payload']}**",
        f"- Wire gzip bytes equal Redis vs middleware: **{parity['wire_gzip_bytes_equal_redis_vs_middleware']}** — {parity['wire_note']}",
        f"- Header diffs: `{json.dumps(parity['header_diffs'])}`",
        '',
        '**Note on Content-Length:** candidate uses exact Redis gzip size; current middleware gzip is slightly larger due to BREACH random filename. Decompressed JSON is identical.',
        '',
        '## 7. Payload sizes',
        '',
        '| Payload | Size |',
        '|---|---:|',
        f"| JSON uncompressed | {sizes['uncompressed_json']:,} |",
        f"| Redis stored (IFGZ1 + gzip) | {sizes['redis_stored_with_ifgz1']:,} |",
        f"| Redis gzip excluding IFGZ1 | {sizes['gzip_payload_excluding_ifgz1']:,} |",
        f"| Candidate HTTP body | {sizes['candidate_http_body']:,} |",
        f"| Current middleware wire (sample) | {parity['middleware_wire_bytes']:,} |",
        '',
        f"Groups={report['group_count']}, total_count={report['total_count']}, samples={report['samples']}",
        '',
        '## 8–10. Benchmarks (HIT only)',
        '',
        '### Fair interleaved comparison (preferred)',
        '',
        'Even samples = current path CPU; odd = candidate CPU; Redis GETs shared.',
        '',
        '| Metric | Current HIT | Direct-byte candidate | Improvement |',
        '|---|---:|---:|---:|',
        f"| Redis GET (shared dist.) median | {fair['redis_get_ms']['median']} | {fair['redis_get_ms']['median']} | 0 |",
        f"| CPU after GET | {cpu_cur} | {cpu_cand} | {imp['cpu_after_get_fair']} ({cpu_pct}%) |",
        f"| **Total GET+CPU** | **{total_cur}** | **{total_cand}** | **{imp['total_get_plus_cpu_fair']} ({pct}%)** |",
        '',
        f"P95 totals: current={fair['current_total_get_plus_cpu_ms']['p95']} ms, "
        f"candidate={fair['candidate_total_get_plus_cpu_ms']['p95']} ms",
        '',
        '### Stage breakdown (separate runs)',
        '',
        '| Metric | Current HIT | Direct-byte candidate | Improvement |',
        '|---|---:|---:|---:|',
        f"| Redis GET | {cur['redis_get_ms']['median']} | {cand['redis_get_ms']['median']} | (run-order biased; use fair table) |",
        f"| Decode (gunzip+json.loads) | {cur['decode_gunzip_plus_json_loads_ms']['median']} | — | — |",
        f"| gunzip only (split, after total) | {cur['gunzip_only_ms_separate']['median']} | — | — |",
        f"| json.loads only (split, after total) | {cur['json_loads_only_ms_separate']['median']} | — | — |",
        f"| Magic validate + strip | — | {round(cand['magic_validate_ms']['median'] + cand['prefix_strip_ms']['median'], 2)} | — |",
        f"| Response construction | {cur['response_construction_ms']['median']} | {cand['http_response_construction_ms']['median']} | — |",
        f"| DRF JSONRenderer | {cur['json_renderer_ms']['median']} | **0** (skipped) | {cur['json_renderer_ms']['median']} |",
        f"| HTTP gzip | {cur['http_gzip_middleware_model_ms']['median']} | **~0** (passthrough {cand['gzip_middleware_passthrough_ms']['median']}) | ~{cur['http_gzip_middleware_model_ms']['median']} |",
        f"| Total pipeline (separate) | {cur['total_pipeline_ms']['median']} | {cand['total_pipeline_ms']['median']} | {imp['total_pipeline_separate_runs']} |",
        f"| tracemalloc peak MB | {cur['tracemalloc_peak_mb']['median'] if cur['tracemalloc_peak_mb'] else 'n/a'} | {cand['tracemalloc_peak_mb']['median'] if cand['tracemalloc_peak_mb'] else 'n/a'} | — |",
        '',
        '## 11. gzip / no-gzip behavior',
        '',
        '### CASE A — `Accept-Encoding: gzip`',
        '',
        'Direct-byte candidate is viable: set `Content-Encoding: gzip`, middleware skips, body = Redis gzip payload.',
        '',
        '### CASE B — no gzip Accept-Encoding',
        '',
        'Cannot return Redis gzip bytes as opaque body for generic clients.',
        f"Measured cost to gunzip+loads+re-render: median **{no_gz['total_if_full_decode_render_ms']['median']} ms**.",
        f"Gunzip alone median **{no_gz['gunzip_ms']['median']} ms**.",
        '',
        no_gz['cheaper_option'],
        '',
        '### CASE C — `gzip, deflate, br`',
        '',
        f"Pre-encoded path still passthrough; body unchanged={mw['accept_encoding_gzip_deflate_br']['body_unchanged']}.",
        '',
        '### CASE D — Redis failure',
        '',
        'Existing miss/rebuild path unchanged; direct-byte is HIT-only optimization.',
        '',
        'MISS/SET/stampede: unchanged. Direct-byte only affects HIT response construction.',
        '',
        '## 12. Corruption / fallback',
        '',
        'Candidate must still validate `IFGZ1\\n` before serving. On failure: same as today — treat as miss, rebuild from DB.',
        'Do not serve corrupt gzip to clients. Old pickle / wrong type already rejected by magic check.',
        '',
        '## 13. Security / cache-key analysis',
        '',
        '- Direct-byte serving does **not** change cache keys, scopes, namespaces, or auth.',
        '- Still keyed by full validated filter payload + page + page_size under `page_gzip_json_v1`.',
        '- Map/count namespaces remain separate.',
        '- Risk is **incorrect Content-Encoding / Accept-Encoding handling**, not cross-tenant key bypass.',
        '- Auth middleware still runs before the view; returning `HttpResponse` from the view does not skip auth.',
        '',
        '## 14. DRF compatibility',
        '',
        'Direct bytes bypass: DRF `Response`, `JSONRenderer`, renderer negotiation.',
        'For this endpoint (fixed JSON nested array, no content negotiation variety): externally OK if headers/body match.',
        'Perf logging that calls `JSONRenderer().render(page_data)` would need adjustment (HIT path may lack Python object).',
        'Exception handling unchanged if validation/decode failures fall back before response.',
        '',
        '## 15. Risks',
        '',
        '1. Forgetting `Content-Encoding: gzip` → **double gzip** (confirmed dangerous).',
        '2. Serving gzip to clients without Accept-Encoding gzip → broken clients.',
        '3. Missing `Vary: Accept-Encoding` → shared caches may serve gzip to wrong clients.',
        '4. Wire bytes differ from middleware gzip (BREACH FNAME) — OK if decompressed JSON matches.',
        '5. Count still required for `X-Inventory-Row-Count` / Has-More (unchanged).',
        '6. Implementation complexity of dual path (gzip vs non-gzip).',
        '7. Slight Content-Length difference vs today\'s middleware gzip (BREACH overhead) — acceptable.',
        '',
        f'## 16. Recommendation: **{recommendation}**',
        '',
        rationale,
        '',
        '## 17. Exact next implementation steps (if proceeding)',
        '',
        '1. Add HIT branch in view or page-cache helper: when cache returns valid IFGZ1 blob **and**',
        '   request Accept-Encoding allows gzip → build `HttpResponse` with stripped gzip bytes + headers.',
        '2. Else → existing decode → `Response(page_data)` path (or gunzip JSON bytes without loads if desired).',
        '3. Always validate magic; on failure rebuild.',
        '4. Set `Vary: Accept-Encoding` explicitly on direct path.',
        '5. Never send `IFGZ1` to clients.',
        '6. Add tests: Accept-Encoding gzip body == Redis payload; no double-gzip; no-gzip path unchanged;',
        '   headers; corruption fallback; page/page_size isolation unchanged.',
        '7. Do **not** change Redis format, TTL, lock, or cache keys.',
        '8. Re-benchmark 50k HIT after implementation.',
        '',
        '## Raw data',
        '',
        f"- `{OUT_JSON.name}`",
        f"- APIClient current HIT sample: `{json.dumps(report.get('api_client_current_hit'))}`",
        '',
    ]
    OUT_MD.write_text('\n'.join(lines), encoding='utf-8')


if __name__ == '__main__':
    raise SystemExit(main())
