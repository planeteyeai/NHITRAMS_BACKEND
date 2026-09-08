"""Phase 4 — Neon ↔ app transfer investigation (read-only, no prod changes).

Measures WHERE wall-clock time goes between PostgreSQL execute (~ms) and
Django receiving rows (seconds). Does not modify settings, schema, or APIs.

Usage:
    set PYTHONUNBUFFERED=1
    python -u tools/investigate_inventory_filter_phase4_transfer.py

Secrets are never printed (hostnames redacted to suffix + endpoint kind).
"""

from __future__ import annotations

import gzip
import json
import os
import re
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
from django.db import connection, connections
from rest_framework.renderers import JSONRenderer

from inventory.serializers import InventoryFilterSerializer
from inventory.services.inventory_filter_queryset import build_inventory_filter_queryset
from inventory.services.inventory_filter_response import (
    INVENTORY_FILTER_CORE_FIELDS,
    _QUANTITY_VALUE_KEY,
    _inventory_filter_quantity_annotation,
    build_inventory_filter_groups,
    serialize_inventory_wide_row,
    group_serialized_rows,
)

PAGE_SIZES = (5000, 15000, 25000, 50000)
FETCH_SIZES = (100, 500, 1000, 5000)
PROJECT = 'Kothakota to Kurnool'
SURVEY_DATE = '2026-07-28'
SAMPLES = 5
RTT_SAMPLES = 21
OUT_JSON = ROOT / 'tools' / 'investigate_inventory_filter_phase4_results.json'
OUT_MD = ROOT / 'tools' / 'investigate_inventory_filter_phase4_report.md'


def _log(msg: str) -> None:
    print(msg, flush=True)


def _rss_mb() -> float | None:
    try:
        import psutil

        return round(psutil.Process(os.getpid()).memory_info().rss / (1024 * 1024), 1)
    except Exception:
        try:
            import resource

            val = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            if sys.platform == 'darwin':
                return round(val / (1024 * 1024), 1)
            # Linux: KB; Windows resource may be unavailable
            return round(val / 1024.0, 1)
        except Exception:
            return None


def _percentile(sorted_vals: list[float], p: float) -> float:
    if not sorted_vals:
        return float('nan')
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    k = (len(sorted_vals) - 1) * (p / 100.0)
    f = int(k)
    c = min(f + 1, len(sorted_vals) - 1)
    if f == c:
        return sorted_vals[f]
    return sorted_vals[f] + (sorted_vals[c] - sorted_vals[f]) * (k - f)


def _stats(vals: list[float]) -> dict[str, float]:
    s = sorted(vals)
    return {
        'n': len(s),
        'min': round(s[0], 3) if s else float('nan'),
        'median': round(statistics.median(s), 3) if s else float('nan'),
        'p95': round(_percentile(s, 95), 3) if s else float('nan'),
        'max': round(s[-1], 3) if s else float('nan'),
        'mean': round(statistics.fmean(s), 3) if s else float('nan'),
    }


def _redact_host(host: str | None) -> dict[str, Any]:
    if not host:
        return {'present': False}
    kind = (
        'pooled'
        if 'pooler' in host.lower()
        else ('direct' if 'neon.tech' in host.lower() else ('local' if host in {'localhost', '127.0.0.1'} else 'other'))
    )
    parts = host.split('.')
    region = next((p for p in parts if re.match(r'^(ap|eu|us|sa|me|af)-[a-z]+-\d+$', p)), None)
    suffix = '.'.join(parts[-4:]) if len(parts) >= 4 else '.'.join(parts[-3:]) if len(parts) >= 3 else '<host>'
    return {
        'present': True,
        'endpoint_kind': kind,
        'region_hint': region,
        'host_suffix': suffix,
        'has_pooler_token': 'pooler' in host.lower(),
        'is_neon': 'neon.tech' in host.lower(),
    }


