"""READ-ONLY 50k inventory_filter performance investigation.

Does not modify production code, schema, Redis config, or API behavior.
"""

from __future__ import annotations

import gzip
import json
import os
import statistics
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'nhit.settings')
os.environ.setdefault('PYTHONUNBUFFERED', '1')

import django

django.setup()

from django.conf import settings
from django.core.cache import cache
from django.db import connection, reset_queries, transaction
from django.test import Client, override_settings
from rest_framework.renderers import JSONRenderer

from inventory.models import InventoryWide
from inventory.serializers import InventoryFilterSerializer
from inventory.services.inventory_filter_queryset import build_inventory_filter_queryset
from inventory.services.inventory_filter_response import (
    INVENTORY_FILTER_CORE_FIELDS,
    build_inventory_filter_groups,
    _QUANTITY_VALUE_KEY,
    _inventory_filter_quantity_annotation,
)
from nhit.caching.constants import (
    CACHE_NS_INVENTORY_FILTER,
    CACHE_TTL_INVENTORY_FILTER,
)
from nhit.caching.inventory_filter_count import (
    get_cached_inventory_filter_count,
    inventory_filter_filter_hash,
)
from nhit.caching.keys import build_cache_key
from nhit.caching.service import (
    get_namespace_version,
    get_or_set_cached_value,
    safe_cache_get,
    safe_cache_set,
)
from nhit.caching.invalidation import invalidate_inventory_filter_cache

PROJECT = 'Kothakota to Kurnool'
PAGE_SIZE = 50000
SAMPLES = 5


def _log(msg: str) -> None:
    print(msg, flush=True)


def _stats(values: list[float]) -> dict[str, float]:
    if not values:
        return {}
    ordered = sorted(values)
    n = len(ordered)
    p95_idx = min(n - 1, max(0, int(round(0.95 * (n - 1)))))
    return {
        'n': n,
        'min': round(ordered[0], 2),
        'median': round(statistics.median(ordered), 2),
        'avg': round(statistics.fmean(ordered), 2),
        'p95': round(ordered[p95_idx], 2),
        'max': round(ordered[-1], 2),
        'samples': [round(v, 2) for v in values],
    }


def _discover_date() -> str:
    from django.db.models import Count

    row = (
        InventoryWide.objects.filter(project_name=PROJECT)
        .exclude(survey_date__isnull=True)
        .values('survey_date')
        .annotate(n=Count('inventory_id'))
        .order_by('-n')
        .first()
    )
    assert row, 'no date for project'
    return row['survey_date'].isoformat()


def _payload(date: str) -> dict[str, Any]:
    return {
        'chainage_start': 0,
        'chainage_end': 99999,
        'date': date,
        'direction': ['All'],
        'project_name': [PROJECT],
        'asset_type': ['All'],
        'page': 1,
        'page_size': PAGE_SIZE,
    }


def _validate(body: dict[str, Any]) -> dict[str, Any]:
    t0 = time.perf_counter()
    ser = InventoryFilterSerializer(data=body)
    assert ser.is_valid(), ser.errors
    ms = (time.perf_counter() - t0) * 1000
    return ser.validated_data, round(ms, 2)


