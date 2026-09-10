"""Lightweight performance logging for POST /inventory_filter/.

When settings.INVENTORY_FILTER_PERF_DEBUG is true, every request is logged with
extra Redis stage fields. When false, preserve historical behavior:
always log slow requests (>=2s); sample 10% of others.
"""

from __future__ import annotations

import json
import logging
import os
import random
import uuid
from typing import Any
from urllib.parse import urlparse

from django.conf import settings

logger = logging.getLogger('nhit.inventory_filter.perf')

# Always log slow requests; sample a fraction of normal ones (no extra deps).
_SLOW_TOTAL_MS = 2000.0
_SAMPLE_RATE = 0.1


def is_inventory_filter_perf_debug_enabled() -> bool:
    return bool(getattr(settings, 'INVENTORY_FILTER_PERF_DEBUG', False))


def resolve_request_id(request) -> str:
    """Reuse inbound X-Request-ID when present; otherwise generate a UUID hex."""
    header = ''
    meta = getattr(request, 'META', None) or {}
    header = str(meta.get('HTTP_X_REQUEST_ID') or '').strip()
    if not header and hasattr(request, 'headers'):
        header = str(request.headers.get('X-Request-ID') or '').strip()
    if header:
        return header[:128]
    return uuid.uuid4().hex


def should_log_inventory_filter_perf(total_ms: float) -> bool:
    if is_inventory_filter_perf_debug_enabled():
        return True
    if total_ms >= _SLOW_TOTAL_MS:
        return True
    return random.random() < _SAMPLE_RATE


def redis_runtime_safe_meta() -> dict[str, Any]:
    """Safe Redis identity for logs — never includes URL/password/secrets."""
    out: dict[str, Any] = {
        'redis_configured': bool(getattr(settings, 'NHIT_CACHE_ENABLED', False)),
        'using_internal_redis': bool(getattr(settings, 'NHIT_REDIS_USING_INTERNAL', False)),
        'cache_backend': (settings.CACHES.get('default') or {}).get('BACKEND'),
        'key_prefix': (settings.CACHES.get('default') or {}).get('KEY_PREFIX'),
        'ignore_exceptions': (settings.CACHES.get('default') or {})
        .get('OPTIONS', {})
        .get('IGNORE_EXCEPTIONS'),
        'worker_pid': os.getpid(),
    }
    # Host class only (internal vs public proxy), never credentials.
    raw = ''
    if out['using_internal_redis']:
        raw = (os.environ.get('REDIS_INTERNAL_URL') or os.environ.get('REDIS_URL') or '').strip()
    else:
        raw = (os.environ.get('REDIS_URL') or os.environ.get('REDIS_INTERNAL_URL') or '').strip()
    if raw:
        try:
            parsed = urlparse(raw)
            host = (parsed.hostname or '').lower()
            if 'railway.internal' in host:
                host_class = 'railway_private_internal'
            elif 'rlwy.net' in host or 'proxy' in host:
                host_class = 'railway_public_proxy'
            elif host in ('localhost', '127.0.0.1'):
                host_class = 'localhost'
            elif host:
                host_class = 'other_hostname'
            else:
                host_class = 'unknown'
            out['redis_scheme'] = parsed.scheme or None
            out['redis_host_class'] = host_class
            out['redis_port'] = parsed.port
        except Exception:
            out['redis_host_class'] = 'parse_error'

    # Connection-pool shape only (no hosts/passwords).
    try:
        from django.core.cache import cache as django_cache

        client = django_cache.client.get_client(write=True)
        pool = getattr(client, 'connection_pool', None)
        if pool is not None:
            out['redis_pool_class'] = type(pool).__name__
            out['redis_pool_max_connections'] = getattr(pool, 'max_connections', None)
            created = getattr(pool, '_created_connections', None)
            if created is not None:
                try:
                    out['redis_pool_created_connections'] = len(created)
                except TypeError:
                    out['redis_pool_created_connections'] = None
            available = getattr(pool, '_available_connections', None)
            if available is not None:
                try:
                    out['redis_pool_available_connections'] = len(available)
                except TypeError:
                    out['redis_pool_available_connections'] = None
            out['redis_pool_pid'] = getattr(pool, 'pid', None)
    except Exception:
        out['redis_pool_inspect_error'] = True
    return out


def apply_inventory_filter_perf_debug_headers(response, fields: dict[str, Any]) -> None:
    """Attach env-gated timing headers for external measurement (no secrets)."""
    if not is_inventory_filter_perf_debug_enabled():
        return

    def _set(name: str, value: Any) -> None:
        if value is None:
            return
        response[name] = str(value)

    _set('X-Inv-Perf-Request-Id', fields.get('request_id'))
    _set('X-Inv-Perf-Cache-Hit', '1' if fields.get('cache_hit') else '0')
    _set('X-Inv-Perf-Count-Hit', '1' if fields.get('count_cache_hit') else '0')
    _set('X-Inv-Perf-Response-Path', fields.get('response_path'))
    _set('X-Inv-Perf-Server-Total-Ms', fields.get('server_total_ms'))
    _set('X-Inv-Perf-Count-Version-Get-Ms', fields.get('count_version_get_ms'))
    _set('X-Inv-Perf-Count-Data-Get-Ms', fields.get('count_data_get_ms'))
    _set('X-Inv-Perf-Page-Version-Get-Ms', fields.get('page_version_get_ms'))
    _set('X-Inv-Perf-Page-Data-Get-Ms', fields.get('page_data_get_ms'))
    _set('X-Inv-Perf-Redis-Round-Trips', fields.get('redis_round_trips'))
    _set('X-Inv-Perf-Cache-Decode-Ms', fields.get('cache_decode_ms'))
    _set('X-Inv-Perf-Ifgz-Extract-Ms', fields.get('ifgz_extract_ms'))
    _set('X-Inv-Perf-Json-Parse-Ms', fields.get('json_parse_ms'))
    _set('X-Inv-Perf-Json-Render-Ms', fields.get('json_render_ms'))
    _set('X-Inv-Perf-Gzip-Ms', fields.get('gzip_ms'))
    _set('X-Inv-Perf-Content-Length', fields.get('content_length'))
    _set('X-Inv-Perf-Redis-Host-Class', fields.get('redis_host_class'))
    _set('X-Inv-Perf-Using-Internal-Redis', '1' if fields.get('using_internal_redis') else '0')
    _set('X-Inv-Perf-Redis-Scheme', fields.get('redis_scheme'))
    _set('X-Inv-Perf-Redis-Port', fields.get('redis_port'))
    _set('X-Inv-Perf-Worker-Pid', fields.get('worker_pid'))
    _set('X-Inv-Perf-Pool-Created', fields.get('redis_pool_created_connections'))
    _set('X-Inv-Perf-Pool-Available', fields.get('redis_pool_available_connections'))
    _set('X-Inv-Perf-Redis-Payload-Bytes', fields.get('redis_payload_bytes'))


def log_inventory_filter_performance(fields: dict[str, Any]) -> None:
    """Emit one JSON log line. Never pass secrets into *fields*."""
    payload = {'event': 'inventory_filter_performance', **fields}
    # Strip accidental secret-like keys.
    for key in list(payload.keys()):
        lowered = str(key).lower()
        if any(tok in lowered for tok in ('password', 'secret', 'token', 'redis_url', 'authorization')):
            payload.pop(key, None)
    logger.info('%s', json.dumps(payload, separators=(',', ':'), default=str))
