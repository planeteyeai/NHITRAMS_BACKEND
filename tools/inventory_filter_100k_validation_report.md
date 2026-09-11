# inventory_filter page_size=100000 — Controlled Validation

**Measurement only — no API/Redis/cache/DB changes made by this test.**

| Item | Value |
|---|---|
| Host | `nhitramsbackend-production.up.railway.app` |
| Mode | External HTTP (`requests`, connection reuse) |
| Project | Kothakota to Kurnool |
| Date | 2026-07-28 |
| Script | `tools/measure_inventory_filter_100k.py` |
| Results JSON | `tools/inventory_filter_100k_validation_results.json` |

---

## Summary table

| Metric | Page1 req1 | Page1 req2 | Page2 |
|---|---:|---:|---:|
| HTTP status | 200 | 200 | 200 |
| Total ms | **7185.51** | **2494.60** | **5455.83** |
| TTFB ms | 6865.30 | 2371.27 | 5371.28 |
| Body transfer ms | 320.21 | 123.32 | 84.55 |
| Content-Encoding | gzip | gzip | gzip |
| Content-Length / wire | 1,269,383 | 1,269,383 | 1,217,595 |
| Uncompressed JSON bytes | 27,035,100 (~25.8 MB) | 27,035,100 | 28,403,100 (~27.1 MB) |
| Returned rows | 100,000 | 100,000 | 100,000 |
| Groups | 92,744 | 92,744 | 85,735 |
| X-Inventory-Paginated | true | true | true |
| X-Inventory-Page | 1 | 1 | 2 |
| X-Inventory-Page-Size | 100000 | 100000 | 100000 |
| X-Inventory-Row-Count | 230523 | 230523 | 230523 |
| X-Inventory-Has-More | true | true | true |
| Client JSON parse ms | 205.43 | 195.09 | 196.34 |
| Client RSS delta | +7.4 MB | +2.6 MB | +2.5 MB |

`X-Inventory-Has-More=true` on page 2 is correct: `230523 − 200000 = 30523` rows remain for page 3.

---

## Cache test (page 1 twice)

| | Observation |
|---|---|
| Request 1 | ~7.2 s (likely MISS / cold build) |
| Request 2 | ~2.5 s (**~4.7 s faster**) |
| Gzip Content-Length | **identical** `1269383` both times |
| Cache header | **Not exposed** by API |
| Interpretation | **LIKELY HIT on request 2** (timing + identical gzip bytes). Not log-proven. |

Redis/cache HIT/MISS from server logs: **not captured** in this run.  
Server `server_total_ms` / inventory perf fields: **not measured**.  
Peak **server** memory: **not measured**.

---

## Page 2

- Status **200**
- Headers coherent (`Page=2`, `Page-Size=100000`, `Row-Count=230523`, `Has-More=true`)
- Returned **100,000** rows (nested groups)
- Different gzip size vs page 1 (`1217595`) — expected (different slice)
- Structure remains nested list; no JSON pagination object

---

## Contract validation

| Check | Result |
|---|---|
| Nested array of groups | **Yes** |
| No JSON `page` / `page_size` / `has_more` body fields | **Yes** |
| Pagination via `X-Inventory-*` only | **Yes** |
| Sparse field sample (page 1 trees row) | `project_name, chainage_end, direction, asset_type, latitude, longitude, date, chainage_start, source, trees` |
| Page 2 sample includes other sparse keys (`carriage_type`, `lane`, `kerb`) | Expected sparse-output behavior |

---

## Browser safety

**Not measured** (no Chrome run).

Proxy signal only: Python `json.loads` of ~27 MB took ~200 ms and client RSS rose several MB.  
Angular change-detection / table binding / zone.js cost is **unknown**.  
**Do not treat backend success as browser-safe.**

---

## Conclusions

### A. Backend/API safety — **PASS (for this dataset)**
Production accepts `page_size=100000`, returns 200, correct headers, 100k rows/page, total count 230523.

### B. Redis/cache performance — **LIKELY OK / not log-confirmed**
Second identical page-1 call ~3× faster with identical gzip bytes → likely page cache HIT. Exact HIT/MISS not in response headers; Railway perf logs not scraped.

### C. Network transfer — **Acceptable on this path; still large**
~1.27 MB gzip vs ~27 MB JSON. Transfer alone ~80–320 ms from this client; TTFB dominates.

### D. Browser/client safety — **NOT MEASURED**
Backend returning 100k rows ≠ safe for Chrome/Angular. Recommend a dedicated browser memory/responsiveness test before making 100k the interactive default.

---

## Artifacts

- `tools/measure_inventory_filter_100k.py`
- `tools/inventory_filter_100k_validation_results.json`
- `tools/inventory_filter_100k_validation_report.md`
