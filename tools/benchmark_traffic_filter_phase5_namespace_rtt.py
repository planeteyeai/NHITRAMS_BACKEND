"""Phase 5 — measure namespace-version RTT elimination for traffic_filter HIT.

Compares:
  CURRENT (legacy sequential): GET version + GET versioned value  (2 RTTs)
  NEW (implemented):           get_many([version, data])          (1 RTT)

Usage:
  python tools/benchmark_traffic_filter_phase5_namespace_rtt.py
"""

from __future__ import annotations

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

from nhit.caching.constants import CACHE_NS_TRAFFIC_FILTER, CACHE_TTL_FILTER
from nhit.caching.keys import build_cache_key
from nhit.caching.service import (
    _version_key,
    get_namespace_version,
    is_cache_usable,
    safe_cache_get,
    safe_cache_get_many,
)
from nhit.caching.traffic_filter_cache import (
    TRAFFIC_FILTER_CACHE_SCOPE,
    build_traffic_filter_data_key,
    encode_traffic_filter_cache,
    get_cached_traffic_filter_response,
    wrap_traffic_filter_entry,
)
from traffic.serializers import TrafficFilterRequestSerializer

SAMPLES = 25
WARMUPS = 5
OUT_JSON = ROOT / 'tools' / 'benchmark_traffic_filter_phase5_namespace_rtt_results.json'
OUT_MD = ROOT / 'tools' / 'traffic_filter_phase5_namespace_rtt_investigation.md'

PRIMARY = {
    'chainage_start': 0,
    'chainage_end': 1381,
    'date': '2026-05-20',
    'direction': ['All'],
    'project_name': ['Abu Road to Swaroopganj'],
}
LARGE = {
    'chainage_start': 0,
    'chainage_end': 99999,
    'date': '2025-08-20',
    'direction': ['All'],
    'project_name': ['Chittorgarh to Kota'],
}


def _log(msg: str) -> None:
    try:
        print(msg, flush=True)
    except UnicodeEncodeError:
        print(msg.encode('ascii', 'replace').decode('ascii'), flush=True)


def _stats(values: list[float]) -> dict:
    if not values:
        return {'n': 0}
    ordered = sorted(values)
    p95 = ordered[min(len(ordered) - 1, max(0, int(round(0.95 * (len(ordered) - 1)))))]
    return {
        'n': len(values),
        'min': round(min(values), 3),
        'median': round(statistics.median(values), 3),
        'p95': round(p95, 3),
        'max': round(max(values), 3),
        'mean': round(statistics.fmean(values), 3),
    }


def _validated(payload: dict) -> dict:
    ser = TrafficFilterRequestSerializer(data=payload)
    assert ser.is_valid(), ser.errors
    return ser.validated_data


def _warm(payload: dict) -> dict:
    data = _validated(payload)

    def builder():
        raise RuntimeError('unexpected miss during phase5 warm')

    # Ensure entry exists via helper (may miss once if cold).
    try:
        get_cached_traffic_filter_response(
            payload=data,
            builder=builder,
            serve_mode='gzip_bytes',
        )
    except RuntimeError:
        from django.db.models import Q
        from traffic.models import Traffic
        from traffic.services.traffic_filter_response import build_traffic_filter_groups

        def real_builder():
            qs = Traffic.objects.select_related('project', 'segment')
            qs = qs.filter(
                segment__chainage_start__gte=data['chainage_start'],
                segment__chainage_end__lte=data['chainage_end'],
                survey_date=data['date'],
            )
            q = Q()
            for name in data['project_name']:
                q |= Q(project__project_name__iexact=name)
            qs = qs.filter(q)
            return build_traffic_filter_groups(qs)

        get_cached_traffic_filter_response(
            payload=data,
            builder=real_builder,
            serve_mode='gzip_bytes',
        )

    body, meta = get_cached_traffic_filter_response(
        payload=data,
        builder=builder,
        serve_mode='gzip_bytes',
    )
    assert meta.get('cache_hit'), meta
    key, digest = build_traffic_filter_data_key(data)
    entry = cache.get(key)
    return {
        'hit': True,
        'redis_round_trips': meta.get('redis_round_trips'),
        'http_body_bytes': meta.get('http_body_bytes') or len(body),
        'data_key_suffix': digest[:12],
        'entry_kind': (entry or {}).get('kind') if isinstance(entry, dict) else None,
        'ns_v': (entry or {}).get('ns_v') if isinstance(entry, dict) else None,
    }


