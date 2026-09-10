# Traffic Filter — Production Runtime Timing Instrumentation

**Production timing instrumentation prepared — no performance optimization implemented.**

Temporary, measurement-only instrumentation for Railway production when Railway CLI is unavailable.

## 1. Exact instrumentation points

| Stage | Where | Notes |
|-------|-------|-------|
| Flag check | `traffic/views.py` → `is_traffic_filter_perf_debug_enabled()` | Only cost when disabled |
| Request start (`T0`) | `TrafficFilterView.post` | Recorded only when debug ON |
| Cache GET | `nhit.caching.service.safe_cache_get` via optional `timings=` | Same Redis client / keys / TTL as production |
| Cache decode | N/A separately | **django-redis unpickles inside `cache.get()`** → logged as `cache_decode_ms=0` + `note=cache_decode_included_in_cache_get` |
| DB query (MISS) | `build_traffic_filter_groups(..., timings=)` | `CaptureQueriesContext` for count / SQL time; **no SQL text or params logged** |
| Serialize / group (MISS) | same builder | `serialize_ms`, `group_ms`, `python_ms` |
| Cache write (MISS) | `safe_cache_set(..., timings=)` | |
| JSON render | diagnostic `JSONRenderer().render(value)` in view | Does **not** replace DRF response rendering; response still `Response(data)` |
| Gzip | `SelectiveGZipMiddleware.process_response` | Times real middleware compression after forcing DRF `response.render()` |
| Log emit | after gzip in middleware | One `TRAFFIC_FILTER_PERF` line per measured request |

Perf context is attached to both the DRF `Request` and the underlying Django `HttpRequest` (`request._request`) so middleware can observe it.

When `TRAFFIC_FILTER_PERF_DEBUG=false` (default):

- View uses the original `cached_filter_response(...)` path
- Middleware only does `getattr(request, 'traffic_filter_perf', None)` → no-op
- Cache helpers receive `timings=None` (single None-check)

**No new public endpoints.** Only existing `POST /traffic_filter/`.

## 2. Environment variable

```text
TRAFFIC_FILTER_PERF_DEBUG=false
```

Settings mapping (`nhit/settings.py`):

```python
TRAFFIC_FILTER_PERF_DEBUG = _env_bool('TRAFFIC_FILTER_PERF_DEBUG', default=False)
```

Logger: `nhit.traffic_filter.perf` (console, INFO by default).  
Optional override: `NHIT_TRAFFIC_FILTER_PERF_LOG_LEVEL`.

## 3. Expected log format

One structured line per measured request:

```text
TRAFFIC_FILTER_PERF request_id=... cache=HIT rows=622 groups=311 response_bytes=321459 gzip_bytes=7175 content_encoding=gzip cache_get_ms=... cache_decode_ms=0 cache_lookup_ms=... python_ms=0 json_ms=... gzip_ms=... server_total_ms=... note=cache_decode_included_in_cache_get
```

MISS example:

```text
TRAFFIC_FILTER_PERF request_id=... cache=MISS rows=622 groups=311 response_bytes=321459 gzip_bytes=7175 content_encoding=gzip cache_get_ms=... cache_lookup_ms=... cache_write_ms=... db_ms=... db_query_count=... db_sql_ms=... db_slowest_ms=... python_ms=... serialize_ms=... group_ms=... json_ms=... gzip_ms=... server_total_ms=... note=cache_decode_included_in_cache_get
```

Fields (when applicable):

| Field | Meaning |
|-------|---------|
| `request_id` | `X-Request-ID` header if present, else UUID hex |
| `cache` | `HIT` or `MISS` |
| `rows` / `groups` | Nested payload sizes |
| `response_bytes` | Uncompressed JSON byte length (diagnostic render) |
| `gzip_bytes` | Wire body length when `Content-Encoding=gzip` |
| `content_encoding` | `gzip` or `identity` |
| `cache_get_ms` | Redis/django cache GET (includes unpickle for django-redis) |
| `cache_decode_ms` | Always `0` for traffic_filter (decode inside GET) |
| `db_ms` | Queryset `values()` evaluation wall time (MISS) |
| `db_query_count` / `db_sql_ms` / `db_slowest_ms` | From captured queries (no SQL logged) |
| `python_ms` | serialize + group (MISS); `0` on HIT |
| `json_ms` | Diagnostic JSON render |
| `gzip_ms` | Middleware compression |
| `server_total_ms` | View start → after gzip finalize |

