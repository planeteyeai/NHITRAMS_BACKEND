"""Controlled E2E measurement: inventory_filter page_size=100000.

MEASUREMENT ONLY — does not modify API, Redis, cache, or DB.

Target:
  project_name = Kothakota to Kurnool
  date = 2026-07-28
  page=1 then page=2, page_size=100000

Usage:
  $env:NHIT_API_BASE_URL="https://nhitramsbackend-production.up.railway.app"
  python tools/measure_inventory_filter_100k.py
"""

from __future__ import annotations

import gzip
import json
import os
import sys
import time
import tracemalloc
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

OUT_JSON = ROOT / 'tools' / 'inventory_filter_100k_validation_results.json'
OUT_MD = ROOT / 'tools' / 'inventory_filter_100k_validation_report.md'

PROJECT = 'Kothakota to Kurnool'
DATE = '2026-07-28'
DEFAULT_BASE = 'https://nhitramsbackend-production.up.railway.app'

try:
    import psutil
except ImportError:  # pragma: no cover
    psutil = None

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None


def _log(msg: str) -> None:
    print(msg, flush=True)


def _base_url() -> str:
    return (os.environ.get('NHIT_API_BASE_URL') or DEFAULT_BASE).strip().rstrip('/')


def _payload(*, page: int, page_size: int = 100000) -> dict[str, Any]:
    return {
        'chainage_start': 0,
        'chainage_end': 99999,
        'date': DATE,
        'direction': ['All'],
        'project_name': [PROJECT],
        'asset_type': ['All'],
        'page': page,
        'page_size': page_size,
    }


def _count_rows(body: Any) -> int | None:
    if not isinstance(body, list):
        return None
    rows = 0
    for group in body:
        if isinstance(group, list):
            rows += len(group)
        else:
            return None
    return rows


def _field_order_sample(body: Any) -> list[str] | None:
    if not isinstance(body, list) or not body:
        return None
    first_group = body[0]
    if not isinstance(first_group, list) or not first_group:
        return None
    row = first_group[0]
    if not isinstance(row, dict):
        return None
    return list(row.keys())


def _measure_http(session: Any, url: str, payload: dict[str, Any], *, accept_gzip: bool) -> dict[str, Any]:
    headers = {
        'Content-Type': 'application/json',
        'Accept': 'application/json',
        'Accept-Encoding': 'gzip' if accept_gzip else 'identity',
    }
    proc = psutil.Process(os.getpid()) if psutil else None
    rss_before = proc.memory_info().rss if proc else None
    tracemalloc.start()
    t0 = time.perf_counter()
    resp = session.post(url, json=payload, headers=headers, timeout=600, stream=True)
    t_headers = time.perf_counter()
    # Preserve wire bytes when possible.
    try:
        resp.raw.decode_content = False
        chunks: list[bytes] = []
        first_byte = None
        while True:
            chunk = resp.raw.read(256 * 1024)
            if not chunk:
                break
            if first_byte is None:
                first_byte = time.perf_counter()
            chunks.append(chunk)
        wire = b''.join(chunks)
    except Exception:
        wire = resp.content
        first_byte = t_headers
    t_done = time.perf_counter()
    current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    rss_after = proc.memory_info().rss if proc else None
    resp.close()

    encoding = (resp.headers.get('Content-Encoding') or '').lower() or None
    content_length = resp.headers.get('Content-Length')
    uncompressed = wire
    gzip_wire = None
    if encoding == 'gzip' or (wire[:2] == b'\x1f\x8b'):
        gzip_wire = len(wire)
        try:
            uncompressed = gzip.decompress(wire)
        except OSError:
            # Library may already have decompressed.
            uncompressed = wire
            gzip_wire = int(content_length) if content_length and content_length.isdigit() else None
    elif content_length and encoding == 'gzip':
        try:
            gzip_wire = int(content_length)
        except ValueError:
            gzip_wire = None

    body: Any
    parse_ms = None
    try:
        t_parse0 = time.perf_counter()
        body = json.loads(uncompressed)
        parse_ms = round((time.perf_counter() - t_parse0) * 1000, 2)
    except Exception as exc:
        body = {'_parse_error': type(exc).__name__}

    return {
        'status': resp.status_code,
        'total_ms': round((t_done - t0) * 1000, 2),
        'ttfb_ms': round(((first_byte or t_headers) - t0) * 1000, 2),
        'body_transfer_ms': round((t_done - (first_byte or t_headers)) * 1000, 2),
        'json_parse_ms': parse_ms,
        'content_encoding': encoding,
        'content_length_header': content_length,
        'wire_bytes': len(wire),
        'gzip_bytes': gzip_wire,
        'uncompressed_json_bytes': len(uncompressed) if isinstance(uncompressed, (bytes, bytearray)) else None,
        'headers': {
            'X-Inventory-Paginated': resp.headers.get('X-Inventory-Paginated'),
            'X-Inventory-Page': resp.headers.get('X-Inventory-Page'),
            'X-Inventory-Page-Size': resp.headers.get('X-Inventory-Page-Size'),
            'X-Inventory-Row-Count': resp.headers.get('X-Inventory-Row-Count'),
            'X-Inventory-Has-More': resp.headers.get('X-Inventory-Has-More'),
            'Content-Type': resp.headers.get('Content-Type'),
            'Vary': resp.headers.get('Vary'),
        },
        'row_count': _count_rows(body),
        'group_count': len(body) if isinstance(body, list) else None,
        'is_no_match': isinstance(body, dict) and body.get('message') == 'No match',
        'field_order_sample': _field_order_sample(body),
        'has_json_page_metadata': (
            isinstance(body, dict) and any(k in body for k in ('page', 'page_size', 'has_more', 'total'))
        ),
        'client_rss_before_bytes': rss_before,
        'client_rss_after_bytes': rss_after,
        'client_rss_delta_bytes': (
            None if rss_before is None or rss_after is None else rss_after - rss_before
        ),
        'tracemalloc_peak_bytes': peak,
        'final_url_path': urlparse(resp.url).path,
        'redirect_count': len(getattr(resp, 'history', []) or []),
    }


