# Phase 1 — inventory_filter Production Optimization Report

**Date:** 2026-09-03  
**Scope:** Railway internal Redis preference, observability, separate COUNT cache, stampede-lock wait improvement  
**Out of scope (Phase 2):** composite indexes, Redis compression, background warming, streaming, zstd, cursor pagination

---

## Files Changed

| File | Why |
|------|-----|
| `nhit/caching/redis_config.py` | **New** — resolve Redis URL (Railway internal vs local public) |
| `nhit/settings.py` | Use resolver; detect Railway via `RAILWAY_*`; perf logger |
| `nhit/caching/constants.py` | Stampede wait schedule (~5.1s backoff) instead of 20×50ms |
| `nhit/caching/service.py` | Backoff waiter; safer Redis `SET` None handling |
| `nhit/caching/inventory_filter_count.py` | **New** — filter-only COUNT cache (no page/page_size) |
| `nhit/caching/__init__.py` | Export new helpers |
| `nhit/caching/tests/test_caching.py` | Redis URL, count-key, stampede tests |
| `inventory/views.py` | COUNT cache + page cache flow + perf logging |
| `inventory/services/inventory_filter_perf.py` | **New** — request_id + sampled JSON perf logs |
| `inventory/services/inventory_filter_response.py` | Optional `timings` for select/serialize |
| `inventory/tests.py` | Count-cache, observability, pagination assert updates |
| `tools/benchmark_inventory_filter_phase1.py` | **New** — HIT/MISS query-count benchmark |
| `tools/inventory_filter_phase1_optimization_report.md` | This report |

---

## Phase 1 Changes

### 1. Railway internal Redis

- `resolve_redis_url(use_internal=...)` prefers `REDIS_INTERNAL_URL` **only when** `RAILWAY_ENVIRONMENT` or `RAILWAY_SERVICE_NAME` is set.
- Local/dev prefers `REDIS_URL` even if `REDIS_INTERNAL_URL` is present in `.env` (internal DNS is unreachable off Railway).
- No hostnames/credentials hardcoded or logged.
- `IGNORE_EXCEPTIONS`, key prefix, and versioning unchanged.

### 2. Observability

- Logger: `nhit.inventory_filter.perf`
- JSON line: `event=inventory_filter_performance` with request_id, endpoint, page, page_size, filter_hash, count_cache_hit, cache_hit, count/select/serialize/build/render/total_ms, query_count, response_bytes
- 100% of requests with `total_ms ≥ 2000`; otherwise ~10% sample
- Never logs Authorization, JWT, Redis URLs/passwords, or full bodies
- `compressed_response_bytes` omitted (gzip runs after the view)

### 3. Separate COUNT cache

- Namespace: same `inventory_filter` + version
- Scope: `count`
- Payload: filters only (excludes `page`, `page_size`)
- TTL: `CACHE_TTL_FILTER` (300s)
- Invalidation: existing `invalidate_inventory_filter_cache()` version bump covers count + page keys

### 4. Stampede protection

- Lock TTL: 30s (unchanged)
- Wait: `(0.1, 0.2, 0.3, 0.5, 0.75, 1.0, 1.0, 1.25)` ≈ **5.1s** bounded backoff (covers typical 1–3s page builds)
- Waiters reuse populated cache when ready; if wait expires, one rebuild is allowed

---

## Cache Flow

```
POST /inventory_filter/
  → validate
  → COUNT cache GET (filters only)
       HIT → total_count
       MISS → queryset.count() → COUNT cache SET
  → if offset >= total → {"message":"No match"} (no pagination headers)
  → PAGE cache GET (filters + page + page_size)
       HIT → body
       MISS → lock → SELECT/serialize/group → PAGE cache SET
  → X-Inventory-* headers
  → GZipMiddleware
```

---

## Tests

```
inventory.tests + nhit.caching.tests — OK (78 tests, 20 skipped)
Focused Phase 1 suites — OK
```

Covered: Redis URL preference, count HIT across pages/page_size, filter isolation, page cache, stampede waiter, pagination headers, no-match, gzip parity, safe perf fields.

---

## Benchmark

Kothakota → Kurnool, `2026-07-28`, `page_size=5000` (local → public Railway Redis after fix).

| Scenario | Before (audit) | After | DB Queries |
|----------|----------------|-------|------------|
| First request (count MISS + page MISS) | ~2 queries (COUNT+SELECT), ~6–9s | **2 queries**, ~9.1s | **2** |
| Page cache HIT (same page) | **1 COUNT** still ran (~1.0s+) | **0 queries**, ~3.5s | **0** |
| Different page (count HIT + page MISS) | COUNT + SELECT | **0 COUNT + 1 SELECT**, ~3.9s | **1** |
| Different page_size | COUNT again | **count HIT**, 1 SELECT | **1** |
| Different filter | COUNT + SELECT | COUNT + SELECT | **2** |

**Primary goal met:** page-cache HIT no longer executes PostgreSQL `COUNT`.

---

## Compatibility

| Contract | Status |
|----------|--------|
| Request URL / fields / defaults | Unchanged |
| Response nested arrays / sparsity / ordering | Unchanged |
| Pagination headers | Unchanged |
| No-match `{"message":"No match"}` | Unchanged |
| Gzip middleware | Unchanged |
| Auth (`AllowAny` + JWT class) | Unchanged |
| Business filter logic | Unchanged |

---

## Remaining Phase 2 Work (do not implement now)

1. Production `EXPLAIN ANALYZE` on Railway/Neon  
2. Composite index `(Project Name, Date)` **if** proven beneficial  
3. Redis value compression **if** HIT latency still justifies it after internal Redis in production  

---

## Deploy note

On Railway, ensure `REDIS_INTERNAL_URL` is set and the service has `RAILWAY_ENVIRONMENT` (default on Railway). Local `.env` may keep both URLs; local will use `REDIS_URL`.
