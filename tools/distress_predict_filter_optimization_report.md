# distress_predict_filter optimization report

Endpoint: `POST /distress_predict_filter/`  
Date: 2026-09-15  
Scope: Redis RTT reduction + evidence-backed MISS DB optimizations  
API contract: **unchanged**

Baseline source: `distress_predict_filter_perf_audit_report.json`  
After source: `tools/distress_predict_filter_optimization_bench.json` (MEASURED)

Measurement context (local workstation → Neon DB + Railway **public Redis proxy**):

| Item | Value |
|------|-------|
| Redis host class | `railway_public_proxy` (hostname class `*.rlwy.net`) |
| Redis port | `45692` |
| `REDIS_INTERNAL_URL` set | yes (not used off Railway) |
| `REDIS_URL` set | yes (used locally) |
| Railway runtime | false |
| Django cache backend | `django_redis.cache.RedisCache` |
| Client | `django_redis.client.DefaultClient` |
| Parser / pool | django-redis DefaultClient connection reuse via `LOCATION` |
| Prefer private Redis on Railway | yes (`resolve_redis_url(use_internal=True)` when `RAILWAY_*` present) |

**No credentials or URLs are logged in this report.**

`DISTRESS_PREDICT_FILTER_PERF_DEBUG` default remains **false**.

---

## Current baseline

### Warm HIT (audit payload, 1,226 rows, page_size=200000)

| Metric | BEFORE (audit) | AFTER (opt) | Δ |
|--------|----------------|-------------|---|
| Redis round trips | **4** sequential GETs | **2** (version GET + MGET) | −50% |
| server_total_ms median | **1475.86** | **1069.67** | **−406 ms (−27.5%)** MEASURED |
| server_total_ms P95 | 1816.67 | 1687.81 | −129 ms |
| server_total_ms min | 1366.14 | 834.77 | −531 ms |
| client wall median | 1477.39 | 1072.12 | −405 ms MEASURED |
| cache decode / DPGZ1 | ~0.01 ms | ~0.01 ms | unchanged |
| JSON parse/render/gzip on HIT | 0 | 0 | unchanged |
| PostgreSQL on HIT | 0 | 0 | unchanged |
| response path | `direct_gzip_bytes` | `direct_gzip_bytes` | unchanged |
| gzip body | 24,297 B | 24,297 B | unchanged |

### Redis microbenchmark (audit, same public proxy)

| Pattern | Time |
|---------|------|
| 4 sequential GETs | ~1462 ms |
| 4 pipelined GETs / MGET | ~399 ms |

Observed after-opt HIT Redis composition (MEASURED medians):

- namespace version GET ≈ **425 ms**
- MGET(count, page) ≈ **595 ms**
- total ≈ **1020 ms** ≈ server HIT (decode negligible)

---

## Redis optimization

### Phase 1 — Redis path verification (no infra change)

Local Django uses Railway **public** Redis proxy (expected off-platform).  
Production Railway prefers `REDIS_INTERNAL_URL` when present.  
No second Redis configuration was introduced.

### Phase 2–5 — Round-trip reduction (implemented)

**Chosen design (simplest correct):**

1. Fetch namespace version **once** per request  
2. `cache.get_many([count_key, page_key])` — one Redis RTT  
3. Keep separate count + DPGZ1 page keys (no cache-format change)  
4. Partial miss → rebuild only missing piece with shared `version=`  
5. Beyond-last-page → count only; skip page rebuild  
6. Corrupt count/page → rebuild; never 500

**Rejected for this phase:**

- Embedding count into DPGZ1 page blob (complexity / invalidation risk)  
- Blindly combining into one key  
- Removing version validation

**Files:**

- `nhit/caching/distress_predict_filter_bundle.py` (new)
- `nhit/caching/service.py` (`version=` on `get_or_set`, `safe_cache_get_many` timings, `safe_cache_delete`)
- `nhit/caching/distress_predict_filter_{count,page}.py` (`version=` reuse)
- `distress/views.py` (wire bundle; debug fields)

