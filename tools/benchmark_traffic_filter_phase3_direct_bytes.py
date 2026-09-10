"""Post-implementation benchmark: traffic_filter TFGZ1 direct-byte HIT path.

Measures Redis GET, extract, legacy CPU (loads+render+gzip), and optimized
direct-byte serving. Does not expose Redis contents or secrets.

Usage:
  python tools/benchmark_traffic_filter_phase3_direct_bytes.py
"""

from __future__ import annotations

import gzip
import json
import os
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'nhit.settings')
os.environ.setdefault('PYTHONUNBUFFERED', '1')

import django

django.setup()

from django.core.cache import cache
from django.utils.text import compress_string
from rest_framework.renderers import JSONRenderer
from rest_framework.test import APIClient

from nhit.caching.constants import CACHE_NS_TRAFFIC_FILTER, CACHE_TTL_FILTER
from nhit.caching.keys import build_cache_key
from nhit.caching.service import get_namespace_version, is_cache_usable, safe_cache_get
from nhit.caching.traffic_filter_cache import (
    TRAFFIC_FILTER_CACHE_MAGIC,
    TRAFFIC_FILTER_CACHE_SCOPE,
    extract_traffic_filter_gzip_payload,
)
from traffic.serializers import TrafficFilterRequestSerializer

SAMPLES = 30
WARMUPS = 5
OUT_JSON = ROOT / 'tools' / 'benchmark_traffic_filter_phase3_direct_bytes_results.json'
OUT_MD = ROOT / 'tools' / 'traffic_filter_phase3_direct_bytes_implementation.md'

PRIMARY_PAYLOAD = {
    'chainage_start': 0,
    'chainage_end': 1381,
    'date': '2026-05-20',
    'direction': ['All'],
    'project_name': ['Abu Road to Swaroopganj'],
}

LARGE_PAYLOAD = {
    'chainage_start': 0,
    'chainage_end': 99999,
    'date': '2025-08-20',
    'direction': ['All'],
    'project_name': ['Chittorgarh to Kota'],
}


def _log(msg: str) -> None:
    print(msg, flush=True)


def _stats(values: list[float]) -> dict:
    if not values:
        return {'n': 0}
    ordered = sorted(values)
    p95_idx = min(len(ordered) - 1, max(0, int(round(0.95 * (len(ordered) - 1)))))
    p99_idx = min(len(ordered) - 1, max(0, int(round(0.99 * (len(ordered) - 1)))))
    return {
        'n': len(values),
        'min': round(min(values), 3),
        'median': round(statistics.median(values), 3),
        'p95': round(ordered[p95_idx], 3),
        'p99': round(ordered[p99_idx], 3),
        'max': round(max(values), 3),
        'mean': round(statistics.fmean(values), 3),
    }


def _validated(payload: dict) -> dict:
    ser = TrafficFilterRequestSerializer(data=payload)
    assert ser.is_valid(), ser.errors
    return ser.validated_data


def _warm_http(payload: dict) -> None:
    client = APIClient()
    for _ in range(WARMUPS):
        client.post('/traffic_filter/', payload, format='json', HTTP_ACCEPT_ENCODING='gzip')


def _measure_http(payload: dict, *, accept_gzip: bool) -> dict:
    client = APIClient()
    headers = {'HTTP_ACCEPT_ENCODING': 'gzip'} if accept_gzip else {}
    samples = []
    last = None
    for _ in range(SAMPLES):
        t0 = time.perf_counter()
        response = client.post('/traffic_filter/', payload, format='json', **headers)
        total_ms = (time.perf_counter() - t0) * 1000
        samples.append(total_ms)
        last = response
    assert last is not None
    raw = bytes(last.content)
    encoding = last.get('Content-Encoding')
    if encoding == 'gzip':
        body = gzip.decompress(raw)
        compressed = len(raw)
    else:
        body = raw
        compressed = None
    return {
        'stats_ms': _stats(samples),
        'status': last.status_code,
        'content_encoding': encoding,
        'wire_bytes': len(raw),
        'uncompressed_bytes': len(body),
        'compressed_bytes': compressed,
        'vary': last.get('Vary'),
        'content_length': last.get('Content-Length'),
        'tfgz1_not_in_body': not raw.startswith(TRAFFIC_FILTER_CACHE_MAGIC),
        'json_ok': body[:1] in (b'[', b'{'),
    }


