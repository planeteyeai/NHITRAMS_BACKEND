# Traffic Filter API — Performance Investigation

**Traffic Filter performance investigation complete — no production behavior changed.**

## 1. Executive Summary

- **Classification:** `A. Redis/network transfer` (HIT path) + `C. PostgreSQL/database` (MISS path) + `H. Infrastructure/network` (local↔cloud RTT caveat)
- **Environment:** `local_dev_machine` → Railway public Redis + Neon `ap-southeast-1` (`on_railway=False`)
- **Primary median HTTP (gzip, after warmup ≈ HIT):** **752 ms** (P95 **1212 ms**)
- **Response:** **321,459 B** JSON → **7,175 B** gzip (~98% reduction)
- **Cached:** Yes — `traffic_filter` namespace, **TTL 300s**, **pickled Python nested list** (not IFGZ1/gzip like inventory_filter)

### Where time goes (primary Abu Road, 622 rows)

| Stage | Median | Share of HIT story |
|-------|-------:|---|
| Redis/django cache GET | **600 ms** | Dominates HIT |
| redis-py raw GET (~143 KB pickle) | **402 ms** | Confirms network/transfer |
| JSON render | **2.4 ms** | Negligible |
| Gzip | **1.5 ms** | Negligible |
| MISS DB values() fetch | **144 ms** | Dominates MISS build |
| MISS Python serialize+group | **~2.6 ms** | Negligible |

**Answer:** For warm/HIT requests, `traffic_filter` is slow primarily because of **Redis GET of a pickled Python object over the (local→public Redis) network path**, not because of Python loops, JSON, or gzip. On MISS, **PostgreSQL/queryset fetch** dominates the build (~147 ms), still smaller than Redis HIT GET on this measurement host.

Unlike `inventory_filter` Phase 2/3 (compressed JSON bytes + direct-byte HTTP), traffic still pickles nested Python and re-renders on every HIT—but for this primary payload, re-render/gzip are tiny; Redis transfer/unpickle path is the main HIT cost.

Findings:

- Redis/cache GET is a large fraction of HIT HTTP time (~600 of ~752 ms)
- DB/queryset `values()` fetch dominates MISS build (~144 of ~147 ms)
- Run was not on Railway — absolute ms include local↔cloud RTT; re-run on Railway before optimizing

## 2. API / request tested

- Endpoint: `POST /traffic_filter/`
- Auth: none (APIView default — no JWT on this view)
- Pagination: none — returns full nested array
- Cache: `{"helper": "cached_filter_response \u2192 get_or_set_cached_value", "namespace": "traffic_filter", "ttl_seconds": 300, "representation": "pickled Python nested list (django-redis default)", "stampede_lock": true}`

Primary request (contract / Railway suite):

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

- Approx matching DB rows: **622**
- Response groups/rows: **311** / **622**

Large request (scale context, same fields):

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

- Approx matching DB rows: **14249**
- Response groups/rows: **1605** / **14249**

## 3. Environment

```json
{
  "runtime": {
    "platform": "win32",
    "python": "3.13.2",
    "on_railway": false,
    "measurement_context": "local_dev_machine",
    "railway_environment": null,
    "railway_region": null
  },
  "django_version": "6.0",
  "cache_enabled": true,
  "cache_backend": "django_redis.cache.RedisCache",
  "cache_ttl_filter_seconds": 300,
  "redis_location": {
    "present": true,
    "scheme": "redis",
    "tls": false,
    "host_kind": "railway_public_proxy",
    "host_suffix": "proxy.rlwy.net",
    "port": 45692
  },
  "postgres_host": {
    "present": true,
    "endpoint_kind": "neon_pooler",
    "region_hint": "ap-southeast-1",
    "host_suffix": "ap-southeast-1.aws.neon.tech",
    "is_neon": true
  },
  "gzip_middleware": true
}
```

## 4. Response size