def _env_snapshot() -> dict[str, Any]:
    import django as dj
    import psycopg2

    db = settings.DATABASES['default']
    host_meta = _redact_host(db.get('HOST'))
    railway_keys = sorted(k for k in os.environ if k.startswith('RAILWAY_'))
    return {
        'runtime': {
            'platform': sys.platform,
            'python': sys.version.split()[0],
            'on_railway_env': bool(
                os.environ.get('RAILWAY_ENVIRONMENT') or os.environ.get('RAILWAY_SERVICE_NAME')
            ),
            'railway_env_keys_present': railway_keys,
            'RAILWAY_REGION': os.environ.get('RAILWAY_REGION') or None,
            'RAILWAY_ENVIRONMENT': os.environ.get('RAILWAY_ENVIRONMENT') or None,
            'AWS_S3_REGION_NAME': os.environ.get('AWS_S3_REGION_NAME') or None,
        },
        'django_version': dj.get_version(),
        'psycopg2_version': psycopg2.__version__,
        'db_engine': db.get('ENGINE'),
        'db_port': db.get('PORT'),
        'conn_max_age': db.get('CONN_MAX_AGE'),
        'sslmode': (db.get('OPTIONS') or {}).get('sslmode'),
        'has_startup_options': bool((db.get('OPTIONS') or {}).get('options')),
        'host': host_meta,
        'driver': 'psycopg2 (not psycopg3)',
        'external_pool_in_app': False,
    }


def _payload(page_size: int) -> dict[str, Any]:
    raw = {
        'chainage_start': 0,
        'chainage_end': 99999,
        'date': SURVEY_DATE,
        'direction': ['All'],
        'project_name': [PROJECT],
        'asset_type': ['All'],
        'page': 1,
        'page_size': page_size,
    }
    ser = InventoryFilterSerializer(data=raw)
    assert ser.is_valid(), ser.errors
    return dict(ser.validated_data)


def _page_sql(page_size: int) -> tuple[str, tuple]:
    data = _payload(page_size)
    qs = build_inventory_filter_queryset(data)
    page_qs = (
        qs.annotate(**{_QUANTITY_VALUE_KEY: _inventory_filter_quantity_annotation()})
        .order_by('-inventory_id')
        .values(*INVENTORY_FILTER_CORE_FIELDS, _QUANTITY_VALUE_KEY)[0:page_size]
    )
    return page_qs.query.sql_with_params()


def _ensure_connection() -> None:
    connection.ensure_connection()


def measure_select1(n: int = RTT_SAMPLES) -> dict[str, Any]:
    _ensure_connection()
    samples: list[float] = []
    with connection.cursor() as cur:
        for _ in range(n):
            t0 = time.perf_counter()
            cur.execute('SELECT 1')
            cur.fetchone()
            samples.append((time.perf_counter() - t0) * 1000)
    return {'label': 'SELECT 1 (existing connection)', 'stats_ms': _stats(samples), 'samples_ms': [round(x, 3) for x in samples]}


def measure_connection_establishment(n: int = 7) -> dict[str, Any]:
    """Close Django connection each sample; time connect + first SELECT 1 + second SELECT 1."""
    establish: list[float] = []
    first_rtt: list[float] = []
    second_rtt: list[float] = []
    for _ in range(n):
        connection.close()
        t0 = time.perf_counter()
        connection.ensure_connection()
        establish.append((time.perf_counter() - t0) * 1000)
        with connection.cursor() as cur:
            t1 = time.perf_counter()
            cur.execute('SELECT 1')
            cur.fetchone()
            first_rtt.append((time.perf_counter() - t1) * 1000)
            t2 = time.perf_counter()
            cur.execute('SELECT 1')
            cur.fetchone()
            second_rtt.append((time.perf_counter() - t2) * 1000)
    return {
        'connection_establishment_ms': _stats(establish),
        'select1_after_connect_ms': _stats(first_rtt),
        'select1_same_connection_ms': _stats(second_rtt),
    }


def measure_conn_reuse() -> dict[str, Any]:
    """Verify Django reuses the underlying connection across ensure_connection calls."""
    connection.close()
    _ensure_connection()
    ids = []
    for i in range(3):
        _ensure_connection()
        raw = connection.connection
        ids.append(id(raw) if raw is not None else None)
        with connection.cursor() as cur:
            cur.execute('SELECT 1')
            cur.fetchone()
        time.sleep(0.05)
    return {
        'conn_max_age': settings.DATABASES['default'].get('CONN_MAX_AGE'),
        'connection_object_ids': ids,
        'reused_same_object': len(set(ids)) == 1 and ids[0] is not None,
    }


