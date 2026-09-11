"""Controlled E2E validation: inventory_filter page_size=150000.

Does not change frontend defaults. Measures production viability only.

Target:
  project_name = Kothakota to Kurnool
  date = 2026-07-28
  page=1 then page=2, page_size=150000

Usage:
  $env:NHIT_API_BASE_URL="https://nhitramsbackend-production.up.railway.app"
  python tools/measure_inventory_filter_150k.py
"""

from __future__ import annotations

import gzip
import json
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

OUT_JSON = ROOT / 'tools' / 'inventory_filter_150k_validation_results.json'
OUT_MD = ROOT / 'tools' / 'inventory_filter_150k_validation_report.md'

PROJECT = 'Kothakota to Kurnool'
DATE = '2026-07-28'
PAGE_SIZE = 150000
# Prior 100k measured baseline (same filter).
BASELINE_100K = {
    'rows': 100000,
    'uncompressed_json_bytes': 27_000_000,  # approximate from prior report (~27 MB)
    'gzip_bytes': 1_269_383,
    'repeat_total_ms': 2490,
    'repeat_ttfb_ms': 2370,
}
DEFAULT_BASE = 'https://nhitramsbackend-production.up.railway.app'

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None


def _log(msg: str) -> None:
    print(msg, flush=True)


def _base_url() -> str:
    return (os.environ.get('NHIT_API_BASE_URL') or DEFAULT_BASE).strip().rstrip('/')


def _payload(*, page: int, page_size: int = PAGE_SIZE) -> dict[str, Any]:
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


def _header_map(resp_headers: Any) -> dict[str, str]:
    return {str(k).lower(): str(v) for k, v in resp_headers.items()}


def _flatten_row_ids(body: Any) -> list[Any]:
    """Stable identity per row for overlap checks (prefer id-like fields)."""
    ids: list[Any] = []
    if not isinstance(body, list):
        return ids
    for group in body:
        if not isinstance(group, list):
            continue
        for row in group:
            if not isinstance(row, dict):
                continue
            for key in ('id', 'ID', 'asset_id', 'objectid', 'ObjectId', 'uuid'):
                if key in row and row[key] is not None:
                    ids.append(row[key])
                    break
            else:
                # Fallback: full row JSON (order-preserving dump).
                ids.append(json.dumps(row, sort_keys=True, default=str))
    return ids


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


def _measure(session: Any, url: str, payload: dict[str, Any]) -> dict[str, Any]:
    request_id = uuid.uuid4().hex
    headers = {
        'Content-Type': 'application/json',
        'Accept': 'application/json',
        'Accept-Encoding': 'gzip',
        'X-Request-ID': request_id,
    }
    t0 = time.perf_counter()
    resp = session.post(url, json=payload, headers=headers, timeout=900, stream=True)
    t_headers = time.perf_counter()
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
    resp.close()

    h = _header_map(resp.headers)
    encoding = (h.get('content-encoding') or '').lower() or None
    content_length = h.get('content-length')
    uncompressed = wire
    gzip_bytes = None
    if encoding == 'gzip' or wire[:2] == b'\x1f\x8b':
        gzip_bytes = len(wire)
        try:
            uncompressed = gzip.decompress(wire)
        except OSError:
            uncompressed = wire
            gzip_bytes = int(content_length) if content_length and content_length.isdigit() else gzip_bytes

    body: Any
    parse_ms = None
    try:
        t_parse0 = time.perf_counter()
        body = json.loads(uncompressed)
        parse_ms = round((time.perf_counter() - t_parse0) * 1000, 2)
    except Exception as exc:
        body = {'_parse_error': type(exc).__name__}

    return {
        'request_id': h.get('x-inv-perf-request-id') or request_id,
        'status': resp.status_code,
        'total_ms': round((t_done - t0) * 1000, 2),
        'ttfb_ms': round(((first_byte or t_headers) - t0) * 1000, 2),
        'body_transfer_ms': round((t_done - (first_byte or t_headers)) * 1000, 2),
        'json_parse_ms': parse_ms,
        'content_encoding': encoding,
        'content_length_header': content_length,
        'wire_bytes': len(wire),
        'gzip_bytes': gzip_bytes,
        'uncompressed_json_bytes': len(uncompressed) if isinstance(uncompressed, (bytes, bytearray)) else None,
        'headers': {
            'X-Inventory-Paginated': h.get('x-inventory-paginated'),
            'X-Inventory-Page': h.get('x-inventory-page'),
            'X-Inventory-Page-Size': h.get('x-inventory-page-size'),
            'X-Inventory-Row-Count': h.get('x-inventory-row-count'),
            'X-Inventory-Has-More': h.get('x-inventory-has-more'),
            'Content-Type': h.get('content-type'),
            'Vary': h.get('vary'),
        },
        'perf': {
            'cache_hit': h.get('x-inv-perf-cache-hit'),
            'count_cache_hit': h.get('x-inv-perf-count-hit'),
            'response_path': h.get('x-inv-perf-response-path'),
            'server_total_ms': h.get('x-inv-perf-server-total-ms'),
            'page_data_get_ms': h.get('x-inv-perf-page-data-get-ms'),
            'redis_round_trips': h.get('x-inv-perf-redis-round-trips'),
            'debug_headers_present': 'x-inv-perf-server-total-ms' in h,
        },
        'row_count': _count_rows(body),
        'group_count': len(body) if isinstance(body, list) else None,
        'row_ids': _flatten_row_ids(body),
        'is_no_match': isinstance(body, dict) and body.get('message') == 'No match',
        'final_url_path': urlparse(resp.url).path,
    }


