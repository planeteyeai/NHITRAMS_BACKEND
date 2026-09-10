"""NHIT-compatible serialization for POST /inventory_filter/."""

from __future__ import annotations

import time
from typing import Any, Mapping

from django.db.models import Case, F, FloatField, Value, When

# NHIT production default when page_size is omitted from POST body.
DEFAULT_INVENTORY_FILTER_PAGE_SIZE = 15000
# Hard upper bound for POST /inventory_filter/ page_size.
MAX_INVENTORY_FILTER_PAGE_SIZE = 100000

INVENTORY_FILTER_HEADER_PAGINATED = 'X-Inventory-Paginated'
INVENTORY_FILTER_HEADER_PAGE = 'X-Inventory-Page'
INVENTORY_FILTER_HEADER_PAGE_SIZE = 'X-Inventory-Page-Size'
INVENTORY_FILTER_HEADER_ROW_COUNT = 'X-Inventory-Row-Count'
INVENTORY_FILTER_HEADER_HAS_MORE = 'X-Inventory-Has-More'

INVENTORY_FILTER_EXPOSED_HEADERS = [
    INVENTORY_FILTER_HEADER_PAGINATED,
    INVENTORY_FILTER_HEADER_PAGE,
    INVENTORY_FILTER_HEADER_PAGE_SIZE,
    INVENTORY_FILTER_HEADER_ROW_COUNT,
    INVENTORY_FILTER_HEADER_HAS_MORE,
]
ASSET_TYPE_FIELD_MAP = {
    'Trees': 'trees',
    'Culvert': 'culvert',
    'Street Lights': 'street_lights',
    'Bridges': 'bridges',
    'Traffic Signals': 'traffic_signals',
    'Bus Stop': 'bus_stop',
    'Truck Layby': 'truck_layby',
    'Toll Plaza': 'toll_plaza',
    'Adjacent Road': 'adjacent_road',
    'Toilet Blocks': 'toilet_blocks',
    'Rest Area': 'rest_area',
    'RCC Drain': 'rcc_drain',
    'Fuel Station': 'fuel_station',
    'Emergency Call Box': 'emergency_call_box',
    'Tunnels': 'tunnels',
    'Footpath': 'footpath',
    'Junction': 'junction',
    'Sign Boards': 'sign_boards',
    'Solar Blinker': 'solar_blinker',
    'Median Plants': 'median_plants',
    'Service Road': 'service_road',
    'KM Stones': 'km_stones',
    'Crash Barrier': 'crash_barrier',
    'Median Opening': 'median_opening',
    'Kerb': 'kerb',
    'Guard Post': 'guard_post',
    'Delineators': 'delineators',
    'Pedestrian Guard Rail': 'pedestrian_guard_rail',
    'Slip Roads': 'slip_roads',
    'Bus Bays': 'bus_bays',
}

# Excel stores DMS strings for a few Sign Boards rows; inventory_wide has 0.0/0.0.
_DMS_COORDINATE_OVERRIDES: dict[tuple, tuple[str, str]] = {
    (1.0, 1.1, 'Increasing', 'Sign Boards'): (
        '27°15\'12.24"N',
        '77°48\'36.01"E',
    ),
    (1.6, 1.7, 'Decreasing', 'Sign Boards'): (
        '27°14\'54.67"N',
        '77°48\'33.78"E',
    ),
    (6.4, 6.5, 'Increasing', 'Sign Boards'): (
        '27°12\'17.27"N',
        '77°48\'35.51"E',
    ),
    (23.3, 23.4, 'Median', 'Sign Boards'): (
        '27°05\'06.97"N',
        '77°53\'41.94"E',
    ),
    (25.4, 25.5, 'Increasing', 'Sign Boards'): (
        '27°04\'41.97"N',
        '77°54\'55.63"E',
    ),
}

# Core columns always needed for NHIT-compatible rows (excludes per-asset quantity
# columns — those are collapsed into a single annotated quantity_value).
INVENTORY_FILTER_CORE_FIELDS = (
    'inventory_id',
    'project_name',
    'chainage_start',
    'chainage_end',
    'direction',
    'asset_type',
    'sub_asset_type',
    'latitude',
    'longitude',
    'survey_date',
    'carriage_type',
    'lane',
    'source',
)

# Backward-compatible alias used by diagnostics / older call sites.
INVENTORY_FILTER_ONLY_FIELDS = (
    *INVENTORY_FILTER_CORE_FIELDS,
    *ASSET_TYPE_FIELD_MAP.values(),
)

_SOURCE_ALIASES = {
    'excel_import': 'SAR',
}

_QUANTITY_VALUE_KEY = 'quantity_value'


def _inventory_filter_quantity_annotation() -> Case:
    """Collapse 30 sparse quantity columns into one float for wire transfer."""
    whens = [
        When(asset_type=asset_name, then=F(field_name))
        for asset_name, field_name in ASSET_TYPE_FIELD_MAP.items()
    ]
    return Case(*whens, default=Value(None), output_field=FloatField())


def _field(item: Any, name: str, default=None):
    if isinstance(item, Mapping):
        return item.get(name, default)
    return getattr(item, name, default)


def _normalize_asset_type(value: str | None) -> str:
    return ' '.join(str(value or '').split())


def _should_omit_chainage_start(item) -> bool:
    return _field(item, 'chainage_start') == 0.0 and _field(item, 'chainage_end') == 0.1


def _format_decimal_coordinate(value: float | None) -> str | None:
    if value is None:
        return None
    number = float(value)
    if number == 0.0:
        return '0'
    text = format(number, 'f').rstrip('0').rstrip('.')
    return text or '0'


