# inventory_filter Backend Performance Feasibility Audit

**Status:** ANALYSIS ONLY — no production code, schema, Redis, middleware, or API contract changes were made.  
**Date:** 2026-09-03  
**Endpoint:** `POST /inventory_filter/`  
**Large case:** Kothakota to Kurnool · `2026-07-28` · **203,386** filtered assets · frontend `page_size=5000`

---

## Executive answer

| Can we safely add it? | Optimization | Verdict |
|-----------------------|--------------|---------|
| **YES (infra)** | Railway internal Redis | Recommended P0 |
| **YES (ops)** | Query observability (log-only) | Recommended P0 |
| **CONDITIONAL** | Composite indexes | P1 after EXPLAIN on prod/Neon; do **not** add blindly |
| **CONDITIONAL** | COUNT caching (separate from page body) | P1 — high value, API-safe if done carefully |
| **CONDITIONAL** | Redis cache compression | P1 — worthwhile for **public** Redis; less urgent after internal Redis |
| **CONDITIONAL** | DB connection pooling / CONN_MAX_AGE | P2 — Neon **already** uses pooler host; avoid double-pooling |
| **CONDITIONAL** | Background cache warming | P2 — only via management command / cron; **no** Celery/RQ today |
| **NO (for now)** | StreamingHttpResponse | **NOT RECOMMENDED** for 5k pages |
| **NO (for now)** | Zstd HTTP compression | **KEEP GZIP** |
| **NO (for now)** | Parallel in-request cache warming | **NOT RECOMMENDED** without workers + stampede controls |
| **NO (API)** | Replace OFFSET with cursor pagination | Public `page`/`page_size` must stay; internal keyset only if proven |

**Recommended order:** internal Redis → observability → COUNT cache → composite index (with EXPLAIN) → Redis compression (if still needed) → optional background warm of pages 1–3 for hot filters.

---

## 1. Current architecture

### Request path

```
POST /inventory_filter/
  → DRF APIView (AllowAny; JWT auth class present but not required)
  → InventoryFilterSerializer (filters + page + page_size)
  → build_inventory_filter_queryset(data)
  → queryset.count()                         # ALWAYS — outside Redis
  → if offset >= total → {"message":"No match"}
  → cached_filter_response(builder=build_inventory_filter_groups)
       → Redis get/set (pickle via django-redis), stampede lock on miss
       → builder: .only(...).order_by('-inventory_id')[offset:offset+limit]
       → serialize rows → group by lat/lon → nested [[...], ...]
  → set X-Inventory-* headers on Response
  → DRF JSON renderer (full body in memory)
  → GZipMiddleware (HTTP gzip when Accept-Encoding includes gzip)
```

### Relevant files

| Concern | Location |
|---------|----------|
| View | `inventory/views.py` · `InventoryFilterView` |
| URL | `inventory/urls.py` · `inventory_filter/` |
| Serializer | `inventory/serializers.py` · `InventoryFilterSerializer` |
| Queryset | `inventory/services/inventory_filter_queryset.py` |
| Response builder | `inventory/services/inventory_filter_response.py` |
| Model | `inventory/models.py` · `InventoryWide` (`managed=False`, table `inventory_wide`) |
| Cache helpers | `nhit/caching/response.py`, `service.py`, `keys.py`, `constants.py` |
| Invalidation | `nhit/caching/invalidation.py` (namespace version bump; **no** auto-invalidate on `inventory_wide` rebuild) |
| Middleware | `nhit/settings.py` · `GZipMiddleware`, CORS expose headers |
| DB | PostgreSQL via `django.db.backends.postgresql`, `CONN_MAX_AGE` default **60** |
| Cache | `django_redis.cache.RedisCache` when `REDIS_URL` set; else `DummyCache` |
| Server | Gunicorn WSGI (`Dockerfile` / `docker/entrypoint.sh`), 3 workers, 120s timeout |

### Filters actually applied

From validated POST body (after `"All"` → empty list):

