"""Versioned cache invalidation helpers."""

from __future__ import annotations

from nhit.caching.constants import (
    CACHE_NS_AIS_FILTER,
    CACHE_NS_DISTRESS_REPORT_FILTER,
    CACHE_NS_INVENTORY_FILTER,
    CACHE_NS_INVENTORY_SUMMARY,
    CACHE_NS_INVENTORY_MASTER,
    CACHE_NS_LABOUR_MASTERS,
    CACHE_NS_MATERIALS_LIST,
    CACHE_NS_PMS_FILTER,
    CACHE_NS_SUPPLIER_MASTERS,
    CACHE_NS_TRAFFIC_FILTER,
)
from nhit.caching.service import bump_namespace_version


def invalidate_inventory_filter_cache() -> int:
    return bump_namespace_version(CACHE_NS_INVENTORY_FILTER)


def invalidate_inventory_summary_cache() -> int:
    return bump_namespace_version(CACHE_NS_INVENTORY_SUMMARY)


def invalidate_distress_report_filter_cache() -> int:
    return bump_namespace_version(CACHE_NS_DISTRESS_REPORT_FILTER)


def invalidate_traffic_filter_cache() -> int:
    return bump_namespace_version(CACHE_NS_TRAFFIC_FILTER)


def invalidate_pms_filter_cache() -> int:
    return bump_namespace_version(CACHE_NS_PMS_FILTER)


def invalidate_ais_filter_cache() -> int:
    return bump_namespace_version(CACHE_NS_AIS_FILTER)


def invalidate_supplier_masters_cache() -> int:
    return bump_namespace_version(CACHE_NS_SUPPLIER_MASTERS)


def invalidate_labour_masters_cache() -> int:
    return bump_namespace_version(CACHE_NS_LABOUR_MASTERS)


def invalidate_inventory_master_cache() -> int:
    return bump_namespace_version(CACHE_NS_INVENTORY_MASTER)


def invalidate_materials_list_cache() -> int:
    return bump_namespace_version(CACHE_NS_MATERIALS_LIST)


def invalidate_all_filter_caches() -> None:
    for invalidate in (
        invalidate_inventory_filter_cache,
        invalidate_inventory_summary_cache,
        invalidate_distress_report_filter_cache,
        invalidate_traffic_filter_cache,
        invalidate_pms_filter_cache,
        invalidate_ais_filter_cache,
    ):
        invalidate()
