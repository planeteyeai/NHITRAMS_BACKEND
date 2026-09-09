# Phase 3 — Direct Redis gzip bytes → HTTP (INVESTIGATION ONLY)

**Production code was NOT modified.**

## Question

Can a Redis HIT return the already gzip-compressed cached JSON bytes directly
to the HTTP client (after stripping internal `IFGZ1\n`), avoiding:

`gunzip → json.loads → DRF Response → JSONRenderer → HTTP GZipMiddleware`?

## Recommendation: **CONDITIONAL**

Direct-byte serving is HTTP-safe when Content-Encoding:gzip is set (middleware skips recompression) and Accept-Encoding includes gzip. Fair interleaved median HIT 826.39→472.33 ms (~42.8% faster); CPU after GET 323.25→0.19 ms (~99.9% less). Non-gzip clients must keep decode/render (or gunzip-to-JSON) path.

## 1. Current HIT architecture (Phase 2)

```
Redis GET (IFGZ1 + gzip(JSON))
  → decode_inventory_filter_page_cache()
      → validate magic
      → gzip.decompress()
      → json.loads()
  → inventory/views.py: Response(page_data)
  → set X-Inventory-* headers
  → DRF finalizes → JSONRenderer.render()
  → SelectiveGZipMiddleware (HTTP gzip if Accept-Encoding includes gzip)
  → client
```

## 2. Exact code path discovered

| Step | Location |
|------|----------|
| Redis GET | `nhit/caching/service.py` `safe_cache_get` via `get_cached_inventory_filter_page` |
| Decode (gunzip+json.loads) | `nhit/caching/inventory_filter_page.py` `decode_inventory_filter_page_cache` |
| Response() | `inventory/views.py` `InventoryFilterView.post` |
| JSONRenderer | DRF response cycle; also measured in perf logs via `JSONRenderer().render(page_data)` |
| HTTP gzip | `nhit.middleware.SelectiveGZipMiddleware` → Django `GZipMiddleware` (2nd in `MIDDLEWARE`) |

Middleware order (relevant): `SecurityMiddleware` → **`SelectiveGZipMiddleware`** → WhiteNoise → CORS → …

## 3. Proposed direct-byte architecture

```
Redis GET
  → validate IFGZ1\n
  → strip magic (do NOT send IFGZ1 to client)
  → HttpResponse(gzip_payload, content_type=application/json)
  → Content-Encoding: gzip
  → Content-Length: len(gzip_payload)
  → Vary: Accept-Encoding
  → X-Inventory-* headers (same as today)
  → SelectiveGZipMiddleware sees Content-Encoding → SKIP (no double-gzip)
  → client
```

## 4–5. HTTP / GZipMiddleware / headers (measured)

Django `GZipMiddleware.process_response` (verified in installed Django source):

- If `Content-Encoding` already present → **return response unchanged** (no recompress, no Vary patch).
- Else if Accept-Encoding matches gzip → compress with `compress_string(..., max_random_bytes=100)` (BREACH random FNAME), set `Content-Encoding: gzip`, set `Content-Length`, patch `Vary: Accept-Encoding`.

Experiment results:

- Pre-encoded + Accept-Encoding gzip: body unchanged=True, equals Redis payload=True, double_gzip=False
- JSON through middleware: decompressed equals JSON=True, wire equals Redis gzip=False (expected False due to BREACH FNAME)
- **Danger:** gzip body WITHOUT Content-Encoding → likely_double_gzip=True
- Candidate without Accept-Encoding still labeled gzip: True — **must branch**

Headers candidate must set explicitly:

- `Content-Type: application/json`
- `Content-Encoding: gzip` (required to prevent double-gzip)
- `Content-Length`
- `Vary: Accept-Encoding` (middleware will NOT add it when Content-Encoding pre-set)
- All `X-Inventory-*` (still derived from count + page/page_size)

## 6. Body parity