| Filter | Column | Operator |
|--------|--------|----------|
| `project_name` | `"Project Name"` | `IN (...)` when non-empty |
| `asset_type` | `"Asset type"` | `IN (...)` when non-empty |
| `date` | `"Date"` | equality |
| `chainage_start` | `"Chainage start"` | `>=` |
| `chainage_end` | `"Chainage end"` | `<=` |
| `direction` | `"Direction"` | `IN (...)` when non-empty |
| pagination | — | Python/SQL `OFFSET` + `LIMIT` after `ORDER BY inventory_id DESC` |

Kothakota large case typically sends `direction: ["All"]`, `asset_type: ["All"]` → those filters are **omitted**. Effective predicates: project + date + chainage range.

### Response contract (must not change)

- Body: nested arrays `[[{sparse fields...}, ...], ...]` — **not** wrapped in `{data: ...}`
- Pagination metadata: **headers only**
- Empty / beyond-last: `200` + `{"message":"No match"}` (no pagination headers)
- Gzip: middleware, not view-level

---

## 2. Current performance baseline

Sources: `tools/inventory_filter_nhit_pagination_report.md`, `tools/inventory_gzip_benchmark_report.md`, and **read-only** local probes against Neon (2026-09-03). No production schema changes.

### Paginated page (current target architecture)

| Metric | Page 1 (`page_size=5000`) | Notes |
|--------|---------------------------|-------|
| Records | 5,000 | |
| Uncompressed JSON | ~1,322,653 B (~1.3 MB) | Measured |
| HTTP gzip wire | ~65–66 KB | Matches prior report (~65,477 B) |
| JSON render | ~16 ms | Local CPU |
| Gzip CPU (JSON) | ~14 ms | Local CPU — **not** the old 2.3 s full-dataset cost |
| `queryset.count()` | ~140–700 ms wall / ~81 ms DB EXPLAIN | Outside Redis |
| Page SELECT + serialize (MISS build) | ~1.0 s page1 · ~3.1 s page40 | Includes DB transfer + Python |
| Full HTTP MISS (Test Client, gzip) | ~6.5 s | 2 queries: COUNT + SELECT |
| Full HTTP HIT (Test Client, gzip) | ~1.0 s | **Still 1 COUNT query**; body from Redis |
| Pre-pagination monolith | ~52.1 MB / ~28 s | Historical; no longer the contract |

### Phase separation (mandatory)

| Layer | Evidence |
|-------|----------|
| PostgreSQL execution (COUNT) | EXPLAIN ANALYZE **~81 ms** |
| PostgreSQL execution (page1 id-only) | EXPLAIN ANALYZE **~14 ms** |
| PostgreSQL execution (deep OFFSET id-only) | EXPLAIN ANALYZE **~83–97 ms** (sort all matches) |
| DB → Django transfer + ORM + serialize | Dominates MISS: page1 build ~1.0 s, page40 ~3.1 s |
| Redis get (public proxy) + DRF render + gzip | Dominates HIT after COUNT (~1.0 s total with COUNT) |

### Gaps (additional measurement still useful)

1. Railway **production** HIT/MISS with **internal** Redis (existing internal benchmark marked invalid when run on public URL).
2. Per-phase timers in production logs (COUNT / SELECT / serialize / cache get / gzip) — not instrumented yet.
3. Redis payload size under load for pages 1–41 of one filter set (~41 × ~0.76 MB pickle ≈ **~31 MB** Redis for one hot filter if all pages warmed).

---

## 3. StreamingHttpResponse feasibility

### Current construction

- Builds **complete page** nested list in memory (`build_inventory_filter_groups`).
- Redis stores that **entire Python object** before HTTP.
- DRF `Response(data)` → full JSON bytes → `GZipMiddleware` compresses non-streaming (and can stream-compress in Django 6).
- Relies on normal buffered response + `Content-Length` after gzip for non-streaming.

### Can streaming preserve NHIT contract?