**Not logged:** Redis URLs/passwords, full cache keys, Authorization, request body dump, response body, SQL parameters.

## 4. How to enable (Railway)

1. Deploy this instrumentation branch/commit (debug still **defaults to false**).
2. In Railway service variables, temporarily set:

```text
TRAFFIC_FILTER_PERF_DEBUG=true
```

3. Redeploy / restart so the process picks up the env var.
4. Call the existing API (Postman / client), **not** a debug URL.

### Recommended measurement plan

Warm-up: **~10** requests  
Measured: **~20** requests  

Primary payload:

```json
{
  "chainage_start": 0,
  "chainage_end": 1381,
  "date": "2026-05-20",
  "direction": ["All"],
  "project_name": ["Abu Road to Swaroopganj"]
}
```

Optional larger payload (few requests only):

```json
{
  "chainage_start": 0,
  "chainage_end": 99999,
  "date": "2025-08-20",
  "direction": ["All"],
  "project_name": ["Chittorgarh to Kota"]
}
```

Client-side, for each call record:

- total HTTP time
- status
- response size / Content-Encoding

Compare:

`CLIENT TOTAL TIME` vs log `server_total_ms`

Difference ≈ network / transfer / TLS / client overhead (do not assume without evidence).

Optional: send `X-Request-ID: <short-id>` to correlate client timings with Railway log lines.

## 5. How to disable

Set Railway variable:

```text
TRAFFIC_FILTER_PERF_DEBUG=false
```

or remove the variable (defaults to false). Redeploy/restart.

After measurements are collected, remove the temporary instrumentation code and re-run traffic_filter regression tests (cleanup phase — not part of this prepare step's optimization).

## 6. How to interpret results

| Case | Pattern | Meaning |
|------|---------|---------|
| **A** | `cache_get_ms` ≈ `server_total_ms` on HIT | Redis/network path dominates |
| **B** | HIT fast; MISS `db_ms` large | PostgreSQL/database dominates MISS |
| **C** | Redis + DB small; `python_ms` large | Backend processing dominates |
| **D** | `server_total_ms` small; client HTTP large | HTTP/response transfer / network |
| **E** | Connection/acquisition style delays (rare here; watch MISS `db_ms` vs `db_sql_ms`) | Pooling / infrastructure |
| **F** | Different dominant stages across requests | Mixed |

Also compare HIT vs MISS lines separately. First request after deploy may be MISS; warm requests should be HIT if cache is enabled.

**Do not treat prior local→public Redis benchmarks as authoritative Railway timings.**

## 7. Security considerations

- Debug mode **off by default**
- No new public debug/benchmark endpoint
- Logs omit secrets, credentials, Redis URLs, full keys, SQL params, bodies
- Response JSON / status / cache keys / TTL / gzip config / serializers **unchanged**
- Instrumentation adds diagnostic duplicate JSON render when ON only (extra CPU for measurement; does not change bytes returned by DRF)

## 8. Files touched

| File | Role |
|------|------|
| `traffic/services/traffic_filter_perf.py` | Flag, request id, log formatting |
| `traffic/views.py` | Gated instrumentation vs original path |
| `traffic/services/traffic_filter_response.py` | Optional MISS timings |
| `nhit/caching/service.py` | Optional `timings=` on get/set (default None) |
| `nhit/middleware.py` | Gzip timing + log finalize |
| `nhit/settings.py` | `TRAFFIC_FILTER_PERF_DEBUG` + logger |
| `traffic/tests_traffic_filter_perf.py` | Isolated tests |
| `tools/traffic_filter_production_perf_instrumentation.md` | This document |

## 9. Cleanup reminder (after you paste Railway logs)

1. Set `TRAFFIC_FILTER_PERF_DEBUG=false`
2. Remove temporary instrumentation
3. Run traffic_filter regression tests
4. Confirm production behavior matches original
5. **Do not implement optimizations from the measurements in the same change**

---

Endpoint to test: `POST /traffic_filter/`  
Env to enable: `TRAFFIC_FILTER_PERF_DEBUG=true`
