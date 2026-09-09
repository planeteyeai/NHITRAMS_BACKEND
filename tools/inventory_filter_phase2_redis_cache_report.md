# Phase 2 Deliverable — inventory_filter Redis cache representation

**Scope:** Redis representation only for legacy `POST /inventory_filter/` page responses.  
**Not in scope:** PostgreSQL, indexes, PostGIS, materialized views, map path, count key semantics, TTLs, public API.

Target request (Kothakota / 50k):

```json
{
  "chainage_start": 0,
  "chainage_end": 99999,
  "date": "2026-07-28",
  "direction": ["All"],
  "project_name": ["Kothakota to Kurnool"],
  "asset_type": ["All"],
  "page": 1,
  "page_size": 50000
}
```

---

## 1. Files changed

| File | Change |
|------|--------|
| `nhit/caching/inventory_filter_page.py` | **New** — gzip JSON encode/decode + `get_cached_inventory_filter_page` (stampede lock preserved) |
| `inventory/views.py` | Legacy page path uses `get_cached_inventory_filter_page` instead of `get_or_set_cached_value` |
| `nhit/caching/tests/test_inventory_filter_page_cache.py` | **New** — encode/decode, HIT/MISS, key isolation, corruption, invalidation, gzip HTTP, no-match |
| `inventory/tests_inventory_filter_ttl.py` | Patch target updated to `get_cached_inventory_filter_page` |
| `inventory/tests_inventory_filter_map.py` | Same patch update for legacy headers test |
| `tools/benchmark_inventory_filter_cache_format.py` | Format comparison (pickle / JSON / gzip / zlib) — measurement |
| `tools/benchmark_inventory_filter_phase2_cache.py` | OLD vs NEW path benchmark |
| `tools/benchmark_inventory_filter_cache_format_results.json` | Format bench results |
| `tools/benchmark_inventory_filter_phase2_cache_results.json` | Phase 2 path bench results |
| `tools/inventory_filter_phase2_redis_cache_report.md` | This report |

Unchanged by design: count cache, map cache, TTLs, lock TTL, queryset/grouping, serializers, public JSON.

---

## 2. Current (OLD) cache representation

- **Value:** full Python nested `list[list[dict]]` page response
- **django-redis:** default **pickle** serializer (no `COMPRESSOR` in `CACHES`)
- **Key:** `inventory_filter:v{N}:{digest}` with **empty scope**
- **TTL:** `CACHE_TTL_INVENTORY_FILTER = 604800` (7 days)
- **HIT path:** Redis GET → unpickle huge object → DRF `Response` → JSON render → HTTP gzip middleware

---

## 3. New cache representation

- **Value:** `b'IFGZ1\n' + gzip.compress(JSONRenderer().render(page), level=6)`
- **Stored via** django-redis as **bytes** (pickle wraps the compact bytes blob; no nested-object pickle)
- **Scope:** `page_gzip_json_v1` (avoids decoding historical empty-scope pickle entries as JSON)
- **HIT path:** Redis GET → verify magic → gunzip → `json.loads` → `Response(page_data)` → same HTTP gzip as before
- **Public body:** unchanged nested arrays (no wrapper, no cache metadata)

---

## 4. Why it is faster

| Problem | Fix |
|---------|-----|
| ~8.3 MB pickled object over Redis | ~641 KB compressed JSON (~13× smaller than pickle, ~21× smaller than raw JSON) |
| SET dominated by transferring/storing multi‑MB payloads | SET of ~0.64 MB |
| HIT dominated by GET + unpickle of nested Python | GET small bytes + cheap gunzip + `json.loads` |

PostgreSQL (~45 ms) was already not the bottleneck; Phase 2 only attacks Redis representation cost.

---

## 5. Compression algorithm selected

Measured on the real 50k page (`tools/benchmark_inventory_filter_cache_format_results.json`):

| Format | Size | Redis SET median | Redis GET median |
|--------|-----:|-----------------:|-----------------:|
| pickle object | 8,347,936 | ~35,025 ms | ~1,447 ms |
| raw JSON bytes | 13,812,337 | ~22,116 ms | ~1,330 ms |
| **gzip JSON** | **640,611** | **~1,385 ms** | **~197 ms** |
| zlib JSON | 640,599 | ~1,379 ms | ~297 ms |

**Chose gzip** — same size as zlib, slightly better GET in that run, matches HTTP gzip familiarity, simple `gzip` stdlib. Magic prefix `IFGZ1\n` versions the blob independently of django-redis.

---

## 6. Cache key / version changes