| Concern | Assessment |
|---------|------------|
| Nested JSON / sparse fields / ordering | Possible only with a custom streaming JSON encoder that emits exact grouping — high complexity |
| Redis caching | **Conflicts** — cache needs complete value (or a second storage model) |
| Error `{"message":"No match"}` | Easy as non-streaming branch |
| Pagination headers | OK if set before streaming starts |
| Gzip | Django **6.0** `GZipMiddleware` **does** compress streaming via `compress_sequence` (verified in installed source) — unlike older “skip streaming” folklore |
| Railway / chunked encoding | Usually OK; still adds proxy/edge variables |

### Worth it for 5,000-row pages?

| Goal | Streaming help? |
|------|-----------------|
| Memory | Modest (~1.3 MB JSON already) — low priority |
| TTFB | Possible if rows streamed before full serialize — but grouping needs consecutive rows; Redis path still buffers |
| Total time | **Unlikely** — CPU still serializes same rows |
| Gzip CPU | ~14 ms already — not a bottleneck |
| Browser crash | Already solved by pagination |

### Verdict: **NOT RECOMMENDED**

SAFE WITH CONDITIONS only if: Redis bypassed/redesigned, custom streaming serializer proven byte-identical, and measured TTFB gain on Railway. For current 5k pages: **not worthwhile**.

---

## 4. PostgreSQL connection pooling feasibility

| Fact | Value |
|------|-------|
| Django | 6.0 |
| Driver | **psycopg2** 2.9.x (not psycopg3 pool) |
| Backend | `django.db.backends.postgresql` |
| `CONN_MAX_AGE` | **60** (env `DB_CONN_MAX_AGE`) |
| Host | Neon (`DB_HOST_KIND=neon`) with **`pooler` in hostname** |
| Settings note | Neon pooled connections reject `search_path` startup options |

### Approaches

| Approach | Fit |
|----------|-----|
| Django persistent connections (`CONN_MAX_AGE`) | **Already on** (60s) |
| psycopg3 / native pool | Would require driver migration — out of scope; not installed |
| External PgBouncer | Extra infra; Neon already offers pooled endpoint |
| Provider-side pooling | **Already in use** (pooler host) |

### Double-pooling risk

Neon pooler **+** long-lived Django `CONN_MAX_AGE` can interact poorly (prepared statements, connection limits) depending on Neon pool mode. Current setup already combines both.

### Verdict: **CONDITIONAL**

- **Do not** add another pool layer blindly.
- **Recommended:** keep Neon pooler; review `CONN_MAX_AGE` against Neon docs (often `0` or short with transaction pooler). Treat as **ops tune**, not a new feature.
- Expected benefit for inventory_filter: **small** vs COUNT/SELECT/serialize/Redis costs already measured.

---

## 5. Composite index audit

### Indexes present on `inventory_wide` (live `pg_indexes`)

| Index | Definition |
|-------|------------|
| `inventory_wide_pkey` | `UNIQUE (inventory_id)` |
| `inventory_wide_project_idx` | `("Project Name")` |
| `inventory_wide_date_idx` | `("Date")` |
| `inventory_wide_asset_idx` | `("Asset type")` |
| `inventory_wide_chainage_idx` | `("Chainage start", "Chainage end")` |

Model Meta declares **no** Django indexes (`managed=False`). Indexes come from `_create_inventory_wide.py` / `_refresh_inventory_wide.py` DDL.

### Query-condition table (Kothakota pattern)

