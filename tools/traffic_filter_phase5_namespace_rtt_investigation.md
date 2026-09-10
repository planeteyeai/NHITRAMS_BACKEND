# Traffic Filter Phase 5 — Namespace Version RTT Elimination

**Implemented for traffic_filter only. API contract unchanged. TFGZ1 unchanged.**

## 1. Current cache-key architecture (before)

```text
version_key = traffic_filter:version          # Redis GET #1
data_key    = traffic_filter:v{N}:{digest}    # Redis GET #2
digest      = sha256({payload, scope:gzip_json_v1})
value       = TFGZ1\n + gzip(JSON)
```

Invalidation: `invalidate_traffic_filter_cache()` → `bump_namespace_version`
increments `traffic_filter:version` so old `v{N}` keys become unreachable.

## 2. Why namespace version was fetched

The dynamic version is embedded in the data key. Without reading it first,
the client cannot know which key holds the current generation.

## 3. Invalidation dependency

`bump_namespace_version` is the shared invalidation mechanism for all filter
namespaces. Traffic has no model signals, but `invalidate_traffic_filter_cache`
and `invalidate_all_filter_caches` must keep working.

## 4. Can the separate namespace GET be removed?

**Yes — safely — by collapsing version+value into one `get_many` round trip.**

Removing version from the architecture entirely (static key only) would break
invalidation. That option was rejected.

## 5. Chosen implementation

Option: **stable data key + generation stamped in entry + get_many**.

```text
version_key = traffic_filter:version
data_key    = traffic_filter:data:gzip_json_v1:{digest}
entry       = {kind: tfgz1_ns_v1, ns_v: <version>, blob: TFGZ1+gzip}
HIT         = cache.get_many([version_key, data_key])  # 1 RTT
            → serve blob only if entry.ns_v == current version
```

Invalidation still bumps `traffic_filter:version`; stale entries fail the
`ns_v` check and rebuild. Stampede lock digest unchanged. TTL 300s unchanged.
Inventory/other namespaces untouched.

## 6. Before/after cache-key format

| | Before | After |
|--|--|--|
| Data key | `traffic_filter:v{N}:{digest}` | `traffic_filter:data:gzip_json_v1:{digest}` |
| Version key | `traffic_filter:version` | unchanged |
| Value | raw TFGZ1 bytes | `{kind, ns_v, blob: TFGZ1}` |
| TFGZ1 blob | unchanged | unchanged |

## 7. Before/after HIT flow

Before: `GET version` → build key → `GET value` → strip TFGZ1 → HttpResponse

After: `get_many(version, data)` → validate ns_v → strip TFGZ1 → HttpResponse

## 8–11. Invalidation / stampede / TTL / contract

- Invalidation: bump namespace version (tested)
- Stampede: existing lock on payload digest (preserved)
- TTL: **300s**
- API contract: **unchanged**

## 12. Tests

See `nhit/caching/tests/test_traffic_filter_direct_bytes.py` + contract/perf suites.

## 13–16. Benchmark results

- environment on_railway=False cache_usable=True

### Primary

- legacy 2-RTT total median: **618.699 ms**
- namespace GET median: **298.947 ms**
- value GET median: **321.478 ms**
- get_many 1-RTT median: **343.253 ms**
- helper HIT median: **360.789 ms** (round_trips=1)
- estimated savings (legacy−get_many) median: **275.446 ms**

### Large

- legacy 2-RTT total median: **933.2 ms**
- get_many 1-RTT median: **500.755 ms**
- helper HIT median: **706.532 ms** (round_trips=1)
- estimated savings median: **432.445 ms**

## 17. Remaining bottleneck

Removing one Redis RTT saves roughly one public-proxy PING (~80–300+ ms locally).
It does **not** by itself explain or eliminate Postman ~6s (still dominated by
client↔Railway HTTP / connection path from Phase 4A).

Next: measure with `NHIT_API_BASE_URL` or Railway-internal Redis — not another
TFGZ1/JSON/gzip rewrite.

## Acceptance criteria

| Criterion | Status |
|--|--|
| One Redis namespace-version GET removed from normal HIT path as a **separate RTT** | **Yes** (folded into `get_many`) |
| Exactly one Redis round trip on warm HIT | **Yes** (`redis_round_trips=1`) |
| No additional DB query | **Yes** |
| Cache invalidation remains correct | **Yes** (ns_v check + bump) |
| Stampede protection remains | **Yes** |
| API response equivalent | **Yes** |
| TTL = 300s | **Yes** |
| TFGZ1 representation unchanged (inside `blob`) | **Yes** |
| Direct gzip-byte serving unchanged | **Yes** |
| Inventory / other namespaces untouched | **Yes** |

## Files changed

| File | Role |
|--|--|
| `nhit/caching/traffic_filter_cache.py` | Stable data key + get_many HIT + ns_v wrapper |
| `nhit/caching/service.py` | `safe_cache_get_many` helper |
| `nhit/caching/tests/test_traffic_filter_direct_bytes.py` | Invalidation / RTT / key tests |
| `tools/benchmark_traffic_filter_phase5_namespace_rtt.py` | Before/after RTT benchmark |
| `tools/traffic_filter_phase5_namespace_rtt_investigation.md` | This report |

**Phase 5 complete — one Redis round trip removed from traffic_filter HIT; invalidation preserved.**

