# Inventory Filter 50K Performance Investigation

**Status:** Investigation only. No production code, schema, index, cache-config, or API behavior changes were made.

**Artifacts:**

- `tools/investigate_inventory_filter_50k.py` (read-only probe)
- `tools/investigate_inventory_filter_50k_results.json`
- Prior context: `tools/benchmark_inventory_filter_page_sizes_results.json`, `tools/investigate_inventory_filter_phase4_report.md`

**Measurement environment:** local Windows client → Neon PostgreSQL (ap-southeast-1) + configured Redis. Wall times include WAN RTT. Railway in-container timings were not re-collected in this run.

---

## 1. Exact request payload

Discovered from existing Kothakota benchmarks / production filter semantics (`InventoryFilterSerializer` + `build_inventory_filter_queryset`):

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

After validation, `"All"` clears to empty lists (no direction/asset filters).  
Filtered population: **230,523** rows. Page returns **50,000** rows.

---

## 2. Current execution flow (inspected)

```text
POST /inventory_filter/
  → InventoryFilterSerializer
  → (no viewport+zoom) legacy branch
  → build_inventory_filter_queryset()
  → get_cached_inventory_filter_count()   # namespace inventory_filter scope=count
  → if offset >= count → {"message":"No match"}  (not page-cached)
  → get_or_set_cached_value()             # namespace inventory_filter, TTL 604800
       → build_inventory_filter_groups():
            annotate quantity_value CASE
            order_by -inventory_id
            values(core fields + quantity_value)
            slice LIMIT 50000
            list(page_qs)                 # SELECT + transfer + values dicts
            serialize_inventory_wide_row  # sparse NHIT rows
            group_serialized_rows         # nested arrays by consecutive coords
  → Response + X-Inventory-* headers
  → SelectiveGZipMiddleware
```

Relevant files: `inventory/views.py`, `inventory/services/inventory_filter_response.py`, `nhit/caching/*`.

Current TTLs:

| Cache | TTL |
|---|---|
| inventory_filter page / count / map | **604800** (7 days) |
| other filter APIs (`CACHE_TTL_FILTER`) | 300 |
| Stampede lock | **30s** |

---

## 3. Headline conclusion

**PostgreSQL is not the 11–12s bottleneck.**

| Layer | Median (this run) | Role in 11–12s story |
|---|---:|---|
| PG `EXPLAIN ANALYZE` (50k page SQL) | **~45 ms** | Negligible |
| Neon → app row transfer (`list(queryset)` / cursor) | **~1.8–2.5 s** typical; spikes to **8–13 s** | Dominant DB-side wall time |
| Python serialize 50k sparse rows | **~0.55 s** | Secondary |
| JSON render (~13.8 MB) | **~0.12 s** | Small |
| Gzip (~640 KB wire) | **~0.09 s** | Small |
| **Redis SET of full page object** | **~24 s** (one measured SET) | Dominant on cache **MISS** when Redis is used |
| **Redis GET + unpickle** of full page | **~1.7–3.4 s** | Dominant on cache **HIT** |

So ~11–12 seconds is explained by combinations of:

1. **WAN transfer variance** of a 50k-row result, and/or  
2. **Redis serialization of a ~13.8 MB nested Python object** on MISS (SET ~24s), and/or  
3. Prior cold runs where `select_ms` alone was ~20s (existing page_size benchmark).

---

## 4. Multi-sample results

### 4.1 Redis OFF (force build every time) — 5 samples

**Phase timings (in-process, no HTTP middleware):**

| Stage | Min | Median | Avg | P95 | Max |
|---|---:|---:|---:|---:|---:|
| COUNT | 199 | **207** | 241 | 380 | 380 |
| SELECT (`list(values_qs)`) | 1860 | **2231** | 2475 | 3705 | 3705 |
| Serialize | 532 | **570** | 566 | 601 | 601 |
| Grouping | 15 | **17** | 18 | 23 | 23 |
| Build total | 2480 | **2871** | 3075 | 4276 | 4276 |
| JSON render | 112 | **116** | 116 | 121 | 121 |
| Gzip | 86 | **90** | 90 | 94 | 94 |

**HTTP Client (`Accept-Encoding: gzip`), Redis disabled — 5 samples:**