def _row_payload_bytes(rows: list[tuple]) -> int:
    """Approximate transferred payload as sum of Python buffer sizes of values."""
    total = 0
    for row in rows:
        for v in row:
            if v is None:
                continue
            if isinstance(v, (bytes, bytearray, memoryview)):
                total += len(v)
            elif isinstance(v, str):
                total += len(v.encode('utf-8'))
            elif isinstance(v, (int, float)):
                total += 8
            else:
                total += len(str(v).encode('utf-8'))
    return total


def measure_pg_column_size(page_size: int) -> dict[str, Any]:
    """Server-side estimate of selected row widths (read-only)."""
    sql, params = _page_sql(page_size)
    # Wrap as subquery and measure pg_column_size of whole row via row()
    wrapped = f"""
    WITH page AS ({sql})
    SELECT
      COUNT(*) AS n,
      COALESCE(SUM(pg_column_size(page.*)), 0) AS sum_bytes,
      COALESCE(AVG(pg_column_size(page.*)), 0) AS avg_bytes,
      COALESCE(MAX(pg_column_size(page.*)), 0) AS max_bytes
    FROM page
    """
    with connection.cursor() as cur:
        t0 = time.perf_counter()
        cur.execute(wrapped, params)
        n, sum_b, avg_b, max_b = cur.fetchone()
        ms = (time.perf_counter() - t0) * 1000
    return {
        'page_size': page_size,
        'rows': int(n),
        'sum_bytes': int(sum_b),
        'avg_bytes': float(avg_b),
        'max_bytes': int(max_b),
        'measure_ms': round(ms, 2),
    }


def measure_raw_fetch(
    page_size: int,
    *,
    arraysize: int | None = None,
    mode: str = 'fetchall',
) -> dict[str, Any]:
    sql, params = _page_sql(page_size)
    rss0 = _rss_mb()
    with connection.cursor() as cur:
        if arraysize:
            cur.itersize = arraysize  # psycopg2 server-side cursor hint when named; still set
            try:
                cur.arraysize = arraysize
            except Exception:
                pass
        t_ex0 = time.perf_counter()
        cur.execute(sql, params)
        execute_ms = (time.perf_counter() - t_ex0) * 1000

        colnames = [c[0] for c in cur.description]
        t_f0 = time.perf_counter()
        if mode == 'fetchall':
            rows = cur.fetchall()
            fetch_ms = (time.perf_counter() - t_f0) * 1000
            first_batch_ms = None
        else:
            assert arraysize is not None
            rows = []
            first_batch_ms = None
            while True:
                t_b0 = time.perf_counter()
                batch = cur.fetchmany(arraysize)
                b_ms = (time.perf_counter() - t_b0) * 1000
                if first_batch_ms is None:
                    first_batch_ms = b_ms
                if not batch:
                    break
                rows.extend(batch)
            fetch_ms = (time.perf_counter() - t_f0) * 1000

    rss1 = _rss_mb()
    payload = _row_payload_bytes(rows)
    total_ms = execute_ms + fetch_ms
    mb = payload / (1024 * 1024)
    sec = max(fetch_ms / 1000.0, 1e-9)
    return {
        'page_size': page_size,
        'arraysize': arraysize,
        'mode': mode,
        'rows': len(rows),
        'cols': len(colnames),
        'colnames': colnames,
        'execute_ms': round(execute_ms, 3),
        'fetch_ms': round(fetch_ms, 3),
        'first_batch_ms': round(first_batch_ms, 3) if first_batch_ms is not None else None,
        'total_ms': round(total_ms, 3),
        'approx_payload_bytes': payload,
        'mb_per_sec_fetch': round(mb / sec, 3),
        'rows_per_sec_fetch': round(len(rows) / sec, 1),
        'rss_before_mb': rss0,
        'rss_after_mb': rss1,
        'rss_delta_mb': round(rss1 - rss0, 1) if rss0 is not None and rss1 is not None else None,
    }