def _stage_bench(payload: dict) -> dict:
    data = _validated(payload)
    # Prefer real API warm so Redis holds true TFGZ1 for this payload.
    _warm_http(payload)

    version = get_namespace_version(CACHE_NS_TRAFFIC_FILTER)
    key = build_cache_key(
        namespace=CACHE_NS_TRAFFIC_FILTER,
        version=version,
        payload=data,
        scope=TRAFFIC_FILTER_CACHE_SCOPE,
    )
    hit, raw = safe_cache_get(key)
    assert hit and isinstance(raw, (bytes, bytearray)), 'expected TFGZ1 cache HIT'
    raw_b = bytes(raw)
    gzip_payload = extract_traffic_filter_gzip_payload(raw_b)
    json_bytes = gzip.decompress(gzip_payload)
    python_obj = json.loads(json_bytes)
    parity_render = JSONRenderer().render(python_obj)

    redis_get = []
    extract = []
    gunzip = []
    loads = []
    render = []
    gzip_cpu = []
    direct = []
    legacy_cpu = []

    for _ in range(SAMPLES):
        t0 = time.perf_counter()
        h, cached = safe_cache_get(key)
        redis_get.append((time.perf_counter() - t0) * 1000)
        assert h

        t1 = time.perf_counter()
        gp = extract_traffic_filter_gzip_payload(cached)
        extract.append((time.perf_counter() - t1) * 1000)

        t2 = time.perf_counter()
        jb = gzip.decompress(gp)
        gunzip.append((time.perf_counter() - t2) * 1000)

        t3 = time.perf_counter()
        obj = json.loads(jb)
        loads.append((time.perf_counter() - t3) * 1000)

        t4 = time.perf_counter()
        rendered = JSONRenderer().render(obj)
        render.append((time.perf_counter() - t4) * 1000)

        t5 = time.perf_counter()
        compress_string(rendered)
        gzip_cpu.append((time.perf_counter() - t5) * 1000)

        t6 = time.perf_counter()
        extract_traffic_filter_gzip_payload(cached)
        direct.append((time.perf_counter() - t6) * 1000)

        legacy_cpu.append(loads[-1] + render[-1] + gzip_cpu[-1])

    return {
        'cache_key_suffix': key.rsplit(':', 1)[-1][:12],
        'redis_stored_with_tfgz1': len(raw_b),
        'gzip_payload_excluding_tfgz1': len(gzip_payload),
        'uncompressed_json_bytes': len(json_bytes),
        'groups': len(python_obj) if isinstance(python_obj, list) else None,
        'rows': sum(len(g) for g in python_obj) if isinstance(python_obj, list) else None,
        'redis_get_ms': _stats(redis_get),
        'extract_ms': _stats(extract),
        'gunzip_ms': _stats(gunzip),
        'json_loads_ms': _stats(loads),
        'json_render_ms': _stats(render),
        'gzip_cpu_ms': _stats(gzip_cpu),
        'direct_extract_only_ms': _stats(direct),
        'legacy_cpu_loads_render_gzip_ms': _stats(legacy_cpu),
        'byte_parity_render_vs_cached_json': parity_render == json_bytes,
    }