def _resolve_coordinates(item) -> tuple[str | None, str | None]:
    asset_type = _normalize_asset_type(_field(item, 'asset_type'))
    override_key = (
        _field(item, 'chainage_start'),
        _field(item, 'chainage_end'),
        _field(item, 'direction'),
        asset_type,
    )
    latitude = _field(item, 'latitude')
    longitude = _field(item, 'longitude')
    if (latitude in (None, 0.0) and longitude in (None, 0.0)):
        override = _DMS_COORDINATE_OVERRIDES.get(override_key)
        if override is not None:
            return override

    return (
        _format_decimal_coordinate(latitude),
        _format_decimal_coordinate(longitude),
    )


def _serialize_source(value: str | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return _SOURCE_ALIASES.get(text, text)


def _quantity_value(field_name: str, value) -> float | int | None:
    if value is None:
        return None
    if field_name == 'median_opening':
        number = float(value)
        if number == 0.0:
            return None
        if number.is_integer():
            return int(number)
        return number
    number = float(value)
    if number == 0.0:
        return None
    if number.is_integer():
        return int(number)
    return number


def serialize_inventory_wide_row(item) -> dict:
    """Serialize one inventory_wide row using NHIT field sparsity rules.

    Accepts a model instance, SimpleNamespace, or dict (from QuerySet.values()).
    """
    asset_type = _normalize_asset_type(_field(item, 'asset_type'))
    latitude, longitude = _resolve_coordinates(item)

    row: dict = {
        'project_name': _field(item, 'project_name'),
        'chainage_end': _field(item, 'chainage_end'),
        'direction': _field(item, 'direction'),
        'asset_type': asset_type,
        'latitude': latitude,
        'longitude': longitude,
        'date': (
            _field(item, 'survey_date').isoformat()
            if _field(item, 'survey_date') is not None
            else None
        ),
    }

    if not _should_omit_chainage_start(item):
        row['chainage_start'] = _field(item, 'chainage_start')

    sub_asset_type = (_field(item, 'sub_asset_type') or '').strip()
    if sub_asset_type:
        row['sub_asset_type'] = sub_asset_type

    source = _serialize_source(_field(item, 'source'))
    if source is not None:
        row['source'] = source

    if _field(item, 'direction') != 'Median':
        carriage_type = (_field(item, 'carriage_type') or '').strip()
        lane = (_field(item, 'lane') or '').strip()
        if carriage_type:
            row['carriage_type'] = carriage_type
        if lane:
            row['lane'] = lane

    quantity_field = ASSET_TYPE_FIELD_MAP.get(asset_type)
    if quantity_field is not None:
        if isinstance(item, Mapping) and _QUANTITY_VALUE_KEY in item:
            raw_quantity = item.get(_QUANTITY_VALUE_KEY)
        else:
            raw_quantity = _field(item, quantity_field)
        quantity = _quantity_value(quantity_field, raw_quantity)
        if quantity is not None:
            row[quantity_field] = quantity

    return row


def _location_group_key(row: dict) -> tuple[str | None, str | None]:
    return row.get('latitude'), row.get('longitude')


def group_serialized_rows(rows: list[dict]) -> list[list[dict]]:
    """Group consecutive rows that share the same coordinate strings."""
    groups: list[list[dict]] = []
    for row in rows:
        if groups and _location_group_key(groups[-1][0]) == _location_group_key(row):
            groups[-1].append(row)
        else:
            groups.append([row])
    return groups


def build_inventory_filter_groups(
    queryset,
    *,
    offset: int = 0,
    limit: int | None = None,
    timings: dict | None = None,
) -> list[list[dict]]:
    """Return NHIT-style nested arrays for one page of inventory_filter rows.

    Uses ``values()`` plus a single annotated quantity column so we:
    - avoid Django model instance construction
    - transfer one quantity float instead of ~30 sparse columns

    Quantity CASE keys match ``ASSET_TYPE_FIELD_MAP`` exactly (same as the
    previous getattr/map lookup), preserving which rows emit a quantity field.

    When *timings* is provided, records select/serialize/grouping milliseconds.
    """
    values_qs = (
        queryset
        .annotate(**{_QUANTITY_VALUE_KEY: _inventory_filter_quantity_annotation()})
        .order_by('-inventory_id')
        .values(*INVENTORY_FILTER_CORE_FIELDS, _QUANTITY_VALUE_KEY)
    )
    if limit is not None:
        page_qs = values_qs[offset : offset + limit]
    elif offset:
        page_qs = values_qs[offset:]
    else:
        page_qs = values_qs

    select_started = time.perf_counter()
    items = list(page_qs)
    select_ms = (time.perf_counter() - select_started) * 1000

    serialize_started = time.perf_counter()
    rows = [serialize_inventory_wide_row(item) for item in items]
    serialize_ms = (time.perf_counter() - serialize_started) * 1000

    group_started = time.perf_counter()
    groups = group_serialized_rows(rows)
    grouping_ms = (time.perf_counter() - group_started) * 1000

    if timings is not None:
        # select_ms includes DB execution + network transfer + values() dict build
        # (no Django model materialization on this path).
        timings['select_ms'] = round(select_ms, 2)
        timings['select_db_ms'] = None
        timings['db_transfer_ms'] = round(select_ms, 2)
        timings['orm_materialize_ms'] = 0.0
        timings['serialize_ms'] = round(serialize_ms, 2)
        timings['grouping_ms'] = round(grouping_ms, 2)
        timings['row_count'] = len(items)

    return groups