- Primary uncompressed: **321,459** bytes
- Primary gzip wire: **7,175** bytes
- Content-Encoding when Accept-Encoding:gzip: `gzip`
- Large uncompressed: **7,528,638**
- Large gzip: **138,486**

## 5. End-to-end timings

| Case | Median | P95 | P99 | Max |
|---|---:|---:|---:|---:|
| Primary + gzip | 751.909 | 1212.289 | 2680.143 | 3197.473 |
| Primary no gzip | 740.135 | 912.14 | 1491.887 | 1727.324 |
| Large + gzip | 3178.14 | 3872.566 | 3997.397 | 4028.604 |

## 6–7. Cache HIT / Redis

```json
{
  "key_meta": {
    "namespace": "traffic_filter",
    "version": 1,
    "ttl_seconds": 300,
    "logical_key_hash": "1d616d2dea04f9fd",
    "logical_key_suffix": "6a9c8002e6127abe"
  },
  "cache_hit": true,
  "value_type": "list",
  "pickle_size_estimate_bytes": 143367,
  "django_safe_cache_get_ms": {
    "n": 30,
    "min": 405.325,
    "mean": 640.189,
    "median": 600.043,
    "p95": 1005.466,
    "p99": 1332.506,
    "max": 1442.548,
    "stdev": 242.496,
    "samples_ms": [
      442.615,
      414.035,
      412.657,
      420.532,
      454.255,
      419.554,
      437.087,
      405.325,
      448.77,
      505.087,
      417.707,
      470.267,
      440.567,
      460.127,
      513.154,
      686.932,
      1442.548,
      1063.092,
      935.034,
      866.147,
      845.44,
      805.898,
      746.235,
      731.239,
      728.17,
      744.733,
      704.001,
      800.439,
      717.292,
      726.726
    ]
  },
  "redis_py_raw_get": {
    "payload_bytes": 143367,
    "latency_ms": {
      "n": 30,
      "min": 372.862,
      "mean": 420.193,
      "median": 402.232,
      "p95": 480.007,
      "p99": 712.909,
      "max": 807.529,
      "stdev": 76.899,
      "samples_ms": [
        403.438,
        394.869,
        400.79,
        406.413,
        404.082,
        391.712,
        401.025,
        405.157,
        427.606,
        478.483,
        433.603,
        382.537,
        481.254,
        418.148,
        406.744,
        372.862,
        398.81,
        413.379,
        388.907,
        396.378,
        413.739,
        807.529,
        384.466,
        395.111,
        397.977,
        413.386,
        388.509,
        412.157,
        387.829,
        398.904
      ]
    },
    "redis_key_hash": "3619d9b948ced537"
  },
  "json_render_from_cached_object_ms": {
    "n": 30,
    "min": 2.124,
    "mean": 2.704,
    "median": 2.37,
    "p95": 4.639,
    "p99": 4.946,
    "max": 4.999,
    "stdev": 0.801,
    "samples_ms": [
      2.638,
      2.236,
      3.611,
      2.322,
      2.23,
      2.149,
      3.421,
      2.429,
      2.186,
      2.292,
      3.572,
      2.418,
      2.21,
      2.141,
      4.999,
      2.245,
      2.271,
      2.124,
      4.818,
      2.231,
      2.195,
      2.28,
      2.633,
      2.334,
      2.493,
      2.513,
      2.851,
      2.454,
      2.405,
      4.421
    ]
  },
  "gzip_compress_rendered_ms": {
    "n": 30,
    "min": 1.416,
    "mean": 1.611,
    "median": 1.493,
    "p95": 2.211,
    "p99": 2.538,
    "max": 2.575,
    "stdev": 0.278,
    "samples_ms": [
      1.492,
      1.621,
      1.494,
      1.59,
      1.424,
      1.613,
      1.472,
      1.452,
      1.427,
      1.568,
      1.478,
      1.465,
      1.423,
      1.416,
      1.786,
      1.443,
      1.44,
      1.865,
      1.485,
      1.436,
      1.423,
      1.92,
      1.475,
      1.632,
      1.617,
      2.45,
      1.612,
      1.613,
      1.627,
      2.575
    ]
  },
  "rendered_json_bytes": 321459,
  "gzip_bytes": 7158
}
```

