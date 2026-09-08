"""PostgreSQL aggregation for POST /inventory_summary/.

Parity rules (measured against inventory_wide for Kothakota / 2026-07-28
and the NHIT inventory_count lat/lon dedupe pattern):

1. For every inventory_filter quantity field (ASSET_TYPE_FIELD_MAP values):
   SUM(column) over the filtered queryset — **not** gated on asset_type label.
   This includes label variants such as Asset type ``Tree`` vs ``Trees`` and
   ``Delineator`` vs ``Delineators``, and quantity stored on a different
   asset_type row (e.g. Crash Barrier meters on Slip Roads rows).

2. ``toll_plaza`` is a site count: COUNT(DISTINCT (Latitude, Longitude))
   where Toll Plaza > 0 or Asset type = 'Toll Plaza'
   (3 direction rows at one site → 1), matching NHIT inventory_count's
   groupby(latitude, longitude) behavior for site-like assets.

3. page/page_size are ignored (shared filter builder does not apply them).

4. NULL sums coalesce to 0. Whole-number floats are returned as int.
"""

from __future__ import annotations

import time
from typing import Any

from django.db.models import (
    CharField,
    Count,
    F,
    FloatField,
    Q,
    Sum,
    Value,
)
from django.db.models.functions import Cast, Coalesce, Concat

from inventory.services.inventory_filter_queryset import build_inventory_filter_queryset
from inventory.services.inventory_filter_response import ASSET_TYPE_FIELD_MAP

# Stable response key order = ASSET_TYPE_FIELD_MAP value order.
INVENTORY_SUMMARY_FIELDS: tuple[str, ...] = tuple(ASSET_TYPE_FIELD_MAP.values())

# Site-like assets: dashboard counts unique coordinates, not raw row SUM.
_DISTINCT_LOCATION_FIELDS: frozenset[str] = frozenset({'toll_plaza'})


def _format_summary_number(value) -> int | float:
    """Match inventory_filter quantity numeric shaping; keep explicit zeros."""
    if value is None:
        return 0
    number = float(value)
    if number == 0.0:
        return 0
    if number.is_integer():
        return int(number)
    # Kill binary float noise (e.g. 21.334000000000003) while keeping 3 dp lengths.
    return float(f'{number:.10g}')


def _location_key_expression():
    return Concat(
        Coalesce(Cast(F('latitude'), CharField()), Value('')),
        Value(','),
        Coalesce(Cast(F('longitude'), CharField()), Value('')),
    )


def build_inventory_summary_aggregates(
    data: dict[str, Any],
    *,
    timings: dict | None = None,
) -> dict[str, int | float]:
    """Run one SQL aggregate over the filtered inventory_wide queryset."""
    queryset = build_inventory_filter_queryset(data)

    aggregate_kwargs: dict[str, Any] = {}
    for field_name in INVENTORY_SUMMARY_FIELDS:
        if field_name in _DISTINCT_LOCATION_FIELDS:
            continue
        aggregate_kwargs[field_name] = Coalesce(
            Sum(field_name),
            Value(0.0),
            output_field=FloatField(),
        )

    # Toll Plaza: unique site count (lat,lon), not SUM of direction rows.
    location_key = _location_key_expression()
    aggregate_kwargs['toll_plaza'] = Coalesce(
        Count(
            location_key,
            distinct=True,
            filter=(
                Q(toll_plaza__gt=0)
                | Q(asset_type='Toll Plaza')
            ),
        ),
        Value(0),
    )

    started = time.perf_counter()
    raw = queryset.aggregate(**aggregate_kwargs)
    aggregate_ms = (time.perf_counter() - started) * 1000

    summary = {
        field_name: _format_summary_number(raw.get(field_name))
        for field_name in INVENTORY_SUMMARY_FIELDS
    }

    if timings is not None:
        timings['aggregate_ms'] = round(aggregate_ms, 2)
        timings['field_count'] = len(summary)

    return summary
