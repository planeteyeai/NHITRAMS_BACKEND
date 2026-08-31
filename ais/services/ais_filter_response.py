"""NHIT-compatible serialization for POST /ais_filter/."""

from __future__ import annotations

import math
from datetime import date

AIS_FILTER_VALUE_FIELDS = (
    'accident_id',
    'accident_date',
    'project__project_name',
    'segment__direction',
    'segment__chainage_start',
    'segment__chainage_end',
    'non_injured_accident',
    'minor_accident',
    'major_accident',
    'fatal_accident',
    'total_accident',
    'fatalities',
    'minor_injury',
    'major_injury',
    'fatal_injury',
    'total_injury',
    'head_tail',
    'head_on',
    'overturning',
    'skidding',
    'sideswipe',
    'pedestrian_hit_run',
    'animals_on_road',
    'mechanical_fault',
    'vehicle_lost_control',
    'overspeed',
    'drunk_drive',
    'fault_of_driver',
    'pedestrian_related',
    'longitude',
    'latitude',
    'grievous_accident',
    'grievous_injury',
    'nature_of_accident',
    'cause_of_accident',
)


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
    return str(value)


def serialize_ais_row(row: dict) -> dict:
    """Serialize one accidents row using NHIT field names and types."""
    return {
        'date': _format_date(row.get('accident_date')),
        'project_name': row.get('project__project_name'),
        'direction': row.get('segment__direction'),
        'chainage_start': _json_float(row.get('segment__chainage_start')),
        'chainage_end': _json_float(row.get('segment__chainage_end')),
        'non-injured_accident': _json_float(row.get('non_injured_accident')),
        'minor_accident': _json_float(row.get('minor_accident')),
        'major_accident': _json_float(row.get('major_accident')),
        'fatal_accident': _json_float(row.get('fatal_accident')),
        'total_accident': _json_float(row.get('total_accident')),
        'fatalities': _json_float(row.get('fatalities')),
        'minor_injury': _json_float(row.get('minor_injury')),
        'major_injury': _json_float(row.get('major_injury')),
        'fatal_injury': _json_float(row.get('fatal_injury')),
        'total_injury': _json_float(row.get('total_injury')),
        'head-tail': _json_float(row.get('head_tail')),
        'head-on': _json_float(row.get('head_on')),
        'overturning': _json_float(row.get('overturning')),
        'skidding': _json_float(row.get('skidding')),
        'sideswipe': _json_float(row.get('sideswipe')),
        'pedestrian/hit_&_run': _json_float(row.get('pedestrian_hit_run')),
        'animals_on_road': _json_float(row.get('animals_on_road')),
        'mechanical_fault': _json_float(row.get('mechanical_fault')),
        'vehicle_lost_control': _json_float(row.get('vehicle_lost_control')),
        'overspeed': _json_float(row.get('overspeed')),
        'drunk_&_drive': _json_float(row.get('drunk_drive')),
        'fault_of_driver': _json_float(row.get('fault_of_driver')),
        'pedestrian_related': _json_float(row.get('pedestrian_related')),
        'longitude': _json_float(row.get('longitude')),
        'latitude': _json_float(row.get('latitude')),
        'grievous_accident': _json_float(row.get('grievous_accident')),
        'grievous_injury': _json_float(row.get('grievous_injury')),
        'nature_of_accident': _text_value(row.get('nature_of_accident')),
        'cause_of_accident': _text_value(row.get('cause_of_accident')),
    }


def build_ais_filter_groups(queryset) -> list[list[dict]]:
    """Return NHIT-style nested arrays with one AIS record per inner group."""
    records = list(
        queryset.values(*AIS_FILTER_VALUE_FIELDS).order_by('accident_id')
    )
    rows = [serialize_ais_row(record) for record in records]
    return [[row] for row in rows]