def measure_explain_execute_ms(page_size: int) -> float:
    sql, params = _page_sql(page_size)
    with connection.cursor() as cur:
        cur.execute(f'EXPLAIN (ANALYZE, FORMAT JSON) {sql}', params)
        plan = cur.fetchone()[0]
    # plan is list with one Planning/Execution Time
    if isinstance(plan, str):
        plan = json.loads(plan)
    node = plan[0]
    return float(node.get('Execution Time', 0.0))


def measure_values(page_size: int) -> dict[str, Any]:
    data = _payload(page_size)
    qs = build_inventory_filter_queryset(data)
    page_qs = (
        qs.annotate(**{_QUANTITY_VALUE_KEY: _inventory_filter_quantity_annotation()})
        .order_by('-inventory_id')
        .values(*INVENTORY_FILTER_CORE_FIELDS, _QUANTITY_VALUE_KEY)[0:page_size]
    )
    # Force SQL compile before timing convert
    sql, params = page_qs.query.sql_with_params()
    rss0 = _rss_mb()
    t0 = time.perf_counter()
    items = list(page_qs)
    total_ms = (time.perf_counter() - t0) * 1000
    rss1 = _rss_mb()

    # Python conversion / serialize / group / JSON / gzip for size split
    t_s0 = time.perf_counter()
    rows = [serialize_inventory_wide_row(item) for item in items]
    serialize_ms = (time.perf_counter() - t_s0) * 1000
    t_g0 = time.perf_counter()
    groups = group_serialized_rows(rows)
    group_ms = (time.perf_counter() - t_g0) * 1000
    t_j0 = time.perf_counter()
    body = JSONRenderer().render(groups)
    json_ms = (time.perf_counter() - t_j0) * 1000
    t_z0 = time.perf_counter()
    gz = gzip.compress(body, compresslevel=6)
    gzip_ms = (time.perf_counter() - t_z0) * 1000

    # rough python object size via JSON of items is better than sys.getsizeof
    try:
        py_bytes = len(json.dumps(items, default=str).encode('utf-8'))
    except Exception:
        py_bytes = None

    return {
        'page_size': page_size,
        'rows': len(items),
        'values_list_ms': round(total_ms, 3),
        'serialize_ms': round(serialize_ms, 3),
        'group_ms': round(group_ms, 3),
        'json_ms': round(json_ms, 3),
        'gzip_ms': round(gzip_ms, 3),
        'python_repr_approx_bytes': py_bytes,
        'json_bytes': len(body),
        'gzip_bytes': len(gz),
        'rss_before_mb': rss0,
        'rss_after_mb': rss1,
        'sql_len': len(sql),
        'param_count': len(params),
    }


def measure_ssl_info() -> dict[str, Any]:
    _ensure_connection()
    info: dict[str, Any] = {'sslmode_setting': (settings.DATABASES['default'].get('OPTIONS') or {}).get('sslmode')}
    try:
        with connection.cursor() as cur:
            cur.execute("SHOW ssl")
            info['pg_show_ssl'] = cur.fetchone()[0]
    except Exception as exc:
        info['pg_show_ssl_error'] = type(exc).__name__
    try:
        raw = connection.connection
        info['psycopg2_status'] = getattr(raw, 'status', None)
        # DSN without password
        dsn = getattr(raw, 'dsn', '') or ''
        # redact password=...
        dsn = re.sub(r'password=[^\s]+', 'password=<redacted>', dsn, flags=re.I)
        dsn = re.sub(r'user=[^\s]+', 'user=<redacted>', dsn, flags=re.I)
        # redact host fully to suffix
        m = re.search(r'host=([^\s]+)', dsn)
        if m:
            meta = _redact_host(m.group(1))
            dsn = re.sub(r'host=[^\s]+', f"host=***{meta.get('host_suffix')}", dsn)
        info['dsn_redacted'] = dsn
    except Exception as exc:
        info['dsn_error'] = type(exc).__name__
    return info


