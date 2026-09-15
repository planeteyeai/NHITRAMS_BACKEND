"""Response builders + pagination constants for POST /distress_predict_filter/."""

from __future__ import annotations

import time
from typing import Any

from distress.services.distress_repair import (
    quantity_breakdown,
    resolved_quantities_by_distress_ids,
)
from distress.services.filter_helpers import json_safe_float

# When page_size is omitted, return up to the hard max (legacy "all rows" behavior).
DEFAULT_DISTRESS_PREDICT_FILTER_PAGE_SIZE = 200000
MAX_DISTRESS_PREDICT_FILTER_PAGE_SIZE = 200000

DISTRESS_PREDICT_HEADER_PAGINATED = 'X-Distress-Paginated'
DISTRESS_PREDICT_HEADER_PAGE = 'X-Distress-Page'
DISTRESS_PREDICT_HEADER_PAGE_SIZE = 'X-Distress-Page-Size'
DISTRESS_PREDICT_HEADER_ROW_COUNT = 'X-Distress-Row-Count'
DISTRESS_PREDICT_HEADER_HAS_MORE = 'X-Distress-Has-More'

DISTRESS_PREDICT_EXPOSED_HEADERS = [
    DISTRESS_PREDICT_HEADER_PAGINATED,
    DISTRESS_PREDICT_HEADER_PAGE,
    DISTRESS_PREDICT_HEADER_PAGE_SIZE,
    DISTRESS_PREDICT_HEADER_ROW_COUNT,
    DISTRESS_PREDICT_HEADER_HAS_MORE,
]

# Exact mapping from the legacy view (order preserved for sparse fields).
DISTRESS_TYPE_FIELD_MAPPING = {
    'pothole': 'pothole',
    'alligator crack': 'alligator_crack',
    'oblique crack': 'block_crack/oblique_crack',
    'edge break': 'edge_break',
    'patching': 'patchwork',
    'bleeding': 'bleeding',
    'hotspots': 'hotspots',
    'rutting': 'rutting',
    'raveling': 'raveling',
    'transverse crack': 'transverse_crack',
    'roughness': 'rough_spot',
}


def serialize_distress_predict_row(obj, resolved_qty: float) -> dict[str, Any]:
    """Serialize one Distress ORM row to the legacy response dict (field order fixed)."""
    distress_type_name = obj.distress_type.distress_type_name.lower().replace('_', ' ')
    breakdown = quantity_breakdown(obj.count_val, resolved_qty)
    display_qty = breakdown['remaining_quantity']
    item = {
        'distress_id': obj.distress_id,
        'latitude': json_safe_float(obj.latitude),
        'longitude': json_safe_float(obj.longitude),
        'project_name': obj.project.project_name,
        'chainage_start': json_safe_float(obj.segment.chainage_start),
        'chainage_end': json_safe_float(obj.segment.chainage_end),
        'total_distress': display_qty,
        'predicted_quantity': breakdown['predicted_quantity'],
        'resolved_quantity': breakdown['resolved_quantity'],
        'remaining_quantity': breakdown['remaining_quantity'],
        'distress_type': obj.distress_type.distress_type_name.title(),
        'direction': obj.segment.direction.lower(),
        'lane': obj.segment.lane or '',
        'date': obj.distress_date.strftime('%Y-%m-%d') if obj.distress_date else None,
        'carriage_type': obj.segment.carriage_type or '',
    }
    for key, field in DISTRESS_TYPE_FIELD_MAPPING.items():
        item[field] = display_qty if distress_type_name == key else 0
    return item


def build_distress_predict_filter_page(
    queryset,
    *,
    offset: int,
    limit: int,
    timings: dict[str, Any] | None = None,
) -> list[list[dict[str, Any]]]:
    """Build nested-list page body: ``[rows]`` matching the legacy contract."""
    t0 = time.perf_counter()
    page_qs = queryset[offset : offset + limit]
    rows = list(page_qs)
    if timings is not None:
        timings['select_ms'] = round((time.perf_counter() - t0) * 1000, 2)

    t1 = time.perf_counter()
    resolved_map = resolved_quantities_by_distress_ids(
        [obj.distress_id for obj in rows]
    )
    if timings is not None:
        timings['repair_lookup_ms'] = round((time.perf_counter() - t1) * 1000, 2)

    t2 = time.perf_counter()
    final_data = [
        serialize_distress_predict_row(obj, resolved_map.get(obj.distress_id, 0.0))
        for obj in rows
    ]
    if timings is not None:
        timings['serialize_ms'] = round((time.perf_counter() - t2) * 1000, 2)

    # Legacy nesting: one outer list containing the page rows.
    return [final_data]
