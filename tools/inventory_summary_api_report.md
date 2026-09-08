# Inventory Summary API — Engineering Report

## 1. Files changed

| File | Why |
|------|-----|
| `inventory/services/inventory_summary.py` | **New** — one-query PostgreSQL aggregation |
| `inventory/views.py` | **New** `InventorySummaryView` (inventory_filter unchanged) |
| `inventory/urls.py` | Route `POST /inventory_summary/` |
| `inventory/tests.py` | Summary unit/API/cache/parity tests |
| `nhit/caching/constants.py` | `CACHE_NS_INVENTORY_SUMMARY` + filter namespace set |
| `nhit/caching/inventory_summary.py` | **New** — cache helper (ignores page/page_size) |
| `nhit/caching/invalidation.py` | Invalidate summary with other filter caches |
| `nhit/caching/__init__.py` | Export new namespace constant |
| `tools/benchmark_inventory_summary.py` | Benchmark + EXPLAIN harness |
| `tools/benchmark_inventory_summary_results.json` | Measured results |
| `tools/benchmark_inventory_summary_report.md` | Short bench notes |

**Not changed:** `/inventory_filter/` contract, pagination, gzip, auth, grouping, or filter queryset semantics.

**Frontend:** Skipped per instruction — use `inventory_filter` field names; no frontend folder inspected or modified.

---

## 2. Final endpoint

```
POST /inventory_summary/
```

Same auth posture as `/inventory_filter/` (no JWT required on that route).

---

## 3. Request body

Identical to `/inventory_filter/` via `InventoryFilterSerializer`:

```json
{
  "chainage_start": 0,
  "chainage_end": 99999,
  "date": "2026-07-28",
  "direction": ["All"],
  "project_name": ["Kothakota to Kurnool"],
  "asset_type": ["All"],
  "page": 1,
  "page_size": 15000
}
```

Confirmed: same serializer / same `build_inventory_filter_queryset()` filters.  
`page` / `page_size` are accepted for compatibility and **ignored** for aggregation and cache keys.

---

## 4. Final response

Actual Kothakota → Kurnool / 2026-07-28 response (measured):

```json
{
  "trees": 12612,
  "culvert": 147,
  "street_lights": 1049,
  "bridges": 0,
  "traffic_signals": 0,
  "bus_stop": 44,
  "truck_layby": 0,
  "toll_plaza": 3,
  "adjacent_road": 63,
  "toilet_blocks": 3,
  "rest_area": 0,
  "rcc_drain": 0,
  "fuel_station": 28,
  "emergency_call_box": 62,
  "tunnels": 0,
  "footpath": 0,
  "junction": 21,
  "sign_boards": 1313,
  "solar_blinker": 119,
  "median_plants": 0,
  "service_road": 21.334000000000003,
  "km_stones": 230,
  "crash_barrier": 62777,
  "median_opening": 42,
  "kerb": 148521,
  "guard_post": 617,
  "delineators": 0,
  "pedestrian_guard_rail": 0,
  "slip_roads": 14,
  "bus_bays": 44
}
```

Keys = `ASSET_TYPE_FIELD_MAP` values (same as `inventory_filter` quantity fields). No pagination headers.

---

## 5. Aggregation mapping

Rule: for each `(Asset type label → field)` in `ASSET_TYPE_FIELD_MAP`,

`SUM(field) FILTER (WHERE "Asset type" = label)` via Django `Sum(Case(When(...), then=F(field)))`.

| Dashboard / API field | DB column / asset_type | Operation | Unit |
|---|---|---|---|
| trees | Trees / `Trees` | SUM | count (float source) |
| culvert | Culvert | SUM | count |
| street_lights | Street Lights | SUM | count |
| bridges | Bridges | SUM | count |
| traffic_signals | Traffic Signals | SUM | count |
| bus_stop | Bus Stop | SUM | count |
| truck_layby | Truck LayBy | SUM | count |
| toll_plaza | Toll Plaza | SUM | count |
| adjacent_road | Adjacent Road | SUM | quantity |
| toilet_blocks | Toilet Blocks | SUM | count |
| rest_area | Rest Area | SUM | count |
| rcc_drain | RCC Drain | SUM | quantity |
| fuel_station | Fuel Station | SUM | count |
| emergency_call_box | Emergency Call Box | SUM | count |
| tunnels | Tunnels | SUM | count |
| footpath | Footpath | SUM | quantity |
| junction | Junction | SUM | count |
| sign_boards | Sign Boards | SUM | count |
| solar_blinker | Solar Blinker | SUM | count |
| median_plants | Median Plants | SUM | count |
| service_road | Service Road | SUM | length (float km/units in source) |
| km_stones | KM Stones | SUM | count |
| crash_barrier | Crash Barrier | SUM | length |
| median_opening | Median Opening | SUM | count |
| kerb | Kerb | SUM | length |
| guard_post | Guard Post | SUM | count |
| delineators | Delineators | SUM | count |
| pedestrian_guard_rail | Pedestrian Guard Rail | SUM | quantity |
| slip_roads | Slip Roads | SUM | count |
| bus_bays | Bus Bays | SUM | count |

Whole-number floats → `int`; zeros kept as `0` (all keys always present).

---

## 6. Query count

| Path | DB queries |
|------|------------|
| Cold cache | **1** (single `aggregate`) |
| Redis / LocMem HIT | **0** |

Verified in DEBUG query capture (`query_count=1`) and cache tests (`mock_agg.call_count == 1` on repeat / page variants).

---

## 7. Performance (Kothakota, ~230k filtered rows)

| Metric | Value |
|--------|------:|
| PG EXPLAIN execution | **~265 ms** |
| Local wall aggregate (India→Neon) | ~2291 ms (RTT-dominated; still **1 row** returned) |
| Summary JSON | **525 B** |
| Summary gzip | **337 B** |
| HTTP cold (test client) | ~1118 ms |
| HTTP HIT | ~187 ms |
| inventory_filter page=15k proxy | ~6.5 s, **~3.8 MB** JSON |

No inventory row materialization. Peak RSS delta on aggregate ~7 MB.

---

## 8. Correctness

```
29 tests OK (summary + inventory_filter regression subset)
```

Includes: request compatibility, page independence, zero-filled empty shape, cache HIT, cache-key ignores page, filter isolation, field-name parity with `ASSET_TYPE_FIELD_MAP`, serialize-sum reference parity.

---

## 9. Cache

| Item | Value |
|------|-------|
| Namespace | `inventory_summary` |
| Scope | `summary` |
| Key payload | filter body **without** `page` / `page_size` |
| TTL | `CACHE_TTL_FILTER` = **300 s** |
| Versioning | same bump mechanism; included in `invalidate_all_filter_caches()` |
| Stampede lock | enabled (`use_lock=True`) |

---

## 10. Materialized view decision

**Not necessary.**

EXPLAIN shows parallel bitmap heap aggregate over existing `inventory_wide_project_date_id_idx` in ~265 ms server time. Redis HIT eliminates repeat DB work. A materialized view would add refresh/ops cost without evidence of a production bottleneck on the aggregate itself.

---

## 11. Frontend

**Not updated** (explicit instruction: do not inspect frontend; use `inventory_filter` field names).

Suggested client wiring (unchanged UI):

```
filters ──► POST /inventory_summary/  ──► Asset Summary panel
       └──► POST /inventory_filter/   ──► map / detail pages
```