- Decompressed JSON byte-equal (Redis gzip vs re-render): **True**
- Candidate decompressed == current middleware decompressed: **True**
- Parsed JSON equal: **True**
- IFGZ1 not sent: **True**
- Candidate body == Redis gzip payload: **True**
- Wire gzip bytes equal Redis vs middleware: **False** — Compressed wire bytes usually DIFFER (Django BREACH random FNAME) but decompressed JSON must match. Candidate serves Redis gzip bytes exactly.
- Header diffs: `{"Content-Length": {"current": "640686", "candidate": "640611"}}`

**Note on Content-Length:** candidate uses exact Redis gzip size; current middleware gzip is slightly larger due to BREACH random filename. Decompressed JSON is identical.

## 7. Payload sizes

| Payload | Size |
|---|---:|
| JSON uncompressed | 13,812,337 |
| Redis stored (IFGZ1 + gzip) | 640,617 |
| Redis gzip excluding IFGZ1 | 640,611 |
| Candidate HTTP body | 640,611 |
| Current middleware wire (sample) | 640,663 |

Groups=45318, total_count=230523, samples=20

## 8–10. Benchmarks (HIT only)

### Fair interleaved comparison (preferred)

Even samples = current path CPU; odd = candidate CPU; Redis GETs shared.

| Metric | Current HIT | Direct-byte candidate | Improvement |
|---|---:|---:|---:|
| Redis GET (shared dist.) median | 493.64 | 493.64 | 0 |
| CPU after GET | 323.25 | 0.19 | 323.06 (99.9%) |
| **Total GET+CPU** | **826.39** | **472.33** | **354.06 (42.8%)** |

P95 totals: current=891.97 ms, candidate=628.31 ms

### Stage breakdown (separate runs)

> **Caution:** these samples enable `tracemalloc`, which inflates allocation-heavy stages
> (`json.loads`, `JSONRenderer`). Prefer the fair interleaved totals above for decision-making.
> Relative shape still useful: decode + re-render + HTTP gzip dominate CPU after Redis GET;
> candidate reduces post-GET CPU to ~0.

| Metric | Current HIT | Direct-byte candidate | Improvement |
|---|---:|---:|---:|
| Redis GET | 361.89 | 523.54 | (run-order biased; use fair table) |
| Decode (gunzip+json.loads) | 615.8 | — | — |
| gunzip only (split, after total) | 19.64 | — | — |
| json.loads only (split, after total) | 126.95 | — | — |
| Magic validate + strip | — | 0.06 | — |
| Response construction | 0.18 | 0.07 | — |
| DRF JSONRenderer | 1093.51 | **0** (skipped) | 1093.51 |
| HTTP gzip | 90.47 | **~0** (passthrough 0.01) | ~90.47 |
| Total pipeline (separate, tracemalloc on) | 2148.86 | 523.91 | 1624.95 |
| tracemalloc peak MB | 75.62 | 1.88 | — |

### Full Django APIClient current HIT (baseline only)

Median **~1.53 s** end-to-end with `Accept-Encoding: gzip` (count HIT + page HIT + DRF + middleware + test client). Candidate not wired into production endpoint (investigation only).

### Remaining bottleneck after a CONDITIONAL implementation

Even with direct bytes, Redis GET of the ~641 KB blob remains ~0.5 s median on this host/network path. Direct-byte removes almost all post-GET CPU (~323 ms → ~0.2 ms in fair run), but does not shrink Redis transfer.

## 11. gzip / no-gzip behavior

### CASE A — `Accept-Encoding: gzip`

Direct-byte candidate is viable: set `Content-Encoding: gzip`, middleware skips, body = Redis gzip payload.

### CASE B — no gzip Accept-Encoding

Cannot return Redis gzip bytes as opaque body for generic clients.
Measured cost to gunzip+loads+re-render: median **257.08 ms**.
Gunzip alone median **20.98 ms**.

