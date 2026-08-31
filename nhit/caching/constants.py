"""Cache namespace names and TTL defaults."""

# Large read-only filter/report APIs (5 minutes)
CACHE_TTL_FILTER = 300

# Stable master/reference APIs (30 minutes)
CACHE_TTL_MASTER = 1800

# Paginated list APIs (15 minutes)
CACHE_TTL_LIST = 900

CACHE_NS_INVENTORY_FILTER = 'inventory_filter'
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

# Short lock to reduce cache stampede on expensive filter endpoints.
CACHE_LOCK_TIMEOUT_SECONDS = 30
CACHE_LOCK_WAIT_SECONDS = 0.05
CACHE_LOCK_MAX_RETRIES = 20