| | |
|--|--|
| Measured improvement | HIT median −27.5% on public proxy |
| Risk | Low (fallback to builders; DPGZ1 unchanged) |
| Complexity | Low–medium |
| Rollback | Revert view to sequential count+page helpers |

---

## Database optimization

### Index (MISS)

EXPLAIN **before** (audit SQL pattern): `Parallel Seq Scan on predicted_distress`  
EXPLAIN **after**: `Index Scan using predicted_distress_proj_date_chain_idx`

Index:

```sql
CREATE INDEX CONCURRENTLY predicted_distress_proj_date_chain_idx
ON predicted_distress (
  (upper(project_name)),
  distress_date,
  chainage_start,
  predicted_distress_id
);
```

Migration: `distress/migrations/0006_predicted_distress_proj_date_chain_idx.py`  
(skips safely if unmanaged table absent in test DB; `atomic = False`)

### Projection (MISS)

`build_distress_predict_filter_page` now uses `.values(...)` for response columns only  
(ORM instance → dict path removed). HTTP fields unchanged.

### COUNT

Same filter index used (`Index Scan` on COUNT path EXPLAIN). Count semantics unchanged.

### Pagination

Still `LIMIT/OFFSET` + `ORDER BY chainage_start, predicted_distress_id`.  
Keyset pagination **not** implemented (API contract). Future option only.

### MISS timings (audit payload, MEASURED)

| Stage | BEFORE | AFTER | Notes |
|-------|--------|-------|-------|
| count_ms | 608.63 | 899.31 | Neon RTT variance; EXPLAIN now Index Scan |
| select_ms | 2636.24 | **481.07** | **−81.8%** MEASURED |
| repair_lookup_ms | 110.71 | 110.67 | unchanged |
| serialize_ms | 5.9 | 14.73 | still negligible |
| build_ms | 2753.04 | **606.73** | **−78.0%** MEASURED |
| server_total_ms (MISS) | 8100.28 | 7124.3 | includes Redis writes on public proxy |

| | |
|--|--|
| Measured improvement | select/build dominated; Index Scan confirmed |
| Risk | Low (additive index; concurrent create) |
| Complexity | Low |
| Rollback | `migrate distress 0005` drops index |

---

## Before/after timings

### Audit payload warm HIT (n=20)

| | BEFORE | AFTER |
|--|--------|-------|
| server median | 1475.86 | **1069.67** |
| server P95 | 1816.67 | 1687.81 |
| client median | 1477.39 | **1072.12** |

### Large payload (~106,626 rows, no date)

MEASURED after-opt:

| | MISS | warm HIT (mixed early rebuilds then stable HIT) |
|--|------|--------------------------------------------------|
| rows | 106626 | 106626 |
| gzip bytes | 1,230,693 | 1,230,693 |
| JSON bytes | 57,651,235 | (same cached) |
| server_total | 30517.82 ms | median series **3974 ms** (includes early rebuilds) |
| true `mget_hit` MGET | — | ~2.3–4.6 s (1.2 MB transfer over public proxy) |
| Redis RTTs | 2 | 2 |

**Note:** Large HIT latency is dominated by **Redis payload transfer** over the public proxy, not Python/JSON. On Railway private Redis this transfer cost should drop sharply (ESTIMATED; not re-measured on private network in this phase).

---

## Redis round trips before/after

| Path | BEFORE | AFTER |
|------|--------|-------|
| Warm HIT | 4 | **2** |
| Partial miss | 4+ rebuild | 2 + targeted rebuild |
| Beyond last page | 4 (wasted page get) | 1–2 (count only) |

---

## Production payload sizes

| Dataset | rows | gzip | JSON (approx) |
|---------|------|------|----------------|
| Abu Road + 2026-08-20 + chainage 0–10000 | 1,226 | 24,297 B | ~671 KB |
| Abu Road, no date | 106,626 | ~1.23 MB | ~57.7 MB |