- Namespace: still `inventory_filter`
- Namespace version bump: still via `invalidate_inventory_filter_cache()`
- **New scope:** `page_gzip_json_v1` (digest includes scope → new keys)
- Payload still includes **all filters + `page` + `page_size`**
- Count remains `scope=count` (no page/page_size)
- Map remains `inventory_filter_map` / `map_v1`
- Old empty-scope pickle keys are orphaned until TTL; never decoded by the new path

---

## 7. TTL behavior

Unchanged:

- `CACHE_TTL_INVENTORY_FILTER = 604800` for page, count, map
- Lock: `CACHE_LOCK_TIMEOUT_SECONDS = 30` (not 7 days)
- Other APIs still use `CACHE_TTL_FILTER = 300`

---

## 8. Invalidation behavior

Unchanged: `invalidate_inventory_filter_cache()` bumps `inventory_filter` **and** `inventory_filter_map` versions. No mass key deletes.

---

## 9. Error / corruption fallback

Redis remains optimization-only:

| Failure | Behavior |
|---------|----------|
| Redis GET/SET error | Rebuild from DB; return correct response |
| Missing / wrong magic | Treat as miss → rebuild → replace |
| Corrupt gzip / JSON | Treat as miss → rebuild → replace |
| Legacy pickle object under new key | `ValueError` → miss → rebuild |
| Cache disabled | Builder only (no Redis) |

No HTTP 500 solely from a bad cache blob.

---

## 10. Tests executed and results

```text
python manage.py test nhit.caching.tests.test_inventory_filter_page_cache \
  inventory.tests_inventory_filter_ttl inventory.tests_inventory_filter_map --keepdb
→ Ran 57 tests — OK

python manage.py test inventory.tests.InventoryFilterGzipMiddlewareTests \
  inventory.tests.InventoryFilterPaginationTests \
  inventory.tests.InventoryFilterCountCacheTests --keepdb
→ OK
```

Coverage includes: MISS/HIT exact body, nested structure/field order, pagination headers, page/page_size key isolation, map/count/legacy-scope isolation, Redis failure fallback, corrupt payload fallback, old pickle rejection, invalidation, HTTP gzip parity, no-match, stores `IFGZ1` bytes not Python lists.

---

## 11–14. OLD vs NEW benchmark (50k page, this machine)

Source: `tools/benchmark_inventory_filter_phase2_cache_results.json` (5 samples; medians).

| Metric | OLD (pickle object) | NEW (IFGZ1+gzip JSON) |
|--------|--------------------:|----------------------:|
| Redis stored size | ~8.3 MB (format bench) | **640,617 B** |
| JSON uncompressed | 13,812,337 B | same |
| HTTP gzip | 640,611 B | same (middleware unchanged) |
| Redis SET median | **126,281 ms** | **693 ms** |
| Redis GET median | **2,802 ms** (includes unpickle) | **438 ms** (bytes) |
| HIT pipeline median | **3,001 ms** (get+render+gzip) | **759 ms** (get+decode+render+gzip) |
| Helper HIT median | — | **859 ms** |
| Encode (JSON+gzip) median | — | **199 ms** |
| Decode (gunzip+json) median | — | **129 ms** |

Direction matches expectations: SET and HIT Redis costs drop sharply; API contract unchanged.

---

## 15. Remaining bottleneck

Phase 2 **does not** make a cold 50k request “fast end-to-end.” After Redis is fixed:

| Stage | Approx (prior / this work) | Notes |
|-------|----------------------------|--------|
| PostgreSQL | ~45 ms | Not Phase 2 |
| Neon → app transfer + build | still multi-second on MISS | Dominant cold-path cost |
| JSON render | ~108 ms | Still paid on every HTTP response |
| HTTP gzip | ~88 ms | Unchanged |
| Redis HIT helper | ~0.86 s | Much better than ~3 s old HIT, but decode+render+gzip remain |

**Next bottlenecks (not this phase):** cold Neon transfer/build; optionally serving cached JSON bytes without full Python reconstruct (only if exact DRF/gzip parity is proven); page_size strategy / map UX — not Redis pickle.

---

## 16. API contract confirmation

| Requirement | Status |
|-------------|--------|
| `POST /inventory_filter/` | Unchanged |
| Request fields / map viewport+zoom | Unchanged |
| Nested array body, field names/order/sparse fields | Unchanged (exact JSON equality in tests) |
| Pagination headers | Unchanged |
| No-match `{"message":"No match"}` | Unchanged |
| HTTP gzip middleware | Unchanged (Redis gzip ≠ HTTP gzip) |
| Auth / filters / count+map caches / TTLs / lock | Unchanged |

**Most important:** Phase 2 only changed the **internal Redis representation** of legacy page responses to compressed JSON bytes with a versioned scope and safe fallbacks.