## 8–9. Database / Python (MISS pipeline, no invalidation)

```json
{
  "note": "MISS pipeline measured in-process without Redis invalidation",
  "row_count_median": 622.0,
  "query_count_median": 1.0,
  "db_connection_establish_ms": {
    "n": 10,
    "min": 666.78,
    "mean": 781.453,
    "median": 697.924,
    "p95": 1184.361,
    "p99": 1475.101,
    "max": 1547.786,
    "stdev": 270.196,
    "samples_ms": [
      687.903,
      700.189,
      1547.786,
      685.111,
      698.063,
      697.786,
      722.959,
      667.777,
      740.174,
      666.78
    ]
  },
  "select1_reused_ms": {
    "n": 30,
    "min": 93.324,
    "mean": 103.204,
    "median": 101.85,
    "p95": 111.231,
    "p99": 138.284,
    "max": 148.843,
    "stdev": 9.668,
    "samples_ms": [
      93.854,
      94.623,
      102.218,
      93.324,
      99.661,
      101.737,
      98.383,
      100.64,
      100.216,
      100.943,
      102.374,
      103.685,
      101.932,
      99.787,
      95.918,
      102.017,
      101.937,
      104.616,
      100.756,
      101.433,
      101.768,
      103.201,
      107.321,
      102.345,
      109.042,
      112.434,
      96.603,
      104.737,
      148.843,
      109.761
    ]
  },
  "values_fetch_ms": {
    "n": 30,
    "min": 110.288,
    "mean": 148.244,
    "median": 144.187,
    "p95": 187.641,
    "p99": 320.276,
    "max": 374.108,
    "stdev": 48.559,
    "samples_ms": [
      374.108,
      118.449,
      110.288,
      147.695,
      119.05,
      113.753,
      144.631,
      114.409,
      114.135,
      146.524,
      113.049,
      111.813,
      163.139,
      186.612,
      111.058,
      164.343,
      133.374,
      174.323,
      171.762,
      143.742,
      146.214,
      143.183,
      146.423,
      124.681,
      155.592,
      141.056,
      147.5,
      118.849,
      159.076,
      188.482
    ]
  },
  "serialize_rows_ms": {
    "n": 30,
    "min": 2.157,
    "mean": 2.512,
    "median": 2.264,
    "p95": 3.633,
    "p99": 4.803,
    "max": 5.243,
    "stdev": 0.642,
    "samples_ms": [
      2.433,
      2.257,
      3.727,
      2.28,
      3.031,
      2.202,
      2.157,
      2.176,
      2.257,
      2.217,
      2.165,
      2.226,
      2.199,
      2.193,
      2.339,
      2.493,
      2.619,
      2.221,
      5.243,
      2.327,
      2.159,
      2.3,
      2.54,
      2.27,
      2.182,
      2.773,
      2.245,
      2.351,
      2.256,
      3.518
    ]
  },
  "group_rows_ms": {
    "n": 30,
    "min": 0.289,
    "mean": 0.577,
    "median": 0.307,
    "p95": 0.904,
    "p99": 4.949,
    "max": 6.56,
    "stdev": 1.141,
    "samples_ms": [
      6.56,
      0.309,
      0.413,
      0.298,
      1.005,
      0.302,
      0.289,
      0.299,
      0.55,
      0.294,
      0.292,
      0.3,
      0.433,
      0.307,
      0.307,
      0.78,
      0.355,
      0.305,
      0.382,
      0.311,
      0.298,
      0.31,
      0.347,
      0.297,
      0.292,
      0.36,
      0.297,
      0.305,
      0.292,
      0.413
    ]
  },
  "build_total_ms": {
    "n": 30,
    "min": 113.791,
    "mean": 151.44,
    "median": 146.816,
    "p95": 191.018,
    "p99": 327.938,
    "max": 383.255,
    "stdev": 49.619,
    "samples_ms": [
      383.255,
      121.101,
      114.522,
      150.359,
      123.221,
      116.348,
      147.16,
      116.97,
      117.124,
      149.151,
      115.591,
      114.421,
      165.899,
      189.2,
      113.791,
      167.948,
      136.459,
      176.931,
      177.483,
      146.471,
      148.754,
      145.887,
      149.402,
      127.34,
      158.163,
      144.301,
      150.126,
      121.606,
      161.706,
      192.504
    ]
  },
  "builder_parity_ok": true
}
```

