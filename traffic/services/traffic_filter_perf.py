"""Temporary production timing helpers for POST /traffic_filter/.

Gated by settings.TRAFFIC_FILTER_PERF_DEBUG (env TRAFFIC_FILTER_PERF_DEBUG).
When disabled, callers must only perform a cheap flag check and leave the
existing request path unchanged.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from django.conf import settings

logger = logging.getLogger('nhit.traffic_filter.perf')

# Attribute attached to the Django request for middleware finalization.
REQUEST_PERF_ATTR = 'traffic_filter_perf'


def is_traffic_filter_perf_debug_enabled() -> bool:
    """Return True only when temporary production instrumentation is enabled."""
    return bool(getattr(settings, 'TRAFFIC_FILTER_PERF_DEBUG', False))


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


def format_traffic_filter_perf_log(fields: dict[str, Any]) -> str:
    """Build one structured TRAFFIC_FILTER_PERF log line (no secrets)."""
    ordered_keys = (
        'request_id',
        'cache',
        'rows',
        'groups',
        'response_bytes',
        'gzip_bytes',
        'content_encoding',
        'cache_get_ms',
        'cache_decode_ms',
        'cache_lookup_ms',
        'cache_write_ms',
        'db_ms',
        'db_query_count',
        'db_slowest_ms',
        'python_ms',
        'serialize_ms',
        'group_ms',
        'json_ms',
        'gzip_ms',
        'server_total_ms',
        'cache_payload_bytes',
    )
    parts = ['TRAFFIC_FILTER_PERF']
    seen = set()
    for key in ordered_keys:
        if key not in fields:
            continue
        value = fields[key]
        if value is None:
            continue
        parts.append(f'{key}={value}')
        seen.add(key)
    for key, value in fields.items():
        if key in seen or value is None:
            continue
        # Never allow accidental secret-like keys.
        lowered = str(key).lower()
        if any(token in lowered for token in ('password', 'secret', 'token', 'redis_url', 'authorization')):
            continue
        parts.append(f'{key}={value}')
    return ' '.join(parts)


def log_traffic_filter_performance(fields: dict[str, Any]) -> None:
    """Emit one TRAFFIC_FILTER_PERF line. Never pass secrets into *fields*."""
    logger.info('%s', format_traffic_filter_perf_log(fields))


def count_traffic_rows(data: Any) -> tuple[int | None, int | None]:
    """Return (groups, rows) for nested traffic_filter payload; else (None, None)."""
    if not isinstance(data, list):
        return None, None
    groups = len(data)
    rows = 0
    for group in data:
        if isinstance(group, list):
            rows += len(group)
        else:
            return groups, None
    return groups, rows
