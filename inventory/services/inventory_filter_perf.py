"""Lightweight performance logging for POST /inventory_filter/."""

from __future__ import annotations

import json
import logging
import random
import uuid
from typing import Any

logger = logging.getLogger('nhit.inventory_filter.perf')

# Always log slow requests; sample a fraction of normal ones (no extra deps).
_SLOW_TOTAL_MS = 2000.0
_SAMPLE_RATE = 0.1


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
    if total_ms >= _SLOW_TOTAL_MS:
        return True
    return random.random() < _SAMPLE_RATE


def log_inventory_filter_performance(fields: dict[str, Any]) -> None:
    """Emit one JSON log line. Never pass secrets into *fields*."""
    payload = {'event': 'inventory_filter_performance', **fields}
    # Single string message keeps console formatters structured without new deps.
    logger.info('%s', json.dumps(payload, separators=(',', ':'), default=str))
