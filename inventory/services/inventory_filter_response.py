"""NHIT-compatible serialization for POST /inventory_filter/."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from inventory.models import InventoryWide

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

# Columns required to build the NHIT-compatible response. Excludes NHIT-ignored
# fields such as image_url and sub_asset_type to reduce remote DB payload size.
INVENTORY_FILTER_ONLY_FIELDS = (
    'inventory_id',
    'project_name',
    'chainage_start',
    'chainage_end',
    'direction',
    'asset_type',
    'latitude',
    'longitude',
    'survey_date',
    'carriage_type',
    'lane',
    'source',
    *ASSET_TYPE_FIELD_MAP.values(),
)

_SOURCE_ALIASES = {
    'excel_import': 'SAR',
}


def _normalize_asset_type(value: str | None) -> str:
    return ' '.join(str(value or '').split())


def _should_omit_chainage_start(item: InventoryWide) -> bool:
    return item.chainage_start == 0.0 and item.chainage_end == 0.1


def _format_decimal_coordinate(value: float | None) -> str | None:
    if value is None:
        return None
    number = float(value)
    if number == 0.0:
        return '0'
    text = format(number, 'f').rstrip('0').rstrip('.')
    return text or '0'


def _resolve_coordinates(item: InventoryWide) -> tuple[str | None, str | None]:
    asset_type = _normalize_asset_type(item.asset_type)
    override_key = (
        item.chainage_start,
        item.chainage_end,
        item.direction,
        asset_type,
    )
    if (item.latitude in (None, 0.0) and item.longitude in (None, 0.0)):
        override = _DMS_COORDINATE_OVERRIDES.get(override_key)
        if override is not None:
            return override

    return (
        _format_decimal_coordinate(item.latitude),
        _format_decimal_coordinate(item.longitude),
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
    """Serialize one inventory_wide row using NHIT field sparsity rules."""
    asset_type = _normalize_asset_type(item.asset_type)
    latitude, longitude = _resolve_coordinates(item)

    row: dict = {
        'project_name': item.project_name,
        'chainage_end': item.chainage_end,
        'direction': item.direction,
        'asset_type': asset_type,
        'latitude': latitude,
        'longitude': longitude,
        'date': item.survey_date.isoformat() if item.survey_date else None,
    }

    if not _should_omit_chainage_start(item):
        row['chainage_start'] = item.chainage_start

    source = _serialize_source(item.source)
    if source is not None:
        row['source'] = source

    if item.direction != 'Median':
        carriage_type = (item.carriage_type or '').strip()
        lane = (item.lane or '').strip()
        if carriage_type:
            row['carriage_type'] = carriage_type
        if lane:
            row['lane'] = lane

    quantity_field = ASSET_TYPE_FIELD_MAP.get(asset_type)
    if quantity_field is not None:
        quantity = _quantity_value(quantity_field, getattr(item, quantity_field, None))
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


def build_inventory_filter_groups(queryset) -> list[list[dict]]:
    """Return NHIT-style nested arrays for inventory_filter."""
    # list() is faster than iterator() against remote PostgreSQL (Neon): server-side
    # cursors add round-trip overhead without benefit for ~1.5k rows.
    items = list(
        queryset.only(*INVENTORY_FILTER_ONLY_FIELDS).order_by('-inventory_id')
    )
    rows = [serialize_inventory_wide_row(item) for item in items]
    return group_serialized_rows(rows)
