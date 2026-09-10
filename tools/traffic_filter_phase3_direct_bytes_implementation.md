# Traffic Filter — Phase 3 Direct-Byte Cache Implementation

**Status:** Implemented. API contract unchanged.

## 1. Files changed

| File | Role |
|------|------|
| `nhit/caching/traffic_filter_cache.py` | **New** — TFGZ1 encode/decode + `get_cached_traffic_filter_response` |
| `traffic/views.py` | Direct-byte HIT/MISS serving via Accept-Encoding |
| `nhit/caching/tests/test_traffic_filter_direct_bytes.py` | **New** focused HIT/MISS/corrupt/gzip/parity tests |
| `traffic/tests_traffic_filter_contract.py` | Parse JSON from `HttpResponse` content (API unchanged) |
| `traffic/tests_traffic_filter_perf.py` | Point at TFGZ1 helper |
| `tools/benchmark_traffic_filter_phase3_direct_bytes.py` | **New** benchmark |
| `tools/benchmark_traffic_filter_phase3_direct_bytes_results.json` | Benchmark output |
| `tools/traffic_filter_phase3_direct_bytes_implementation.md` | This report |

## 2. Exact optimization

On cache MISS: build existing NHIT nested groups → `JSONRenderer` → gzip once → store `TFGZ1\n` + gzip bytes.

On cache HIT with gzip: Redis GET → validate/strip magic → `HttpResponse` gzip body (no loads/render/gzip).

On cache HIT without gzip: Redis GET → gunzip to JSON bytes → `HttpResponse` (no Python object / no DRF render).

## 3. Old cache format

- Namespace: `traffic_filter`
- Scope: `''` (empty)
- Value: **pickled Python** nested `list[list[dict]]` via `cached_filter_response` / django-redis
- TTL: **300s**

## 4. New cache format

- Namespace: `traffic_filter` (unchanged)
- Scope: **`gzip_json_v1`** (new — avoids decoding historical pickle under empty scope)
- Value: **`TFGZ1\n` + gzip(JSON bytes)** (`JSONRenderer`, gzip level 6)
- TTL: **300s** (unchanged)
- No namespace version bump required; new scope creates new keys. Old pickle keys expire naturally.

## 5. Cache HIT path before

```text
Redis GET (pickle)
→ Python nested lists/dicts
→ DRF Response + JSONRenderer
→ SelectiveGZipMiddleware gzip
→ HTTP
```

## 6. Cache HIT path after (gzip client)

```text
Redis GET (TFGZ1 + gzip JSON)
→ validate TFGZ1 / strip magic
→ HttpResponse(
     body=gzip_payload,
     Content-Type=application/json,
     Content-Encoding=gzip,
     Content-Length=<gzip size>,
     Vary=Accept-Encoding,
   )
→ SelectiveGZipMiddleware sees Content-Encoding → skip (no double-gzip)
```

## 7. Gzip clients bypass JSON rendering?

**Yes.** `serve_mode='gzip_bytes'` returns cached gzip payload; no `json.loads`, no DRF render, no middleware gzip.

## 8. Double-gzip avoided?

**Yes.** Pre-set `Content-Encoding: gzip` causes Django `GZipMiddleware` to return early. Covered by tests.

## 9. Cache TTL before/after

**300s → 300s** (`CACHE_TTL_FILTER`)

## 10. Cache key behavior

- Namespace / payload digest / versioning / stampede lock: preserved
- Scope changed empty → `gzip_json_v1` (documented; same pattern as inventory `page_gzip_json_v1`)
- Corrupt TFGZ1 / legacy non-bytes → treat as miss → rebuild

## 11. API contract changes

**NONE.** Same request fields, same nested JSON structure/order/field names, no pagination, no auth change.

## 12. Test results

```text
python manage.py test nhit.caching.tests.test_traffic_filter_direct_bytes \
  traffic.tests_traffic_filter_contract traffic.tests_traffic_filter_perf --keepdb
→ Ran 27 tests — OK
```

Coverage includes: gzip HIT, non-gzip HIT, q-values, missing Accept-Encoding, corrupt TFGZ1, old pickle, miss→hit, Content-Encoding/Length/Vary, byte parity, middleware no double-gzip, TTL constant, contract tests.

## 13. Benchmark results

Environment: **local_dev_machine → Railway public Redis** (`on_railway=False`). Absolute GET ms include cloud RTT.

### Primary payload note

Live DB currently returned **1 group / 2 rows** for Abu Road (≈1 KB JSON). Earlier investigation used ~622 rows; treat primary CPU deltas as lower-bound. **Chittorgarh (large)** matches prior scale (~14k rows).

### Large (Chittorgarh, 14,249 rows) — authoritative scale

| Stage | Median | P95 |
|---|---:|---:|
| Redis GET | **428.0 ms** | 437.5 ms |
| Extract TFGZ1 | 0.014 ms | 0.056 ms |
| Gunzip (for comparison only) | 10.8 ms | 13.1 ms |
| json.loads | 61.5 ms | 65.8 ms |
| JSONRenderer | 62.6 ms | 66.6 ms |
| gzip CPU | 35.8 ms | 38.2 ms |
| Direct extract only | **0.017 ms** | 0.024 ms |
| Legacy CPU (loads+render+gzip) | **160.2 ms** | 166.1 ms |
| HTTP gzip (APIClient) | **702.3 ms** | 774.0 ms |

Sizes:

| Metric | Value |
|---|---:|
| Redis TFGZ1+gzip | **138,458** bytes |
| Uncompressed JSON | **7,528,638** bytes |
| Prior pickle size (investigation) | ~3.6 MB |

Application post-GET CPU for gzip HIT dropped from ~160 ms (loads+render+gzip on this host) to ~0.02 ms extract. Redis transfer still dominates local measurements.

### Primary (current DB, 2 rows)

| Stage | Median | P95 |
|---|---:|---:|
| Redis GET | 300.2 ms | 408.7 ms |
| Direct extract | 0.001 ms | 0.003 ms |
| Legacy CPU | 0.139 ms | 0.227 ms |
| HTTP gzip | 603.6 ms | 778.8 ms |

Redis blob: **386** bytes TFGZ1+gzip / **1,087** JSON.

## 14. Expected impact on ~6s Postman

1. **Removes** HIT-path pickle → Python → DRF JSON → middleware gzip (~160 ms CPU on large payload locally; more under load).
2. **Shrinks Redis value** dramatically vs pickled nested objects (~3.6 MB pickle → ~138 KB gzip for large case) → less transfer time.
3. Remaining ~Postman seconds, if still high after deploy, are likely **Redis RTT/bandwidth** or **client↔Railway HTTP**, not JSON CPU.

Postman should send `Accept-Encoding: gzip` (browsers/Postman usually do) to take the direct gzip path.

## 15. Remaining bottleneck

**Redis/network transfer of the cached blob** (and app↔Redis / client↔app RTT). Not PostgreSQL and not JSON/gzip CPU on optimized HIT.

Do **not** proceed to DB redesign / MVT / pagination / Redis provider changes based solely on this phase — measure Postman after deploy first.

## HIT path diagrams

### Before

```text
Redis GET (pickle) → Python nested lists → DRF JSONRenderer → GZipMiddleware → HTTP
```

### After (Accept-Encoding allows gzip)

```text
Redis GET (TFGZ1+gzip) → validate/strip TFGZ1 → HttpResponse(gzip bytes)
  Content-Type: application/json
  Content-Encoding: gzip
  Content-Length: <gzip size>
  Vary: Accept-Encoding
SelectiveGZipMiddleware sees Content-Encoding → skip (no double-gzip)
```
