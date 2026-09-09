"""Redis cache for map branch of POST /inventory_filter/ (viewport+zoom).

Separate namespace from legacy inventory_filter page/count caches.
"""

from __future__ import annotations

from typing import Any, Callable

from nhit.caching.constants import CACHE_NS_INVENTORY_FILTER_MAP, CACHE_TTL_FILTER
from nhit.caching.keys import payload_digest
from nhit.caching.service import get_or_set_cached_value

INVENTORY_FILTER_MAP_SCOPE = 'map_v1'


def inventory_filter_map_cache_payload(data: dict[str, Any]) -> dict[str, Any]:
    """Normalize map cache identity: filters + viewport + zoom (no page fields)."""
    viewport = data.get('viewport') or {}
    return {
        'project_name': data.get('project_name') or [],
        'asset_type': data.get('asset_type') or [],
        'direction': data.get('direction') or [],
        'date': data.get('date'),
        'chainage_start': data.get('chainage_start'),
        'chainage_end': data.get('chainage_end'),
        'viewport': {
            'min_lat': viewport.get('min_lat'),
            'min_lng': viewport.get('min_lng'),
            'max_lat': viewport.get('max_lat'),
            'max_lng': viewport.get('max_lng'),
        },
        'zoom': data.get('zoom'),
    }


def inventory_filter_map_filter_hash(data: dict[str, Any]) -> str:
    """Short stable hash for logs (filters + viewport + zoom)."""
    return payload_digest(inventory_filter_map_cache_payload(data))[:16]


def get_cached_inventory_filter_map(
    *,
    payload: dict[str, Any],
    builder: Callable[[], dict[str, Any]],
    timeout: int = CACHE_TTL_FILTER,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Get or build map payload under the inventory_filter_map namespace."""
    cache_payload = inventory_filter_map_cache_payload(payload)
    value, meta = get_or_set_cached_value(
        namespace=CACHE_NS_INVENTORY_FILTER_MAP,
        payload=cache_payload,
        builder=builder,
        timeout=timeout,
        scope=INVENTORY_FILTER_MAP_SCOPE,
        use_lock=True,
    )
    return value, meta
