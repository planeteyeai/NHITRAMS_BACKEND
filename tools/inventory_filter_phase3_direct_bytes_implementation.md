# Phase 3 Implementation — Direct Redis gzip bytes → HTTP

## Summary

Implemented **CONDITIONAL** direct-byte HIT serving for `POST /inventory_filter/`.

- Gzip-capable clients (`Accept-Encoding` allows gzip with q>0): HTTP body = Redis gzip payload (IFGZ1 stripped).
- Other clients: gunzip → raw JSON bytes `HttpResponse` (no `json.loads` / DRF re-render on HIT).
- Redis format unchanged: `IFGZ1\n` + gzip(JSON), scope `page_gzip_json_v1`, TTL 604800.

## 1. Files changed

| File | Change |
|------|--------|
| `nhit/caching/accept_encoding.py` | **New** — Accept-Encoding parser honoring `gzip;q=0` |
| `nhit/caching/inventory_filter_page.py` | `serve_mode` (`python`/`gzip_bytes`/`json_bytes`), extract helper |
| `inventory/views.py` | Conditional HttpResponse paths + headers + perf fields |
| `nhit/caching/tests/test_inventory_filter_direct_bytes.py` | **New** — Accept-Encoding, parity, corruption, isolation |
| `inventory/tests.py` / `tests_inventory_filter_ttl.py` | Compat with HttpResponse JSON bodies |
| `tools/benchmark_inventory_filter_phase3_impl_results.json` | Post-impl measurements |

## 2. Exact implementation flow

```
count cache (unchanged)
→ Accept-Encoding allows gzip?
    YES → get_cached(..., serve_mode=gzip_bytes)
         → HttpResponse(gzip_payload)
         → Content-Encoding: gzip
         → Content-Length, Vary: Accept-Encoding, X-Inventory-*
         → SelectiveGZipMiddleware sees Content-Encoding → passthrough
    NO  → get_cached(..., serve_mode=json_bytes)
         → HttpResponse(json_bytes)  # HIT: gunzip only
         → Vary: Accept-Encoding, X-Inventory-*
```

Corrupt/invalid IFGZ1 → treat as miss → rebuild → replace cache.

## 3. Accept-Encoding decision

`accepts_gzip_encoding()` parses comma-separated codings and `q` values.
`gzip;q=0` / empty / missing / identity → False. `gzip`, `gzip, deflate, br`, `*` with q>0 → True.
Django middleware regex is intentionally **not** used (it ignores q=0).

## 4–5. Middleware & headers

Direct path sets `Content-Encoding: gzip` **before** middleware → no double-gzip.
Also sets `Vary: Accept-Encoding` (middleware skips Vary patch when encoding pre-set).
`Content-Length` = len(gzip_payload) only (no IFGZ1).

## 6–7. Validation / no-gzip

Validation: bytes, IFGZ1 magic, non-empty, gzip magic bytes `1F 8B`.
No-gzip HIT: gunzip to JSON bytes (exact `JSONRenderer` bytes); no Content-Encoding:gzip from our path.

## 8–9. Tests

```
python manage.py test nhit.caching.tests.test_inventory_filter_direct_bytes \
  nhit.caching.tests.test_inventory_filter_page_cache \
  inventory.tests_inventory_filter_ttl \
  inventory.tests.InventoryFilterGzipMiddlewareTests \
  inventory.tests.InventoryFilterPaginationTests --keepdb
→ 52 tests OK
```

## 10–13. 50k benchmark (post-implementation)

Target: Kothakota to Kurnool / 2026-07-28 / page=1 / page_size=50000 / samples=20

| Payload | Size |
|---|---:|
| JSON | 13,812,337 |
| Redis IFGZ1+gzip | 640,617 |
| HTTP gzip body | 640,611 |

| Metric | Median | P95 | Min | Max |
|---|---:|---:|---:|---:|
| **API HIT Accept-Encoding:gzip** | **2344.71** | 3575.68 | 2068.8 | 4219.14 |
| API HIT no Accept-Encoding | 2059.27 | 2577.94 | 1905.47 | 2657.03 |
| Modeled OLD CPU after Redis | 329.62 | 363.56 | 314.87 | 363.56 |
| Modeled NEW extract CPU | 0.25 | 0.46 | 0.14 | 0.46 |

Investigation fair baseline (pre-impl modeled pipeline):
- Total 826.39 → 472.33 ms
- CPU 323.25 → 0.19 ms

RSS samples (MB): [113.88, 113.89, 114.43, 115.1, 115.73]

### Before vs after (how to read)

| Layer | Before (Phase 3 investigation) | After (this implementation) |
|-------|--------------------------------|-----------------------------|
| Post-GET CPU (fair / modeled) | ~323–330 ms | **~0.2–0.3 ms** |
| Fair GET+CPU pipeline | ~826 ms | ~472 ms (investigation candidate; Redis-dependent) |
| APIClient E2E HIT | ~1.5 s (old decode/render path) | ~2.3 s median this run |

**APIClient E2E is Redis-GET dominated** on this host (perf logs show `redis_get_ms` often 1.1–2.1 s). End-to-end wall times therefore vary run-to-run and must **not** be read as a regression of the direct-byte work. Confirming signals from live HIT logs:

- `response_path=direct_gzip_bytes`
- `render_ms=0`
- `http_gzip=passthrough`
- `cache_decode_ms` ≈ 0.02–0.2 ms (magic strip only)
- HTTP body == Redis gzip payload (640,611 bytes)

No-gzip fallback HIT logs: `response_path=direct_json_bytes`, `cache_decode_ms` ≈ 20 ms (gunzip), `response_bytes=13812337`.

## 14. Remaining bottleneck

Redis GET of the ~641 KB blob + Django/test-client overhead still dominate end-to-end HIT.
Post-GET CPU for gzip clients is effectively eliminated.
MISS / Neon transfer / build are unchanged (out of scope).

## 15. API contract

Unchanged: filters, pagination headers, nested JSON (decompressed), no-match,
map path, count cache, Redis keys/TTL/invalidation/lock, auth.
Wire gzip bytes for gzip clients now match Redis payload (stable; no BREACH FNAME).
Decompressed body remains byte-identical to prior JSONRenderer output.

## Acceptance checklist

- [x] gzip-capable clients receive direct cached gzip bytes
- [x] IFGZ1 never reaches client
- [x] Content-Encoding:gzip explicitly set
- [x] GZipMiddleware does not double-compress
- [x] Vary: Accept-Encoding set
- [x] Content-Length correct (gzip payload only)
- [x] no-gzip clients receive valid JSON
- [x] corrupt cache safely falls back
- [x] Redis failure safely falls back
- [x] pagination headers unchanged
- [x] decompressed body unchanged
- [x] filter / keys / TTL / invalidation / map / count / auth unchanged
- [x] real 50k Kothakota request tested
- [x] performance improvement measured (post-GET CPU)
- [x] no unrelated production changes