def _overlap_report(page1_ids: list[Any], page2_ids: list[Any]) -> dict[str, Any]:
    s1 = set(page1_ids)
    s2 = set(page2_ids)
    overlap = s1 & s2
    return {
        'page1_unique_ids': len(s1),
        'page2_unique_ids': len(s2),
        'page1_row_ids_len': len(page1_ids),
        'page2_row_ids_len': len(page2_ids),
        'overlap_count': len(overlap),
        'page1_duplicate_within': len(page1_ids) - len(s1),
        'page2_duplicate_within': len(page2_ids) - len(s2),
        'no_cross_page_overlap': len(overlap) == 0,
        'page1_ids_unique': len(page1_ids) == len(s1),
        'page2_ids_unique': len(page2_ids) == len(s2),
    }


def _write_md(results: dict[str, Any]) -> None:
    p1a = results.get('page1_request1') or {}
    p1b = results.get('page1_request2') or {}
    p2 = results.get('page2') or {}
    cmp_ = results.get('comparison_vs_100k') or {}
    lines = [
        '# inventory_filter page_size=150000 — Controlled Validation',
        '',
        '**Validation change only: MAX_INVENTORY_FILTER_PAGE_SIZE 100000 → 150000.**',
        'Default page_size remains 15000. Frontend default unchanged.',
        '',
        f"- Host: `{results.get('base_url_host')}`",
        f"- Project: `{PROJECT}` / Date: `{DATE}`",
        f"- page_size: `{PAGE_SIZE}`",
        '',
        '## Page 1 — Request 1',
        '',
        f"- status: `{p1a.get('status')}`",
        f"- total_ms: `{p1a.get('total_ms')}`",
        f"- ttfb_ms: `{p1a.get('ttfb_ms')}`",
        f"- body_transfer_ms: `{p1a.get('body_transfer_ms')}`",
        f"- Content-Encoding: `{p1a.get('content_encoding')}`",
        f"- Content-Length: `{p1a.get('content_length_header')}`",
        f"- gzip_bytes: `{p1a.get('gzip_bytes')}`",
        f"- uncompressed_json_bytes: `{p1a.get('uncompressed_json_bytes')}`",
        f"- row_count: `{p1a.get('row_count')}`",
        f"- headers: `{p1a.get('headers')}`",
        f"- perf: `{p1a.get('perf')}`",
        '',
        '## Page 1 — Request 2 (cache HIT candidate)',
        '',
        f"- status: `{p1b.get('status')}`",
        f"- total_ms: `{p1b.get('total_ms')}`",
        f"- ttfb_ms: `{p1b.get('ttfb_ms')}`",
        f"- body_transfer_ms: `{p1b.get('body_transfer_ms')}`",
        f"- gzip/uncompressed: `{p1b.get('gzip_bytes')}` / `{p1b.get('uncompressed_json_bytes')}`",
        f"- row_count: `{p1b.get('row_count')}`",
        f"- perf: `{p1b.get('perf')}`",
        f"- cache_interpretation: `{results.get('cache_interpretation')}`",
        '',
        '## Page 2',
        '',
        f"- status: `{p2.get('status')}`",
        f"- total_ms: `{p2.get('total_ms')}`",
        f"- ttfb_ms: `{p2.get('ttfb_ms')}`",
        f"- row_count: `{p2.get('row_count')}` (expected ≈ 80523)",
        f"- headers: `{p2.get('headers')}`",
        f"- gzip/uncompressed: `{p2.get('gzip_bytes')}` / `{p2.get('uncompressed_json_bytes')}`",
        '',
        '## Page 1 ↔ Page 2 overlap',
        '',
        f"`{json.dumps(results.get('overlap'))}`",
        '',
        '## Comparison vs prior 100K',
        '',
        f"| Metric | 100K baseline | 150K measured |",
        f"| --- | ---: | ---: |",
        f"| rows (page1) | {BASELINE_100K['rows']} | {cmp_.get('rows_150k')} |",
        f"| gzip_bytes | {BASELINE_100K['gzip_bytes']} | {cmp_.get('gzip_bytes_150k')} |",
        f"| uncompressed_json_bytes | ~{BASELINE_100K['uncompressed_json_bytes']} | {cmp_.get('uncompressed_150k')} |",
        f"| repeat total_ms | ~{BASELINE_100K['repeat_total_ms']} | {cmp_.get('repeat_total_ms_150k')} |",
        f"| repeat ttfb_ms | ~{BASELINE_100K['repeat_ttfb_ms']} | {cmp_.get('repeat_ttfb_ms_150k')} |",
        '',
        '## Recommendation',
        '',
        results.get('recommendation', 'pending'),
        '',
        results.get('recommendation_rationale', ''),
        '',
    ]
    OUT_MD.write_text('\n'.join(lines) + '\n', encoding='utf-8')


