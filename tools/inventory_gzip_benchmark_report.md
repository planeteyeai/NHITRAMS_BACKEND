# Railway Internal Redis Performance Benchmark — inventory_filter Gzip

> Measured locally against production Neon DB (Kothakota to Kurnool, survey date 2026-07-28).
> Re-run on Railway after deploy: `python tools/benchmark_inventory_gzip.py`

## Environment

| Setting | Value |
|---------|-------|
| Django | 6.0 |
| Database | PostgreSQL (Neon) |
| GZipMiddleware | **Enabled** (new) |
| Project | Kothakota to Kurnool |
| Records | 203,386 in 181,965 groups |

## Change Made

**GZipMiddleware was NOT present.** Added to `nhit/settings.py`:

```python
'django.middleware.gzip.GZipMiddleware',
```

Placed immediately after `SecurityMiddleware` (before WhiteNoise). No view, serializer, or response-structure changes.

## Compression Size (measured)

| Metric | Value |
|--------|-------|
| Uncompressed size | 54,670,584 bytes (~52.1 MB) |
| Gzip size | 2,518,742 bytes (~2.4 MB) |
| Bytes saved | 52,151,842 |
| Compression ratio | 21.71 : 1 |
| Reduction % | **95.4%** |

## HTTP Performance (10 runs, cache disabled)

| Mode | Avg | Median | P95 | Min | Max | Wire bytes | DB queries |
|------|-----|--------|-----|-----|-----|------------|------------|
| No gzip | 28,062 ms | 24,605 ms | 28,656 ms | — | — | 54,670,584 | 1 |
| Gzip | 55,298 ms | 45,270 ms | 95,454 ms | — | — | 2,518,742 | 1 |
| **Delta** | **+27,236 ms** | | | | | **−52,151,842 bytes** | 0 |

## Builder Phase Breakdown (single run)

| Phase | Time |
|-------|------|
| DB / query | 58,248 ms |
| Serialization | 1,814 ms |
| JSON render | 870 ms |
| Gzip compress (CPU) | 2,281 ms |
| **Total builder** | ~63 s |

## Response Correctness

| Check | Result |
|-------|--------|
| OFF vs gzip-decompressed | **MATCH** |
| `Content-Encoding: gzip` when requested | **Yes** |
| HTTP status | 200 |
| Error responses (400) gzip-corrupted | **No** — 400 returns without gzip |

## Pagination

`POST /inventory_filter/` has **no pagination** (`page` / `page_size` not supported). It returns the **full filtered dataset** as nested `[[...], ...]` arrays.

Dashboard aggregate counts must be computed separately if needed — this endpoint does not paginate and should not be used as a paginated contract.

## Redis Interaction

Unchanged. `cached_filter_response` stores uncompressed Python objects in Redis. `GZipMiddleware` compresses the **HTTP response only**, after the cached value is retrieved and serialized. Transparent to cache keys/TTL.

## Tests

```
python manage.py test inventory.tests.InventoryFilterGzipMiddlewareTests \
  inventory.tests.InventoryFilterSerializerTests \
  inventory.tests.InventoryFilterResponseTests --keepdb
```

Result: **9/9 passed**

## Recommendation

**KEEP GZIP**

- **Bandwidth:** 95% reduction (52 MB saved per response for Kothakota Kurnool).
- **Server CPU:** ~2.3 s gzip compression per build; HTTP path adds ~27 s avg vs uncompressed on this measurement host (includes compressing 52 MB responses).
- **Client benefit:** Browsers with `Accept-Encoding: gzip` download ~2.4 MB instead of ~52 MB — major win on slow/mobile networks.
- **Do not assume faster server time** — gzip trades CPU for bytes on the wire.
- **Separate issue:** Response size (52 MB+) should eventually be addressed with pagination or a summary endpoint; gzip does not replace that.
