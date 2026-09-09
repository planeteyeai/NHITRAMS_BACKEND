"""Cache namespace names and TTL defaults."""

# Large read-only filter/report APIs (5 minutes)
CACHE_TTL_FILTER = 300

# POST /inventory_filter/ only (legacy page + count + map): max lifetime 7 days.
# Freshness is controlled by namespace version invalidation, not by short TTL.
CACHE_TTL_INVENTORY_FILTER = 604800

# Stable master/reference APIs (30 minutes)
CACHE_TTL_MASTER = 1800

# Paginated list APIs (15 minutes)
CACHE_TTL_LIST = 900

CACHE_NS_INVENTORY_FILTER = 'inventory_filter'
CACHE_NS_INVENTORY_FILTER_MAP = 'inventory_filter_map'
CACHE_NS_INVENTORY_SUMMARY = 'inventory_summary'
CACHE_NS_DISTRESS_REPORT_FILTER = 'distress_report_filter'
CACHE_NS_TRAFFIC_FILTER = 'traffic_filter'
CACHE_NS_PMS_FILTER = 'pms_filter'
CACHE_NS_AIS_FILTER = 'ais_filter'

CACHE_NS_SUPPLIER_MASTERS = 'supplier_masters'
CACHE_NS_LABOUR_MASTERS = 'labour_masters'
CACHE_NS_INVENTORY_MASTER = 'inventory_master'
CACHE_NS_MATERIALS_LIST = 'materials_list'

FILTER_NAMESPACES = frozenset({
    CACHE_NS_INVENTORY_FILTER,
    CACHE_NS_INVENTORY_FILTER_MAP,
    CACHE_NS_INVENTORY_SUMMARY,
    CACHE_NS_DISTRESS_REPORT_FILTER,
    CACHE_NS_TRAFFIC_FILTER,
    CACHE_NS_PMS_FILTER,
    CACHE_NS_AIS_FILTER,
})

MASTER_NAMESPACES = frozenset({
    CACHE_NS_SUPPLIER_MASTERS,
    CACHE_NS_LABOUR_MASTERS,
    CACHE_NS_INVENTORY_MASTER,
})

LIST_NAMESPACES = frozenset({
    CACHE_NS_MATERIALS_LIST,
})

# Stampede lock for expensive filter page builds (inventory_filter typically 1–3s).
# Lock TTL must outlive a slow build; wait schedule must outlive a typical build so
# waiters reuse the populated cache instead of starting parallel rebuilds.
CACHE_LOCK_TIMEOUT_SECONDS = 30
# Bounded backoff waits (~5.1s total) — avoids tight 50ms polling and covers 1–3s builds.
CACHE_LOCK_WAIT_SCHEDULE_SECONDS = (0.1, 0.2, 0.3, 0.5, 0.75, 1.0, 1.0, 1.25)