| Query condition | Existing index | Frequency | EXPLAIN evidence | Candidate index | Expected benefit | Risk |
|-----------------|----------------|-----------|------------------|-----------------|------------------|------|
| `"Date" = d` | `inventory_wide_date_idx` | Every filtered request | Bitmap Index Scan on date (~203k rows) | — | Already used | Low |
| `"Project Name" = p` (filter after date) | `inventory_wide_project_idx` | Every request | **Not chosen** for COUNT; applied as Heap Filter | `("Project Name", "Date")` or `("Date", "Project Name")` | Smaller bitmap/heap; better COUNT | Write cost on refresh; must EXPLAIN after create |
| Chainage range | `inventory_wide_chainage_idx` | Always in payload | Low selectivity (0–99999); not driving plan | Do **not** lead with chainage for this case | Low | Wrong leading columns waste space |
| `ORDER BY inventory_id DESC` + LIMIT | `pkey` | Every page | Page1: **Index Scan Backward** on pkey (~14 ms) | — | Good for shallow pages | — |
| Deep `OFFSET` + ORDER BY | pkey / date | Pages ≫ 1 | Planner switches to **Bitmap date + Sort all matches** (~80–97 ms id-only) | `("Project Name", "Date", inventory_id DESC)` | May enable index-only / avoid full sort | Must validate with EXPLAIN ANALYZE; wrong order hurts |
| `asset_type` / `direction` IN | asset idx / none | When not All | Not in large default path | Defer until EXPLAIN of those filters | Unknown | Avoid speculative indexes |
| COUNT(*) matching filters | date-driven | **Every page** (even Redis HIT) | Parallel Bitmap Heap + Filter project | Composite (project, date) | Directly helps HIT path | Migration/DDL outside Django managed model |

**Do not** add an index merely because a column appears in WHERE. Chainage-leading and asset-only composites are **not** justified by the dominant Kothakota plan.

### Strongest candidate (evidence-based)

```text
("Project Name", "Date")           -- COUNT + filter selectivity
("Project Name", "Date", inventory_id DESC)  -- optional follow-up for deep pages
```

Only after a dedicated EXPLAIN ANALYZE **before/after** on Neon; create via controlled DDL (table is unmanaged) — **not** in this audit.

---

## 6. EXPLAIN / query plan audit (read-only)

### COUNT

- Plan: Parallel Bitmap Heap on `inventory_wide_date_idx`, filter project + chainage.
- Planning ~3.4 ms · Execution **~81 ms**.
- Rows: 203,386 actual; planner estimates understated (~43k in related plans).

### Page SELECT (inventory_id only)

| Page | OFFSET | Plan shape | Execution time |
|------|--------|------------|----------------|
| 1 | 0 | Index Scan Backward on **pkey** + filter | ~14 ms |
| 10 | 45,000 | Bitmap date → Sort → Limit | ~95 ms |
| 20 | 95,000 | Same + external merge disk | ~83 ms |
| 30 | 145,000 | Same | ~90 ms |
| 40 | 195,000 | Same | ~97 ms |

Deep OFFSET is a **measurable but moderate** DB cost (~80–100 ms). Full-row ORM build cost rises more (page40 ~3.1 s vs page1 ~1.0 s) due to scanning/sorting/transferring before the slice.

---

## 7. Redis cache compression feasibility

| Fact | Value |
|------|-------|
| Backend | `django_redis.cache.RedisCache` |
| Client | `DefaultClient`, `IGNORE_EXCEPTIONS=True` |
| Serializer | Default (**pickle** of Python nested lists) |
| Compression today | **None** in Redis |
| Key | `nhit` prefix + `inventory_filter:v{n}:{sha256}` including **page + page_size** |
| TTL | `CACHE_TTL_FILTER = 300` s |
| Production URL in local env | **Public** `*.proxy.rlwy.net` (`redis://`, not TLS) |
| Internal URL present? | `REDIS_INTERNAL_URL` **set** but **not** used as `REDIS_URL` |

### Measured page-1 payload

| Form | Size |
|------|------|
| Pickle (what Redis stores) | **763,990 B** (~746 KB) |
| zlib/gzip of pickle | **~73 KB** (~10×) |
| HTTP JSON gzip | ~65 KB |

### Evaluation

