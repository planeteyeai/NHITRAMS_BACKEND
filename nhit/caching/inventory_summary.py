"""Redis cache for POST /inventory_summary/ (filter body only, no page)."""

from __future__ import annotations

from typing import Any, Callable

from nhit.caching.constants import CACHE_NS_INVENTORY_SUMMARY, CACHE_TTL_FILTER
from nhit.caching.inventory_filter_count import filter_payload_without_pagination
from nhit.caching.service import get_or_set_cached_value

INVENTORY_SUMMARY_SCOPE = 'summary'


def get_cached_inventory_summary(
    *,
    payload: dict[str, Any],
    builder: Callable[[], dict[str, Any]],
    timeout: int = CACHE_TTL_FILTER,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Get or compute summary; cache key ignores page and page_size."""
    filter_payload = filter_payload_without_pagination(payload)
    value, meta = get_or_set_cached_value(
        namespace=CACHE_NS_INVENTORY_SUMMARY,
        payload=filter_payload,
        builder=builder,
        timeout=timeout,
        scope=INVENTORY_SUMMARY_SCOPE,
        use_lock=True,
    )
    return value, meta