def _phase_build(data: dict[str, Any], *, do_count: bool) -> dict[str, Any]:
    """Build page without Redis; optional COUNT."""
    settings.DEBUG = True
    reset_queries()

    t_qs0 = time.perf_counter()
    queryset = build_inventory_filter_queryset(data)
    qs_ms = (time.perf_counter() - t_qs0) * 1000

    count_ms = None
    total_count = None
    if do_count:
        t_c0 = time.perf_counter()
        total_count = queryset.count()
        count_ms = (time.perf_counter() - t_c0) * 1000

    timings: dict = {}
    t_build0 = time.perf_counter()
    groups = build_inventory_filter_groups(
        queryset,
        offset=(data['page'] - 1) * data['page_size'],
        limit=data['page_size'],
        timings=timings,
    )
    build_ms = (time.perf_counter() - t_build0) * 1000

    t_r0 = time.perf_counter()
    uncompressed = JSONRenderer().render(groups)
    render_ms = (time.perf_counter() - t_r0) * 1000

    t_g0 = time.perf_counter()
    compressed = gzip.compress(uncompressed, compresslevel=6)
    gzip_ms = (time.perf_counter() - t_g0) * 1000

    return {
        'qs_ms': round(qs_ms, 2),
        'count_ms': round(count_ms, 2) if count_ms is not None else None,
        'total_count': total_count,
        'select_ms': timings.get('select_ms'),
        'serialize_ms': timings.get('serialize_ms'),
        'grouping_ms': timings.get('grouping_ms'),
        'row_count': timings.get('row_count'),
        'build_ms': round(build_ms, 2),
        'render_ms': round(render_ms, 2),
        'gzip_ms': round(gzip_ms, 2),
        'uncompressed_bytes': len(uncompressed),
        'compressed_bytes': len(compressed),
        'db_queries': len(connection.queries),
        'groups': groups,
    }


def _http_run(client: Client, body: dict[str, Any]) -> dict[str, Any]:
    settings.DEBUG = True
    reset_queries()
    t0 = time.perf_counter()
    response = client.post(
        '/inventory_filter/',
        data=json.dumps(body, default=str),
        content_type='application/json',
        HTTP_ACCEPT_ENCODING='gzip',
    )
    total_ms = (time.perf_counter() - t0) * 1000
    content = response.content
    encoding = response.get('Content-Encoding')
    if encoding == 'gzip':
        raw = gzip.decompress(content)
        wire = len(content)
        uncomp = len(raw)
    else:
        wire = len(content)
        uncomp = wire
    return {
        'status_code': response.status_code,
        'http_ms': round(total_ms, 2),
        'db_queries': len(connection.queries),
        'encoding': encoding,
        'wire_bytes': wire,
        'uncompressed_bytes': uncomp,
        'row_count_header': response.get('X-Inventory-Row-Count'),
        'page_size_header': response.get('X-Inventory-Page-Size'),
        'has_more': response.get('X-Inventory-Has-More'),
    }


def _page_cache_key(data: dict[str, Any]) -> str:
    version = get_namespace_version(CACHE_NS_INVENTORY_FILTER)
    return build_cache_key(
        namespace=CACHE_NS_INVENTORY_FILTER,
        version=version,
        payload=data,
        scope='',
    )


def _explain_analyze(date: str, data: dict[str, Any]) -> dict[str, Any]:
    """EXPLAIN the real Django values() page query (same as inventory_filter)."""
    queryset = build_inventory_filter_queryset(data)
    values_qs = (
        queryset
        .annotate(**{_QUANTITY_VALUE_KEY: _inventory_filter_quantity_annotation()})
        .order_by('-inventory_id')
        .values(*INVENTORY_FILTER_CORE_FIELDS, _QUANTITY_VALUE_KEY)
    )[:PAGE_SIZE]
    sql, params = values_qs.query.sql_with_params()
    explain_sql = f'EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) {sql}'
    with connection.cursor() as cur:
        cur.execute(explain_sql, params)
        plan = cur.fetchone()[0][0]

    def walk(node, acc=None):
        acc = acc or {'indexes': [], 'nodes': []}
        if isinstance(node, dict):
            acc['nodes'].append(
                {
                    'type': node.get('Node Type'),
                    'index': node.get('Index Name'),
                    'actual_rows': node.get('Actual Rows'),
                    'actual_total_ms': node.get('Actual Total Time'),
                    'shared_hit': node.get('Shared Hit Blocks'),
                    'shared_read': node.get('Shared Read Blocks'),
                    'temp_read': node.get('Temp Read Blocks'),
                    'temp_written': node.get('Temp Written Blocks'),
                }
            )
            if node.get('Index Name'):
                acc['indexes'].append(node['Index Name'])
            for child in node.get('Plans') or []:
                walk(child, acc)
        return acc

    summary = walk(plan.get('Plan', {}))
    return {
        'planning_ms': plan.get('Planning Time'),
        'execution_ms': plan.get('Execution Time'),
        'indexes': summary['indexes'],
        'nodes': summary['nodes'][:12],
        'sql_preview': sql[:500],
    }


