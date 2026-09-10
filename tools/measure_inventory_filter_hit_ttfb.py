"""Warm-HIT TTFB breakdown for inventory_filter page_size=100000.

MEASUREMENT ONLY — does not change cache keys, TTL, Redis config, or API contract.

Requires production deploy with INVENTORY_FILTER_PERF_DEBUG=true so response
carries X-Inv-Perf-* timing headers (default remains false).

Target:
  project_name = Kothakota to Kurnool
  date = 2026-07-28
  page=1, page_size=100000

Usage:
  $env:NHIT_API_BASE_URL="https://nhitramsbackend-production.up.railway.app"
  python tools/measure_inventory_filter_hit_ttfb.py
"""

from __future__ import annotations

import gzip
import json
import math
import os
import statistics
import sys
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

OUT_JSON = ROOT / 'tools' / 'inventory_filter_hit_ttfb_results.json'
OUT_MD = ROOT / 'tools' / 'inventory_filter_hit_ttfb_investigation.md'

PROJECT = 'Kothakota to Kurnool'
DATE = '2026-07-28'
DEFAULT_BASE = 'https://nhitramsbackend-production.up.railway.app'
WARM_REQUESTS = 2
SAMPLE_REQUESTS = 20

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None


def _log(msg: str) -> None:
    print(msg, flush=True)


def _base_url() -> str:
    return (os.environ.get('NHIT_API_BASE_URL') or DEFAULT_BASE).strip().rstrip('/')


def _payload() -> dict[str, Any]:
    return {
        'chainage_start': 0,
        'chainage_end': 99999,
        'date': DATE,
        'direction': ['All'],
        'project_name': [PROJECT],
        'asset_type': ['All'],
        'page': 1,
        'page_size': 100000,
    }