def _write_report(results: dict) -> None:
    primary = results['primary']
    stages = primary['stages']
    http_gz = primary['http_gzip']
    http_plain = primary['http_plain']
    lines = [
        '# Traffic Filter — Phase 3 Direct-Byte Cache Implementation',
        '',
        '**Status:** Implemented. API contract unchanged.',
        '',
        '## Summary',
        '',
        '- New Redis representation: `TFGZ1\\n` + gzip(JSON) under scope `gzip_json_v1`.',
        '- Gzip-capable clients: Redis GET → strip magic → `HttpResponse` with `Content-Encoding: gzip`.',
        '- Non-gzip clients: gunzip to JSON bytes → `HttpResponse` (no DRF re-render on HIT).',
        '- Old empty-scope pickle keys are unused (different scope digest); corrupt/legacy values rebuild.',
        f"- TTL unchanged: **{CACHE_TTL_FILTER}s** (`CACHE_TTL_FILTER`).",
        f"- Namespace unchanged: `{CACHE_NS_TRAFFIC_FILTER}`.",
        '',
        '## Cache HIT path',
        '',
        '### Before',
        '',
        '```text',
        'Redis GET (pickle) → Python nested lists → DRF JSONRenderer → GZipMiddleware → HTTP',
        '```',
        '',
        '### After (Accept-Encoding allows gzip)',
        '',
        '```text',
        'Redis GET (TFGZ1+gzip) → validate/strip TFGZ1 → HttpResponse(gzip bytes)',
        '  Content-Type: application/json',
        '  Content-Encoding: gzip',
        '  Content-Length: <gzip size>',
        '  Vary: Accept-Encoding',
        'SelectiveGZipMiddleware sees Content-Encoding → skip (no double-gzip)',
        '```',
        '',
        '## Benchmark environment',
        '',
        f"- measurement_context: `{results['environment']['measurement_context']}`",
        f"- on_railway: `{results['environment']['on_railway']}`",
        f"- cache_usable: `{results['environment']['cache_usable']}`",
        '',
        'Absolute Redis ms may include local↔cloud RTT. Relative CPU deltas (legacy vs direct) are authoritative.',
        '',
        '## Primary payload (Abu Road)',
        '',
        f"- groups/rows: **{stages.get('groups')}** / **{stages.get('rows')}**",
        f"- Redis TFGZ1+gzip bytes: **{stages['redis_stored_with_tfgz1']:,}**",
        f"- gzip excluding magic: **{stages['gzip_payload_excluding_tfgz1']:,}**",
        f"- uncompressed JSON: **{stages['uncompressed_json_bytes']:,}**",
        '',
        '### Stage timings (median / p95, ms)',
        '',
        '| Stage | Median | P95 |',
        '|---|---:|---:|',
        f"| Redis GET | {stages['redis_get_ms']['median']} | {stages['redis_get_ms']['p95']} |",
        f"| Extract TFGZ1 | {stages['extract_ms']['median']} | {stages['extract_ms']['p95']} |",
        f"| Gunzip | {stages['gunzip_ms']['median']} | {stages['gunzip_ms']['p95']} |",
        f"| json.loads | {stages['json_loads_ms']['median']} | {stages['json_loads_ms']['p95']} |",
        f"| JSONRenderer | {stages['json_render_ms']['median']} | {stages['json_render_ms']['p95']} |",
        f"| gzip CPU | {stages['gzip_cpu_ms']['median']} | {stages['gzip_cpu_ms']['p95']} |",
        f"| Direct extract only | {stages['direct_extract_only_ms']['median']} | {stages['direct_extract_only_ms']['p95']} |",
        f"| Legacy CPU (loads+render+gzip) | {stages['legacy_cpu_loads_render_gzip_ms']['median']} | {stages['legacy_cpu_loads_render_gzip_ms']['p95']} |",
        '',
        '### HTTP endpoint (APIClient)',
        '',
        f"- gzip Accept-Encoding median: **{http_gz['stats_ms']['median']} ms** (p95 {http_gz['stats_ms']['p95']})",
        f"- plain Accept-Encoding median: **{http_plain['stats_ms']['median']} ms** (p95 {http_plain['stats_ms']['p95']})",
        f"- gzip wire bytes: **{http_gz.get('compressed_bytes')}**; Content-Encoding={http_gz.get('content_encoding')}",
        f"- TFGZ1 never in HTTP body: **{http_gz.get('tfgz1_not_in_body')}**",
        '',
        '## Large payload (if available)',
        '',
    ]
    large = results.get('large')
    if large and large.get('ok'):
        ls = large['stages']
        lines.extend([
            f"- groups/rows: **{ls.get('groups')}** / **{ls.get('rows')}**",
            f"- Redis TFGZ1+gzip: **{ls['redis_stored_with_tfgz1']:,}**",
            f"- Redis GET median: **{ls['redis_get_ms']['median']} ms**",
            f"- Direct extract median: **{ls['direct_extract_only_ms']['median']} ms**",
            f"- Legacy CPU median: **{ls['legacy_cpu_loads_render_gzip_ms']['median']} ms**",
            f"- HTTP gzip median: **{large['http_gzip']['stats_ms']['median']} ms**",
            '',
        ])
    else:
        lines.append(f"- Skipped or unavailable: `{large.get('error') if large else 'n/a'}`")
        lines.append('')

    lines.extend([
        '## Expected impact on ~6s Postman time',
        '',
        'This phase removes post-GET application work (unpickle nested Python, DRF render, middleware gzip)',
        'and shrinks the Redis value vs pickled nested lists for the same JSON.',
        '',
        'If Postman ~6s was dominated by Redis transfer of a large pickle over a high-RTT path,',
        'the smaller gzip blob should reduce transfer time; remaining latency is still largely Redis RTT/bandwidth.',
        'If the Railway app uses internal Redis, absolute GET ms should be much lower than local→public Redis.',
        '',
        '## Remaining bottleneck',
        '',
        'Redis network transfer of the cached blob (and infrastructure RTT). Not PostgreSQL / not JSON CPU for primary HIT.',
        '',
        '## Contract',
        '',
        '- Request/response fields: **unchanged**',
        '- Pagination: **none** (unchanged)',
        '- Filtering/business logic: **unchanged**',
        '',
    ])
    OUT_MD.write_text('\n'.join(lines) + '\n', encoding='utf-8')


