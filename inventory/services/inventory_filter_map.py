"""Map-optimized inventory viewport + server-side grid clustering (Phase 1).

Used as an internal branch of POST /inventory_filter/ when both viewport and
zoom are present. Uses latitude/longitude predicates (no geom/GiST). Structured
so ``apply_map_viewport_filter`` can later switch to
``geom && ST_MakeEnvelope(...)`` without rewriting the public API.
"""

from __future__ import annotations

import math
import time
from typing import Any, Mapping

from django.db.models import Count, FloatField, Q, QuerySet
from django.db.models.expressions import F, Value
from django.db.models.functions import Floor

from inventory.services.inventory_filter_queryset import build_inventory_filter_queryset

# Soft threshold: at or below → individual markers.
MAP_MARKER_SOFT_MAX = 1000
# Probe fetch ceiling (SOFT decision uses SOFT_MAX; HARD+1 avoids full COUNT).
MAP_MARKER_HARD_MAX = 2000
# Prefer staying under this many cluster cells in a response.
MAP_CLUSTER_SOFT_MAX = 500
# Hard ceiling: enlarge grid until estimated/actual cells ≤ this.
MAP_CLUSTER_HARD_MAX = 1000

# Response contract version (included in Redis payload for future evolution).
MAP_RESPONSE_VERSION = 1

MARKER_FIELDS = (
    'inventory_id',
    'latitude',
    'longitude',
    'asset_type',
    'direction',
)


def get_grid_size_for_zoom(zoom: int) -> float:
    """Return deterministic WGS84 grid cell size (degrees) for a zoom level.

    Tuned for India highway corridors (tens–hundreds of km). Larger cells at
    low zoom; smaller cells as zoom increases. Values are geographic degrees
    (~0.01° ≈ 1.1 km at equator).

    Zoom ladder (inclusive lower bound → cell size):
        0–3   → 2.0°
        4–5   → 1.0°
        6–7   → 0.5°
        8–9   → 0.2°
        10–11 → 0.08°
        12–13 → 0.03°
        14–15 → 0.01°
        16–17 → 0.004°
        18–19 → 0.0015°
        20–21 → 0.0006°
        22    → 0.00025°
    """
    z = int(zoom)
    if z <= 3:
        return 2.0
    if z <= 5:
        return 1.0
    if z <= 7:
        return 0.5
    if z <= 9:
        return 0.2
    if z <= 11:
        return 0.08
    if z <= 13:
        return 0.03
    if z <= 15:
        return 0.01
    if z <= 17:
        return 0.004
    if z <= 19:
        return 0.0015
    if z <= 21:
        return 0.0006
    return 0.00025


def resolve_grid_size(zoom: int, viewport: Mapping[str, float]) -> float:
    """Zoom base size, inflated so estimated cells stay ≤ MAP_CLUSTER_HARD_MAX.

    Estimation uses viewport span / cell size (upper bound on occupied cells).
    Doubling is deterministic and pan-stable for a fixed zoom+viewport.
    """
    grid = get_grid_size_for_zoom(zoom)
    lat_span = max(float(viewport['max_lat']) - float(viewport['min_lat']), 1e-12)
    lng_span = max(float(viewport['max_lng']) - float(viewport['min_lng']), 1e-12)
    # Cap inflation so we never grow unbounded.
    for _ in range(16):
        est = math.ceil(lat_span / grid) * math.ceil(lng_span / grid)
        if est <= MAP_CLUSTER_HARD_MAX:
            return grid
        grid *= 2.0
    return grid


def apply_map_viewport_filter(queryset: QuerySet, viewport: Mapping[str, float]) -> QuerySet:
    """Restrict queryset to the map viewport (Phase 1: lat/lng columns).

    Phase 2 can replace the predicate with geom && ST_MakeEnvelope without
    changing callers.

    Policy:
    - Require non-null latitude/longitude.
    - Exclude (0, 0) placeholders (invalid for NHIT India highway maps).
    - Antimeridian (min_lng > max_lng) is rejected at serializer level for now;
      this function assumes min_lng < max_lng.
    """
    min_lat = float(viewport['min_lat'])
    max_lat = float(viewport['max_lat'])
    min_lng = float(viewport['min_lng'])
    max_lng = float(viewport['max_lng'])

    return (
        queryset.filter(
            latitude__isnull=False,
            longitude__isnull=False,
            latitude__gte=min_lat,
            latitude__lte=max_lat,
            longitude__gte=min_lng,
            longitude__lte=max_lng,
        )
        .exclude(Q(latitude=0.0) & Q(longitude=0.0))
    )


def _marker_dict(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        'id': int(row['inventory_id']),
        'latitude': float(row['latitude']),
        'longitude': float(row['longitude']),
        'asset_type': row['asset_type'],
        'direction': row['direction'],
    }