def _measure_case(payload: dict, label: str) -> dict:
    data = _validated(payload)
    warm = _warm(payload)
    version_key = _version_key(CACHE_NS_TRAFFIC_FILTER)
    data_key, digest = build_traffic_filter_data_key(data)

    # Seed a legacy-style versioned key for apples-to-apples sequential probe.
    version = get_namespace_version(CACHE_NS_TRAFFIC_FILTER)
    legacy_key = build_cache_key(
        namespace=CACHE_NS_TRAFFIC_FILTER,
        version=version,
        payload=data,
        scope=TRAFFIC_FILTER_CACHE_SCOPE,
    )
    entry = cache.get(data_key)
    if isinstance(entry, dict) and isinstance(entry.get('blob'), (bytes, bytearray)):
        cache.set(legacy_key, entry['blob'], timeout=CACHE_TTL_FILTER)

    for _ in range(WARMUPS):
        safe_cache_get(version_key)
        safe_cache_get(legacy_key)
        safe_cache_get_many([version_key, data_key])
        get_cached_traffic_filter_response(
            payload=data,
            builder=lambda: (_ for _ in ()).throw(RuntimeError('miss')),
            serve_mode='gzip_bytes',
        )

    legacy_version = []
    legacy_value = []
    legacy_total = []
    many_total = []
    helper_total = []
    helper_rtts = []

    for _ in range(SAMPLES):
        t0 = time.perf_counter()
        safe_cache_get(version_key)
        v_ms = (time.perf_counter() - t0) * 1000
        t1 = time.perf_counter()
        safe_cache_get(legacy_key)
        g_ms = (time.perf_counter() - t1) * 1000
        legacy_version.append(v_ms)
        legacy_value.append(g_ms)
        legacy_total.append(v_ms + g_ms)

        t2 = time.perf_counter()
        safe_cache_get_many([version_key, data_key])
        many_total.append((time.perf_counter() - t2) * 1000)

        t3 = time.perf_counter()
        _, meta = get_cached_traffic_filter_response(
            payload=data,
            builder=lambda: (_ for _ in ()).throw(RuntimeError('miss')),
            serve_mode='gzip_bytes',
        )
        helper_total.append((time.perf_counter() - t3) * 1000)
        helper_rtts.append(meta.get('redis_round_trips'))

    return {
        'label': label,
        'payload': payload,
        'warm': warm,
        'legacy_sequential': {
            'namespace_version_get_ms': _stats(legacy_version),
            'value_get_ms': _stats(legacy_value),
            'total_two_rtt_ms': _stats(legacy_total),
            'redis_round_trips': 2,
        },
        'proposed_get_many': {
            'total_one_rtt_ms': _stats(many_total),
            'redis_round_trips': 1,
        },
        'implemented_helper_hit': {
            'total_ms': _stats(helper_total),
            'redis_round_trips_samples': helper_rtts,
            'redis_round_trips': helper_rtts[-1] if helper_rtts else None,
        },
        'estimated_savings_ms': {
            'median': round(
                statistics.median(legacy_total) - statistics.median(many_total),
                3,
            ),
            'vs_helper_median': round(
                statistics.median(legacy_total) - statistics.median(helper_total),
                3,
            ),
        },
    }