def measure_neon_diagnostics() -> dict[str, Any]:
    out: dict[str, Any] = {}
    with connection.cursor() as cur:
        try:
            cur.execute('SELECT version()')
            out['pg_version'] = cur.fetchone()[0]
        except Exception as exc:
            out['pg_version_error'] = type(exc).__name__
        try:
            cur.execute('SHOW server_version')
            out['server_version'] = cur.fetchone()[0]
        except Exception:
            pass
        try:
            cur.execute(
                """
                SELECT count(*) FROM pg_stat_activity
                WHERE datname = current_database()
                """
            )
            out['connections_same_db'] = int(cur.fetchone()[0])
        except Exception as exc:
            out['connections_error'] = type(exc).__name__
        try:
            # Neon may expose neon.* settings — ignore failures
            cur.execute("SELECT name, setting FROM pg_settings WHERE name LIKE 'neon%' OR name IN ('ssl','max_connections') ORDER BY name")
            out['settings'] = {r[0]: r[1] for r in cur.fetchall()}
        except Exception as exc:
            out['settings_error'] = type(exc).__name__
    return out


def run_multi(fn, *, samples: int, label: str, **kwargs) -> dict[str, Any]:
    cold = fn(**kwargs)
    warms = []
    for _ in range(samples - 1):
        warms.append(fn(**kwargs))
    all_runs = [cold, *warms]
    return {
        'label': label,
        'kwargs': kwargs,
        'cold': cold,
        'warm_runs': warms,
        'all': all_runs,
    }


def aggregate_field(runs: list[dict], field: str) -> dict[str, float]:
    vals = [float(r[field]) for r in runs if r.get(field) is not None]
    return _stats(vals)