| Factor | Assessment |
|--------|------------|
| Memory savings | **High** if many pages cached |
| CPU | Modest compress/decompress vs 746 KB public RTT |
| HIT latency | Likely **helps on public Redis**; smaller benefit on internal |
| MISS | Slight extra CPU on SET |
| Compatibility | Needs custom serializer / compressor; **invalidates** existing pickle entries (TTL 5 min → natural expiry, or bump namespace version) |
| django-redis | Supports custom serializers — feasible without changing key scheme |
| Necessity | **Conditional** — page already small on HTTP; Redis value still ~0.75 MB |

### Verdict: **SAFE WITH CONDITIONS** → recommend **after** internal Redis, or sooner if Redis memory/public RTT hurts HITs.

Do **not** compress “because compression exists”; compress because **pickle of a page is ~12× larger than HTTP gzip JSON** and travels on every HIT.

---

## 8. Railway Redis internal network

| Environment | Finding |
|-------------|---------|
| **LOCAL DEV** | Must use **public** proxy URL; `*.railway.internal` is unreachable locally (prior benchmark tooling documents this). |
| **RAILWAY PRODUCTION** | App + Redis in same project should use `redis.railway.internal` (or Railway private URL). |
| Current `REDIS_URL` (local probe) | Public proxy host — **not** internal |
| `REDIS_INTERNAL_URL` | Present in env but unused by settings (settings only read `REDIS_URL`) |
| TLS | Public sample used `redis://` (non-TLS); follow Railway Redis URL scheme as provided — do not invent |
| Prior internal benchmark JSON | `"BENCHMARK INVALID"` / `"Redis networking is PUBLIC, expected INTERNAL"` |

### Expected benefit

Lower HIT latency and more stable Redis RTT vs public proxy — **does not change API contract**.

### Verdict: **SAFE TO IMPLEMENT** (config/ops only on Railway).  
**P0.** Local keeps public URL; Railway sets `REDIS_URL` to internal.

---

## 9. Zstandard HTTP compression feasibility

| Factor | Finding |
|--------|---------|
| Django native HTTP zstd | **No** — only `GZipMiddleware` in stack |
| Extra package | Would be required — **not** in `requirements.txt` |
| Gunicorn / WSGI | Compatible in principle with custom middleware |
| Railway / Hikari proxy | Unknown preservation of `Content-Encoding: zstd` — needs proxy proof |
| Browsers | Modern Chromium often supports zstd; not universal for all API clients |
| Other consumers | Possible non-browser clients of `/inventory_filter/` |
| Vs measured gzip on pages | Gzip already ~65 KB / ~14 ms CPU — **diminishing returns** |

### Verdict: **KEEP GZIP** / **ZSTD NOT APPROPRIATE** for now.

Do not replace gzip. Revisit only if proxy + all clients proven and page sizes grow again.

---

## 10. Parallel cache warming feasibility

### Existing async infrastructure

| Mechanism | Present? |
|-----------|----------|
| Celery / workers | **No** |
| Django-Q / RQ | **No** |
| Railway worker service | **Not** in repo Dockerfile/compose as a separate worker |
| Management commands | Yes (`benchmark_railway_redis`, `import_kml_chainage`) |
| In-request threads | Possible but unsafe with Gunicorn sync workers |

### Warm pages 2–4 after page 1?

| Risk | Detail |
|------|--------|
| DB load | Extra COUNT (today) + SELECT per page |
| Duplicate warming | Concurrent users × same filters |
| Stampede | Existing lock is **per cache key**; warming 3 keys = 3 builds |
| Memory | 3 × ~0.75 MB pickle + build CPU on request worker |
| UX | Frontend already loads sequentially — warming helps pages 2–4 only if user continues |

### Verdict: **NOT RECOMMENDED** as in-request/parallel warming.

If ever: concurrency **1** (sequential warm), max pages **2–3**, only on MISS of page 1, via **background process** — not inside the web worker.

---

## 11. Background cache warming feasibility

Separate from parallel warming.

### Triggers (design only)

