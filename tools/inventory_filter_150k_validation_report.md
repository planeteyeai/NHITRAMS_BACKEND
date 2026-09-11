# inventory_filter page_size=150000 — Controlled Validation

**Validation change only: `MAX_INVENTORY_FILTER_PAGE_SIZE` 100000 → 150000.**  
Default `page_size` remains **15000**. Frontend default unchanged.

- Host: `nhitramsbackend-production.up.railway.app`
- Project: `Kothakota to Kurnool` / Date: `2026-07-28`
- page_size: `150000`

## Boundary checks

| page_size | Result |
| ---: | --- |
| 100000 | accepted (still) |
| 150000 | accepted (`200`) |
| 150001 | rejected (`400`: must be ≤ 150000) |
| omitted | default **15000** (unchanged) |

## Page 1 — Request 1

- status: `200`
- total_ms: `3651.6`
- ttfb_ms: `2643.08`
- body_transfer_ms: `1008.52`
- Content-Encoding: `gzip`
- Content-Length: `1879224`
- gzip_bytes: `1879224`
- uncompressed_json_bytes: `41960879` (~42.0 MB)
- row_count: `150000`
- X-Inventory-Page / Page-Size / Row-Count / Has-More: `1` / `150000` / `230523` / `true`
- perf: cache_hit=`1`, response_path=`direct_gzip_bytes`, server_total_ms=`1812.73`, page_data_get_ms=`1317.47`, redis_round_trips=`4`

## Page 1 — Request 2 (warm / Redis HIT)

- status: `200`
- total_ms: `3016.53`
- ttfb_ms: `2114.51`
- body_transfer_ms: `902.02`
- gzip / uncompressed: `1879224` / `41960879`
- row_count: `150000`
- **CONFIRMED Redis HIT** — `X-Inv-Perf-Cache-Hit=1`
- server_total_ms: `1892.74`
- page_data_get_ms (Redis page GET): `1374.35`

## Page 2

- status: `200`
- total_ms: `7735.13` (MISS / build path; cache_hit=`0`)
- ttfb_ms: `7269.67`
- row_count: **`80523`** (expected 230523 − 150000)
- X-Inventory-Has-More: **`false`**
- gzip / uncompressed: `985773` / `21658921`

## Page 1 ↔ Page 2 overlap

- cross-page overlap: **0** (no duplicate/missing split across pages)
- page1 within-page identity collisions: 3 (does **not** indicate pagination failure; cross-page split is clean)

## Comparison: 100K vs 150K

| Metric | 100K baseline | 150K measured |
| --- | ---: | ---: |
| rows (page1) | 100000 | 150000 |
| gzip_bytes | ~1.27 MB (1,269,383) | **1.88 MB (1,879,224)** |
| uncompressed_json_bytes | ~27 MB | **~42.0 MB (41,960,879)** |
| warm repeat total_ms | ~2490 | **3016.53** |
| warm repeat ttfb_ms | ~2370 | **2114.51** |
| warm body transfer_ms | ~120 | **~902** |
| pages to cover 230523 rows | 3 | **2** |

## Recommendation

**C. Keep 100K for UI but allow 150K for bulk/export use**

Rationale (from production measurements, not assumptions):

1. **150K is technically viable** — accepted, headers correct, pagination closes on page 2 with 80523 rows and `Has-More=false`, warm HIT works on `direct_gzip_bytes`.
2. **It is not a clear interactive win** — warm total rose (~2.5s → ~3.0s) because gzip/body transfer grew (~1.27MB → ~1.88MB; transfer ~0.1s → ~0.9s) and client JSON parse is ~0.6s on ~42MB.
3. **Default must stay 15000**; do not make 150K the UI default.
4. **Allow 150K as a max** for bulk/export callers that want fewer round trips (2 pages vs 3 for this dataset).

## Ops note

If `INVENTORY_FILTER_PERF_DEBUG` was enabled for this capture, turn it back to `false` in the Railway dashboard (no CLI required).
