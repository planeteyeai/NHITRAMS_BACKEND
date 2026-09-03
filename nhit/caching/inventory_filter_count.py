"""Separate COUNT cache for POST /inventory_filter/ (filters only, no page)."""

from __future__ import annotations

from typing import Any, Callable

from nhit.caching.constants import CACHE_NS_INVENTORY_FILTER, CACHE_TTL_FILTER
from nhit.caching.keys import payload_digest
from nhit.caching.service import get_or_set_cached_value

# Scope distinguishes count keys from page response keys under the same namespace/version.
INVENTORY_FILTER_COUNT_SCOPE = 'count'

_PAGE_KEYS = frozenset({'page', 'page_size'})


def filter_payload_without_pagination(payload: dict[str, Any]) -> dict[str, Any]:
    """Return filter fields only — excludes page/page_size so all pages share one count."""
    return {key: value for key, value in payload.items() if key not in _PAGE_KEYS}


def inventory_filter_filter_hash(payload: dict[str, Any]) -> str:
    """Stable hash of filter criteria (no pagination) for logs and diagnostics."""
    return payload_digest(filter_payload_without_pagination(payload))


def get_cached_inventory_filter_count(
    *,
    payload: dict[str, Any],
    builder: Callable[[], int],
    timeout: int = CACHE_TTL_FILTER,
) -> tuple[int, dict[str, Any]]:
    """Get or compute filtered row count; key ignores page and page_size."""
    filter_payload = filter_payload_without_pagination(payload)
    value, meta = get_or_set_cached_value(
        namespace=CACHE_NS_INVENTORY_FILTER,
        payload=filter_payload,
        builder=builder,
        timeout=timeout,
        scope=INVENTORY_FILTER_COUNT_SCOPE,
        use_lock=True,
    )
    return int(value), meta