def _inprocess_fallback(payload: dict[str, Any], *, accept_gzip: bool) -> dict[str, Any]:
    """Local Django APIClient path if remote rejects page_size=100000 (not yet deployed)."""
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'nhit.settings')
    import django

    django.setup()
    from django.test.utils import override_settings
    from rest_framework.test import APIClient

    client = APIClient()
    headers = {}
    if accept_gzip:
        headers['HTTP_ACCEPT_ENCODING'] = 'gzip'

    proc = psutil.Process(os.getpid()) if psutil else None
    rss_before = proc.memory_info().rss if proc else None
    tracemalloc.start()
    t0 = time.perf_counter()
    with override_settings(NHIT_CACHE_ENABLED=True):
        resp = client.post('/inventory_filter/', payload, format='json', **headers)
    t_done = time.perf_counter()
    current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    rss_after = proc.memory_info().rss if proc else None

    raw = bytes(resp.content)
    encoding = (resp.get('Content-Encoding') or '').lower() or None
    uncompressed = raw
    gzip_wire = None
    if encoding == 'gzip' or raw[:2] == b'\x1f\x8b':
        gzip_wire = len(raw)
        uncompressed = gzip.decompress(raw)
    body = json.loads(uncompressed)
    return {
        'mode': 'django_api_client_inprocess',
        'status': resp.status_code,
        'total_ms': round((t_done - t0) * 1000, 2),
        'ttfb_ms': None,
        'body_transfer_ms': None,
        'json_parse_ms': None,
        'content_encoding': encoding,
        'content_length_header': resp.get('Content-Length'),
        'wire_bytes': len(raw),
        'gzip_bytes': gzip_wire,
        'uncompressed_json_bytes': len(uncompressed),
        'headers': {
            'X-Inventory-Paginated': resp.get('X-Inventory-Paginated'),
            'X-Inventory-Page': resp.get('X-Inventory-Page'),
            'X-Inventory-Page-Size': resp.get('X-Inventory-Page-Size'),
            'X-Inventory-Row-Count': resp.get('X-Inventory-Row-Count'),
            'X-Inventory-Has-More': resp.get('X-Inventory-Has-More'),
            'Content-Type': resp.get('Content-Type'),
            'Vary': resp.get('Vary'),
        },
        'row_count': _count_rows(body),
        'group_count': len(body) if isinstance(body, list) else None,
        'is_no_match': isinstance(body, dict) and body.get('message') == 'No match',
        'field_order_sample': _field_order_sample(body),
        'has_json_page_metadata': False,
        'client_rss_before_bytes': rss_before,
        'client_rss_after_bytes': rss_after,
        'client_rss_delta_bytes': (
            None if rss_before is None or rss_after is None else rss_after - rss_before
        ),
        'tracemalloc_peak_bytes': peak,
        'server_timing': 'not measured (no TRAFFIC/INVENTORY perf log capture in this script)',
        'cache_hit': 'not directly observable from response headers',
    }