def _fetch_marker_probe(queryset: QuerySet, *, limit: int) -> list[dict[str, Any]]:
    """Fetch at most *limit* narrow marker rows (DB-side LIMIT)."""
    rows = list(
        queryset.order_by('inventory_id').values(*MARKER_FIELDS)[:limit]
    )
    return [_marker_dict(row) for row in rows]


def _run_grid_cluster_query(queryset: QuerySet, grid_size: float) -> list[dict[str, Any]]:
    """Deterministic floor-grid clustering with cell-center coordinates.

    Uses cell centers ``(floor(coord/grid)+0.5)*grid`` rather than
    ``ST_Centroid(ST_Collect(...))`` for Phase 1 because:
    - cheaper (no geometry collect)
    - pan-stable for a fixed grid
    - does not require GeoDjango wiring
    PostGIS remains available for a later geom/MVT phase.
    """
    grid_value = Value(float(grid_size), output_field=FloatField())
    grouped = (
        queryset.annotate(
            gx=Floor(F('longitude') / grid_value),
            gy=Floor(F('latitude') / grid_value),
        )
        .values('gx', 'gy')
        .annotate(count=Count('inventory_id'))
        .order_by('gy', 'gx')
    )

    clusters: list[dict[str, Any]] = []
    g = float(grid_size)
    for row in grouped.iterator(chunk_size=500):
        gx = int(row['gx'])
        gy = int(row['gy'])
        clusters.append(
            {
                'latitude': (gy + 0.5) * g,
                'longitude': (gx + 0.5) * g,
                'count': int(row['count']),
                'cell_id': f'{gx}:{gy}',
            }
        )
    return clusters


def _build_clusters(
    queryset: QuerySet,
    *,
    zoom: int,
    viewport: Mapping[str, float],
    timings: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], float]:
    grid = resolve_grid_size(zoom, viewport)
    # Adaptive fallback if estimate under-counted occupied density.
    for _ in range(8):
        started = time.perf_counter()
        clusters = _run_grid_cluster_query(queryset, grid)
        elapsed = (time.perf_counter() - started) * 1000
        if timings is not None:
            timings['cluster_db_ms'] = round(elapsed, 2)
            timings['grid_size'] = grid
        if len(clusters) <= MAP_CLUSTER_HARD_MAX:
            return clusters, grid
        grid *= 2.0
    return clusters, grid


def build_inventory_filter_map_payload(
    data: dict[str, Any],
    *,
    timings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build markers-or-clusters payload for map requests on POST /inventory_filter/.

    Flow:
      shared filters → viewport → probe ≤ HARD_MAX+1 rows → markers or clusters.
    Never paginates. Never returns an incomplete marker subset for large viewports.
    page/page_size on the request body are ignored.
    """
    t0 = time.perf_counter()
    viewport = data['viewport']
    zoom = int(data['zoom'])

    base_qs = build_inventory_filter_queryset(data)
    map_qs = apply_map_viewport_filter(base_qs, viewport)

    probe_limit = MAP_MARKER_HARD_MAX + 1
    probe_started = time.perf_counter()
    markers = _fetch_marker_probe(map_qs, limit=probe_limit)
    probe_ms = (time.perf_counter() - probe_started) * 1000
    if timings is not None:
        timings['probe_db_ms'] = round(probe_ms, 2)
        timings['probe_rows'] = len(markers)

    if len(markers) <= MAP_MARKER_SOFT_MAX:
        payload = {
            'mode': 'markers',
            'markers': markers,
            'meta': {
                'version': MAP_RESPONSE_VERSION,
                'zoom': zoom,
                'marker_count': len(markers),
                'cluster_count': 0,
            },
        }
        if timings is not None:
            timings['mode'] = 'markers'
            timings['marker_count'] = len(markers)
            timings['cluster_count'] = 0
            timings['build_ms'] = round((time.perf_counter() - t0) * 1000, 2)
        return payload

    # Too many points for marker mode — discard probe and aggregate in DB.
    clusters, grid = _build_clusters(
        map_qs,
        zoom=zoom,
        viewport=viewport,
        timings=timings,
    )
    # Strip cell_id from wire if we want minimal — keep cell_id as optional useful field.
    wire_clusters = [
        {
            'latitude': c['latitude'],
            'longitude': c['longitude'],
            'count': c['count'],
            'cell_id': c['cell_id'],
        }
        for c in clusters
    ]
    payload = {
        'mode': 'clusters',
        'clusters': wire_clusters,
        'meta': {
            'version': MAP_RESPONSE_VERSION,
            'zoom': zoom,
            'marker_count': 0,
            'cluster_count': len(wire_clusters),
            'grid_size_deg': grid,
        },
    }
    if timings is not None:
        timings['mode'] = 'clusters'
        timings['marker_count'] = 0
        timings['cluster_count'] = len(wire_clusters)
        timings['build_ms'] = round((time.perf_counter() - t0) * 1000, 2)
    return payload