| | Min | Median | Avg | P95 | Max |
|---|---:|---:|---:|---:|---:|
| HTTP ms | 2908 | **3028** | 3187 | 3547 | 3547 |
| DB queries | 2 | 2 | 2 | 2 | 2 |

Payload: **13,812,337** uncompressed bytes (~13.2 MiB), **640,611** gzip bytes (~0.61 MiB).

**First-run note from earlier aborted probe:** HTTP totals also included **~9.2 s** and **~7.2 s** outliers with `select_ms` 8192 / 6390 — high network variance. Do not treat 3s as a guaranteed production number.

### 4.2 Redis MISS (verified absent key / version bump) — 5 pipeline samples

| Stage | Min | Median | Max |
|---|---:|---:|---:|
| Redis GET (miss) | 169 | **186** | 204 |
| SELECT | 2794 | **7263** | **13280** |
| Serialize | 524 | **570** | 638 |
| Build (`build_ms`) | 3395 | **7868** | 13835 |
| Render | 110 | **117** | 126 |
| Gzip | 88 | **92** | 95 |
| **Full miss pipeline** | 30643 | **34659** | **43762** |

Miss pipeline includes **Redis SET of the full groups object**. Microbench isolated:

```text
safe_cache_set(... 50k nested groups ...)  set_ms ≈ 23987 ms (~24 s)
```

That alone can push a “cold with Redis” experience into the **30–40 s** range from this workstation.

### 4.3 Redis HIT — 5 samples

| Stage | Min | Median | Max |
|---|---:|---:|---:|
| Redis GET (+ deserialize) | 1674 | **1987** | 3377 |
| JSON render | 114 | **116** | 122 |
| Gzip | 90 | **94** | 99 |
| Pipeline | 1882 | **2193** | 3590 |
| HTTP total | 2386 | **2909** | 4439 |
| HTTP DB queries | **0** | **0** | **0** |

HIT correctly skips PostgreSQL. Remaining cost is **fetching/unpickling a huge cached Python structure** + JSON + gzip.

---

## 5. PostgreSQL EXPLAIN (ANALYZE, BUFFERS)

Exact Django page SQL (`.values()` + quantity CASE + `ORDER BY inventory_id DESC LIMIT 50000`):

| Metric | Value |
|---|---|
| Planning | **2.3 ms** |
| Execution | **44.5 ms** |
| Plan | `Limit` → **Index Scan Backward** on `inventory_wide_pkey` |
| Actual rows | 50,000 |
| Shared hit blocks | 3023 |
| Shared read | 0 |
| Temp spill | none |

**Index used:** `inventory_wide_pkey` (not a problem).  
**No sequential scan. No sort node. No disk spill.**

Server execution (~45 ms) vs app `select_ms` (~2–13 s) proves the gap is **client transfer / driver receive**, not query planning or scanning.

---

## 6. Raw cursor transfer split

### Unnamed cursor (Django-like)

| | Median |
|---|---:|
| execute_ms | **1847** (transfer occurs here) |
| fetch_ms | **53** (mostly local unpack) |
| materialize_ms | **13** |
| rows | 50000 |

### Named cursor (transfer isolated in fetch)

| | Median |
|---|---:|
| execute_ms | **401** |
| fetch_ms | **2189** |

Consistent with Phase 4: with unnamed cursors, Neon transfer time lands in `execute()`, not `fetchall()`.

---

## 7. Stage attribution for a “typical” cold Redis-off 50k call

Approximate median budget from this run:

```text
serializer          ~0.00 s
queryset build      ~0.00 s
COUNT               ~0.21 s
SELECT+transfer     ~2.23 s   ← largest non-Redis cold cost
serialize           ~0.57 s
grouping            ~0.02 s
JSON render         ~0.12 s
gzip                ~0.09 s
--------------------------------
≈ 3.2–3.5 s HTTP cold (good network)
```

When transfer spikes (observed earlier / miss samples):

```text
SELECT+transfer     ~8–13 s
+ serialize/render  ~0.7 s
≈ 9–14 s   ← matches the reported 11–12 s class
```

When Redis MISS also SETs the page:

```text
build (~3–13 s) + Redis SET (~24 s) ≈ 30–40 s pipeline
```

---

## 8. Is Redis the cause of 11–12s?

| Scenario | Finding |
|---|---|
| Redis OFF | Still multi-second; can reach ~11–12s when Neon transfer spikes |
| Redis MISS | Often **worse** than Redis OFF because of **huge Redis SET** |
| Redis HIT | No DB queries; still ~2.5–4.5s due to large Redis GET/unpickle + JSON/gzip |