## 10–11. Serialization / Gzip

- HIT JSON render median: **2.37 ms**
- HIT gzip median: **1.493 ms**
- MISS JSON render median: **2.127 ms**
- MISS gzip median: **1.472 ms**

## 12. Network / transfer analysis

- Client-observed HTTP time includes Test Client stack + Redis GET + render + middleware.
- Compare Redis GET + render + gzip sum vs HTTP total; residual is framework/transfer overhead.
- Primary gzip ratio: 97.8% size reduction

## 13. Concurrency

| Conc | OK | Err | Median | P95 | req/s |
|---:|---:|---:|---:|---:|---:|
| 1 | 20 | 0 | 926.804 | 1175.055 | 1.028 |
| 2 | 20 | 0 | 974.153 | 1623.534 | 1.741 |
| 5 | 20 | 0 | 911.167 | 2773.809 | 4.024 |
| 10 | 20 | 0 | 1115.592 | 3594.911 | 5.526 |

## 14. Local vs Railway

- This run: **local_dev_machine**
- Railway CLI may be required for authoritative production numbers.
- Re-run: `python tools/benchmark_traffic_filter_performance.py` on Railway.

## 15. Timing breakdown (primary)

| Stage | Median | P95 |
|---|---:|---:|
| HTTP total (Accept-Encoding:gzip, mostly HIT after warmup) | 751.909 | 1212.289 |
| Redis/django cache GET | 600.043 | 1005.466 |
| Redis-py raw GET | 402.232 | 480.007 |
| JSON render from cached object | 2.37 | 4.639 |
| Gzip compress (modeled) | 1.493 | 2.211 |
| MISS values() fetch | 144.187 | 187.641 |
| MISS serialize rows | 2.264 | 3.633 |
| MISS group rows | 0.307 | 0.904 |
| MISS build total | 146.816 | 191.018 |
| Neon SELECT 1 reused | 101.85 | 111.231 |

## 16. Bottleneck classification

- Classes: A. Redis/network transfer, C. PostgreSQL/database, H. Infrastructure/network
- Largest stage: `http_total_hit`

## 17. Recommendation / answers

1. Median total response: **751.909 ms**
2. P95: **1212.289 ms**
3. Response size: `{'uncompressed': 321459, 'gzip': 7175}`
4. Cached: **True**
5. Redis: `{'django_cache_get': 600.043, 'redis_py': 402.232}`
6. PostgreSQL: `{'miss_values_fetch': 144.187, 'select1_reused': 101.85}`
7. Python processing: `{'serialize_rows': 2.264, 'group_rows': 0.307}`
8. Serialization/JSON: **2.37 ms**
9. Gzip: **1.493 ms**
10. Largest time: **http_total_hit**
11. Next: Re-run on Railway for authoritative numbers; then optimize the largest HIT stage (likely Redis pickle GET and/or re-render, depending on Railway results).

Compare to inventory_filter: inventory HIT uses compressed JSON bytes + direct-byte serving; traffic_filter still pickles a Python nested object and re-renders JSON on every HIT. Do not assume identical bottlenecks without Railway confirmation.

---

**Traffic Filter performance investigation complete — no production behavior changed.**

Raw: `benchmark_traffic_filter_performance_results.json`
