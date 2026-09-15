"""Lightweight performance logging for POST /distress_predict_filter/.

When settings.DISTRESS_PREDICT_FILTER_PERF_DEBUG is true, every request is logged.
When false: always log slow requests (>=2s); sample 10% of others.
"""

from __future__ import annotations

import json
import logging
import random
import uuid
from typing import Any

from django.conf import settings

logger = logging.getLogger('nhit.distress_predict_filter.perf')

_SLOW_TOTAL_MS = 2000.0
_SAMPLE_RATE = 0.1


def is_distress_predict_filter_perf_debug_enabled() -> bool:
    return bool(getattr(settings, 'DISTRESS_PREDICT_FILTER_PERF_DEBUG', False))


def resolve_request_id(request) -> str:
    header = ''
    meta = getattr(request, 'META', None) or {}
    header = str(meta.get('HTTP_X_REQUEST_ID') or '').strip()
    if not header and hasattr(request, 'headers'):
        header = str(request.headers.get('X-Request-ID') or '').strip()
    if header:
        return header[:128]
    return uuid.uuid4().hex


def should_log_distress_predict_filter_perf(total_ms: float) -> bool:
    if is_distress_predict_filter_perf_debug_enabled():
        return True
    if total_ms >= _SLOW_TOTAL_MS:
        return True
    return random.random() < _SAMPLE_RATE


def log_distress_predict_filter_performance(fields: dict[str, Any]) -> None:
    payload = {'event': 'distress_predict_filter_performance', **fields}
    for key in list(payload.keys()):
        lowered = str(key).lower()
        if any(tok in lowered for tok in ('password', 'secret', 'token', 'redis_url', 'authorization')):
            payload.pop(key, None)
    logger.info('%s', json.dumps(payload, separators=(',', ':'), default=str))