Serve gunzipped JSON bytes directly as HttpResponse(json_bytes) without json.loads + JSONRenderer — still need gunzip (~tens of ms) but skip parse/render

### CASE C — `gzip, deflate, br`

Pre-encoded path still passthrough; body unchanged=True.

### CASE D — Redis failure

Existing miss/rebuild path unchanged; direct-byte is HIT-only optimization.

MISS/SET/stampede: unchanged. Direct-byte only affects HIT response construction.

## 12. Corruption / fallback

Candidate must still validate `IFGZ1\n` before serving. On failure: same as today — treat as miss, rebuild from DB.
Do not serve corrupt gzip to clients. Old pickle / wrong type already rejected by magic check.

## 13. Security / cache-key analysis

- Direct-byte serving does **not** change cache keys, scopes, namespaces, or auth.
- Still keyed by full validated filter payload + page + page_size under `page_gzip_json_v1`.
- Map/count namespaces remain separate.
- Risk is **incorrect Content-Encoding / Accept-Encoding handling**, not cross-tenant key bypass.
- Auth middleware still runs before the view; returning `HttpResponse` from the view does not skip auth.

## 14. DRF compatibility

Direct bytes bypass: DRF `Response`, `JSONRenderer`, renderer negotiation.
For this endpoint (fixed JSON nested array, no content negotiation variety): externally OK if headers/body match.
Perf logging that calls `JSONRenderer().render(page_data)` would need adjustment (HIT path may lack Python object).
Exception handling unchanged if validation/decode failures fall back before response.

## 15. Risks

1. Forgetting `Content-Encoding: gzip` → **double gzip** (confirmed dangerous).
2. Serving gzip to clients without Accept-Encoding gzip → broken clients.
3. Missing `Vary: Accept-Encoding` → shared caches may serve gzip to wrong clients.
4. Wire bytes differ from middleware gzip (BREACH FNAME) — OK if decompressed JSON matches.
5. Count still required for `X-Inventory-Row-Count` / Has-More (unchanged).
6. Implementation complexity of dual path (gzip vs non-gzip).
7. Slight Content-Length difference vs today's middleware gzip (BREACH overhead) — acceptable.

## 16. Recommendation: **CONDITIONAL**

Direct-byte serving is HTTP-safe when Content-Encoding:gzip is set (middleware skips recompression) and Accept-Encoding includes gzip. Fair interleaved median HIT 826.39→472.33 ms (~42.8% faster); CPU after GET 323.25→0.19 ms (~99.9% less). Non-gzip clients must keep decode/render (or gunzip-to-JSON) path.

## 17. Exact next implementation steps (if proceeding)

1. Add HIT branch in view or page-cache helper: when cache returns valid IFGZ1 blob **and**
   request Accept-Encoding allows gzip → build `HttpResponse` with stripped gzip bytes + headers.
2. Else → existing decode → `Response(page_data)` path (or gunzip JSON bytes without loads if desired).
3. Always validate magic; on failure rebuild.
4. Set `Vary: Accept-Encoding` explicitly on direct path.
5. Never send `IFGZ1` to clients.
6. Add tests: Accept-Encoding gzip body == Redis payload; no double-gzip; no-gzip path unchanged;
   headers; corruption fallback; page/page_size isolation unchanged.
7. Do **not** change Redis format, TTL, lock, or cache keys.
8. Re-benchmark 50k HIT after implementation.

## Raw data

- `benchmark_inventory_filter_phase3_direct_bytes_results.json`
- APIClient current HIT sample: `{"n": 5, "total_http_hit_ms": {"n": 5, "samples_ms": [1527.76, 1304.89, 1433.2, 1564.89, 1676.31], "min": 1304.89, "median": 1527.76, "p95": 1676.31, "max": 1676.31, "mean": 1501.41}, "content_encoding": "gzip", "note": "Full Django stack including count cache HIT + view overhead"}`
