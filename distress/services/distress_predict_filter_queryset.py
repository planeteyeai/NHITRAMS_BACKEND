"""Queryset builder for POST /distress_predict_filter/."""

from __future__ import annotations

from typing import Any

from django.db.models import Q, QuerySet

from distress.models import Distress
from distress.services.filter_helpers import apply_carriage_type_filter


def build_distress_predict_filter_queryset(data: dict[str, Any]) -> QuerySet:
    """Apply the same filters as the legacy DistressPredictFilterView."""
    queryset = Distress.objects.select_related('segment', 'project', 'distress_type')

    queryset = queryset.filter(
        latitude__isnull=False,
        longitude__isnull=False,
        project__isnull=False,
    )

    if 'chainage_start' in data and data['chainage_start'] is not None:
        queryset = queryset.filter(segment__chainage_start__gte=data['chainage_start'])
    if 'chainage_end' in data and data['chainage_end'] is not None:
        queryset = queryset.filter(segment__chainage_end__lte=data['chainage_end'])
    if 'date' in data and data['date']:
        queryset = queryset.filter(distress_date=data['date'])
    if 'direction' in data and data['direction'] and not any(
        d.lower() == 'all' for d in data['direction']
    ):
        queryset = queryset.filter(segment__direction__in=data['direction'])
    if 'project_name' in data and data['project_name'] and not any(
        p.lower() == 'all' for p in data['project_name']
    ):
        q_objects = Q()
        for name in data['project_name']:
            q_objects |= Q(project__project_name__iexact=name)
        queryset = queryset.filter(q_objects)
    if 'distress_type' in data and data['distress_type'] and not any(
        dt.lower() == 'all' for dt in data['distress_type']
    ):
        q_objects = Q()
        for dtype in data['distress_type']:
            q_objects |= Q(distress_type__distress_type_name__iexact=dtype)
        queryset = queryset.filter(q_objects)

    queryset = apply_carriage_type_filter(queryset, data)
    return queryset.order_by('segment__chainage_start', 'distress_id')
