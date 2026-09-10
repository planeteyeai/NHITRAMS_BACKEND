# Traffic Filter — Phase 4A End-to-End Latency Investigation

**Investigation only — no production behavior changed.**

## 1. Current 6-second observation

Postman reports approximately **6 seconds** for `POST /traffic_filter/`.
Phase 3 already made gzip HIT path HTTP-ready (TFGZ1 → direct bytes).
Local Phase 3 APIClient benchmark showed ~702 ms for the large payload —
so the ~6s Postman time is **not** explained by JSON/gzip CPU.

## 2. Exact benchmark environment

- measurement_context: `local_dev_machine`
- on_railway: `False`
- cache_usable: `True` / backend `RedisCache`
- redis: `{"present": true, "scheme": "redis", "host_kind": "railway_public_redis_proxy", "host_suffix": "proxy.rlwy.net", "port": 45692, "path": "/"}`
- postgres: `{"present": true, "host_suffix": "ap-southeast-1.aws.neon.tech", "conn_max_age": 60, "is_neon": true}`
- middleware: `['django.middleware.security.SecurityMiddleware', 'nhit.middleware.SelectiveGZipMiddleware', 'whitenoise.middleware.WhiteNoiseMiddleware', 'corsheaders.middleware.CorsMiddleware', 'django.contrib.sessions.middleware.SessionMiddleware', 'django.middleware.common.CommonMiddleware', 'django.middleware.csrf.CsrfViewMiddleware', 'django.contrib.auth.middleware.AuthenticationMiddleware', 'django.contrib.messages.middleware.MessageMiddleware', 'django.middleware.clickjacking.XFrameOptionsMiddleware']`
- traffic_filter auth: none on view
- NHIT_API_BASE_URL: `{"present": false}`
- note: APIClient measurements are in-process (no real client↔Railway HTTP). Set NHIT_API_BASE_URL for Postman-comparable external HTTP timings.

## 3. Request payloads

### Primary

```json
{
  "chainage_start": 0,
  "chainage_end": 1381,
  "date": "2026-05-20",
  "direction": [
    "All"
  ],
  "project_name": [
    "Abu Road to Swaroopganj"
  ]
}
```

### Large

```json
{
  "chainage_start": 0,
  "chainage_end": 99999,
  "date": "2025-08-20",
  "direction": [
    "All"
  ],
  "project_name": [
    "Chittorgarh to Kota"
  ]
}
```

## 4. Cache state

- Primary: HIT confirmed=True (hits=25, misses=0)
- Large: HIT confirmed=True (hits=25, misses=0)
- Format: TFGZ1 / scope `gzip_json_v1` / TTL 300s
- Primary warm: `{'warmed': True, 'cache_hit': True, 'response_path': 'direct_gzip_bytes', 'redis_bytes': 7164, 'gzip_bytes': 7158, 'json_bytes': 321459, 'key_suffix': '618006033e13'}`
- Large warm: `{'warmed': True, 'cache_hit': True, 'response_path': 'direct_gzip_bytes', 'redis_bytes': 138458, 'gzip_bytes': 138452, 'json_bytes': 7528638, 'key_suffix': '547e09e1b68a'}`

## 5. Payload sizes

- Primary redis/gzip/json: 7164 / 7158 / 321459
- Large redis/gzip/json: 138458 / 138452 / 7528638

## 6. Median / P95 by stage (APIClient in-process, gzip HIT)

### Primary

| Stage | Median | P95 |
|---|---:|---:|
| middleware_before_view_ms | 0.25 | 0.434 |
| view_total_ms | 299.351 | 325.504 |
| namespace_version_ms | 98.219 | 104.737 |
| cache_key_build_ms | 0.07 | 0.117 |
| cache_key_ms | 98.307 | 104.801 |
| redis_get_ms | 100.95 | 126.01 |
| redis_total_est_ms | 199.413 | 226.698 |
| cache_extract_ms | 0.01 | 0.01 |
| cache_helper_ms | 201.179 | 234.829 |
| response_build_ms | 299.345 | 325.487 |
| gzip_decision_ms | 0.008 | 0.049 |
| middleware_after_view_ms | 0.118 | 0.653 |
| server_handler_ms | 299.715 | 326.551 |
| server_total_ms | 299.716 | 326.555 |

| client_total_gzip_ms | 300.049 | 327.364 |

### Large

| Stage | Median | P95 |
|---|---:|---:|
| middleware_before_view_ms | 0.245 | 0.569 |
| view_total_ms | 291.35 | 362.611 |
| namespace_version_ms | 79.102 | 125.21 |
| cache_key_build_ms | 0.07 | 0.221 |
| cache_key_ms | 79.571 | 125.328 |
| redis_get_ms | 113.61 | 158.78 |
| redis_total_est_ms | 191.435 | 279.447 |
| cache_extract_ms | 0.01 | 0.02 |
| cache_helper_ms | 213.658 | 267.564 |
| response_build_ms | 291.341 | 362.599 |
| gzip_decision_ms | 0.007 | 0.011 |
| middleware_after_view_ms | 0.11 | 0.159 |
| server_handler_ms | 291.665 | 362.995 |
| server_total_ms | 291.666 | 362.997 |

| client_total_gzip_ms | 291.992 | 363.491 |

