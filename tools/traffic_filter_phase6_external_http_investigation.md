# Traffic Filter Phase 6 — External HTTP Latency Diagnosis

**Diagnosis only — no production behavior changed.**

Base URL used: `https://nhitramsbackend-production.up.railway.app`  
Endpoint: `POST /traffic_filter/`

> Note: `fantastic-reportapi-production.up.railway.app` is a different service (no `traffic_filter`; returns 404). Do not use it for this endpoint.

## Final question

> Why does Postman take ~6 seconds when the optimized traffic_filter HIT handler takes ~300–700 ms?

**Because the live Railway endpoint itself spends ~5.3–5.8 s in TTFB for this request.** That is reproduced by Python `requests`, `httpx`, and `curl`. It is **not** Postman-only overhead, and it is **not** gzip body download time.

The Phase 4A/5 in-process warm HIT (~300 ms) is **not** what Railway is currently exhibiting on this external path.

**Primary classification: G — mixed (server TTFB dominates; Railway path not confirmed HIT)**

Closest single letter if forced to pick one driver of the ~6 s: **server-side TTFB** (cache MISS / rebuild or non-TFGZ1 path on Railway), not client transfer.

### Time budget (primary Abu Road, gzip, warm reused session)

| Bucket | Approx | Notes |
|---|---:|---|
| 1. connection/TLS | ~299 ms | DNS+TCP+TLS probe median; new−reused savings ~371 ms |
| 2. TTFB / server processing | ~5313 ms | Dominates; matches Postman |
| 3. response transfer (gzip) | ~0.02 ms | Wire ~7.2 KB |
| 4. Postman/client overhead | ~687 ms | Postman 6000 − Python reused 5313 |
| 5. unexplained inside TTFB | ~5313 ms | Needs Railway `TRAFFIC_FILTER_PERF_DEBUG` to split Redis vs DB vs CPU |

## Cache HIT status (Step 6)

**NOT confirmed on Railway external HTTP.**

Evidence Railway is **not** serving a stable TFGZ1 direct-byte HIT:

- Identical payload, repeated warm requests, **gzip `Content-Length` / wire bytes change** every time.
- Primary examples: cold wire **7251**, warm confirm **7159**; reused-sample wire min/median/max **7160 / 7199.5 / 7258**.
- Uncompressed JSON size is stable (**321,459** primary; **7,528,638** large).
- Stable TFGZ1 HIT would return **identical** gzip bytes.
- Cold first (~5812 ms) and subsequent “warm” (~5406 / ~5313 ms) are both multi-second — not the Phase 4A/5 HIT profile.

Local shared-Redis probe briefly reported HIT after populating from this machine; that is **not authoritative** for the Railway process (and local Redis later showed **0** `traffic*` keys / MISS). Treat external timings as **MISS/rebuild-like**, not Phase 5 HIT.

## Latency waterfall (measured)

```text
Postman total (observed)              ~ 6000 ms
│
├── DNS                               ~ 0.58 ms
├── TCP connection                    ~ 112 ms
├── TLS handshake                     ~ 105 ms
├── request upload                    (not separately instrumented; small JSON body)
├── TTFB                              ~ 5313 ms   ← dominant
│   ├── Railway request processing    unknown without server debug logs
│   ├── Redis                         unknown (HIT not confirmed)
│   ├── DB / rebuild                  likely (varying gzip sizes)
│   └── response construction/gzip    likely (re-gzip each request)
│
└── response body transfer (gzip)     ~ 0.02 ms
```

## Connection reuse (Group A vs B)

| Group | median | P95 |
|---|---:|---:|
| A — new connection each request (gzip) | 5684 ms | 5771 ms |
| B — reused session (gzip) | 5313 ms | 5408 ms |
| Savings (A−B) | ~371 ms | |

TLS/connection matters (~0.3–0.4 s) but **does not explain ~6 s**.

## Gzip vs identity

### Primary (Abu Road)

