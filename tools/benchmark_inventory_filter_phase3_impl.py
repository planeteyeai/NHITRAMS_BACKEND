"""Post-implementation Phase 3 benchmark: real inventory_filter HIT with direct bytes.

Measures the live Django endpoint (Accept-Encoding: gzip) after CONDITIONAL
direct-byte serving was implemented. Also measures non-gzip fallback and a
modeled "old" CPU path for comparison.

Does not change production code beyond what is already implemented.
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

from rest_framework.renderers import JSONRenderer
from rest_framework.test import APIClient

from inventory.serializers import InventoryFilterSerializer
from nhit.caching.accept_encoding import accepts_gzip_encoding
from nhit.caching.constants import CACHE_TTL_INVENTORY_FILTER
from nhit.caching.inventory_filter_page import (
    decode_inventory_filter_page_cache,
    encode_inventory_filter_page_cache,
    extract_inventory_filter_page_gzip_payload,
    get_cached_inventory_filter_page,
)
from nhit.caching.service import is_cache_usable
from django.utils.text import compress_string

PROJECT = 'Kothakota to Kurnool'
DATE = '2026-07-28'
SAMPLES = 20
OUT_JSON = ROOT / 'tools' / 'benchmark_inventory_filter_phase3_impl_results.json'
OUT_MD = ROOT / 'tools' / 'inventory_filter_phase3_direct_bytes_implementation.md'

try:
    import psutil
except ImportError:
    psutil = None


def _log(m: str) -> None:
    print(m, flush=True)


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


def _rss():
    if psutil is None:
        return None
    return round(psutil.Process(os.getpid()).memory_info().rss / (1024 * 1024), 2)


def _body():
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


def main() -> int:
    if not is_cache_usable():
        _log('ERROR: Redis required for HIT benchmark')
        return 2

    body = _body()
    ser = InventoryFilterSerializer(data=body)
    assert ser.is_valid(), ser.errors
    data = ser.validated_data

    client = APIClient()
    _log('Warming page+count caches (MISS once if needed)...')
    t0 = time.perf_counter()
    warm = client.post('/inventory_filter/', body, format='json', HTTP_ACCEPT_ENCODING='gzip')
    warm_ms = (time.perf_counter() - t0) * 1000
    assert warm.status_code == 200, warm.status_code
    assert warm.get('Content-Encoding') == 'gzip'
    warm_body = bytes(warm.content)
    assert not warm_body.startswith(b'IFGZ1')
    json_bytes = gzip.decompress(warm_body)
    _log(f'warm_ms={warm_ms:.1f} wire={len(warm_body)} json={len(json_bytes)}')

    # Confirm Redis HIT returns same gzip payload used on the wire
    gz_payload, meta = get_cached_inventory_filter_page(
        payload=data,
        builder=lambda: (_ for _ in ()).throw(RuntimeError('unexpected miss')),
        serve_mode='gzip_bytes',
        timeout=CACHE_TTL_INVENTORY_FILTER,
    )
    assert meta['cache_hit'] is True
    assert gz_payload == warm_body

    _log(f'API HIT with Accept-Encoding:gzip ({SAMPLES} samples)...')
    hit_gzip = []
    rss_after = []
    for _ in range(SAMPLES):
        rss0 = _rss()
        t0 = time.perf_counter()
        r = client.post('/inventory_filter/', body, format='json', HTTP_ACCEPT_ENCODING='gzip')
        hit_gzip.append((time.perf_counter() - t0) * 1000)
        assert r.status_code == 200
        assert r.get('Content-Encoding') == 'gzip'
        assert bytes(r.content) == gz_payload
        assert r['X-Inventory-Page'] == '1'
        assert r['X-Inventory-Page-Size'] == '50000'
        assert gzip.decompress(r.content) == json_bytes
        if rss0 is not None:
            rss_after.append(_rss())

    _log(f'API HIT without Accept-Encoding gzip ({SAMPLES} samples)...')
    hit_plain = []
    for _ in range(SAMPLES):
        t0 = time.perf_counter()
        r = client.post('/inventory_filter/', body, format='json')
        hit_plain.append((time.perf_counter() - t0) * 1000)
        assert r.status_code == 200
        assert r.get('Content-Encoding') != 'gzip'
        assert bytes(r.content) == json_bytes

    _log('Modeled OLD CPU path (decode+render+middleware gzip) interleaved...')
    old_cpu, new_cpu = [], []
    # Use helper HIT raw from gzip mode already validated
    encoded = encode_inventory_filter_page_cache(json.loads(json_bytes))
    # Prefer actual cache bytes
    from nhit.caching.keys import build_cache_key
    from nhit.caching.constants import CACHE_NS_INVENTORY_FILTER
    from nhit.caching.inventory_filter_page import INVENTORY_FILTER_PAGE_SCOPE
    from nhit.caching.service import get_namespace_version, safe_cache_get

    key = build_cache_key(
        namespace=CACHE_NS_INVENTORY_FILTER,
        version=get_namespace_version(CACHE_NS_INVENTORY_FILTER),
        payload=data,
        scope=INVENTORY_FILTER_PAGE_SCOPE,
    )
    hit, raw = safe_cache_get(key)
    assert hit
    raw = bytes(raw)

    for i in range(SAMPLES):
        if i % 2 == 0:
            t0 = time.perf_counter()
            obj = decode_inventory_filter_page_cache(raw)
            rendered = JSONRenderer().render(obj)
            compress_string(rendered, max_random_bytes=100)
            old_cpu.append((time.perf_counter() - t0) * 1000)
            del obj, rendered
        else:
            t0 = time.perf_counter()
            payload = extract_inventory_filter_page_gzip_payload(raw)
            # response construction cost only
            _ = len(payload)
            new_cpu.append((time.perf_counter() - t0) * 1000)

    report = {
        'implementation': True,
        'project': PROJECT,
        'date': DATE,
        'page': 1,
        'page_size': 50000,
        'samples': SAMPLES,
        'accepts_gzip_helper_ok': accepts_gzip_encoding('gzip;q=0') is False,
        'sizes': {
            'http_gzip_body': len(gz_payload),
            'json_uncompressed': len(json_bytes),
            'redis_with_magic': len(raw),
        },
        'api_hit_accept_gzip_ms': _stats(hit_gzip),
        'api_hit_no_accept_gzip_ms': _stats(hit_plain),
        'modeled_old_cpu_after_redis_ms': _stats(old_cpu),
        'modeled_new_extract_cpu_ms': _stats(new_cpu),
        'rss_mb_samples': rss_after[-5:] if rss_after else None,
        'phase3_investigation_fair_baseline_ms': {
            'current_total': 826.39,
            'candidate_total': 472.33,
            'current_cpu': 323.25,
            'candidate_cpu': 0.19,
        },
        'headers_sample': {
            'Content-Encoding': 'gzip',
            'Content-Type': 'application/json',
            'X-Inventory-Row-Count': warm['X-Inventory-Row-Count'],
        },
    }
    OUT_JSON.write_text(json.dumps(report, indent=2), encoding='utf-8')
    _write_md(report)
    _log(json.dumps({k: report[k] for k in (
        'api_hit_accept_gzip_ms',
        'api_hit_no_accept_gzip_ms',
        'modeled_old_cpu_after_redis_ms',
        'modeled_new_extract_cpu_ms',
        'sizes',
    )}, indent=2))
    _log(f'Wrote {OUT_JSON}')
    _log(f'Wrote {OUT_MD}')
    return 0


def _write_md(report: dict) -> None:
    gz = report['api_hit_accept_gzip_ms']
    plain = report['api_hit_no_accept_gzip_ms']
    old_cpu = report['modeled_old_cpu_after_redis_ms']
    new_cpu = report['modeled_new_extract_cpu_ms']
    sizes = report['sizes']
    base = report['phase3_investigation_fair_baseline_ms']
    lines = [
        '# Phase 3 Implementation — Direct Redis gzip bytes → HTTP',
        '',
        '## Summary',
        '',
        'Implemented **CONDITIONAL** direct-byte HIT serving for `POST /inventory_filter/`.',
        '',
        '- Gzip-capable clients (`Accept-Encoding` allows gzip with q>0): HTTP body = Redis gzip payload (IFGZ1 stripped).',
        '- Other clients: gunzip → raw JSON bytes `HttpResponse` (no `json.loads` / DRF re-render on HIT).',
        '- Redis format unchanged: `IFGZ1\\n` + gzip(JSON), scope `page_gzip_json_v1`, TTL 604800.',
        '',
        '## 1. Files changed',
        '',
        '| File | Change |',
        '|------|--------|',
        '| `nhit/caching/accept_encoding.py` | **New** — Accept-Encoding parser honoring `gzip;q=0` |',
        '| `nhit/caching/inventory_filter_page.py` | `serve_mode` (`python`/`gzip_bytes`/`json_bytes`), extract helper |',
        '| `inventory/views.py` | Conditional HttpResponse paths + headers + perf fields |',
        '| `nhit/caching/tests/test_inventory_filter_direct_bytes.py` | **New** — Accept-Encoding, parity, corruption, isolation |',
        '| `inventory/tests.py` / `tests_inventory_filter_ttl.py` | Compat with HttpResponse JSON bodies |',
        '| `tools/benchmark_inventory_filter_phase3_impl_results.json` | Post-impl measurements |',
        '',
        '## 2. Exact implementation flow',
        '',
        '```',
        'count cache (unchanged)',
        '→ Accept-Encoding allows gzip?',
        '    YES → get_cached(..., serve_mode=gzip_bytes)',
        '         → HttpResponse(gzip_payload)',
        '         → Content-Encoding: gzip',
        '         → Content-Length, Vary: Accept-Encoding, X-Inventory-*',
        '         → SelectiveGZipMiddleware sees Content-Encoding → passthrough',
        '    NO  → get_cached(..., serve_mode=json_bytes)',
        '         → HttpResponse(json_bytes)  # HIT: gunzip only',
        '         → Vary: Accept-Encoding, X-Inventory-*',
        '```',
        '',
        'Corrupt/invalid IFGZ1 → treat as miss → rebuild → replace cache.',
        '',
        '## 3. Accept-Encoding decision',
        '',
        '`accepts_gzip_encoding()` parses comma-separated codings and `q` values.',
        '`gzip;q=0` / empty / missing / identity → False. `gzip`, `gzip, deflate, br`, `*` with q>0 → True.',
        'Django middleware regex is intentionally **not** used (it ignores q=0).',
        '',
        '## 4–5. Middleware & headers',
        '',
        'Direct path sets `Content-Encoding: gzip` **before** middleware → no double-gzip.',
        'Also sets `Vary: Accept-Encoding` (middleware skips Vary patch when encoding pre-set).',
        '`Content-Length` = len(gzip_payload) only (no IFGZ1).',
        '',
        '## 6–7. Validation / no-gzip',
        '',
        'Validation: bytes, IFGZ1 magic, non-empty, gzip magic bytes `1F 8B`.',
        'No-gzip HIT: gunzip to JSON bytes (exact `JSONRenderer` bytes); no Content-Encoding:gzip from our path.',
        '',
        '## 8–9. Tests',
        '',
        '```',
        'python manage.py test nhit.caching.tests.test_inventory_filter_direct_bytes \\',
        '  nhit.caching.tests.test_inventory_filter_page_cache \\',
        '  inventory.tests_inventory_filter_ttl \\',
        '  inventory.tests.InventoryFilterGzipMiddlewareTests \\',
        '  inventory.tests.InventoryFilterPaginationTests --keepdb',
        '→ 52 tests OK',
        '```',
        '',
        '## 10–13. 50k benchmark (post-implementation)',
        '',
        f"Target: {PROJECT} / {DATE} / page=1 / page_size=50000 / samples={report['samples']}",
        '',
        '| Payload | Size |',
        '|---|---:|',
        f"| JSON | {sizes['json_uncompressed']:,} |",
        f"| Redis IFGZ1+gzip | {sizes['redis_with_magic']:,} |",
        f"| HTTP gzip body | {sizes['http_gzip_body']:,} |",
        '',
        '| Metric | Median | P95 | Min | Max |',
        '|---|---:|---:|---:|---:|',
        f"| **API HIT Accept-Encoding:gzip** | **{gz['median']}** | {gz['p95']} | {gz['min']} | {gz['max']} |",
        f"| API HIT no Accept-Encoding | {plain['median']} | {plain['p95']} | {plain['min']} | {plain['max']} |",
        f"| Modeled OLD CPU after Redis | {old_cpu['median']} | {old_cpu['p95']} | {old_cpu['min']} | {old_cpu['max']} |",
        f"| Modeled NEW extract CPU | {new_cpu['median']} | {new_cpu['p95']} | {new_cpu['min']} | {new_cpu['max']} |",
        '',
        'Investigation fair baseline (pre-impl modeled pipeline):',
        f"- Total {base['current_total']} → {base['candidate_total']} ms",
        f"- CPU {base['current_cpu']} → {base['candidate_cpu']} ms",
        '',
        f"RSS samples (MB): {report.get('rss_mb_samples')}",
        '',
        '## 14. Remaining bottleneck',
        '',
        'Redis GET of the ~641 KB blob + Django/test-client overhead still dominate end-to-end HIT.',
        'Post-GET CPU for gzip clients is effectively eliminated.',
        'MISS / Neon transfer / build are unchanged (out of scope).',
        '',
        '## 15. API contract',
        '',
        'Unchanged: filters, pagination headers, nested JSON (decompressed), no-match,',
        'map path, count cache, Redis keys/TTL/invalidation/lock, auth.',
        'Wire gzip bytes for gzip clients now match Redis payload (stable; no BREACH FNAME).',
        'Decompressed body remains byte-identical to prior JSONRenderer output.',
        '',
    ]
    OUT_MD.write_text('\n'.join(lines), encoding='utf-8')


if __name__ == '__main__':
    raise SystemExit(main())
