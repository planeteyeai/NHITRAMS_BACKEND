# Investigation: Service Road mismatch (`inventory_summary` vs `inventory_filter`)

**Status:** Investigation only — no production code changed.  
**Project:** `Kothakota to Kurnool`  
**Date:** `2026-07-28`  
**Filter:** chainage 0–99999, direction All, asset_type All  

Artifacts:

- `tools/investigate_service_road_mismatch.py`
- `tools/investigate_service_road_mismatch_results.json`

---

## Executive answer

For this project/date/filter, **`POST /inventory_summary/` and `POST /inventory_filter/` agree on Service Road.**

| Source | Service Road total |
|--------|-------------------:|
| `inventory_summary` | **21.334** |
| SQL `SUM("Service Road")` on same filters | **21.334** |
| Sum of quantities `inventory_filter` would **emit** (serialize rules, full dataset) | **21.334** |
| Sum of `service_road` on **page 1 only** (`page_size=15000`) | **0** |
| Claimed dashboard value `39.33` | **Not reproducible** from Neon `inventory_wide` or from inventory_filter serialization |

**Root cause of the perceived “summary is wrong / filter is right” story:**

1. **There is no backend disagreement** — both APIs derive from the same `inventory_wide."Service Road"` column under the same filters; full-dataset totals match at **21.334**.
2. **Page 1 of `/inventory_filter/` contains zero Service Road rows** for this filter (ordering is `-inventory_id`; all 34 Service Road rows fall **after** the first 15 000 rows). A UI that only inspects page 1 cannot “see” Service Road quantities at all (`page1_sum=0`).
3. **`39.33` does not exist** as `SUM("Service Road")` for any date of this project in `inventory_wide` (only date present: 2026-07-28 → 21.334). It is not produced by inventory_filter’s sparse `service_road` field either.

So summary is **not** returning a “lower wrong” value relative to inventory_filter. If the dashboard shows **39.33**, that value is **not** coming from summing current Neon inventory_filter Service Road quantities for this filter.

---

## Method

Same validated body as production:

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

Compared:

1. Current `build_inventory_summary_aggregates()` → `service_road`
2. Raw SQL column aggregates on `inventory_wide`
3. **Serialize-equivalent** sum (what inventory_filter includes as `service_road`):
   - `asset_type = 'Service Road'`
   - AND `"Service Road" <> 0`  
   (matches `ASSET_TYPE_FIELD_MAP` + `_quantity_value` zero-omission)
4. Real `build_inventory_filter_groups(..., limit=15000)` page 1 emissions
5. Pagination position of Service Road rows under `ORDER BY inventory_id DESC`
6. Alternate formulas that might yield ~39.33

---

## Findings

### 1. Row inventory

| Metric | Value |
|--------|------:|
| Filtered rows | 230 523 |
| Rows with `Asset type = 'Service Road'` | 34 |
| Of those with `"Service Road" <> 0` | 16 |
| Of those with `"Service Road" = 0` | 18 |

Non-zero lengths sum to **21.334** (Increasing **10.0** + Decreasing **11.334**).

### 2. Summary vs filter serialize path

```
summary_api                              = 21.334
sql SUM("Service Road")                  = 21.334
inventory_filter serialize-equivalent SUM = 21.334
```

`summary_equals_filter_serialize_equiv = true`

### 3. Why page 1 looks empty for Service Road

`inventory_filter` orders by **`-inventory_id`**.

All 34 Service Road rows have `row_number > 15000` in that order → **`page_bucket = later_page`**.

Therefore:

```
inventory_filter page 1 service_road emissions = 0
inventory_filter page 1 service_road sum       = 0
```

A client that only loads page 1 **never receives** Service Road quantity fields for this filter, even though 16 non-zero Service Road records exist later in the result set.

### 4. Claimed `39.33`

Checked hypotheses (same filters):

| Hypothesis | Result |
|------------|-------:|
| `SUM("Service Road")` | 21.334 |
| `SUM(ABS(chainage_end - chainage_start))` | 0 |
| Increasing only | 10.0 |
| Decreasing only | 11.334 |
| `Service Road + Adjacent Road` | 84.334 |
| `Service Road + Slip Roads` | 35.334 |
| Other dates for this project near 39.33 | **none** (only 2026-07-28) |

`claimed_dashboard_39_33_reproduced = false`

---

## Root cause (precise)

**PRIMARY FINDING:**

> For Kothakota → Kurnool / 2026-07-28, `inventory_summary.service_road` (**21.334**) equals the Service Road total implied by `inventory_filter` serialization over the **full** filtered dataset. The summary API is not under-counting relative to inventory_filter.

**SECONDARY FINDING (UX / pagination illusion):**

> With `page_size=15000`, page 1 contains **no** Service Road rows (they sort later by `inventory_id`). So inventory_filter “doesn’t show” Service Road on the first page even though the data exists — this can look like a summary/filter disagreement if the UI only inspects page 1.

**TERTIARY FINDING (39.33):**

> The value **39.33** is **not** present in Neon `inventory_wide` Service Road aggregates for this project, and is **not** recoverable from inventory_filter quantity semantics. It must come from another source (different dataset/export, different filter, cached UI state, or a frontend formula not in this backend). That source is **not** identified in this repo’s backend.

---

## What is NOT the root cause

- Summary using a different project/date than the stated filter  
- Summary omitting `Tree`-style label variants for Service Road (all SR rows are exactly `Asset type = 'Service Road'`)  
- Summary vs filter using different quantity columns  
- Float truncation to produce 39.33  

---

## Recommended next steps (still no code change required to conclude)

1. **Confirm on the live dashboard** whether Service Road **39.33** is still shown for this exact filter against production Neon.  
2. If yes, capture the **network response** of `/inventory_filter/` (all pages or asset_type=`["Service Road"]`) and `/inventory_summary/` and compare.  
3. Call inventory_filter with `"asset_type": ["Service Road"]"` — expected quantity sum remains **21.334**, not 39.33.  
4. Only if a frontend formula producing 39.33 is found should summary be changed; do **not** change summary to 39.33 without that evidence.

---

## Decision

```
ROOT CAUSE: NO BACKEND MISMATCH BETWEEN inventory_summary AND inventory_filter
            FOR service_road ON THIS FILTER.

            Both equal SUM("Service Road") = 21.334.
            Dashboard 39.33 is unexplained by current Neon inventory_wide /
            inventory_filter semantics.
            Page-1 empty Service Road is a pagination/ordering effect, not a
            summary bug.
```

**No production fix is justified for Service Road until 39.33’s real source is identified.**