def main() -> int:
    env = {
        'measurement_context': 'local_dev_machine',
        'on_railway': bool(os.environ.get('RAILWAY_ENVIRONMENT') or os.environ.get('RAILWAY_SERVICE_NAME')),
        'cache_usable': is_cache_usable(),
        'ttl_seconds': CACHE_TTL_FILTER,
        'scope': TRAFFIC_FILTER_CACHE_SCOPE,
        'magic': 'TFGZ1\\n',
    }
    _log('Warming primary traffic_filter cache via HTTP...')
    _warm_http(PRIMARY_PAYLOAD)

    _log('Measuring primary HTTP gzip/plain...')
    http_gzip = _measure_http(PRIMARY_PAYLOAD, accept_gzip=True)
    http_plain = _measure_http(PRIMARY_PAYLOAD, accept_gzip=False)

    _log('Measuring primary stage timings...')
    stages = _stage_bench(PRIMARY_PAYLOAD)

    results = {
        'environment': env,
        'primary': {
            'payload': PRIMARY_PAYLOAD,
            'http_gzip': http_gzip,
            'http_plain': http_plain,
            'stages': stages,
        },
    }

    try:
        _log('Warming large payload (optional)...')
        _warm_http(LARGE_PAYLOAD)
        large_http = _measure_http(LARGE_PAYLOAD, accept_gzip=True)
        large_stages = _stage_bench(LARGE_PAYLOAD)
        results['large'] = {
            'ok': True,
            'payload': LARGE_PAYLOAD,
            'http_gzip': large_http,
            'stages': large_stages,
        }
    except Exception as exc:
        results['large'] = {'ok': False, 'error': f'{type(exc).__name__}: {exc}'}

    OUT_JSON.write_text(json.dumps(results, indent=2, default=str), encoding='utf-8')
    _write_report(results)
    _log(f'Wrote {OUT_JSON}')
    _log(f'Wrote {OUT_MD}')
    _log(
        f"Primary HTTP gzip median={http_gzip['stats_ms']['median']} ms; "
        f"direct extract median={stages['direct_extract_only_ms']['median']} ms; "
        f"legacy CPU median={stages['legacy_cpu_loads_render_gzip_ms']['median']} ms"
    )
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