def _raw_cursor_split(date: str, samples: int = 5) -> dict[str, Any]:
    # Approximate narrow page columns (without CASE for transfer baseline).
    sql = """
    SELECT inventory_id, "Project Name", "Chainage start", "Chainage end",
           "Direction", "Asset type", "Sub Asset Type", "Latitude", "Longitude",
           "Date", "Carriage Type", "Lane", "Source"
    FROM inventory_wide
    WHERE "Project Name" = %s AND "Date" = %s
      AND "Chainage start" >= 0 AND "Chainage end" <= 99999
    ORDER BY inventory_id DESC
    LIMIT 50000
    """
    execute_ms: list[float] = []
    fetch_ms: list[float] = []
    materialize_ms: list[float] = []
    row_counts: list[int] = []

    for _ in range(samples):
        with connection.cursor() as cur:
            t0 = time.perf_counter()
            cur.execute(sql, [PROJECT, date])
            e = (time.perf_counter() - t0) * 1000
            t1 = time.perf_counter()
            rows = cur.fetchall()
            f = (time.perf_counter() - t1) * 1000
            t2 = time.perf_counter()
            # Cheap materialize: list of tuples already; convert first cols to dict-ish cost proxy
            _ = [{'id': r[0], 'asset': r[5]} for r in rows]
            m = (time.perf_counter() - t2) * 1000
        execute_ms.append(e)
        fetch_ms.append(f)
        materialize_ms.append(m)
        row_counts.append(len(rows))

    # Named cursor split (transfer in fetch)
    named_execute: list[float] = []
    named_fetch: list[float] = []
    for _ in range(samples):
        with transaction.atomic():
            with connection.cursor() as cur:
                # Use server-side cursor via DECLARE
                cur.execute('DECLARE inv50k_cur NO SCROLL CURSOR FOR ' + sql, [PROJECT, date])
                t0 = time.perf_counter()
                # DECLARE is the execute of the portal setup; FETCH does transfer
                # Re-measure: close and use named django cursor if available
        # Prefer psycopg named cursor if present
        try:
            raw = connection.connection
            with raw.transaction():
                with raw.cursor(name='inv_filter_50k_named') as ncur:
                    t0 = time.perf_counter()
                    ncur.execute(sql, [PROJECT, date])
                    e = (time.perf_counter() - t0) * 1000
                    t1 = time.perf_counter()
                    rows = ncur.fetchall()
                    f = (time.perf_counter() - t1) * 1000
                named_execute.append(e)
                named_fetch.append(f)
        except Exception as exc:
            return {
                'unnamed': {
                    'execute_ms': _stats(execute_ms),
                    'fetch_ms': _stats(fetch_ms),
                    'materialize_ms': _stats(materialize_ms),
                    'rows': row_counts[-1] if row_counts else None,
                },
                'named_error': f'{type(exc).__name__}: {exc}',
            }

    return {
        'unnamed': {
            'execute_ms': _stats(execute_ms),
            'fetch_ms': _stats(fetch_ms),
            'materialize_ms': _stats(materialize_ms),
            'rows': row_counts[-1] if row_counts else None,
            'note': 'With unnamed cursor, Neon/psycopg usually transfers during execute().',
        },
        'named': {
            'execute_ms': _stats(named_execute),
            'fetch_ms': _stats(named_fetch),
            'note': 'Named cursor moves transfer into fetchall().',
        },
    }


