# inventory_filter NHIT Pagination Report

## 1. Current NHIT Pagination Behavior (production API inspected)

**Endpoint:** `POST https://fantastic-reportapi-production.up.railway.app/inventory_filter`

| Behavior | NHIT production |
|----------|-----------------|
| Pagination params | `page`, `page_size` in **POST JSON body** |
| Default `page_size` (omitted) | **15000** |
| Frontend incremental load | **`page_size: 5000`** (~45 requests for Kothakota) |
| Wire size per page (gzip) | ~112–138 KB transferred |
| Response body | NHIT nested `[[{...}, ...], ...]` arrays |
| Beyond last page | `200` + `{"message":"No match"}` (no pagination headers) |
| Empty filter match | `200` + `{"message":"No match"}` |
| Gzip | `Content-Encoding: gzip` when `Accept-Encoding: gzip` |
| CORS expose | `X-Inventory-Paginated`, `X-Inventory-Page`, `X-Inventory-Page-Size`, `X-Inventory-Row-Count`, `X-Inventory-Has-More` |

**NHIT quirk:** Production `X-Inventory-Row-Count` returns the **current page row count** (5000), not the full filtered total. Our Django implementation uses the **full filtered total** so the dashboard can read total assets immediately (per requirement).

## 2. Django Implementation

### Files modified

| File | Change |
|------|--------|
| `inventory/serializers.py` | Added optional `page` (default 1), `page_size` (default 15000) |
| `inventory/services/inventory_filter_queryset.py` | Shared filter queryset builder (new) |
| `inventory/services/inventory_filter_response.py` | Paginated `build_inventory_filter_groups(offset, limit)` + header constants |
| `inventory/views.py` | COUNT → slice → serialize one page; set pagination headers |
| `nhit/settings.py` | `CORS_EXPOSE_HEADERS` for inventory pagination headers |
| `inventory/tests.py` | Pagination + updated gzip tests |

### Flow

```
POST body validated (filters + page + page_size)
    ↓
build_inventory_filter_queryset(data)
    ↓
total_count = queryset.count()          ← DB COUNT only
    ↓
if offset >= total_count → {"message":"No match"}
    ↓
build_inventory_filter_groups(qs, offset, limit)   ← LIMIT/OFFSET in SQL
    ↓
serialize + group page rows only
    ↓
Redis cache (key includes page + page_size in payload)
    ↓
Response headers + nested array body
    ↓
GZipMiddleware compresses HTTP response
```

## 3. Request Pagination Parameters

```json
{
  "chainage_start": 0,
  "chainage_end": 99999,
  "date": "2026-07-28",
  "direction": ["All"],
  "project_name": ["Kothakota to Kurnool"],
  "asset_type": ["All"],
  "page": 1,
  "page_size": 5000
}
```

| Param | Default | Source |
|-------|---------|--------|
| `page` | 1 | NHIT POST body |
| `page_size` | **15000** | NHIT when omitted; frontend sends **5000** |

## 4. Page Size

- **Default:** 15000 (matches NHIT when `page_size` not sent)
- **Frontend load:** 5000 (matches Network tab ~45 pages for large projects)

## 5. Response Headers

| Header | Example (page 1, Kothakota) |
|--------|----------------------------|
| `X-Inventory-Paginated` | `true` |
| `X-Inventory-Page` | `1` |
| `X-Inventory-Page-Size` | `5000` |
| `X-Inventory-Row-Count` | `203386` (full filtered total) |
| `X-Inventory-Has-More` | `true` |

Final page: `X-Inventory-Has-More: false`

## 6. Total Row Count

Uses `queryset.count()` on the filtered queryset **before** slicing. Never uses `len(page_records)`.

## 7. Redis Cache Key

Cache payload is `serializer.validated_data`, which now includes `page` and `page_size`. Same filters + different page → different cache key automatically via `build_cache_key`.

## 8. Gzip

`GZipMiddleware` remains enabled. Paginated page 1 (Kothakota, 5000 rows):

| Metric | Before pagination | After pagination (page 1) |
|--------|-------------------|---------------------------|
| Uncompressed body | ~52.1 MB | **~1.3 MB** |
| Gzip wire size | ~2.4 MB (full) | **~65 KB** |
| Response time | ~28 s | **~3.3 s** |
| Records in response | 203,386 | **5,000** |

## 9. Frontend Changes

No frontend code in this repository (hosted at `ramsnhit.com`). External frontend should:

```javascript
const total = response.headers.get('X-Inventory-Row-Count');
const hasMore = response.headers.get('X-Inventory-Has-More') === 'true';
```

Load pages sequentially (`page: 1, 2, 3…`) with `page_size: 5000` — do not use `records.length` for total count.

## 10. Browser Test Checklist

After deploy, verify in Chrome DevTools:

- [ ] Multiple `inventory_filter` requests (~41 for 203k rows @ 5000/page)
- [ ] Each response ~100–150 KB gzip transferred
- [ ] `Content-Encoding: gzip`
- [ ] All five `X-Inventory-*` headers visible
- [ ] `X-Inventory-Row-Count` = 203386 on every page
- [ ] Map loads progressively without 52 MB single response

## 11. Performance Before/After

**Dataset:** Kothakota to Kurnool, 2026-07-28, 203,386 assets

| Metric | Before (no pagination) | After (page 1, size 5000) |
|--------|------------------------|---------------------------|
| Records in response | 203,386 | 5,000 |
| Uncompressed JSON | 54,670,584 B | 1,322,653 B |
| Gzip wire | ~2.5 MB | **65,477 B** |
| HTTP time | ~28,062 ms | **~3,270 ms** |
| DB queries | 1 SELECT (all rows) | 1 COUNT + 1 SELECT (5000 rows) |

## 12. Response Parity

- Body structure unchanged: nested `[[...], ...]` arrays
- Pagination metadata in **headers only** (not JSON body)
- Beyond-last-page / empty: `{"message":"No match"}` (NHIT parity)
- Field names, sparsity rules, grouping unchanged

## 13. Test Results

```
inventory.tests.InventoryFilterPaginationTests — 6 passed
inventory.tests.InventoryFilterGzipMiddlewareTests — 2 passed
inventory.tests.InventoryFilterSerializerTests — 3 passed
```

Run: `python manage.py test inventory.tests.InventoryFilterPaginationTests inventory.tests.InventoryFilterGzipMiddlewareTests --keepdb`

## Recommendation

**Deploy pagination** — meets all success criteria:

- No single 203k-record response
- Total count available via `X-Inventory-Row-Count` on page 1
- ~65 KB gzip pages vs 52 MB monolithic response
- Gzip remains transparent
- Redis cache keys are page-specific
- Frontend can load map coordinates incrementally per page