def _pct(values: list[float], p: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return round(ordered[0], 2)
    k = (len(ordered) - 1) * (p / 100.0)
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return round(ordered[int(k)], 2)
    return round(ordered[f] + (ordered[c] - ordered[f]) * (k - f), 2)


def _stats(values: list[float | None]) -> dict[str, Any]:
    clean = [float(v) for v in values if v is not None]
    if not clean:
        return {'n': 0, 'median': None, 'p95': None, 'mean': None, 'min': None, 'max': None}
    return {
        'n': len(clean),
        'median': _pct(clean, 50),
        'p95': _pct(clean, 95),
        'mean': round(statistics.fmean(clean), 2),
        'min': round(min(clean), 2),
        'max': round(max(clean), 2),
    }


def _f(headers: dict[str, str], name: str) -> float | None:
    raw = headers.get(name)
    if raw is None or raw == '':
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _i(headers: dict[str, str], name: str) -> int | None:
    raw = headers.get(name)
    if raw is None or raw == '':
        return None
    try:
        return int(float(raw))
    except ValueError:
        return None


def _measure(session: Any, url: str, payload: dict[str, Any], *, label: str) -> dict[str, Any]:
    request_id = uuid.uuid4().hex
    headers = {
        'Content-Type': 'application/json',
        'Accept': 'application/json',
        'Accept-Encoding': 'gzip',
        'X-Request-ID': request_id,
    }
    t0 = time.perf_counter()
    resp = session.post(url, json=payload, headers=headers, timeout=600, stream=True)
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

    h = {k: v for k, v in resp.headers.items()}
    encoding = (h.get('Content-Encoding') or '').lower() or None
    content_length = h.get('Content-Length')
    gzip_bytes = len(wire) if encoding == 'gzip' or wire[:2] == b'\x1f\x8b' else None
    if gzip_bytes is None and content_length and content_length.isdigit():
        gzip_bytes = int(content_length)

    count_v = _f(h, 'X-Inv-Perf-Count-Version-Get-Ms')
    count_d = _f(h, 'X-Inv-Perf-Count-Data-Get-Ms')
    page_v = _f(h, 'X-Inv-Perf-Page-Version-Get-Ms')
    page_d = _f(h, 'X-Inv-Perf-Page-Data-Get-Ms')
    redis_parts = [x for x in (count_v, count_d, page_v, page_d) if x is not None]
    redis_total = round(sum(redis_parts), 2) if redis_parts else None
    server_total = _f(h, 'X-Inv-Perf-Server-Total-Ms')
    ttfb = round(((first_byte or t_headers) - t0) * 1000, 2)
    python_processing = None
    if server_total is not None and redis_total is not None:
        # Remainder after Redis GETs (IFGZ extract + Django overhead + headers).
        other = round(server_total - redis_total, 2)
        extract = _f(h, 'X-Inv-Perf-Ifgz-Extract-Ms') or 0.0
        python_processing = round(max(0.0, other - extract), 2)

    return {
        'label': label,
        'request_id': h.get('X-Inv-Perf-Request-Id') or request_id,
        'status': resp.status_code,
        'cache_hit': h.get('X-Inv-Perf-Cache-Hit'),
        'count_cache_hit': h.get('X-Inv-Perf-Count-Hit'),
        'response_path': h.get('X-Inv-Perf-Response-Path'),
        'server_total_ms': server_total,
        'ttfb_ms': ttfb,
        'body_transfer_ms': round((t_done - (first_byte or t_headers)) * 1000, 2),
        'total_ms': round((t_done - t0) * 1000, 2),
        'count_version_get_ms': count_v,
        'count_data_get_ms': count_d,
        'page_version_get_ms': page_v,
        'page_data_get_ms': page_d,
        'redis_total_ms': redis_total,
        'redis_round_trips': _i(h, 'X-Inv-Perf-Redis-Round-Trips'),
        'cache_decode_ms': _f(h, 'X-Inv-Perf-Cache-Decode-Ms'),
        'ifgz_extract_ms': _f(h, 'X-Inv-Perf-Ifgz-Extract-Ms'),
        'json_parse_ms': _f(h, 'X-Inv-Perf-Json-Parse-Ms'),
        'json_render_ms': _f(h, 'X-Inv-Perf-Json-Render-Ms'),
        'gzip_ms': _f(h, 'X-Inv-Perf-Gzip-Ms'),
        'python_processing_ms': python_processing,
        'content_length_header': content_length or h.get('X-Inv-Perf-Content-Length'),
        'wire_bytes': len(wire),
        'gzip_bytes': gzip_bytes,
        'content_encoding': encoding,
        'redis_host_class': h.get('X-Inv-Perf-Redis-Host-Class'),
        'using_internal_redis': h.get('X-Inv-Perf-Using-Internal-Redis'),
        'redis_scheme': h.get('X-Inv-Perf-Redis-Scheme'),
        'redis_port': h.get('X-Inv-Perf-Redis-Port'),
        'worker_pid': h.get('X-Inv-Perf-Worker-Pid'),
        'pool_created': h.get('X-Inv-Perf-Pool-Created'),
        'pool_available': h.get('X-Inv-Perf-Pool-Available'),
        'redis_payload_bytes': _i(h, 'X-Inv-Perf-Redis-Payload-Bytes'),
        'row_count_header': h.get('X-Inventory-Row-Count'),
        'page_size_header': h.get('X-Inventory-Page-Size'),
        'debug_headers_present': 'X-Inv-Perf-Server-Total-Ms' in h,
        'final_url_path': urlparse(resp.url).path,
    }


def _table_row(name: str, stats: dict[str, Any]) -> str:
    med = stats.get('median')
    p95 = stats.get('p95')
    med_s = 'n/a' if med is None else f'{med:.2f}'
    p95_s = 'n/a' if p95 is None else f'{p95:.2f}'
    return f'| {name:<28} | {med_s:>10} | {p95_s:>10} |'


def main() -> int:
    if requests is None:
        _log('ERROR: requests is required')
        return 2

    base = _base_url()
    url = f'{base}/inventory_filter/'
    payload = _payload()
    _log(f'BASE={base}')
    _log(f'TARGET={PROJECT} / {DATE} page=1 page_size=100000')
    _log(f'Warm={WARM_REQUESTS} samples={SAMPLE_REQUESTS}')

    session = requests.Session()
    warm: list[dict[str, Any]] = []
    samples: list[dict[str, Any]] = []

    for i in range(WARM_REQUESTS):
        row = _measure(session, url, payload, label=f'warm_{i+1}')
        warm.append(row)
        _log(
            f"warm[{i+1}] status={row['status']} hit={row['cache_hit']} "
            f"server={row['server_total_ms']} ttfb={row['ttfb_ms']} "
            f"path={row['response_path']} debug={row['debug_headers_present']}"
        )
        if not row['debug_headers_present']:
            _log(
                'ERROR: X-Inv-Perf-* headers missing. '
                'Deploy instrumentation and set INVENTORY_FILTER_PERF_DEBUG=true.'
            )
            OUT_JSON.write_text(json.dumps({'error': 'debug_headers_missing', 'warm': warm}, indent=2), encoding='utf-8')
            return 1

    for i in range(SAMPLE_REQUESTS):
        row = _measure(session, url, payload, label=f'sample_{i+1}')
        samples.append(row)
        _log(
            f"sample[{i+1:02d}] hit={row['cache_hit']} rtts={row['redis_round_trips']} "
            f"redis_total={row['redis_total_ms']} page_data={row['page_data_get_ms']} "
            f"server={row['server_total_ms']} ttfb={row['ttfb_ms']} pid={row['worker_pid']}"
        )

    hit_samples = [s for s in samples if s.get('cache_hit') == '1']
    if len(hit_samples) < max(5, SAMPLE_REQUESTS // 2):
        _log(f'WARNING: only {len(hit_samples)}/{len(samples)} reported cache HIT')

    use = hit_samples or samples

    components = {
        'Redis namespace GET (count)': _stats([s.get('count_version_get_ms') for s in use]),
        'Redis data GET (count)': _stats([s.get('count_data_get_ms') for s in use]),
        'Redis namespace GET (page)': _stats([s.get('page_version_get_ms') for s in use]),
        'Redis data GET (page)': _stats([s.get('page_data_get_ms') for s in use]),
        'Redis total': _stats([s.get('redis_total_ms') for s in use]),
        'Cache decode / IFGZ extract': _stats([s.get('ifgz_extract_ms') for s in use]),
        'JSON parsing': _stats([s.get('json_parse_ms') for s in use]),
        'JSON rendering': _stats([s.get('json_render_ms') for s in use]),
        'Gzip': _stats([s.get('gzip_ms') for s in use]),
        'Python processing (residual)': _stats([s.get('python_processing_ms') for s in use]),
        'Server total': _stats([s.get('server_total_ms') for s in use]),
        'Network/TTFB (client)': _stats([s.get('ttfb_ms') for s in use]),
        'Body transfer (client)': _stats([s.get('body_transfer_ms') for s in use]),
    }

    server_med = components['Server total']['median']
    redis_med = components['Redis total']['median']
    redis_pct = None
    if server_med and redis_med is not None and server_med > 0:
        redis_pct = round(100.0 * redis_med / server_med, 1)

    rtts = sorted({s.get('redis_round_trips') for s in use if s.get('redis_round_trips') is not None})
    paths = sorted({s.get('response_path') for s in use if s.get('response_path')})
    host_classes = sorted({s.get('redis_host_class') for s in use if s.get('redis_host_class')})
    internal_flags = sorted({s.get('using_internal_redis') for s in use if s.get('using_internal_redis') is not None})
    pids = [s.get('worker_pid') for s in use if s.get('worker_pid')]
    pool_created = [s.get('pool_created') for s in use if s.get('pool_created') is not None]

    # Cold vs warm Redis: first warm request vs later HIT samples (same worker if possible).
    first_warm = warm[0] if warm else None
    steady = use
    connection_reuse = {
        'first_warm_page_data_get_ms': (first_warm or {}).get('page_data_get_ms'),
        'first_warm_redis_total_ms': (first_warm or {}).get('redis_total_ms'),
        'first_warm_worker_pid': (first_warm or {}).get('worker_pid'),
        'first_warm_pool_created': (first_warm or {}).get('pool_created'),
        'steady_page_data_get_ms': components['Redis data GET (page)'],
        'steady_redis_total_ms': components['Redis total'],
        'worker_pids_seen': sorted(set(pids)),
        'pool_created_values': sorted(set(pool_created)),
        'note': (
            'django-redis/redis-py reuse connections via ConnectionPool. '
            'pool_created staying flat across requests indicates reuse; '
            'first request after a new worker PID may include TCP connect cost.'
        ),
    }

    recommendations = []
    page_data_med = components['Redis data GET (page)']['median']
    extract_med = components['Cache decode / IFGZ extract']['median']
    if page_data_med is not None and server_med and page_data_med / server_med >= 0.5:
        recommendations.append(
            {
                'rank': 1,
                'impact': 'highest',
                'item': 'Reduce Redis page-blob GET cost / payload size or fetch path',
                'evidence': f'page_data_get median={page_data_med}ms of server_total median={server_med}ms',
            }
        )
    if rtts and max(rtts) >= 4:
        recommendations.append(
            {
                'rank': 2 if recommendations else 1,
                'impact': 'high',
                'item': 'Collapse warm-HIT Redis round trips (count+page each do version GET + data GET → 4 RTTs)',
                'evidence': f'redis_round_trips observed={rtts}',
            }
        )
    if extract_med is not None and extract_med < 5 and page_data_med and page_data_med > 200:
        recommendations.append(
            {
                'rank': len(recommendations) + 1,
                'impact': 'medium',
                'item': 'Keep direct_gzip_bytes path; IFGZ extract is cheap vs Redis transfer',
                'evidence': f'ifgz_extract median={extract_med}ms vs page_data_get={page_data_med}ms',
            }
        )
    recommendations.append(
        {
            'rank': len(recommendations) + 1,
            'impact': 'ops',
            'item': 'Turn INVENTORY_FILTER_PERF_DEBUG back to false after capture',
            'evidence': 'debug headers/logs add overhead and expose internal timings',
        }
    )

    results = {
        'base_url': base,
        'project': PROJECT,
        'date': DATE,
        'page': 1,
        'page_size': 100000,
        'warm_requests': warm,
        'sample_requests': samples,
        'hit_sample_count': len(hit_samples),
        'components': components,
        'redis_percent_of_server_total_median': redis_pct,
        'redis_round_trips_observed': rtts,
        'response_paths_observed': paths,
        'redis_host_class_observed': host_classes,
        'using_internal_redis_observed': internal_flags,
        'connection_reuse': connection_reuse,
        'recommendations': recommendations,
        'primary_question': (
            'Why does a warm 100K inventory_filter cache HIT still take ~2.4s TTFB '
            'when the gzip response is only ~1.27 MB?'
        ),
    }

    OUT_JSON.write_text(json.dumps(results, indent=2), encoding='utf-8')

    lines = [
        '# inventory_filter warm HIT TTFB investigation',
        '',
        '**Measurement only — no production behavior changes in this report.**',
        '',
        f'- Host: `{urlparse(base).netloc}`',
        f'- Filter: `{PROJECT}` / `{DATE}` page=1 page_size=100000',
        f'- Warm requests: {WARM_REQUESTS}; timed HIT samples: {len(use)} (of {len(samples)} total)',
        f'- Response path(s): {", ".join(paths) or "n/a"}',
        f'- Redis round trips on HIT: {rtts or "n/a"}',
        f'- Redis host class: {", ".join(host_classes) or "n/a"}',
        f'- Using internal Redis flag: {", ".join(internal_flags) or "n/a"}',
        f'- Redis % of server_total (median): **{redis_pct}%**' if redis_pct is not None else '- Redis % of server_total: n/a',
        '',
        '## Timing table (HIT samples)',
        '',
        '| Component                    |     Median |        P95 |',
        '| ---------------------------- | ---------: | ---------: |',
    ]
    for name, st in components.items():
        lines.append(_table_row(name, st))

    lines.extend(
        [
            '',
            '## Redis operations on warm HIT (from instrumentation + code path)',
            '',
            'On a warm count+page HIT the request performs **4 Redis GETs**:',
            '',
            '1. Count namespace/version GET',
            '2. Count data GET',
            '3. Page namespace/version GET (same namespace, separate call)',
            '4. Page data GET (IFGZ1 + gzip JSON blob ≈ response size)',
            '',
            'Observed `redis_round_trips` header values: '
            + (', '.join(str(x) for x in rtts) if rtts else 'n/a'),
            '',
            '## Connection reuse',
            '',
            f"- First warm page_data_get_ms: {connection_reuse['first_warm_page_data_get_ms']}",
            f"- Steady page_data_get median: {components['Redis data GET (page)']['median']}",
            f"- Worker PIDs seen: {connection_reuse['worker_pids_seen']}",
            f"- Pool created values: {connection_reuse['pool_created_values']}",
            f"- Note: {connection_reuse['note']}",
            '',
            '## Primary answer',
            '',
        ]
    )

    if redis_pct is not None and redis_pct >= 60:
        lines.append(
            f'Warm HIT server time is dominated by **Redis work** '
            f'(~{redis_pct}% of median server_total), especially the **page data GET** '
            f'of the ~1.27 MB gzip cache blob (median {page_data_med} ms). '
            'Client body transfer after TTFB is comparatively small.'
        )
    elif server_med is not None:
        lines.append(
            f'Median server_total={server_med} ms; Redis total median={redis_med} ms '
            f'({redis_pct}%); page_data_get median={page_data_med} ms; '
            f'client TTFB median={components["Network/TTFB (client)"]["median"]} ms.'
        )
    else:
        lines.append('Insufficient server timings to conclude.')

    lines.extend(['', '## Ranked recommendations (do not implement in this task)', ''])
    for rec in recommendations:
        lines.append(
            f"{rec['rank']}. **[{rec['impact']}]** {rec['item']}  \n   Evidence: {rec['evidence']}"
        )

    lines.extend(
        [
            '',
            '## Artifacts',
            '',
            f'- `{OUT_JSON.name}`',
            f'- `{OUT_MD.name}`',
            '',
        ]
    )
    OUT_MD.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    _log(f'Wrote {OUT_JSON}')
    _log(f'Wrote {OUT_MD}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