def _write_report(results: dict[str, Any]) -> None:
    p1a = results.get('page1_request1') or {}
    p1b = results.get('page1_request2') or {}
    p2 = results.get('page2') or {}
    lines = [
        '# inventory_filter page_size=100000 — Controlled Validation',
        '',
        '**Measurement only — no API/Redis/cache/DB changes.**',
        '',
        f"- Base URL: `{results.get('base_url_host')}`",
        f"- Mode: `{results.get('mode')}`",
        f"- Project: `{PROJECT}`",
        f"- Date: `{DATE}`",
        '',
        '## Page 1 — Request 1 (warm-up / likely MISS)',
        '',
        f"- status: `{p1a.get('status')}`",
        f"- total_ms: `{p1a.get('total_ms')}`",
        f"- ttfb_ms: `{p1a.get('ttfb_ms')}`",
        f"- body_transfer_ms: `{p1a.get('body_transfer_ms')}`",
        f"- Content-Encoding: `{p1a.get('content_encoding')}`",
        f"- Content-Length header: `{p1a.get('content_length_header')}`",
        f"- wire_bytes: `{p1a.get('wire_bytes')}`",
        f"- gzip_bytes: `{p1a.get('gzip_bytes')}`",
        f"- uncompressed_json_bytes: `{p1a.get('uncompressed_json_bytes')}`",
        f"- row_count: `{p1a.get('row_count')}`",
        f"- group_count: `{p1a.get('group_count')}`",
        f"- headers: `{p1a.get('headers')}`",
        f"- json_parse_ms: `{p1a.get('json_parse_ms')}`",
        f"- client_rss_delta_bytes: `{p1a.get('client_rss_delta_bytes')}`",
        f"- tracemalloc_peak_bytes: `{p1a.get('tracemalloc_peak_bytes')}`",
        '',
        '## Page 1 — Request 2 (repeat / HIT candidate)',
        '',
        f"- status: `{p1b.get('status')}`",
        f"- total_ms: `{p1b.get('total_ms')}`",
        f"- ttfb_ms: `{p1b.get('ttfb_ms')}`",
        f"- body_transfer_ms: `{p1b.get('body_transfer_ms')}`",
        f"- Content-Encoding: `{p1b.get('content_encoding')}`",
        f"- wire/gzip/uncompressed: `{p1b.get('wire_bytes')}` / `{p1b.get('gzip_bytes')}` / `{p1b.get('uncompressed_json_bytes')}`",
        f"- row_count: `{p1b.get('row_count')}`",
        f"- headers: `{p1b.get('headers')}`",
        f"- timing_delta_req1_minus_req2_ms: `{results.get('page1_timing_delta_ms')}`",
        f"- cache_interpretation: `{results.get('cache_interpretation')}`",
        '',
        '## Page 2 — page_size=100000',
        '',
        f"- status: `{p2.get('status')}`",
        f"- total_ms: `{p2.get('total_ms')}`",
        f"- row_count: `{p2.get('row_count')}`",
        f"- headers: `{p2.get('headers')}`",
        f"- uncompressed_json_bytes: `{p2.get('uncompressed_json_bytes')}`",
        f"- gzip_bytes: `{p2.get('gzip_bytes')}`",
        '',
        '## Contract',
        '',
        f"- nested list: `{results.get('contract_nested_list')}`",
        f"- JSON pagination metadata absent: `{results.get('contract_no_json_pagination_meta')}`",
        f"- field order sample: `{results.get('field_order_sample')}`",
        f"- X-Inventory headers present: `{results.get('contract_headers_present')}`",
        '',
        '## Server instrumentation',
        '',
        f"- server_total_ms / inventory perf log: `{results.get('server_timing_note')}`",
        f"- peak server memory: `{results.get('server_memory_note')}`",
        '',
        '## Browser safety',
        '',
        f"- `{results.get('browser_note')}`",
        '',
        '## Conclusions',
        '',
        '### A. Backend/API safety',
        results.get('conclusion_backend', 'not measured'),
        '',
        '### B. Redis/cache performance',
        results.get('conclusion_cache', 'not measured'),
        '',
        '### C. Network transfer',
        results.get('conclusion_network', 'not measured'),
        '',
        '### D. Browser/client safety',
        results.get('conclusion_browser', 'not measured'),
        '',
    ]
    OUT_MD.write_text('\n'.join(lines) + '\n', encoding='utf-8')