| Trigger | Feasibility |
|---------|-------------|
| After deploy | Management command / Railway cron |
| After `inventory_wide` refresh | Hook in refresh script calling `invalidate_inventory_filter_cache` then warm |
| Popular project/date | Scheduled job for known hot filters |
| On inventory_report save | **Wrong target** — signals invalidate inventory **master**, not `inventory_wide` filter data |

### Proposed design (not implemented)

| Field | Proposal |
|-------|----------|
| TRIGGER | Cron or post-refresh CLI |
| TASK | `python manage.py warm_inventory_filter --project ... --date ... --pages 1-3 --page-size 5000` |
| CACHE KEY | Same as live: namespace + version + normalized payload including page/page_size |
| PAGE RANGE | **1–3** default; never all 41 unless off-peak + concurrency 1 |
| CONCURRENCY | **1** |
| TTL | Keep 300 s (or warm just before business hours) |
| FAILURE HANDLING | Log + continue; never fail deploy |

### Benefit

Measurable for **first user** after TTL expiry; limited if filters vary widely. Useful for one dominant dashboard filter.

### Verdict: **CONDITIONAL** — P2, management-command based only.

---

## 12. Query observability proposal

### Existing

- Console `LOGGING` with `nhit.cache` logger (DEBUG has hit/build_ms).
- **No** Sentry / OpenTelemetry / Prometheus in requirements.

### Lowest-complexity useful option: **structured log-only** (sampled)

Log one JSON line per `inventory_filter` request (INFO, sample 100% of slow >2s + 10% of others):

| Field | Include |
|-------|---------|
| `request_id` | Yes (middleware or DRF) |
| `endpoint` | `inventory_filter` |
| `page`, `page_size` | Yes |
| `filter_hash` | `payload_digest` (not full body) |
| `cache_hit` | Yes (plumb meta from `get_or_set_cached_value`) |
| `count_ms`, `select_ms`, `serialize_ms` | Yes |
| `json_ms`, `total_ms` | Yes |
| `response_bytes`, `compressed_bytes` | If cheap |
| `query_count` | When DEBUG or connection.queries wrapper |

### Do **not** log

JWT, passwords, Redis URLs with credentials, full filter PII dumps, response bodies.

### Verdict: **SAFE TO IMPLEMENT** · **P0** · log-only first; metrics later if needed.

---

## 13. COUNT query audit

### Facts

- SQL shape: `SELECT COUNT(*) ...` on filtered `inventory_wide`.
- Runs on **every** successful page request **before** cache lookup — including Redis **HIT** (~170 ms observed).
- Not stored in Redis today.
- HIT path: **1 query** (COUNT only). MISS: **2 queries**.

### Options

| Option | Pros | Cons | Recommendation |
|--------|------|------|----------------|
| **A. COUNT every request** | Simple, always fresh | Adds ~80–200 ms to every HIT | Status quo — suboptimal |
| **B. Cache count independently** | HIT can be **0 DB queries**; same `X-Inventory-Row-Count` | Stale up to TTL; need key = filters without page | **Recommended** |
| **C. Materialized summary table** | Fastest | Ops complexity; refresh coupling | Only if B insufficient |
| **D. Other** | e.g. approximate counts | Breaks exact header contract | **No** |

### Verdict: **Option B — CONDITIONAL / P1**

Cache `total_count` under a key derived from **filters only** (exclude `page`/`page_size`), TTL ≤ filter TTL, invalidate with `invalidate_inventory_filter_cache()`. Does **not** change response body or header names.

---

## 14. OFFSET pagination audit

- Backend uses Django slice → SQL **`LIMIT`/`OFFSET`** with `ORDER BY inventory_id DESC`.
- Deep pages: DB execution ~80–100 ms; full build slower (~3 s page40 locally).
- Replacing public API with cursors: **forbidden** for NHIT contract.

### Internal optimization while keeping `page`/`page_size`

Possible later (not now): keyset/`inventory_id` seek derived from `(page-1)*page_size` only if ordering stays total and deterministic — **hard** without a covering composite index and careful equivalence tests. Treat as P3 research.