def main() -> int:
    report: dict[str, Any] = {
        'phase': 4,
        'project': PROJECT,
        'survey_date': SURVEY_DATE,
        'samples_per_test': SAMPLES,
        'note': (
            'Local Windows → Neon measurements when RAILWAY_* unset. '
            'Railway in-container run requires authenticated railway CLI.'
        ),
    }

    _log('=== A. Environment ===')
    env = _env_snapshot()
    report['environment'] = env
    _log(json.dumps(env, indent=2))

    _log('\n=== SSL / diagnostics ===')
    ssl_info = measure_ssl_info()
    neon_diag = measure_neon_diagnostics()
    report['ssl'] = ssl_info
    report['neon_diagnostics'] = neon_diag
    _log(json.dumps({'ssl': ssl_info, 'neon': {k: neon_diag[k] for k in neon_diag if k != 'pg_version'}}, indent=2, default=str))

    _log('\n=== B. SELECT 1 RTT + connection establishment ===')
    rtt = measure_select1()
    conn_est = measure_connection_establishment()
    reuse = measure_conn_reuse()
    report['network'] = {'select1': rtt, 'connection': conn_est, 'reuse': reuse}
    _log(json.dumps(report['network'], indent=2))

    _log('\n=== Column width / selected columns ===')
    sql, _params = _page_sql(5000)
    report['selected_sql_preview'] = sql[:800]
    widths = []
    for ps in PAGE_SIZES:
        w = measure_pg_column_size(ps)
        widths.append(w)
        _log(f"pg_column_size page={ps}: rows={w['rows']} avg={w['avg_bytes']:.1f}B sum={w['sum_bytes']} ({w['sum_bytes']/(1024*1024):.3f} MiB)")
    report['pg_column_size'] = widths

    _log('\n=== C/D. Raw fetch vs values() by page size ===')
    page_results = []
    for ps in PAGE_SIZES:
        _log(f'-- page_size={ps}')
        explain_ms = measure_explain_execute_ms(ps)
        raw_bundle = run_multi(measure_raw_fetch, samples=SAMPLES, label=f'raw_{ps}', page_size=ps, mode='fetchall')
        values_bundle = run_multi(measure_values, samples=SAMPLES, label=f'values_{ps}', page_size=ps)
        entry = {
            'page_size': ps,
            'explain_execution_ms': explain_ms,
            'raw': {
                'cold': raw_bundle['cold'],
                'execute_stats_ms': aggregate_field(raw_bundle['all'], 'execute_ms'),
                'fetch_stats_ms': aggregate_field(raw_bundle['all'], 'fetch_ms'),
                'total_stats_ms': aggregate_field(raw_bundle['all'], 'total_ms'),
                'mb_per_sec_stats': aggregate_field(raw_bundle['all'], 'mb_per_sec_fetch'),
                'rows_per_sec_stats': aggregate_field(raw_bundle['all'], 'rows_per_sec_fetch'),
                'rss_after_stats': aggregate_field(
                    [{'rss_after_mb': r['rss_after_mb'] or 0} for r in raw_bundle['all']],
                    'rss_after_mb',
                ),
                'approx_payload_bytes_median': statistics.median(
                    [r['approx_payload_bytes'] for r in raw_bundle['all']]
                ),
            },
            'values': {
                'cold': values_bundle['cold'],
                'values_stats_ms': aggregate_field(values_bundle['all'], 'values_list_ms'),
                'serialize_stats_ms': aggregate_field(values_bundle['all'], 'serialize_ms'),
                'group_stats_ms': aggregate_field(values_bundle['all'], 'group_ms'),
                'json_stats_ms': aggregate_field(values_bundle['all'], 'json_ms'),
                'gzip_stats_ms': aggregate_field(values_bundle['all'], 'gzip_ms'),
                'json_bytes': values_bundle['all'][-1]['json_bytes'],
                'gzip_bytes': values_bundle['all'][-1]['gzip_bytes'],
                'python_repr_approx_bytes': values_bundle['all'][-1]['python_repr_approx_bytes'],
            },
        }
        page_results.append(entry)
        _log(
            f"  EXPLAIN exec={explain_ms:.2f}ms | "
            f"raw fetch med={entry['raw']['fetch_stats_ms']['median']}ms | "
            f"values med={entry['values']['values_stats_ms']['median']}ms | "
            f"MB/s med={entry['raw']['mb_per_sec_stats']['median']}"
        )
    report['page_size_results'] = page_results

    _log('\n=== E. Fetch-size experiment (page_size=15000) ===')
    fetch_size_results = []
    for fs in FETCH_SIZES:
        bundle = run_multi(
            measure_raw_fetch,
            samples=SAMPLES,
            label=f'fetchmany_{fs}',
            page_size=15000,
            arraysize=fs,
            mode='fetchmany',
        )
        entry = {
            'fetch_size': fs,
            'cold': bundle['cold'],
            'total_stats_ms': aggregate_field(bundle['all'], 'total_ms'),
            'fetch_stats_ms': aggregate_field(bundle['all'], 'fetch_ms'),
            'mb_per_sec_stats': aggregate_field(bundle['all'], 'mb_per_sec_fetch'),
            'rss_after_stats': aggregate_field(
                [{'rss_after_mb': r['rss_after_mb'] or 0} for r in bundle['all']],
                'rss_after_mb',
            ),
        }
        fetch_size_results.append(entry)
        _log(
            f"  arraysize={fs}: total med={entry['total_stats_ms']['median']}ms "
            f"MB/s med={entry['mb_per_sec_stats']['median']} "
            f"RSS med={entry['rss_after_stats']['median']}"
        )
    report['fetch_size_results'] = fetch_size_results

    # Cold vs warm summary for 15k
    _log('\n=== F. Cold vs warm (15k raw) ===')
    fifteen = next(p for p in page_results if p['page_size'] == 15000)
    cold = fifteen['raw']['cold']
    report['cold_vs_warm_15k'] = {
        'cold_execute_ms': cold['execute_ms'],
        'cold_fetch_ms': cold['fetch_ms'],
        'cold_total_ms': cold['total_ms'],
        'fetch_stats_all_including_cold': fifteen['raw']['fetch_stats_ms'],
        'execute_stats_all_including_cold': fifteen['raw']['execute_stats_ms'],
        'interpretation_hint': (
            'If cold≈warm fetch, latency is transfer-bound/stable, not connection-setup-bound.'
        ),
    }

    # Breakdown percentages using median 15k
    raw_med_fetch = fifteen['raw']['fetch_stats_ms']['median']
    raw_med_exec = fifteen['raw']['execute_stats_ms']['median']
    values_med = fifteen['values']['values_stats_ms']['median']
    ser_med = fifteen['values']['serialize_stats_ms']['median']
    grp_med = fifteen['values']['group_stats_ms']['median']
    json_med = fifteen['values']['json_stats_ms']['median']
    gzip_med = fifteen['values']['gzip_stats_ms']['median']
    # values() includes execute+fetch+dict conversion; isolate python conversion approx:
    py_convert = max(values_med - raw_med_fetch - raw_med_exec, 0.0)
    render = ser_med + grp_med + json_med + gzip_med
    # End-to-end page path approx = values + render (count excluded)
    e2e = values_med + render
    report['breakdown_15k_median'] = {
        'explain_execution_ms': fifteen['explain_execution_ms'],
        'raw_execute_ms': raw_med_exec,
        'raw_fetch_ms': raw_med_fetch,
        'values_ms': values_med,
        'python_convert_approx_ms': round(py_convert, 3),
        'serialize_ms': ser_med,
        'group_ms': grp_med,
        'json_ms': json_med,
        'gzip_ms': gzip_med,
        'e2e_without_count_ms': round(e2e, 3),
        'pct': {
            'postgresql_execution': round(100.0 * fifteen['explain_execution_ms'] / e2e, 2) if e2e else None,
            'transfer_fetch': round(100.0 * raw_med_fetch / e2e, 2) if e2e else None,
            'python_processing': round(100.0 * (py_convert + ser_med + grp_med) / e2e, 2) if e2e else None,
            'response_rendering': round(100.0 * (json_med + gzip_med) / e2e, 2) if e2e else None,
        },
    }
    _log(json.dumps(report['breakdown_15k_median'], indent=2))

    OUT_JSON.write_text(json.dumps(report, indent=2, default=str), encoding='utf-8')
    _log(f'\nWrote {OUT_JSON}')

    # Markdown report skeleton filled with numbers
    md = _render_markdown(report)
    OUT_MD.write_text(md, encoding='utf-8')
    _log(f'Wrote {OUT_MD}')
    return 0