def _redis_get_set_timing(data: dict[str, Any], groups: list) -> dict[str, Any]:
    key = _page_cache_key(data)
    # ensure miss
    try:
        cache.delete(key)
    except Exception:
        pass
    t0 = time.perf_counter()
    hit, _ = safe_cache_get(key)
    get_miss_ms = (time.perf_counter() - t0) * 1000
    t1 = time.perf_counter()
    ok = safe_cache_set(key, groups, timeout=CACHE_TTL_INVENTORY_FILTER)
    set_ms = (time.perf_counter() - t1) * 1000
    t2 = time.perf_counter()
    hit2, cached = safe_cache_get(key)
    get_hit_ms = (time.perf_counter() - t2) * 1000
    return {
        'cache_key_suffix': key[-16:],
        'get_miss_ms': round(get_miss_ms, 2),
        'get_miss_hit_flag': hit,
        'set_ok': ok,
        'set_ms': round(set_ms, 2),
        'get_hit_ms': round(get_hit_ms, 2),
        'get_hit_flag': hit2,
        'cached_type': type(cached).__name__ if cached is not None else None,
    }


def main() -> int:
    date = _discover_date()
    body = _payload(date)
    data, validate_ms = _validate(body)
    out: dict[str, Any] = {
        'investigation': 'inventory_filter_50k_performance',
        'project': PROJECT,
        'date': date,
        'page_size': PAGE_SIZE,
        'exact_request_payload': body,
        'validated_note': 'direction/asset_type All cleared to [] by serializer',
        'filter_hash': inventory_filter_filter_hash(data),
        'serializer_validate_ms': validate_ms,
        'cache_enabled_env': bool(getattr(settings, 'NHIT_CACHE_ENABLED', False)),
        'cache_ttl_inventory_filter': CACHE_TTL_INVENTORY_FILTER,
        'db_engine': settings.DATABASES['default'].get('ENGINE'),
        'conn_max_age': settings.DATABASES['default'].get('CONN_MAX_AGE'),
    }

    # --- Redis OFF multiphase ---
    _log('=== Redis OFF phase samples ===')
    redis_off_phases = []
    with override_settings(NHIT_CACHE_ENABLED=False):
        client = Client()
        http_off = []
        for i in range(SAMPLES):
            _log(f' redis-off phase {i+1}/{SAMPLES}')
            phase = _phase_build(data, do_count=True)
            # drop groups from stored phase to keep JSON smaller
            phase_store = {k: v for k, v in phase.items() if k != 'groups'}
            phase_store['validate_ms'] = validate_ms
            redis_off_phases.append(phase_store)
            _log(f' redis-off http {i+1}/{SAMPLES}')
            http_off.append(_http_run(client, body))
    out['redis_off'] = {
        'phases': redis_off_phases,
        'phase_stats': {
            'count_ms': _stats([p['count_ms'] for p in redis_off_phases if p.get('count_ms') is not None]),
            'select_ms': _stats([p['select_ms'] for p in redis_off_phases if p.get('select_ms') is not None]),
            'serialize_ms': _stats([p['serialize_ms'] for p in redis_off_phases if p.get('serialize_ms') is not None]),
            'grouping_ms': _stats([p['grouping_ms'] for p in redis_off_phases if p.get('grouping_ms') is not None]),
            'build_ms': _stats([p['build_ms'] for p in redis_off_phases]),
            'render_ms': _stats([p['render_ms'] for p in redis_off_phases]),
            'gzip_ms': _stats([p['gzip_ms'] for p in redis_off_phases]),
        },
        'http': http_off,
        'http_stats': _stats([h['http_ms'] for h in http_off]),
        'payload_bytes_sample': {
            'uncompressed': redis_off_phases[0].get('uncompressed_bytes'),
            'compressed': redis_off_phases[0].get('compressed_bytes'),
        },
    }

    # --- EXPLAIN + raw transfer ---
    _log('=== EXPLAIN ANALYZE ===')
    out['explain_analyze'] = _explain_analyze(date, data)
    _log(f" explain execution_ms={out['explain_analyze'].get('execution_ms')}")

    # Persist redis-off results early in case later stages fail.
    early = ROOT / 'tools' / 'investigate_inventory_filter_50k_partial.json'
    early.write_text(json.dumps({k: out[k] for k in out if k != 'groups'}, indent=2, default=str), encoding='utf-8')
    _log(f'wrote partial {early}')

    _log('=== Raw cursor transfer split ===')
    out['raw_cursor'] = _raw_cursor_split(date, samples=SAMPLES)

    # --- Redis MISS / HIT (only if Redis enabled) ---
    if getattr(settings, 'NHIT_CACHE_ENABLED', False):
        _log('=== Redis MISS / HIT ===')
        # Force miss via namespace bump so we do not fight TTL
        invalidate_inventory_filter_cache()
        client = Client()

        miss_http = []
        hit_http = []
        miss_pipeline = []
        hit_pipeline = []

        for i in range(SAMPLES):
            invalidate_inventory_filter_cache()
            key = _page_cache_key(data)
            try:
                cache.delete(key)
            except Exception:
                pass
            hit_before, _ = safe_cache_get(key)
            assert not hit_before, 'expected cache miss before request'

            _log(f' redis-miss http {i+1}/{SAMPLES}')
            # Instrument pipeline around get_or_set
            timings: dict = {}
            t_pipe0 = time.perf_counter()
            t_get0 = time.perf_counter()
            pre_hit, _ = safe_cache_get(key)
            redis_get_ms = (time.perf_counter() - t_get0) * 1000

            def builder():
                return build_inventory_filter_groups(
                    build_inventory_filter_queryset(data),
                    offset=0,
                    limit=PAGE_SIZE,
                    timings=timings,
                )

            # count separately like view
            t_count0 = time.perf_counter()
            total_count, count_meta = get_cached_inventory_filter_count(
                payload=data,
                builder=lambda: build_inventory_filter_queryset(data).count(),
            )
            count_wall = (time.perf_counter() - t_count0) * 1000

            page_data, page_meta = get_or_set_cached_value(
                namespace=CACHE_NS_INVENTORY_FILTER,
                payload=data,
                builder=builder,
                timeout=CACHE_TTL_INVENTORY_FILTER,
                use_lock=True,
            )
            # measure set already inside helper; approximate with build_ms
            t_render0 = time.perf_counter()
            rendered = JSONRenderer().render(page_data)
            render_ms = (time.perf_counter() - t_render0) * 1000
            t_gz0 = time.perf_counter()
            gz = gzip.compress(rendered, compresslevel=6)
            gzip_ms = (time.perf_counter() - t_gz0) * 1000
            pipe_ms = (time.perf_counter() - t_pipe0) * 1000

            miss_pipeline.append(
                {
                    'pre_hit': pre_hit,
                    'redis_get_ms': round(redis_get_ms, 2),
                    'count_ms_wall': round(count_wall, 2),
                    'count_cache_hit': bool(count_meta.get('cache_hit')),
                    'page_cache_hit': bool(page_meta.get('cache_hit')),
                    'build_ms': page_meta.get('build_ms'),
                    'select_ms': timings.get('select_ms'),
                    'serialize_ms': timings.get('serialize_ms'),
                    'grouping_ms': timings.get('grouping_ms'),
                    'render_ms': round(render_ms, 2),
                    'gzip_ms': round(gzip_ms, 2),
                    'pipeline_ms': round(pipe_ms, 2),
                    'uncompressed_bytes': len(rendered),
                    'compressed_bytes': len(gz),
                    'total_count': total_count,
                }
            )
            miss_http.append(_http_run(client, body))  # this will be HIT if previous set — bump again next loop

            # HIT measurement: same key present
            _log(f' redis-hit pipeline {i+1}/{SAMPLES}')
            t_hit0 = time.perf_counter()
            t_g0 = time.perf_counter()
            hit_flag, cached = safe_cache_get(_page_cache_key(data))
            get_ms = (time.perf_counter() - t_g0) * 1000
            t_r0 = time.perf_counter()
            rendered2 = JSONRenderer().render(cached)
            render2 = (time.perf_counter() - t_r0) * 1000
            t_z0 = time.perf_counter()
            gz2 = gzip.compress(rendered2, compresslevel=6)
            gz2_ms = (time.perf_counter() - t_z0) * 1000
            hit_pipeline.append(
                {
                    'cache_hit': hit_flag,
                    'redis_get_ms': round(get_ms, 2),
                    'render_ms': round(render2, 2),
                    'gzip_ms': round(gz2_ms, 2),
                    'pipeline_ms': round((time.perf_counter() - t_hit0) * 1000, 2),
                    'uncompressed_bytes': len(rendered2),
                    'compressed_bytes': len(gz2),
                    'db_queries_expected': 0,
                }
            )
            # HTTP HIT: do not invalidate
            hit_http.append(_http_run(client, body))

        # Redis get/set microbench using last built page
        last_groups = page_data
        out['redis_micro'] = _redis_get_set_timing(data, last_groups)

        out['redis_miss'] = {
            'pipeline': miss_pipeline,
            'pipeline_stats': {
                'pipeline_ms': _stats([p['pipeline_ms'] for p in miss_pipeline]),
                'select_ms': _stats([p['select_ms'] for p in miss_pipeline if p.get('select_ms') is not None]),
                'build_ms': _stats([p['build_ms'] for p in miss_pipeline if p.get('build_ms') is not None]),
                'serialize_ms': _stats([p['serialize_ms'] for p in miss_pipeline if p.get('serialize_ms') is not None]),
                'render_ms': _stats([p['render_ms'] for p in miss_pipeline]),
                'gzip_ms': _stats([p['gzip_ms'] for p in miss_pipeline]),
                'redis_get_ms': _stats([p['redis_get_ms'] for p in miss_pipeline]),
            },
            'http': miss_http,
            'http_note': 'HTTP samples after pipeline may include HIT contamination; prefer pipeline_stats for MISS.',
        }
        out['redis_hit'] = {
            'pipeline': hit_pipeline,
            'pipeline_stats': {
                'pipeline_ms': _stats([p['pipeline_ms'] for p in hit_pipeline]),
                'redis_get_ms': _stats([p['redis_get_ms'] for p in hit_pipeline]),
                'render_ms': _stats([p['render_ms'] for p in hit_pipeline]),
                'gzip_ms': _stats([p['gzip_ms'] for p in hit_pipeline]),
            },
            'http': hit_http,
            'http_stats': _stats([h['http_ms'] for h in hit_http]),
            'http_db_queries': [h['db_queries'] for h in hit_http],
        }
    else:
        out['redis_miss'] = {'skipped': True, 'reason': 'NHIT_CACHE_ENABLED is False'}
        out['redis_hit'] = {'skipped': True, 'reason': 'NHIT_CACHE_ENABLED is False'}

    path = ROOT / 'tools' / 'investigate_inventory_filter_50k_results.json'
    path.write_text(json.dumps(out, indent=2, default=str), encoding='utf-8')
    _log(f'wrote {path}')
    # compact summary
    summary = {
        'payload': body,
        'redis_off_http_stats': out['redis_off']['http_stats'],
        'redis_off_select_stats': out['redis_off']['phase_stats']['select_ms'],
        'explain_execution_ms': out['explain_analyze'].get('execution_ms'),
        'raw_unnamed_execute': out['raw_cursor']['unnamed']['execute_ms'],
        'bytes': out['redis_off']['payload_bytes_sample'],
    }
    if 'pipeline_stats' in out.get('redis_hit', {}):
        summary['redis_hit_pipeline'] = out['redis_hit']['pipeline_stats']['pipeline_ms']
        summary['redis_hit_http'] = out['redis_hit']['http_stats']
    _log(json.dumps(summary, indent=2, default=str))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
