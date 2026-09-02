"""Shared inventory_filter queryset construction."""

from __future__ import annotations

from typing import Any

from inventory.models import InventoryWide


def build_inventory_filter_queryset(data: dict[str, Any]):
    """Apply inventory_filter filters — same rules as InventoryFilterView."""
    queryset = InventoryWide.objects.all()

    if data.get('project_name'):
        queryset = queryset.filter(project_name__in=data['project_name'])

    if data.get('asset_type'):
        queryset = queryset.filter(asset_type__in=data['asset_type'])

    if data.get('date'):
        queryset = queryset.filter(survey_date=data['date'])

    if data.get('chainage_start') is not None:
        queryset = queryset.filter(chainage_start__gte=data['chainage_start'])

    if data.get('chainage_end') is not None:
        queryset = queryset.filter(chainage_end__lte=data['chainage_end'])

    if data.get('direction'):
        queryset = queryset.filter(direction__in=data['direction'])

    return queryset