def main() -> int:
    if requests is None:
        _log('requests not installed')
        return 1

    base = _base_url()
    host = urlparse(base).hostname or base
    url = f'{base}/inventory_filter/'
    _log(f'Measuring against {host} ...')

    session = requests.Session()
    results: dict[str, Any] = {
        'base_url_host': host,
        'endpoint': '/inventory_filter/',
        'project': PROJECT,
        'date': DATE,
        'page_size': 100000,
        'browser_note': 'Not measured — no Chrome automation run in this session.',
        'server_timing_note': 'Not captured — Railway log scrape not performed in this script.',
        'server_memory_note': 'Not measured — no in-container memory sampler attached.',
    }

    # Probe whether 100k is accepted remotely.
    probe = _measure_http(session, url, _payload(page=1), accept_gzip=True)
    if probe['status'] == 400:
        _log('Remote rejected page_size=100000 (likely not deployed). Falling back to in-process APIClient.')
        results['mode'] = 'inprocess_fallback_after_remote_400'
        results['remote_reject'] = {
            'status': probe['status'],
            'note': 'Remote validation rejected 100000; measuring local code path instead.',
        }
        p1 = _payload(page=1)
        r1 = _inprocess_fallback(p1, accept_gzip=True)
        r2 = _inprocess_fallback(p1, accept_gzip=True)
        r3 = _inprocess_fallback(_payload(page=2), accept_gzip=True)
        results['page1_request1'] = r1
        results['page1_request2'] = r2
        results['page2'] = r3
    else:
        results['mode'] = 'external_http'
        # Use probe as request 1; then immediate repeat as request 2.
        results['page1_request1'] = {**probe, 'label': 'request1'}
        _log(f"req1 status={probe['status']} total_ms={probe['total_ms']} rows={probe['row_count']}")
        r2 = _measure_http(session, url, _payload(page=1), accept_gzip=True)
        results['page1_request2'] = {**r2, 'label': 'request2'}
        _log(f"req2 status={r2['status']} total_ms={r2['total_ms']} rows={r2['row_count']}")
        r3 = _measure_http(session, url, _payload(page=2), accept_gzip=True)
        results['page2'] = {**r3, 'label': 'page2'}
        _log(f"page2 status={r3['status']} total_ms={r3['total_ms']} rows={r3['row_count']}")

    session.close()

    r1 = results['page1_request1']
    r2 = results['page1_request2']
    r3 = results['page2']
    if r1.get('total_ms') is not None and r2.get('total_ms') is not None:
        results['page1_timing_delta_ms'] = round(r1['total_ms'] - r2['total_ms'], 2)
    else:
        results['page1_timing_delta_ms'] = None

    # Cache interpretation from timings only (no cache header on this API).
    if (
        r1.get('status') == 200
        and r2.get('status') == 200
        and r1.get('total_ms')
        and r2.get('total_ms')
        and r2['total_ms'] < r1['total_ms'] * 0.6
        and r1['total_ms'] > 2000
    ):
        results['cache_interpretation'] = (
            'LIKELY_HIT_ON_REQUEST2 — second call materially faster; '
            'cache HIT not confirmed by response header (API does not expose it).'
        )
    elif r1.get('status') == 200 and r2.get('status') == 200 and abs((r1.get('total_ms') or 0) - (r2.get('total_ms') or 0)) < 500:
        results['cache_interpretation'] = (
            'INCONCLUSIVE_OR_LIKELY_MISS — similar timings on both calls; '
            'may indicate cache not persisting or both HIT already warm.'
        )
    else:
        results['cache_interpretation'] = 'INCONCLUSIVE'

    results['contract_nested_list'] = isinstance(r1.get('row_count'), int) and r1.get('group_count') is not None
    results['contract_no_json_pagination_meta'] = not bool(r1.get('has_json_page_metadata'))
    results['field_order_sample'] = r1.get('field_order_sample')
    hdr = r1.get('headers') or {}
    results['contract_headers_present'] = all(
        hdr.get(k) is not None
        for k in (
            'X-Inventory-Paginated',
            'X-Inventory-Page',
            'X-Inventory-Page-Size',
            'X-Inventory-Row-Count',
            'X-Inventory-Has-More',
        )
    )

    ok = r1.get('status') == 200 and r1.get('headers', {}).get('X-Inventory-Page-Size') == '100000'
    results['conclusion_backend'] = (
        f"{'PASS' if ok else 'FAIL/PARTIAL'}: status={r1.get('status')}, "
        f"page_size header={hdr.get('X-Inventory-Page-Size')}, rows={r1.get('row_count')}, "
        f"groups={r1.get('group_count')}, no_match={r1.get('is_no_match')}."
    )
    results['conclusion_cache'] = results['cache_interpretation']
    results['conclusion_network'] = (
        f"gzip_bytes={r1.get('gzip_bytes')}, uncompressed={r1.get('uncompressed_json_bytes')}, "
        f"transfer_ms={r1.get('body_transfer_ms')}, total_ms={r1.get('total_ms')}."
    )
    results['conclusion_browser'] = (
        'NOT MEASURED in Chrome. Client-side Python parse of the JSON took '
        f"{r1.get('json_parse_ms')} ms in this script; browser Angular/DOM cost unknown. "
        'Do not treat backend success as browser-safe.'
    )

    OUT_JSON.write_text(json.dumps(results, indent=2, default=str), encoding='utf-8')
    _write_report(results)
    _log(f'Wrote {OUT_JSON}')
    _log(f'Wrote {OUT_MD}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
