# inventory_filter max page_size 150000 → 200000

**Narrow API-limit change only. No CLI used for this change set.**

## 1. Files changed

| File | Change |
| --- | --- |
| `inventory/services/inventory_filter_response.py` | `MAX_INVENTORY_FILTER_PAGE_SIZE` 150000 → **200000** |
| `inventory/tests.py` | Boundary + contract tests updated/added for 200000 |
| `tools/measure_inventory_filter_200k.py` | Production validation script (manual run) |
| `tools/inventory_filter_200k_limit_change_report.md` | This report |

`inventory/serializers.py` already references `MAX_INVENTORY_FILTER_PAGE_SIZE` — **not duplicated**, no serializer edit required.

Historical docs (`inventory_filter_150k_validation_*`, `inventory_filter_100k_validation_*`) left unchanged.

## 2. Old maximum

`150000`

## 3. New maximum

`200000`

## 4. Default page size

`DEFAULT_INVENTORY_FILTER_PAGE_SIZE = 15000` (**unchanged**)

## 5. Only inventory_filter changed

Confirmed via repository search:

- Limit constant lives only in `inventory/services/inventory_filter_response.py`
- Consumed by `InventoryFilterSerializer.page_size.max_value` in `inventory/serializers.py`
- Other apps keep their own `MAX_PAGE_SIZE = 100` (bulk_stock, supplier, etc.) — untouched
- No traffic / PMS / AIS / RWFIS / distress / map page-size limits modified

## 6. API response contract unchanged

No changes to:

- URL / method / auth / filters / DB / ordering
- nested-list JSON body / NHIT fields / sparse output / "No match"
- pagination semantics or `X-Inventory-*` headers
- no pagination metadata added to JSON

Only functional change: `page_size` may be up to **200000**.

## 7. Cache behavior unchanged

No changes to Redis namespace, keys, TTL, IFGZ1, gzip, `direct_gzip_bytes`, invalidation, locking, or Redis config.

`page_size=200000` uses a distinct cache key via existing payload digest (includes `page_size`).

No flush / no invalidation performed.

## 8. Tests executed and results

**Not executed in this session** (user instruction: do not use CLI at all).

Tests prepared / updated (ready to run when allowed):

| Test | Expectation |
| --- | --- |
| `test_default_page_size_matches_nhit` | omitted → 15000 |
| `test_page_size_accepts_5000_through_200000` | includes 100000, 150000, **200000** |
| `test_page_size_rejects_above_200000` | **200001** rejected |
| `test_page_size_zero_and_negative_rejected` | preserved |
| `test_page_size_100000_headers_and_body_contract` | still accepted + headers |
| `test_page_size_150000_headers_and_body_contract` | still accepted + headers |
| `test_page_size_200000_headers_and_body_contract` | **new** — accepted + headers-only contract |
| no-match / pagination header tests | unchanged |

Also recommended (when CLI allowed):

- `inventory.tests.InventoryFilterPaginationTests`
- `nhit.caching.tests.test_inventory_filter_page_cache`
- `nhit.caching.tests.test_inventory_filter_direct_bytes`

## 9. Production 200K test results

**Not performed in this session** (no CLI / no remote measurement run).

Manual script ready: `tools/measure_inventory_filter_200k.py`

Expected checks after deploy:

- page1 `page_size=200000` → ~200000 rows, `Has-More=true`, `Row-Count=230523`
- page2 → ~**30523** rows, `Has-More=false`
- no cross-page duplicates
- optional HIT timing if `INVENTORY_FILTER_PERF_DEBUG=true`

## 10. Performance / memory concerns (expected, not newly measured)

From prior 100k/150k production measurements, extrapolating:

| page_size | gzip (approx) | uncompressed (approx) | warm HIT total |
| ---: | ---: | ---: | ---: |
| 100000 | ~1.27 MB | ~27 MB | ~2.5 s |
| 150000 | ~1.88 MB | ~42 MB | ~3.0 s |
| 200000 | likely ~2.4–2.6 MB | likely ~55–56 MB | likely higher TTFB/transfer; Redis page GET larger |

Concerns:

- **Not safe as Angular/UI default** — keep frontend at current page size (default API 15k)
- Redis warm HIT still dominated by large blob GET + 4 RTTs
- Worker/client memory for ~55MB JSON inflate will be material

## Intended behavior after deploy

```
page_size omitted     → 15000
page_size <= 200000   → accepted
page_size > 200000    → rejected
all other APIs        → unchanged
```
