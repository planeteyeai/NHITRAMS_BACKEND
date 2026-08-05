"""Nearest-chainage lookup from KML-imported GPS reference points."""

from __future__ import annotations

import math

from rest_framework.exceptions import NotFound, ValidationError

from inventory.models import ChainageReferencePoint

EARTH_RADIUS_M = 6_371_000.0
# ~1.1 km / ~5.5 km bounding boxes used as progressive pre-filters.
NARROW_DELTA_DEG = 0.01
WIDE_DELTA_DEG = 0.05


def haversine_meters(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two WGS84 points, in meters."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    a = (
        math.sin(d_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    )
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


def _format_chainage_display(chainage_km: float) -> str:
    return f'Km {chainage_km:.3f}'


def _candidates_in_box(latitude: float, longitude: float, delta: float, project_name=None):
    qs = ChainageReferencePoint.objects.filter(
        latitude__gte=latitude - delta,
        latitude__lte=latitude + delta,
        longitude__gte=longitude - delta,
        longitude__lte=longitude + delta,
    )
    if project_name:
        qs = qs.filter(project_name=project_name)
    return list(
        qs.only(
            'project_name',
            'chainage_meters',
            'chainage_km',
            'latitude',
            'longitude',
        )
    )


def find_nearest_chainage(
    latitude: float,
    longitude: float,
    *,
    project_name: str | None = None,
) -> dict:
    """Return the nearest chainage reference point for a GPS coordinate."""
    if latitude < -90 or latitude > 90:
        raise ValidationError({'latitude': 'Latitude must be between -90 and 90.'})
    if longitude < -180 or longitude > 180:
        raise ValidationError({'longitude': 'Longitude must be between -180 and 180.'})

    project_filter = project_name.strip() if project_name else None
    if project_filter == '':
        project_filter = None

    candidates = _candidates_in_box(latitude, longitude, NARROW_DELTA_DEG, project_filter)
    if not candidates:
        candidates = _candidates_in_box(latitude, longitude, WIDE_DELTA_DEG, project_filter)

    if not candidates:
        detail = 'No chainage reference points found near this location.'
        if project_filter:
            detail = (
                f'No chainage reference points found near this location '
                f'for project "{project_filter}".'
            )
        raise NotFound(detail)

    nearest = None
    nearest_distance = float('inf')
    for point in candidates:
        distance = haversine_meters(
            latitude, longitude, point.latitude, point.longitude
        )
        if distance < nearest_distance:
            nearest_distance = distance
            nearest = point

    assert nearest is not None
    return {
        'project_name': nearest.project_name,
        'chainage_display': _format_chainage_display(nearest.chainage_km),
        'chainage_meters': nearest.chainage_meters,
        'distance_from_road_meters': round(nearest_distance, 1),
        'nearest_latitude': nearest.latitude,
        'nearest_longitude': nearest.longitude,
    }