## 7. Client-side total (in-process APIClient)

- Primary gzip median: **300.049 ms**
- Large gzip median: **291.992 ms**

## 8. Server-side total

- Primary server_total median: **299.716 ms**
- Large server_total median: **291.666 ms**

## 9. Client vs server difference

- Primary client−server: **0.333 ms** (expected near-zero for APIClient)

## 10. Redis contribution

- Primary redis_get median: **100.95 ms**
- Large redis_get median: **113.61 ms**
- Primary namespace_version median: **98.219 ms**
- Primary redis_total_est median: **199.413 ms**
- Primary redis PING median: **100.05 ms**
- Large redis PING median: **99.915 ms**

**Important:** every HIT performs **at least two Redis round trips** (`get_namespace_version` + value GET). With ~300ms public-proxy PING, that alone is ~600ms+.

## 11. DB contribution (HIT)

- Primary db_queries samples: `[0, 0, 0, 0, 0]...`
- Large db_queries samples: `[0, 0, 0, 0, 0]...`
- Expected: **0** SQL queries on TFGZ1 HIT

## 12. Middleware contribution

- Primary before-view median: **0.25 ms**
- Primary after-view median: **0.118 ms**
- Primary gzip_decision median: **0.008 ms** (already_encoded expected true on direct gzip HIT)

## 13. HTTP transfer contribution

- External HTTP skipped: `NHIT_API_BASE_URL not set — cannot compare to Postman/deployed HTTP`
- In-process APIClient does **not** include real TLS/HTTP transfer to Railway.
- To compare with Postman: `$env:NHIT_API_BASE_URL="https://<railway-app>"; python tools/traffic_filter_phase4_end_to_end_benchmark.py`

## 14. Postman comparison

- Postman reported ~6000 ms.
- In-process APIClient large median: **291.992 ms**.
- These measure different paths: Postman = real network to deployed app;
  APIClient = local Django handler + public Redis from this machine.
- Across Phase 4A runs on this machine, in-process HIT totals ranged roughly
  **~300 ms to ~1700 ms** depending on public-Redis proxy RTT variance — still far below 6s.

## 15. Root-cause classification

**G - mixed**

Factors:
- **A - Redis/network** — dominates the in-process server path (two Redis RTTs: namespace version + TFGZ1 value)
- **E/F - HTTP/client transfer / connection** — leading explanation for the Postman gap (not yet measured; `NHIT_API_BASE_URL` unset)
- **H - unknown** — until external Postman-comparable HTTP is measured

Not the bottleneck on HIT:
- PostgreSQL (0 queries)
- TFGZ1 extract (~0.01 ms)
- Django middleware (~0.25 ms)
- gzip decision (~0.01 ms; already encoded)

### Direct answer

> **Postman shows approximately 6 seconds. Exactly where are those 6 seconds going?**

**Not inside Phase-3 application CPU.** On confirmed TFGZ1 gzip HITs measured in-process:

| Where | Approx. time |
|--|--:|
| Django middleware | ~0.3 ms |
| Redis namespace version GET | ~80–100+ ms (varies with public proxy) |
| Redis value GET | ~100–450+ ms (varies; larger for big payloads / slow RTT) |
| TFGZ1 extract | ~0.01 ms |
| HttpResponse + gzip passthrough | ~0.01 ms |
| DB | **0 queries** |
| **In-process total** | **~300 ms median this run** (other runs up to ~1–2 s when Redis RTT spikes) |

That leaves roughly **~4–5.7 seconds of the Postman ~6s unexplained by the Django view/middleware/TFGZ1 path**.

Those remaining seconds are **outside** the optimized HIT handler and are most consistent with:

1. **Client ↔ Railway HTTPS** (TTFB + body transfer + TLS), and/or  
2. **Postman UI / connection setup**, and/or  
3. **A cold path** (cache MISS, worker cold start) on the request Postman measured  

**Do not spend Phase 5 on another JSON/gzip/cache-format optimization.**  
Next measurement: set `NHIT_API_BASE_URL` to the Railway app URL and re-run this benchmark (requests/httpx TTFB vs transfer), or capture Railway logs with `TRAFFIC_FILTER_PERF_DEBUG` on the live app.

### Headers verification (last gzip HIT)

- Primary: `{'status': 200, 'content_type': 'application/json', 'content_encoding': 'gzip', 'content_length': '7158', 'vary': 'Accept-Encoding, Accept, origin', 'transfer_encoding': None, 'wire_bytes': 7158, 'uncompressed_bytes': 321459, 'tfgz1_not_in_body': True, 'response_path': 'direct_gzip_bytes', 'db_queries': 0}`
- Large: `{'status': 200, 'content_type': 'application/json', 'content_encoding': 'gzip', 'content_length': '138452', 'vary': 'Accept-Encoding, Accept, origin', 'transfer_encoding': None, 'wire_bytes': 138452, 'uncompressed_bytes': 7528638, 'tfgz1_not_in_body': True, 'response_path': 'direct_gzip_bytes', 'db_queries': 0}`

### Phase 5 guidance

- Do **not** re-optimize TFGZ1/JSON/gzip CPU next.
- Next: measure real client→Railway HTTP (set `NHIT_API_BASE_URL`) and/or
  Railway app→internal Redis path before changing infrastructure.