| Case | total | TTFB | body transfer | Content-Encoding | Content-Length / wire |
|---|---:|---:|---:|---|---:|
| gzip reused | 5313 | 5313 | 0.02 | gzip | ~7200 |
| identity reused | 5391 | 5313 | 77 | (none) | 321459 |

### Large (Chittorgarh)

| Case | total | TTFB | body transfer | Content-Encoding | Content-Length / wire |
|---|---:|---:|---:|---|---:|
| gzip reused | 5682 | 5675 | 13 | gzip | ~138512 |
| identity reused | 7611 | 5649 | **1913** | (none) | 7528638 |

Gzip works on the wire (`Content-Encoding: gzip`, ~138 KB vs ~7.5 MB JSON).  
For gzip, transfer is not the bottleneck. For large **identity**, download adds ~1.9 s on top of ~5.6 s TTFB.

## Client comparison (same URL + body)

| Client | primary gzip median total | Notes |
|---|---:|---|
| Postman (observed) | ~6000 ms | Matches live Railway TTFB band |
| Python requests (reused) | 5313 ms | |
| httpx (reused) | 5270 ms | |
| curl | 5610 ms | `time_starttransfer` ≈ total |

**Postman does not uniquely account for the ~6 s.** Python/curl see the same multi-second TTFB.

## Redirects

- Redirect count: **0** on `POST .../traffic_filter/` (HTTPS direct).
- Trailing slash required: `/traffic_filter` (no slash) returned **500** in smoke test; use `/traffic_filter/`.

## Cold start vs warm

| Request | total |
|---|---:|
| First HTTP in warm sequence | 5812 ms |
| Immediate follow-up | 5406 ms |
| Later reused median | 5313 ms |

Slight improvement after the first call, but **still ~5.3 s**. This is **not** “first request several seconds, then sub-second warm HIT.”

Classify idle Railway dyno cold-start as **not the primary story** for repeated warm calls.

## Response sizes

| Payload | uncompressed JSON | gzip wire | matches expectation |
|---|---:|---:|---|
| primary | 321,459 | ~7.2 KB | yes |
| large | 7,528,638 | ~138.5 KB | yes (~7.5 MB / ~138 KB) |

Postman displaying large JSON after gzip is normal client decompression; wire size remains ~gzip.

## Root-cause classification

**PRIMARY = G — mixed**

With emphasis:

1. **Server TTFB ~5.3 s on Railway** — explains Postman ~6 s (reproduced externally).
2. **Not confirmed HIT** — varying gzip bytes ⇒ not TFGZ1 stable blob; likely MISS/rebuild or pre-Phase-3 behavior on the deployed app.
3. **Connection/TLS (F)** — secondary (~0.3 s).
4. **Body transfer (E)** — negligible for gzip; relevant only for large identity (~1.9 s).
5. **Postman overhead** — small (~0.7 s vs Python).

If Phase 7 must pick one optimization *direction* after deploy verification: **make Railway actually serve the Phase 5 TFGZ1 HIT path** (deployment / Redis on Railway / MISS diagnosis) — **do not** redesign TFGZ1, Redis keys, JSON, gzip, or API contract based on this phase alone.

## Phase 7 guidance (diagnosis only — no changes in Phase 6)

1. Confirm Phases 3/5 are **deployed** to `nhitramsbackend-production`.
2. Enable env-gated `TRAFFIC_FILTER_PERF_DEBUG` on Railway and capture one Postman-equivalent request’s `server_total_ms` / Redis / DB split (no secrets).
3. Verify Redis from the Railway process: HIT after one populate, **identical** gzip `Content-Length` on repeat.
4. Only after HIT is proven on Railway should further micro-optimizations be considered.

## Artifacts

- `tools/traffic_filter_phase6_external_http_benchmark.py`
- `tools/traffic_filter_phase6_external_http_results.json`
- `tools/traffic_filter_phase6_external_http_investigation.md`

## Production code

No production code, TFGZ1, Redis key architecture, JSON, gzip, TTL, or API contract changes in this phase.
