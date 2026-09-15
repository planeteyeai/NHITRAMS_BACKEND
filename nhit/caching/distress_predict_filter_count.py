"""Separate COUNT cache for POST /distress_predict_filter/ (filters only, no page)."""

from __future__ import annotations

from typing import Any, Callable

from nhit.caching.constants import CACHE_NS_DISTRESS_PREDICT_FILTER, CACHE_TTL_FILTER
from nhit.caching.keys import build_cache_key, payload_digest
from nhit.caching.service import (
    get_namespace_version,
    get_or_set_cached_value,
    safe_cache_delete,
)
DISTRESS_PREDICT_FILTER_COUNT_SCOPE = 'count'

_PAGE_KEYS = frozenset({'page', 'page_size'})


def filter_payload_without_pagination(payload: dict[str, Any]) -> dict[str, Any]:
    """Return filter fields only — excludes page/page_size so all pages share one count."""
    return {key: value for key, value in payload.items() if key not in _PAGE_KEYS}


def distress_predict_filter_filter_hash(payload: dict[str, Any]) -> str:
    """Stable hash of filter criteria (no pagination) for logs and diagnostics."""
    return payload_digest(filter_payload_without_pagination(payload))


def get_cached_distress_predict_filter_count(
    *,
    payload: dict[str, Any],
    builder: Callable[[], int],
    timeout: int = CACHE_TTL_FILTER,
    version: int | None = None,
) -> tuple[int, dict[str, Any]]:
    """Get or compute filtered row count; key ignores page and page_size."""
    filter_payload = filter_payload_without_pagination(payload)

    def _as_int(raw: Any) -> int:
        return int(raw)

    value, meta = get_or_set_cached_value(
        namespace=CACHE_NS_DISTRESS_PREDICT_FILTER,
        payload=filter_payload,
        builder=builder,
        timeout=timeout,
        scope=DISTRESS_PREDICT_FILTER_COUNT_SCOPE,
        use_lock=True,
        version=version,
    )
    try:
        return _as_int(value), meta
    except (TypeError, ValueError):
        # Corrupt entry treated as miss: delete then rebuild.
        resolved_version = version
        if resolved_version is None:
            resolved_version = get_namespace_version(CACHE_NS_DISTRESS_PREDICT_FILTER)
        cache_key = build_cache_key(
            namespace=CACHE_NS_DISTRESS_PREDICT_FILTER,
            version=resolved_version,
            payload=filter_payload,
            scope=DISTRESS_PREDICT_FILTER_COUNT_SCOPE,
        )
        safe_cache_delete(cache_key)
        value, meta = get_or_set_cached_value(
            namespace=CACHE_NS_DISTRESS_PREDICT_FILTER,
            payload=filter_payload,
            builder=builder,
            timeout=timeout,
            scope=DISTRESS_PREDICT_FILTER_COUNT_SCOPE,
            use_lock=True,
            version=resolved_version,
        )
        meta['cache_hit'] = False
        meta['lookup_path'] = 'corrupt_count_rebuild'
        return _as_int(value), meta