### Verdict: OFFSET is acceptable for 41 pages; optimize indexes/COUNT/Redis first. Do **not** change the public pagination API.

---

## 15. Response ordering audit

| Item | Finding |
|------|---------|
| ORDER BY | `-inventory_id` (descending PK) |
| Stability | **Deterministic** — unique PK |
| Duplicates/missing across pages | Unlikely while data static |
| Concurrent refresh of `inventory_wide` | Rebuild/replace could shift IDs mid-pagination — operational risk |
| Index support | PK supports shallow backward scan; deep pages may not use it efficiently |

**Risk:** unmanaged full-table refresh during multi-page frontend load. Mitigation is operational (refresh off-peak + cache invalidate), not API change.

---

## 16. Page size audit

| Size | Approx pages for 203k | Uncompressed / page | Gzip / page | Notes |
|------|----------------------|---------------------|-------------|-------|
| 5000 | ~41 | ~1.3 MB | ~65 KB | Current frontend — good UX/memory balance |
| 7500 | ~28 | ~1.9 MB | ~higher | Fewer round trips; heavier parse |
| 10000 | ~21 | ~2.6 MB | ~higher | |
| 15000 | ~14 | ~3.9 MB | ~higher | API default when omitted |

**Recommendation:** keep frontend **5000** and API default **15000** unchanged. No evidence that raising page size improves backend more than it hurts browser parse/map work.

---

## 17. Cache stampede audit

Implementation (`get_or_set_cached_value`, `use_lock=True`):

- `cache.add` lock, TTL 30s, wait 0.05s × 20 retries (~1 s).
- Waiters that still miss **still run the builder** after the wait loop.

| Scenario | Result |
|----------|--------|
| 10 users × same page | One holder builds; others wait ~1s then may **also build** if lock wait too short vs ~1–3 s build |
| Lock timeout 30s | Long enough for build, but waiter max wait ~1s is **shorter than build** |

### Verdict: lock **helps but is insufficient** under concurrent MISS.

**Recommended improvement (future):** extend waiter retries to cover typical build time (or use `cache.get_or_set` / lock-wait ≈ build SLA). Do not implement in this audit.

---

## 18. Compatibility matrix

| Optimization | Compatible? | Performance Benefit | Complexity | Risk | Recommended? |
|--------------|-------------|---------------------|------------|------|--------------|
| StreamingHttpResponse | CONDITIONAL | Low (for 5k pages) | High | High (Redis + parity) | **NO** |
| DB connection pooling | CONDITIONAL | Low–medium | Medium | Double-pool with Neon | **CONDITIONAL** (tune only) |
| Composite indexes | CONDITIONAL | Medium (COUNT/deep pages) | Medium | Wrong index / write cost | **YES** after EXPLAIN |
| Redis compression | CONDITIONAL | Medium on public Redis | Medium | Serializer migration | **YES** if internal Redis not enough |
| Parallel cache warming | CONDITIONAL | Low–medium | High | Stampede / worker load | **NO** |
| Railway internal Redis | YES | Medium–high on HIT | Low | Misconfig local vs prod | **YES** |
| Zstd HTTP compression | CONDITIONAL | Low vs current gzip | High | Client/proxy gaps | **NO** |
| Query observability | YES | Indirect (enables tuning) | Low | Log volume | **YES** |
| Background cache warming | CONDITIONAL | Medium for hot filters | Medium | Redis memory | **CONDITIONAL** |
| COUNT optimization | YES (if header semantics kept) | **High on HIT** | Low–medium | Stale count ≤ TTL | **YES** |
| OFFSET optimization | CONDITIONAL | Low–medium deep pages | High | Parity risk | **NO** for now |

---

## 19. Prioritization roadmap

### P0

1. **Railway internal Redis for production**  
   - Why: HIT still ~1 s locally over public proxy; body ~746 KB pickle.  
   - Impact: lower HIT latency, no API change.  
   - Difficulty: low (env). Risk: low if local keeps public URL.  
   - Migration: no. Deploy/infra: **yes**.