**Redis is not required to explain 11–12s**, but with the current “cache the entire 50k nested response” design, Redis MISS can make cold path **much slower**, and HIT still costs seconds.

---

## 9. Noise / caveats

- High run-to-run variance on `select_ms` (WAN India → Neon Singapore).
- Prior `benchmark_inventory_filter_page_sizes_results.json` recorded 50k HTTP **~15.2 s** / phase select **~20.1 s** — same root cause class, worse network day.
- This report’s Redis-off medians (~3 s) are **optimistic** relative to the user’s 11–12s complaint; both are consistent with transfer-dominated behavior.
- Gzip middleware compresses on the way out; cache stores **uncompressed Python objects**, not gzip bytes.

---

## 10. Ranked recommended optimizations (DO NOT IMPLEMENT HERE)

### Rank 1 — Stop treating 50k table pages as an interactive default
**Why:** Returning 50k NHIT nested rows is inherently multi-MB and multi-second over WAN.  
**Expected impact:** Largest UX win. Prefer smaller `page_size` for tables; use viewport+zoom map path for maps.  
**Risk:** Product/frontend contract change if UI assumes 50k.

### Rank 2 — Do not Redis-cache multi‑MB page payloads as pickled Python lists (or compress / store bytes)
**Why:** Measured `safe_cache_set` ≈ **24 s** for one 50k page; HIT GET ≈ **2 s**.  
**Expected impact:** Removes catastrophic MISS penalty; HIT becomes cheaper if storing gzip/JSON bytes.  
**Risk:** Invalidation/versioning must stay correct; stampede lock still needed.

### Rank 3 — Reduce page payload further for table consumers
**Why:** Uncompressed ~13.8 MB drives transfer, serialize, render, Redis.  
**Ideas (future):** fewer fields, flatter structure, optional sparse modes already partly done via quantity CASE.  
**Risk:** NHIT compatibility constraints are strict.

### Rank 4 — Stream / chunk responses or lower max interactive page_size
**Why:** Hard upper bound 50k allows pathological interactive requests.  
**Expected impact:** Caps worst-case CPU/RAM/Redis.  
**Risk:** API contract / NHIT client expectations.

### Rank 5 — Measure from Railway (sin1) not India laptop before infra changes
**Why:** Phase 4 already showed Railway and Neon are colocated; local RTT inflates transfer.  
**Expected impact:** Correctly sizes whether any DB infra change is justified.  
**Risk:** None (measurement only).

### Rank 6 — DB index / geom / GiST
**Why:** EXPLAIN already ~45 ms with pkey; **not** the current 50k table bottleneck.  
**Expected impact:** Low for this specific 50k ordered page query.  
**Reserve for:** map viewport path, not table pagination.

### Rank 7 — COUNT optimization
**Why:** COUNT ~200 ms median; small vs transfer. Already separately cached.  
**Expected impact:** Minor.

---

## 11. Answers to investigation goals

| Question | Answer |
|---|---|
| Why ~11–12s for page_size=50000? | Mostly **result transfer + large payload processing**, sometimes worsened by **Redis SET/GET of the full page**. PG CPU time is ~45 ms. |
| Is Postgres slow? | **No** for this query plan. |
| Is Redis the primary cause? | **Not exclusively**; Redis MISS SET can dominate; HIT still multi-second. |
| Does HIT skip DB? | **Yes** — measured HTTP `db_queries=0`. |
| Exact payload? | Documented in §1. |
| No-match cached? | **No** — returns before page cache. |

---

## 12. Suggested next measurement (optional, still no code change)

Re-run the same probe **inside Railway** (same region as Neon) for:

- Redis OFF HTTP p50/p95  
- Redis MISS SET time  
- Redis HIT GET time  

If Railway Redis OFF p95 is already ≪ 11s, the user’s 11–12s is largely **client geography / transfer**. If Railway MISS SET remains multi-second, **Rank 2** remains urgent even in production.

---

**Bottom line:** The 50k `inventory_filter` path is slow because it **moves and transforms tens of thousands of rich JSON rows** (and currently may **cache that entire object in Redis**). The database executes the page query in tens of milliseconds; optimization priority is **payload size / caching strategy / request shape**, not new SQL indexes for this ordered page.
