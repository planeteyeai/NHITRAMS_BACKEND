"""NHIT-compatible serialization for POST /traffic_filter/."""

from __future__ import annotations

import math
from datetime import date

TRAFFIC_FILTER_VALUE_FIELDS = (
    'survey_date',
    'year_val',
    'length_val',
    'three_wheeler_auto',
    'car_jeep_van_taxi',
    'standard_bus',
    'lcv',
    'two_axle_trucks',
    'three_axle_trucks',
    'mav',
    'osv',
    'tractor',
    'others',
    'aadt_vehicles',
    'aadt_pcu',
    'cvd_vehicles',
    'latitude',
    'longitude',
    'toll_plaza',
    'project__road_code',
    'project__project_name',
    'project__district',
    'project__block_taluka',
    'segment__chainage_start',
    'segment__chainage_end',
    'segment__direction',
)

_DIRECTION_ORDER = {'Increasing': 0, 'Decreasing': 1}


def _json_number(value):
    """Return JSON-safe numbers without rounding."""
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    if number.is_integer():
        return int(number) if abs(number) < 2**53 else number
    return number


def _json_float(value):
    """Preserve float values from the database without rounding."""
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def _format_date(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def _text_value(value) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def serialize_traffic_row(row: dict) -> dict:
    """Serialize one traffic row using NHIT field names and types."""
    road_code = _text_value(row.get('project__road_code'))
    return {
        'date': _format_date(row.get('survey_date')),
        'road_code': road_code,
        'project_name': row.get('project__project_name'),
        'block': _text_value(row.get('project__block_taluka')),
        'district': _text_value(row.get('project__district')),
        'year': _json_float(row.get('year_val')),
        'direction': row.get('segment__direction'),
        'length': _json_float(row.get('length_val')),
        'chainage_start': _json_float(row.get('segment__chainage_start')),
        'chainage_end': _json_float(row.get('segment__chainage_end')),
        '3-wheeler/_auto': _json_number(row.get('three_wheeler_auto')),
        'car/_jeep/_van/_taxi': _json_number(row.get('car_jeep_van_taxi')),
        'standard_bus': _json_number(row.get('standard_bus')),
        'lcv': _json_number(row.get('lcv')),
        '2-axle_trucks': _json_number(row.get('two_axle_trucks')),
        '3-axle_trucks': _json_number(row.get('three_axle_trucks')),
        'mav': _json_number(row.get('mav')),
        'osv': _json_number(row.get('osv')),
        'tractor': _json_number(row.get('tractor')),
        'others': _json_number(row.get('others')),
        'aadt_in_vehicles': _json_number(row.get('aadt_vehicles')),
        'aadt_in_pcu': _json_float(row.get('aadt_pcu')),
        'cvd_in_vehicles': _json_float(row.get('cvd_vehicles')),
        'longitude': _json_float(row.get('longitude')),
        'latitude': _json_float(row.get('latitude')),
        'toll_plaza': _text_value(row.get('toll_plaza')),
    }


def _chainage_group_key(row: dict) -> tuple:
    return row.get('chainage_start'), row.get('chainage_end')


def _direction_sort_key(row: dict) -> tuple:
    direction = row.get('direction') or ''
    return (
        _DIRECTION_ORDER.get(direction, 99),
        direction,
    )


def group_traffic_rows(rows: list[dict]) -> list[list[dict]]:
    """Group consecutive rows sharing chainage; Increasing before Decreasing."""
    groups: list[list[dict]] = []
    bucket: dict[tuple, list[dict]] = {}
    order: list[tuple] = []

    for row in rows:
        key = _chainage_group_key(row)
        if key not in bucket:
            bucket[key] = []
            order.append(key)
        bucket[key].append(row)

    for key in order:
        group = sorted(bucket[key], key=_direction_sort_key)
        groups.append(group)

    return groups


def build_traffic_filter_groups(queryset) -> list[list[dict]]:
    """Return NHIT-style nested traffic arrays."""
    records = list(
        queryset.values(*TRAFFIC_FILTER_VALUE_FIELDS).order_by(
            'segment__chainage_start',
            'segment__chainage_end',
        )
    )
    rows = [serialize_traffic_row(record) for record in records]
    return group_traffic_rows(rows)