def main() -> int:
    if requests is None:
        _log('requests required')
        return 2

    base = _base_url()
    host = urlparse(base).hostname or base
    url = f'{base}/inventory_filter/'
    _log(f'Measuring 150k against {host}')

    session = requests.Session()

    # Boundary probe: 150001 must still reject after deploy.
    reject = _measure(session, url, _payload(page=1, page_size=150001))
    _log(f"reject_150001 status={reject['status']} (expect 400)")

    p1a = _measure(session, url, _payload(page=1))
    _log(
        f"page1#1 status={p1a['status']} rows={p1a['row_count']} "
        f"total={p1a['total_ms']} ttfb={p1a['ttfb_ms']} gzip={p1a['gzip_bytes']} "
        f"hit={p1a['perf'].get('cache_hit')}"
    )
    p1b = _measure(session, url, _payload(page=1))
    _log(
        f"page1#2 status={p1b['status']} rows={p1b['row_count']} "
        f"total={p1b['total_ms']} ttfb={p1b['ttfb_ms']} gzip={p1b['gzip_bytes']} "
        f"hit={p1b['perf'].get('cache_hit')} server={p1b['perf'].get('server_total_ms')} "
        f"page_data_get={p1b['perf'].get('page_data_get_ms')}"
    )
    p2 = _measure(session, url, _payload(page=2))
    _log(
        f"page2 status={p2['status']} rows={p2['row_count']} "
        f"has_more={p2['headers'].get('X-Inventory-Has-More')} "
        f"total={p2['total_ms']}"
    )
    session.close()

    overlap = _overlap_report(p1a.get('row_ids') or [], p2.get('row_ids') or [])
    # Drop heavy id lists from persisted JSON (keep counts only).
    for block in (p1a, p1b, p2):
        block.pop('row_ids', None)

    hit2 = p1b.get('perf', {}).get('cache_hit')
    if hit2 == '1':
        cache_interp = 'CONFIRMED_HIT — X-Inv-Perf-Cache-Hit=1 on request 2'
    elif (
        p1a.get('status') == 200
        and p1b.get('status') == 200
        and p1a.get('total_ms')
        and p1b.get('total_ms')
        and p1b['total_ms'] < p1a['total_ms'] * 0.6
        and (p1a['total_ms'] or 0) > 3000
    ):
        cache_interp = 'LIKELY_HIT_ON_REQUEST2 — materially faster; perf headers absent/incomplete'
    else:
        cache_interp = 'INCONCLUSIVE from timings alone'

    rows_150k = p1b.get('row_count') or p1a.get('row_count')
    gzip_150k = p1b.get('gzip_bytes') or p1a.get('gzip_bytes')
    unc_150k = p1b.get('uncompressed_json_bytes') or p1a.get('uncompressed_json_bytes')
    repeat_total = p1b.get('total_ms')
    repeat_ttfb = p1b.get('ttfb_ms')

    # Recommendation heuristic based on measured warm repeat efficiency.
    # Prefer 150k for bulk if page2 closes dataset in one more call and warm HIT is usable.
    total_rows = None
    try:
        total_rows = int((p1a.get('headers') or {}).get('X-Inventory-Row-Count') or 0) or None
    except ValueError:
        total_rows = None

    recommendation = 'C'
    rationale_parts = []
    if p1a.get('status') != 200 or (p1a.get('headers') or {}).get('X-Inventory-Page-Size') != '150000':
        recommendation = 'A'
        rationale_parts.append('150k not safely accepted in production measurement.')
    elif reject.get('status') != 400:
        recommendation = 'A'
        rationale_parts.append('150001 was not rejected; validation boundary unsafe.')
    elif not overlap.get('no_cross_page_overlap'):
        recommendation = 'A'
        rationale_parts.append('Cross-page pagination overlap detected.')
    else:
        # Efficiency: covering 230523 rows needs 2 pages at 150k vs 3 at 100k.
        pages_100k = 3 if (total_rows or 230523) > 200000 else 2
        pages_150k = 2 if (total_rows or 230523) <= 300000 else 3
        rationale_parts.append(
            f'Warm 150k repeat total_ms={repeat_total}, ttfb={repeat_ttfb}, '
            f'gzip={gzip_150k}, uncompressed={unc_150k}; '
            f'100k baseline gzip≈{BASELINE_100K["gzip_bytes"]}, warm≈{BASELINE_100K["repeat_total_ms"]}ms. '
            f'Pages to cover dataset: ~{pages_150k} at 150k vs ~{pages_100k} at 100k.'
        )
        if overlap.get('page1_duplicate_within'):
            rationale_parts.append(
                f"Note: page1 had {overlap.get('page1_duplicate_within')} within-page identity "
                'collisions; cross-page overlap was still 0.'
            )
        # Default recommendation for a raised max that is heavier than 100k:
        # keep UI at ≤100k, allow 150k for bulk/export.
        recommendation = 'C'
        if repeat_ttfb and repeat_ttfb > 4000:
            rationale_parts.append(
                'Warm TTFB is high for interactive UI; keep UI at ≤100k (or default 15k) '
                'but allow 150k for bulk/export.'
            )
        else:
            rationale_parts.append(
                '150k is technically viable; do not change UI default — allow 150k for bulk/export.'
            )

    results = {
        'base_url_host': host,
        'project': PROJECT,
        'date': DATE,
        'page_size': PAGE_SIZE,
        'reject_150001': {
            'status': reject.get('status'),
            'expected': 400,
            'ok': reject.get('status') == 400,
        },
        'page1_request1': p1a,
        'page1_request2': p1b,
        'page2': p2,
        'overlap': overlap,
        'cache_interpretation': cache_interp,
        'comparison_vs_100k': {
            'rows_150k': rows_150k,
            'gzip_bytes_150k': gzip_150k,
            'uncompressed_150k': unc_150k,
            'repeat_total_ms_150k': repeat_total,
            'repeat_ttfb_ms_150k': repeat_ttfb,
            'baseline_100k': BASELINE_100K,
        },
        'recommendation': recommendation,
        'recommendation_rationale': ' '.join(rationale_parts),
        'options': {
            'A': 'Keep 100K',
            'B': 'Adopt 150K',
            'C': 'Keep 100K for UI but allow 150K for bulk/export use',
        },
    }
    OUT_JSON.write_text(json.dumps(results, indent=2, default=str), encoding='utf-8')
    _write_md(results)
    _log(f'Wrote {OUT_JSON}')
    _log(f'Wrote {OUT_MD}')
    _log(f'RECOMMENDATION={recommendation}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
