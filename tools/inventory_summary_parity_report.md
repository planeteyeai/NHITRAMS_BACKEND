# Inventory Summary API — Parity Correction Report

**Project verified:** `Kothakota to Kurnool`  
**Date:** `2026-07-28`  
**Filter:** chainage 0–99999, direction All, asset_type All

---

## Corrected aggregation rules (from DB evidence)

| Rule | Definition |
|------|------------|
| Default | `SUM(column)` over the **filtered** `inventory_wide` queryset — **not** gated on `"Asset type"` matching the map label |
| `toll_plaza` | `COUNT(DISTINCT (Latitude, Longitude))` where `Toll Plaza > 0` OR `"Asset type" = 'Toll Plaza'` |
| NULL | `Coalesce(sum, 0)` |
| Precision | Integers as `int`; non-integers keep float (float noise trimmed) |
| Pagination | `page` / `page_size` ignored |

Still **one** PostgreSQL `aggregate()` query. Redis cache unchanged. `/inventory_filter/` untouched.

---

## Why previous API mismatched

Old rule: `SUM(field) WHERE asset_type = ASSET_TYPE_FIELD_MAP label`

| Issue | Evidence on Kothakota / 2026-07-28 |
|-------|-------------------------------------|
| Trees | Label `Tree` (1755) + `Trees` (12612); typed SUM missed `Tree` → 12612 vs **14367** |
| Delineators | Label is **`Delineator`** (singular); map key `Delineators` → typed SUM **0** vs column SUM **481** |
| Crash Barrier | 10 m also on `Slip Roads` rows; typed SUM 62777 vs column SUM **62787** |
| Toll Plaza | 3 direction rows, one lat/lon; SUM=3 vs site count **1** |
| Service Road | See below |

---

## Parity table (this filter)

| Metric | Existing Dashboard | New API | Difference | Calculation |
|---|---:|---:|---:|---|
| trees | 14367 | **14367** | **0** | `SUM("Trees")` |
| delineators | 481 | **481** | **0** | `SUM("Delineators")` |
| crash_barrier | 62787 | **62787** | **0** | `SUM("Crash Barrier")` |
| toll_plaza | 1 | **1** | **0** | `COUNT(DISTINCT (Latitude, Longitude))` filtered |
| service_road | 39.33 (claimed) | **21.334** | **-17.996** | `SUM("Service Road")` = **21.334** in DB |

### Service Road — not inventing a formula

Under this exact filter, `inventory_wide` contains **34** Service Road rows whose `"Service Road"` values sum to **21.334** (Increasing 10.0 + Decreasing 11.334).

Checked:

- No project/date in `inventory_wide` has `SUM("Service Road") ≈ 39.33`
- Direction filters, max-per-chainage, and lat/lon dedupe do **not** produce 39.33
- 39.33 appeared earlier as a **conceptual example** in the original API prompt; it is **not** reproducible from Neon `inventory_wide` for Kothakota / 2026-07-28

**API returns the DB truth: `21.334`.**  
If the live UI still shows `39.33`, that value is coming from another source or a frontend formula that is **not** present in this backend/repo — provide that formula/source to adjust.

---

## Actual API response (verified)

```json
{
  "trees": 14367,
  "culvert": 147,
  "street_lights": 1049,
  "bridges": 0,
  "traffic_signals": 0,
  "bus_stop": 44,
  "truck_layby": 3,
  "toll_plaza": 1,
  "adjacent_road": 63,
  "toilet_blocks": 3,
  "rest_area": 0,
  "rcc_drain": 0,
  "fuel_station": 28,
  "emergency_call_box": 62,
  "tunnels": 0,
  "footpath": 0,
  "junction": 84,
  "sign_boards": 1313,
  "solar_blinker": 119,
  "median_plants": 0,
  "service_road": 21.334,
  "km_stones": 230,
  "crash_barrier": 62787,
  "median_opening": 42,
  "kerb": 148521,
  "guard_post": 617,
  "delineators": 481,
  "pedestrian_guard_rail": 0,
  "slip_roads": 14,
  "bus_bays": 44
}
```

---

## Performance (local → Neon)

| Metric | Value |
|--------|------:|
| Cold DB queries | **1** |
| Redis/LocMem HIT DB queries | **0** (builder not called on repeat) |
| Aggregate wall (local) | ~1142 ms |
| HTTP cold | ~1429 ms |
| HTTP HIT | ~265 ms |
| Response JSON | **515 B** |
| Materialized view | **Not needed** |

---

## Tests

11 summary-related tests **OK**.

---

## Files touched in this correction

- `inventory/services/inventory_summary.py` — unrestricted column SUM + toll distinct location
- `inventory/tests.py` — updated parity/unit expectations
- `tools/verify_inventory_summary_parity.py` — Kothakota parity harness
- `tools/inventory_summary_parity_results.json` — measured output
- `tools/inventory_summary_api_report.md` — superseded by this parity report for aggregation rules

---

## Bottom line

| Metric | Status |
|--------|--------|
| Trees / Delineators / Crash Barrier / Toll Plaza | **Match dashboard** |
| Service Road | **Matches DB `SUM(Service Road)=21.334`**; claimed `39.33` unexplained from `inventory_wide` |

Architecture constraints preserved: 1 SQL aggregate, Redis, same request body, no row materialization, no inventory_filter changes, no materialized view.