2. **Query observability (structured logs)**  
   - Why: missing per-phase production timings.  
   - Impact: enables evidence-based next steps.  
   - Difficulty: low. Risk: low if no secrets.  
   - API: unchanged.

### P1

3. **Cache filtered COUNT separately**  
   - Why: COUNT runs on every HIT today.  
   - Impact: HIT → **0 DB queries** possible.  
   - Difficulty: low–medium. Risk: count stale ≤ TTL (acceptable with filter cache).  
   - API: unchanged headers/body.

4. **Composite index `(Project Name, Date)`** (+ optional `inventory_id`)  
   - Why: EXPLAIN shows date-driven plans + heap filter on project; COUNT is on critical path.  
   - Impact: faster COUNT/SELECT.  
   - Difficulty: medium (unmanaged DDL). Risk: medium — require before/after EXPLAIN.  
   - Migration: **DDL yes** (not Django migration necessarily). API: no.

5. **Redis value compression** (if HIT still heavy after internal Redis)  
   - Why: 746 KB → ~73 KB.  
   - Difficulty: medium. Risk: cache format bump. API: no.

6. **Stampede wait alignment**  
   - Why: waiters can rebuild after ~1 s.  
   - Difficulty: low. Risk: low.

### P2

7. **CONN_MAX_AGE vs Neon pooler review**  
8. **Background warm pages 1–3** for known hot filters (management command)

### P3

9. Research internal keyset while preserving `page`/`page_size`  
10. Zstd / StreamingHttpResponse — only if metrics justify

---

## 20. Strict NHIT compatibility check

Every **recommended** item above changes **only** internals (Redis URL, logs, optional count cache key, optional DDL indexes, optional pickle compressor).

Must remain identical:

| Contract | Status |
|----------|--------|
| URL `POST /inventory_filter/` | Unchanged |
| POST filters + `page` + `page_size` | Unchanged |
| Nested array body / sparse fields / ordering | Unchanged |
| `X-Inventory-*` headers | Unchanged semantics (`Row-Count` = full filtered total) |
| Empty / beyond-last `{"message":"No match"}` | Unchanged |
| Gzip available | Unchanged |

**Rejected** for compatibility or poor ROI: StreamingHttpResponse, zstd replacement, public cursor API, parallel in-request warming of all pages.

---

## 21. Recommended implementation sequence

```text
1. Point Railway REDIS_URL at internal Redis; keep public for local
2. Add sampled structured timing logs for inventory_filter
3. Cache total_count by filter digest (exclude page/page_size)
4. EXPLAIN → add ("Project Name","Date") composite if proven
5. Re-measure HIT; add Redis compression only if still needed
6. Optionally: management-command warm pages 1–3 for hot project/date
7. Stop — do not stream, do not zstd, do not Promise.all 41 pages
```

---

## Risks and dependencies

| Risk | Dependency |
|------|------------|
| Internal Redis only works on Railway private network | Correct env per environment |
| COUNT cache staleness | Same 5-minute filter TTL / namespace bump after wide refresh |
| Composite index on unmanaged table | Manual DDL + refresh scripts must recreate indexes |
| Stampede under load | Lock wait must exceed typical MISS build (~1–3 s+) |
| Warming all 41 pages | ~31 MB Redis per filter set — avoid |
| Neon pooler + CONN_MAX_AGE | Confirm with Neon pooling mode before changing |

---

## Final recommendation (one paragraph)

For `POST /inventory_filter` after pagination, **do not** pursue StreamingHttpResponse, HTTP zstd, or in-request parallel page warming. **Do** switch Railway to **internal Redis**, add **log-based observability**, and **cache the filtered COUNT** so Redis HITs stop paying for `queryset.count()` on every page. Next, validate a **`(Project Name, Date)`** composite with EXPLAIN ANALYZE; add Redis **pickle compression** only if HIT latency remains high. Keep gzip, keep `page`/`page_size`, and keep the nested NHIT body unchanged.
