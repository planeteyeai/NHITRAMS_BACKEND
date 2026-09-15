"""Shared helpers for distress filter views."""

from __future__ import annotations

import math

from django.db.models import Q


def json_safe_float(value, default=0.0):
    """Coerce DB floats to JSON-safe numbers (NaN/Inf → default)."""
    if value is None:
        return default
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(number):
        return default
    return number


CARRIAGE_TYPE_DROPDOWN = [
    {'id': 'Flexible', 'name': 'Flexible'},
    {'id': 'Rigid', 'name': 'Rigid'},
]


def apply_carriage_type_filter(queryset, data):
    """Filter by segment.carriage_type when carriage_type is provided (not All)."""
    values = data.get('carriage_type') or []
    if not values or any(str(v).lower() == 'all' for v in values):
        return queryset
    q_objects = Q()
    for value in values:
        q_objects |= Q(segment__carriage_type__iexact=value)
    return queryset.filter(q_objects)


def apply_predicted_carriage_type_filter(queryset, data):
    """Filter denormalized predicted_distress.carriage_type (not All)."""
    values = data.get('carriage_type') or []
    if not values or any(str(v).lower() == 'all' for v in values):
        return queryset
    q_objects = Q()
    for value in values:
        q_objects |= Q(carriage_type__iexact=value)
    return queryset.filter(q_objects)