def _render_markdown(report: dict[str, Any]) -> str:
    env = report['environment']
    net = report['network']
    pages = report['page_size_results']
    fetch_sizes = report['fetch_size_results']
    br = report['breakdown_15k_median']
    host = env['host']

    lines = []
    lines.append('# Phase 4 — Inventory Filter Neon ↔ App Transfer Investigation')
    lines.append('')
    lines.append('Investigation only. No production configuration changes.')
    lines.append('')
    lines.append('## A. Environment')
    lines.append('')
    lines.append(f"- Runtime platform: `{env['runtime']['platform']}`")
    lines.append(f"- On Railway env vars: `{env['runtime']['on_railway_env']}`")
    lines.append(f"- Railway region (from app env): `{env['runtime'].get('RAILWAY_REGION') or 'unset'}`")
    lines.append(f"- Neon region (host hint): `{host.get('region_hint')}`")
    lines.append(f"- DB driver: `{env['driver']}` / `{env['psycopg2_version']}`")
    lines.append(f"- Django version: `{env['django_version']}`")
    lines.append(f"- Connection endpoint: `{host.get('endpoint_kind')}`")
    lines.append(f"- Pooler token in hostname: `{host.get('has_pooler_token')}`")
    lines.append(f"- CONN_MAX_AGE: `{env['conn_max_age']}`")
    lines.append(f"- SSL mode: `{env['sslmode']}`")
    lines.append(f"- Host suffix (redacted): `{host.get('host_suffix')}`")
    lines.append('')
    lines.append('## B. Network latency')
    lines.append('')
    lines.append('| Test | Min | Median | P95 | Max |')
    lines.append('|---|---:|---:|---:|---:|')
    s = net['select1']['stats_ms']
    lines.append(f"| SELECT 1 | {s['min']} | {s['median']} | {s['p95']} | {s['max']} |")
    c = net['connection']['connection_establishment_ms']
    lines.append(f"| connection establishment | {c['min']} | {c['median']} | {c['p95']} | {c['max']} |")
    c1 = net['connection']['select1_after_connect_ms']
    lines.append(f"| SELECT 1 after connect | {c1['min']} | {c1['median']} | {c1['p95']} | {c1['max']} |")
    c2 = net['connection']['select1_same_connection_ms']
    lines.append(f"| SELECT 1 same connection | {c2['min']} | {c2['median']} | {c2['p95']} | {c2['max']} |")
    lines.append('')
    lines.append(f"Connection reuse observed: `{net['reuse']['reused_same_object']}` (CONN_MAX_AGE={net['reuse']['conn_max_age']})")
    lines.append('')
    lines.append('## C. Raw database fetch')
    lines.append('')
    lines.append('| page_size | PG EXPLAIN | Execute med | Fetch med | Total med | MB/s med | Rows/s med | RSS med |')
    lines.append('|---:|---:|---:|---:|---:|---:|---:|---:|')
    for p in pages:
        lines.append(
            f"| {p['page_size']} | {p['explain_execution_ms']:.2f} | "
            f"{p['raw']['execute_stats_ms']['median']} | {p['raw']['fetch_stats_ms']['median']} | "
            f"{p['raw']['total_stats_ms']['median']} | {p['raw']['mb_per_sec_stats']['median']} | "
            f"{p['raw']['rows_per_sec_stats']['median']} | {p['raw']['rss_after_stats']['median']} |"
        )
    lines.append('')
    lines.append('## D. Raw vs Django values()')
    lines.append('')
    lines.append('| page_size | Raw total med | Django values() med | Difference |')
    lines.append('|---:|---:|---:|---:|')
    for p in pages:
        raw_t = p['raw']['total_stats_ms']['median']
        val_t = p['values']['values_stats_ms']['median']
        lines.append(f"| {p['page_size']} | {raw_t} | {val_t} | {round(val_t - raw_t, 3)} |")
    lines.append('')
    lines.append('## E. Fetch-size experiment (15k)')
    lines.append('')
    lines.append('| Fetch size | Time med | Throughput MB/s med | RSS med |')
    lines.append('|---:|---:|---:|---:|')
    for f in fetch_sizes:
        lines.append(
            f"| {f['fetch_size']} | {f['total_stats_ms']['median']} | "
            f"{f['mb_per_sec_stats']['median']} | {f['rss_after_stats']['median']} |"
        )
    lines.append('')
    lines.append('## F. Cold vs warm')
    lines.append('')
    cvw = report['cold_vs_warm_15k']
    lines.append(f"- Cold execute_ms: {cvw['cold_execute_ms']}")
    lines.append(f"- Cold fetch_ms: {cvw['cold_fetch_ms']}")
    lines.append(f"- All-sample fetch stats: {cvw['fetch_stats_all_including_cold']}")
    lines.append('')
    lines.append('## G. Network vs application (15k median)')
    lines.append('')
    pct = br['pct']
    lines.append(f"- PostgreSQL execution: {pct['postgresql_execution']}%")
    lines.append(f"- transfer/fetch: {pct['transfer_fetch']}%")
    lines.append(f"- Python processing: {pct['python_processing']}%")
    lines.append(f"- response rendering: {pct['response_rendering']}%")
    lines.append('')
    lines.append('## Size split (last warm sample per page)')
    lines.append('')
    lines.append('| page_size | PG sum bytes | Python approx | JSON | gzip |')
    lines.append('|---:|---:|---:|---:|---:|')
    width_by = {w['page_size']: w for w in report['pg_column_size']}
    for p in pages:
        w = width_by[p['page_size']]
        lines.append(
            f"| {p['page_size']} | {w['sum_bytes']} | {p['values']['python_repr_approx_bytes']} | "
            f"{p['values']['json_bytes']} | {p['values']['gzip_bytes']} |"
        )
    lines.append('')
    lines.append('## H–J')
    lines.append('')
    lines.append('See assistant final report for PRIMARY BOTTLENECK / ranked next actions / NEXT STEP.')
    lines.append('')
    lines.append(f"Raw JSON: `{OUT_JSON.name}`")
    return '\n'.join(lines)


if __name__ == '__main__':
    raise SystemExit(main())