---

## Cache HIT performance

- Path: version + MGET → DPGZ1 strip → `HttpResponse` gzip passthrough  
- `lookup_path=mget_hit`, `response_path=direct_gzip_bytes`  
- Decode ~0.01 ms (small) / ~0.3 ms (large)  
- Auth / serializer still negligible (not re-optimized)

---

## Cache MISS performance

- Index scan + `.values()` projection  
- Repair batch unchanged  
- Cache write still expensive for ~1.2 MB over public proxy (SET probe ~12 s for 1.2 MB MEASURED)

---

## Query EXPLAIN before/after

**Before:** Parallel Seq Scan + Sort/Gather Merge (cost ~41k)  
**After:** Index Scan on `predicted_distress_proj_date_chain_idx` (cost ~28)

---

## Memory before/after

Not re-profiled in this phase. HIT still serves cached gzip bytes (no JSON rebuild).  
`.values()` reduces ORM object overhead on MISS (ESTIMATED memory win; not heap-traced here).

---

## API contract verification

Verified via existing contract/direct-byte tests + warm HIT samples:

- URL / method / JWT auth unchanged  
- JSON nested `[rows]` structure unchanged  
- Sparse distress fields + repair quantity fields unchanged  
- Headers: `X-Distress-Paginated|Page|Page-Size|Row-Count|Has-More`  
- `Content-Encoding: gzip` on Accept-Encoding gzip  
- `page_size` max 200000  
- No-match beyond last page unchanged  
- DPGZ1 magic retained (no flush / no new magic required)

---

## Regression test results

| Suite | Result |
|-------|--------|
| `nhit.caching.tests.test_distress_predict_filter_bundle` | OK (incl. partial miss, corrupt, concurrent stampede) |
| `nhit.caching.tests.test_distress_predict_filter_direct_bytes` | OK |
| `distress.tests_predict_filter_contract` | OK |
| `distress.tests_repair` + `nhit.caching.tests.test_caching` | OK |

---

## Remaining bottlenecks

1. **Public Redis proxy RTT** (~300–500 ms per RTT) — still ~100% of small HIT time  
2. **Large cached page transfer** (~1.2 MB) over public proxy — multi-second MGET  
3. MISS repair lookup for 100k+ ids (~2 s MEASURED on large set)  
4. OFFSET pagination cost on very deep pages (unchanged by design)

---

## Recommended next optimization

1. **Confirm Railway private Redis in production** (`NHIT_REDIS_USING_INTERNAL=true`) and re-benchmark HIT there — expected largest remaining win (ESTIMATED: approach microbench ~400 ms class for small pages).  
2. Optionally store large pages with a size-aware strategy / compression tune only if private Redis HIT still transfer-bound (measure first).  
3. Do **not** optimize JWT, DPGZ1 extract, or HIT JSON (already ~0 ms).  
4. Keyset pagination only as a future API version if deep OFFSET becomes a product issue.

---

## Optimization scorecard

| Change | Measured improvement | Risk | Complexity | Rollback |
|--------|---------------------|------|------------|----------|
| Shared version + MGET (2 RTT) | HIT −27.5% median (public proxy) | Low | Low–med | Revert bundle wiring |
| Composite functional index | select −82% on audit MISS; Index Scan | Low | Low | Reverse migration |
| `.values()` projection | contributes to build −78% | Low | Low | Revert response builder |
| Embed count in DPGZ1 | not implemented | — | — | — |

---

## Success criteria

| Goal | Status |
|------|--------|
| Reduce warm HIT without API change | **Met** (4→2 RTT, −27.5% median MEASURED) |
| Evidence-backed MISS index + projection | **Met** |
| Keep DPGZ1 / headers / pagination contract | **Met** |
| PERF_DEBUG default false | **Met** |
| No secrets in reports | **Met** |