def _write_report(results: dict) -> None:
    p = results['primary']
    l = results['large']
    lines = [
        '# Traffic Filter Phase 5 — Namespace Version RTT Elimination',
        '',
        '**Implemented for traffic_filter only. API contract unchanged. TFGZ1 unchanged.**',
        '',
        '## 1. Current cache-key architecture (before)',
        '',
        '```text',
        'version_key = traffic_filter:version          # Redis GET #1',
        'data_key    = traffic_filter:v{N}:{digest}    # Redis GET #2',
        'digest      = sha256({payload, scope:gzip_json_v1})',
        'value       = TFGZ1\\n + gzip(JSON)',
        '```',
        '',
        'Invalidation: `invalidate_traffic_filter_cache()` → `bump_namespace_version`',
        'increments `traffic_filter:version` so old `v{N}` keys become unreachable.',
        '',
        '## 2. Why namespace version was fetched',
        '',
        'The dynamic version is embedded in the data key. Without reading it first,',
        'the client cannot know which key holds the current generation.',
        '',
        '## 3. Invalidation dependency',
        '',
        '`bump_namespace_version` is the shared invalidation mechanism for all filter',
        'namespaces. Traffic has no model signals, but `invalidate_traffic_filter_cache`',
        'and `invalidate_all_filter_caches` must keep working.',
        '',
        '## 4. Can the separate namespace GET be removed?',
        '',
        '**Yes — safely — by collapsing version+value into one `get_many` round trip.**',
        '',
        'Removing version from the architecture entirely (static key only) would break',
        'invalidation. That option was rejected.',
        '',
        '## 5. Chosen implementation',
        '',
        'Option: **stable data key + generation stamped in entry + get_many**.',
        '',
        '```text',
        'version_key = traffic_filter:version',
        'data_key    = traffic_filter:data:gzip_json_v1:{digest}',
        'entry       = {kind: tfgz1_ns_v1, ns_v: <version>, blob: TFGZ1+gzip}',
        'HIT         = cache.get_many([version_key, data_key])  # 1 RTT',
        '            → serve blob only if entry.ns_v == current version',
        '```',
        '',
        'Invalidation still bumps `traffic_filter:version`; stale entries fail the',
        '`ns_v` check and rebuild. Stampede lock digest unchanged. TTL 300s unchanged.',
        'Inventory/other namespaces untouched.',
        '',
        '## 6. Before/after cache-key format',
        '',
        '| | Before | After |',
        '|--|--|--|',
        '| Data key | `traffic_filter:v{N}:{digest}` | `traffic_filter:data:gzip_json_v1:{digest}` |',
        '| Version key | `traffic_filter:version` | unchanged |',
        '| Value | raw TFGZ1 bytes | `{kind, ns_v, blob: TFGZ1}` |',
        '| TFGZ1 blob | unchanged | unchanged |',
        '',
        '## 7. Before/after HIT flow',
        '',
        'Before: `GET version` → build key → `GET value` → strip TFGZ1 → HttpResponse',
        '',
        'After: `get_many(version, data)` → validate ns_v → strip TFGZ1 → HttpResponse',
        '',
        '## 8–11. Invalidation / stampede / TTL / contract',
        '',
        '- Invalidation: bump namespace version (tested)',
        '- Stampede: existing lock on payload digest (preserved)',
        '- TTL: **300s**',
        '- API contract: **unchanged**',
        '',
        '## 12. Tests',
        '',
        'See `nhit/caching/tests/test_traffic_filter_direct_bytes.py` + contract/perf suites.',
        '',
        '## 13–16. Benchmark results',
        '',
        f"- environment on_railway={results['environment']['on_railway']} "
        f"cache_usable={results['environment']['cache_usable']}",
        '',
        '### Primary',
        '',
        f"- legacy 2-RTT total median: **{p['legacy_sequential']['total_two_rtt_ms']['median']} ms**",
        f"- namespace GET median: **{p['legacy_sequential']['namespace_version_get_ms']['median']} ms**",
        f"- value GET median: **{p['legacy_sequential']['value_get_ms']['median']} ms**",
        f"- get_many 1-RTT median: **{p['proposed_get_many']['total_one_rtt_ms']['median']} ms**",
        f"- helper HIT median: **{p['implemented_helper_hit']['total_ms']['median']} ms** "
        f"(round_trips={p['implemented_helper_hit']['redis_round_trips']})",
        f"- estimated savings (legacy−get_many) median: **{p['estimated_savings_ms']['median']} ms**",
        '',
        '### Large',
        '',
        f"- legacy 2-RTT total median: **{l['legacy_sequential']['total_two_rtt_ms']['median']} ms**",
        f"- get_many 1-RTT median: **{l['proposed_get_many']['total_one_rtt_ms']['median']} ms**",
        f"- helper HIT median: **{l['implemented_helper_hit']['total_ms']['median']} ms** "
        f"(round_trips={l['implemented_helper_hit']['redis_round_trips']})",
        f"- estimated savings median: **{l['estimated_savings_ms']['median']} ms**",
        '',
        '## 17. Remaining bottleneck',
        '',
        'Removing one Redis RTT saves roughly one public-proxy PING (~80–300+ ms locally).',
        'It does **not** by itself explain or eliminate Postman ~6s (still dominated by',
        'client↔Railway HTTP / connection path from Phase 4A).',
        '',
        'Next: measure with `NHIT_API_BASE_URL` or Railway-internal Redis — not another',
        'TFGZ1/JSON/gzip rewrite.',
        '',
    ]
    OUT_MD.write_text('\n'.join(lines) + '\n', encoding='utf-8')


def main() -> int:
    env = {
        'on_railway': bool(
            os.environ.get('RAILWAY_ENVIRONMENT') or os.environ.get('RAILWAY_SERVICE_NAME')
        ),
        'cache_usable': is_cache_usable(),
        'ttl_seconds': CACHE_TTL_FILTER,
    }
    _log(f'Phase 5 env cache_usable={env["cache_usable"]}')
    primary = _measure_case(PRIMARY, 'primary')
    _log(
        f"primary legacy={primary['legacy_sequential']['total_two_rtt_ms']['median']} "
        f"get_many={primary['proposed_get_many']['total_one_rtt_ms']['median']} "
        f"helper={primary['implemented_helper_hit']['total_ms']['median']}"
    )
    large = _measure_case(LARGE, 'large')
    _log(
        f"large legacy={large['legacy_sequential']['total_two_rtt_ms']['median']} "
        f"get_many={large['proposed_get_many']['total_one_rtt_ms']['median']} "
        f"helper={large['implemented_helper_hit']['total_ms']['median']}"
    )
    results = {'environment': env, 'primary': primary, 'large': large}
    OUT_JSON.write_text(json.dumps(results, indent=2, default=str), encoding='utf-8')
    _write_report(results)
    _log(f'Wrote {OUT_JSON}')
    _log(f'Wrote {OUT_MD}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
