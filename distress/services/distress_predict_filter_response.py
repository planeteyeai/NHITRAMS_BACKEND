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

# Response sparse field names (slash key preserved for FE contract).
DISTRESS_TYPE_FIELD_MAPPING = {
    'pothole': 'pothole',
    'alligator crack': 'alligator_crack',
    'oblique crack': 'block_crack/oblique_crack',
    'block crack': 'block_crack/oblique_crack',
    'block crack/oblique crack': 'block_crack/oblique_crack',
    'edge break': 'edge_break',
    'patching': 'patchwork',
    'patchwork': 'patchwork',
    'bleeding': 'bleeding',
    'hotspots': 'hotspots',
    'rutting': 'rutting',
    'raveling': 'raveling',
    'transverse crack': 'transverse_crack',
    'roughness': 'rough_spot',
    'rough spot': 'rough_spot',
}

# Columns required to build the HTTP response (MISS projection).
PREDICTED_DISTRESS_RESPONSE_VALUES = (
    'predicted_distress_id',
    'latitude',
    'longitude',
    'project_name',
    'chainage_start',
    'chainage_end',
    'total_distress',
    'distress_type',
    'direction',
    'lane',
    'distress_date',
    'carriage_type',
)


def _normalize_type_name(raw: str | None) -> str:
    return str(raw or '').lower().replace('_', ' ').strip()


def _row_get(row: Any, name: str):
    if isinstance(row, dict):
        return row.get(name)
    return getattr(row, name, None)


def serialize_distress_predict_row(row, resolved_qty: float) -> dict[str, Any]:
    """Serialize one predicted row (ORM instance or values() dict) to API dict."""
    distress_type_raw = _row_get(row, 'distress_type')
    distress_type_name = _normalize_type_name(distress_type_raw)
    breakdown = quantity_breakdown(_row_get(row, 'total_distress'), resolved_qty)
    display_qty = breakdown['remaining_quantity']
    direction = str(_row_get(row, 'direction') or '').lower()
    distress_date = _row_get(row, 'distress_date')
    item = {
        'distress_id': _row_get(row, 'predicted_distress_id'),
        'latitude': json_safe_float(_row_get(row, 'latitude')),
        'longitude': json_safe_float(_row_get(row, 'longitude')),
        'project_name': _row_get(row, 'project_name') or '',
        'chainage_start': json_safe_float(_row_get(row, 'chainage_start')),
        'chainage_end': json_safe_float(_row_get(row, 'chainage_end')),
        'total_distress': display_qty,
        'predicted_quantity': breakdown['predicted_quantity'],
        'resolved_quantity': breakdown['resolved_quantity'],
        'remaining_quantity': breakdown['remaining_quantity'],
        'distress_type': str(distress_type_raw or '').title(),
        'direction': direction,
        'lane': _row_get(row, 'lane') or '',
        'date': distress_date.strftime('%Y-%m-%d') if distress_date else None,
        'carriage_type': _row_get(row, 'carriage_type') or '',
    }
    active_field = DISTRESS_TYPE_FIELD_MAPPING.get(distress_type_name)
    response_sparse_fields = (
        'pothole',
        'alligator_crack',
        'block_crack/oblique_crack',
        'edge_break',
        'patchwork',
        'bleeding',
        'hotspots',
        'rutting',
        'raveling',
        'transverse_crack',
        'rough_spot',
    )
    for field in response_sparse_fields:
        item[field] = display_qty if field == active_field else 0
    return item


def build_distress_predict_filter_page(
    queryset,
    *,
    offset: int,
    limit: int,
    timings: dict[str, Any] | None = None,
) -> list[list[dict[str, Any]]]:
    """Build nested-list page body: ``[rows]`` matching the API contract."""
    t0 = time.perf_counter()
    page_qs = queryset.values(*PREDICTED_DISTRESS_RESPONSE_VALUES)[offset : offset + limit]
    rows = list(page_qs)
    if timings is not None:
        timings['select_ms'] = round((time.perf_counter() - t0) * 1000, 2)

    t1 = time.perf_counter()
    resolved_map = resolved_quantities_by_distress_ids(
        [row['predicted_distress_id'] for row in rows]
    )
    if timings is not None:
        timings['repair_lookup_ms'] = round((time.perf_counter() - t1) * 1000, 2)

    t2 = time.perf_counter()
    final_data = [
        serialize_distress_predict_row(
            row, resolved_map.get(row['predicted_distress_id'], 0.0)
        )
        for row in rows
    ]
    if timings is not None:
        timings['serialize_ms'] = round((time.perf_counter() - t2) * 1000, 2)

    return [final_data]
