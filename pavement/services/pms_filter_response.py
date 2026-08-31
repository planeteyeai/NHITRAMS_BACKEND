"""NHIT-compatible serialization for POST /pms_filter/."""

from __future__ import annotations

import math
from collections import OrderedDict
from datetime import date
from itertools import groupby

_DIRECTION_ORDER = {'Increasing': 0, 'Decreasing': 1}

PMS_FILTER_VALUE_FIELDS = (
    'pavement_id',
    'segment_id',
    'date_val',
    'date_of_survey',
    'road_id',
    'sum_chainage',
    'latitude',
    'longitude',
    'aadt',
    'pavement_condition_score_pcs',
    'international_roughness_index_iri',
    'iri_index',
    'roughness_index_status',
    'commercial_vehicle_damage_cvd_class',
    'temperature_c',
    'rainfall_mm',
    'drainage_condition',
    'type_of_last_maintenance',
    'last_maintenance_date',
    'carriageway_width',
    'project__project_name',
    'project__district',
    'project__block_taluka',
    'segment__chainage_start',
    'segment__chainage_end',
    'segment__direction',
    'segment__carriage_type',
)


def _json_float(value):
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def _string_value(value) -> str:
    if value is None:
        return ''
    return str(value)


def _text_or_null(value) -> str | None:
    """Return null for missing/blank values on NHIT-null fields."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _format_request_date(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def _format_survey_date(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value.strftime('%d-%m-%Y')
    text = str(value).strip()
    if not text:
        return None
    if len(text) == 10 and text[4] == '-' and text[7] == '-':
        year, month, day = text.split('-')
        return f'{day}-{month}-{year}'
    return text


def _assign_sub_chainages(records: list[dict]) -> list[dict]:
    """Compute 0.01 km chainage windows within each segment/direction group."""
    enriched: list[dict] = []
    for (_segment_id, _direction), group in groupby(
        records,
        key=lambda row: (row['segment_id'], row['segment__direction']),
    ):
        for index, row in enumerate(group):
            segment_start = float(row['segment__chainage_start'])
            step = float(row['sum_chainage'])
            row = dict(row)
            row['_chainage_start'] = segment_start + index * step
            row['_chainage_end'] = segment_start + (index + 1) * step
            enriched.append(row)
    return enriched


def serialize_pms_row(row: dict) -> dict:
    """Serialize one pavement row using exact NHIT field names."""
    road_id = _text_or_null(row.get('road_id'))
    return OrderedDict([
        ('road_id', road_id),
        ('project_name', row.get('project__project_name')),
        ('district', _text_or_null(row.get('project__district'))),
        ('chainage_start', _json_float(row.get('_chainage_start'))),
        ('chainage_end', _json_float(row.get('_chainage_end'))),
        ('date_of_survey', _format_survey_date(row.get('date_of_survey'))),
        ('direction', row.get('segment__direction')),
        ('carriage_type', _string_value(row.get('segment__carriage_type'))),
        ('carriageway_width', _string_value(row.get('carriageway_width'))),
        ('pavement_condition_score_(pcs):', _string_value(row.get('pavement_condition_score_pcs'))),
        ('international_roughness_index_(iri):', _string_value(row.get('international_roughness_index_iri'))),
        ('iri_index', _string_value(row.get('iri_index'))),
        ('roughness_index_status', _string_value(row.get('roughness_index_status'))),
        ('aadt', _json_float(row.get('aadt'))),
        ('commercial_vehicle_damage_(cvd)_class:', _string_value(row.get('commercial_vehicle_damage_cvd_class'))),
        ('temperature_(°c):', _string_value(row.get('temperature_c'))),
        ('rainfall_(mm):', _json_float(row.get('rainfall_mm'))),
        ('drainage_condition:', _string_value(row.get('drainage_condition'))),
        ('last_maintenance_date', _format_request_date(row.get('last_maintenance_date'))),
        ('type_of_last_maintenance:', _text_or_null(row.get('type_of_last_maintenance'))),
        ('latitude', _json_float(row.get('latitude'))),
        ('longitude', _json_float(row.get('longitude'))),
        ('block', _text_or_null(row.get('project__block_taluka'))),
        ('date', _format_request_date(row.get('date_val'))),
        ('sum_chainage', _json_float(row.get('sum_chainage'))),
    ])


def _chainage_group_key(row: dict) -> tuple:
    return row.get('chainage_start'), row.get('chainage_end')


def _direction_sort_key(row: dict) -> tuple:
    direction = row.get('direction') or ''
    return _DIRECTION_ORDER.get(direction, 99), direction


def group_pms_rows(rows: list[dict]) -> list[list[dict]]:
    """Group rows by sub-chainage; Increasing before Decreasing in each group."""
    bucket: dict[tuple, list[dict]] = {}
    order: list[tuple] = []

    for row in rows:
        key = _chainage_group_key(row)
        if key not in bucket:
            bucket[key] = []
            order.append(key)
        bucket[key].append(row)

    groups: list[list[dict]] = []
    for key in order:
        groups.append(sorted(bucket[key], key=_direction_sort_key))
    return groups


def build_pms_filter_groups(queryset, *, direction_filter_all: bool = True) -> list[list[dict]]:
    """Return NHIT-style nested pavement arrays."""
    records = list(
        queryset.values(*PMS_FILTER_VALUE_FIELDS).order_by(
            'segment__chainage_start',
            'segment__direction',
            'pavement_id',
        )
    )
    records = _assign_sub_chainages(records)
    rows = [serialize_pms_row(record) for record in records]
    if direction_filter_all:
        return group_pms_rows(rows)
    return [[row] for row in rows]
