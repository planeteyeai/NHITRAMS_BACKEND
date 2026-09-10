# inventory_filter warm HIT TTFB investigation

**Measurement only — no production behavior / cache / Redis / API changes.**

Captured on production after deploying env-gated `INVENTORY_FILTER_PERF_DEBUG`
instrumentation. Flag set to `true` for capture, then returned to `false`.

- Host: `nhitramsbackend-production.up.railway.app`
- Filter: `Kothakota to Kurnool` / `2026-07-28` page=1 page_size=100000
- Warm requests: 2; timed HIT samples: **20/20 cache HIT**
- Response path: **`direct_gzip_bytes`** (JSON parse/render/gzip all **0 ms**)
- Content-Length / gzip wire: **1,269,383** bytes
- Redis payload bytes (IFGZ1 + gzip): **1,269,389**
- Redis round trips on HIT: **4** (always)
- Redis URL class at runtime: **`railway_private_internal`** (`using_internal_redis=1`, scheme=`redis`, port=`6379`)
- Redis % of median `server_total_ms`: **99.7%**

## Timing table (20 warm HIT samples)

| Component                    |     Median |        P95 |
| ---------------------------- | ---------: | ---------: |
| Redis namespace GET (count)  |     172.88 |     181.92 |
| Redis data GET (count)       |     173.07 |     181.64 |
| Redis namespace GET (page)   |     171.29 |     177.20 |
| Redis data GET (page)        |    1208.22 |    2598.14 |
| Redis total                  |    1725.50 |    3116.47 |
| Cache decode / IFGZ extract  |       0.66 |       5.03 |
| JSON parsing                 |       0.00 |       0.00 |
| JSON rendering               |       0.00 |       0.00 |
| Gzip                         |       0.00 |       0.00 |
| Python processing (residual) |       2.87 |       7.12 |
| Server total                 |    1730.72 |    3129.09 |
| Network/TTFB (client)        |    1966.49 |    3320.94 |
| Body transfer (client)       |     290.15 |     405.29 |

`Redis percentage of server_total_ms (median) = 1725.50 / 1730.72 ≈ **99.7%**`

## Redis operations on warm HIT

Confirmed by instrumentation (`redis_round_trips=4` on every sample) and code path:

| # | Operation | Typical median |
| - | --------- | -------------: |
| 1 | Count **namespace/version GET** | ~173 ms |
| 2 | Count **data GET** (small int) | ~173 ms |
| 3 | Page **namespace/version GET** (same namespace, separate call) | ~171 ms |
| 4 | Page **data GET** (~1.27 MB IFGZ1 blob) | ~1208 ms |

**Answer: warm HIT performs 2+ Redis operations — specifically 4 sequential GETs, not 1.**

No other Redis calls on the HIT path (no SET, no lock wait) when both count and page are warm HITs.

## Railway Redis path

Safe runtime identity from production headers (no URL/password logged):

- `redis_host_class=railway_private_internal`
- `using_internal_redis=1`
- `redis_scheme=redis`
- `redis_port=6379`

**Django on Railway is using private/internal Redis**, not the public `*.proxy.rlwy.net` proxy.

## Connection reuse

- Worker PIDs seen across samples: `37`, `38`, `39` (Gunicorn multi-worker)
- First warm `page_data_get_ms`: **1388.5**; steady median: **1208.22**
- Small GETs remain ~170 ms even after warm-up (not a one-shot TCP connect tax only)
- `pool_available` observed as `1`; `pool_created` not exposed by this redis-py build (best-effort inspect)
- Steady-state still dominated by **large page blob GET**, not cold-connect alone

Cold connection can inflate the first op after a new worker (warm[1] count version GET ~1181 ms; earlier probe ~1356 ms), but **steady warm HITs still spend ~1.2 s on page data GET + ~0.5 s on three small GETs**.

## Primary answer

**Why ~2.4 s client TTFB on a warm 100K HIT when gzip is only ~1.27 MB?**

Production measurements show:

1. **Server work ≈ Redis** (~1.73 s median `server_total`, ~99.7% Redis).
2. **Largest single cost:** Redis **page data GET** of the ~1.27 MB cache blob (~1.21 s median). That transfer happens **inside the app before TTFB** (Redis → Django), then the same bytes are written to the HTTP response.
3. **Four sequential Redis round trips** add ~0.5 s for three small GETs (~170 ms each) on top of the blob GET.
4. **Direct gzip path is working** (no JSON parse/render/re-gzip). IFGZ extract is ~1 ms.
5. **Client TTFB** (~1.97 s median from India) ≈ server_total + edge/network to Singapore; **body transfer after TTFB** is only ~0.29 s median — matching the earlier finding that wire transfer is not the primary bottleneck.

So the ~2.4 s TTFB is **not** “gzip download time”; it is **waiting on Redis (especially fetching the cached gzip blob) plus a few small Redis GETs**, then a smaller client/network add-on.

## Ranked recommendations (do **not** implement in this task)

1. **[highest]** Cut Redis page-blob GET cost — e.g. smaller/chunked cache value, avoid double-wrapping (django-redis pickle around IFGZ1), or serve from a path that does not pull the full blob through Python first.
2. **[high]** Collapse warm-HIT Redis RTTs from **4 → 1–2** (combine count+page namespace version with data via `get_many`, or drop redundant version GETs / cache count in-process briefly).
3. **[medium]** Investigate ~170 ms floor on tiny internal Redis GETs (pool/settings/Redis CPU); even after RTT collapse this floor matters.
4. **[keep]** Retain `direct_gzip_bytes`; JSON/gzip re-work is already eliminated on HIT.
5. **[ops]** Keep `INVENTORY_FILTER_PERF_DEBUG=false` except during timed captures (now restored to false).

## Artifacts

- `tools/inventory_filter_hit_ttfb_results.json`
- `tools/inventory_filter_hit_ttfb_investigation.md`
- `tools/measure_inventory_filter_hit_ttfb.py`
