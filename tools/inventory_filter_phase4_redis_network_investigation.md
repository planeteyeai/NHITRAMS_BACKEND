# Phase 4 — Redis / Network Investigation (measurement only)

**Phase 4 investigation complete — no production behavior changed.**

## 1. Executive Summary

- **Classification:** `G. Mixed bottleneck (pending Railway runtime confirmation)`
- **Measurement context:** `local_dev_machine_to_configured_redis` (Windows → Railway **public** Redis proxy)
- **On Railway runtime:** `False` — **this is not yet the authoritative production path**

### What this run *does* prove (evidence-backed)

| Claim | Evidence |
|-------|----------|
| Remaining HIT cost is **not** Phase-3 extract/CPU | extract median **0.15 ms** vs redis-py GET median **~509 ms** |
| Redis **server** is fast | `cmdstat_get.usec_per_call` ≈ **2809 µs (~2.8 ms)** |
| App-observed GET is dominated by **client↔Redis path** | ~509 ms app GET vs ~2.8 ms server-side |
| New connections are expensive on this path | reused GET ~794 ms vs new connect+GET ~4019 ms |
| Fixed RTT dominates small payloads | PING ~342 ms; 64 KiB GET ~449 ms; 640 KiB ~469 ms |

### What this run does *not* prove

- Absolute latency of **Railway app → Redis internal** (private network)
- Whether production already uses `REDIS_INTERNAL_URL` at runtime (`NHIT_REDIS_USING_INTERNAL=False` here because not on Railway)
- Whether django-redis overhead remains material on Railway internal (local public-proxy numbers include long RTT + pickle unwrap)

### Ranked recommendations

1. **Re-run inside Railway** before Phase 5: `python manage.py benchmark_inventory_filter_phase4_redis_network`
2. On that run, compare internal vs public Redis (script already attempts both when on Railway)
3. Keep Redis connection reuse/pooling (avoid per-request connects)
4. Do **not** spend Phase 5 on JSON/render — extract is already ~0.15 ms
5. Do **not** change cache format/keys/TTL based on local-proxy numbers alone

Findings:

- Redis GET >> extract (Phase 3 conclusion confirmed)
- New connection substantially slower than reuse
- Redis server GET usec_per_call tiny vs app-observed GET
- Both Redis PING and Neon SELECT 1 elevated on this local path — expected for cross-internet RTT
- IMPORTANT: this run was NOT on Railway app runtime — local→Redis path inflates latency

Evidence:

- redis_py GET median=508.812ms; extract median=0.154ms
- reused GET=793.62ms; new connect+GET=4019.178ms
- server usec_per_call=2808.78µs (~2.809ms) vs app 508.812ms
- Neon SELECT 1 reused=203.01ms; Redis PING=342.02ms


## 2. Environment

- Platform: `win32` Python `3.13.2`
- Django `6.0`, redis-py `7.4.0`, django-redis `6.0.0`
- Cache enabled: `True`, backend `django_redis.cache.RedisCache`
- Resolved Redis: `{"present": true, "scheme": "redis", "tls": false, "host_kind": "railway_public_proxy", "host_suffix": "proxy.rlwy.net", "port": 45692, "has_password": true, "path": "/"}`
- NHIT_REDIS_USING_INTERNAL: `False`
- Postgres host: `{"present": true, "endpoint_kind": "neon_pooler", "region_hint": "ap-southeast-1", "host_suffix": "ap-southeast-1.aws.neon.tech", "is_neon": true}`

## 3. Test Methodology

- Warmups: 10; measured samples: 30 (concurrency uses 40 requests/level)
- Real inventory key via application `build_cache_key` + django-redis `make_key` (GET only; not modified)
- Synthetic size-ladder keys under `nhit:phase4_bench:` with TTL 120s, deleted after
- Secrets redacted (no URLs/passwords in report)

## 4. Real 50K Inventory Cache Results

- Key hash: `bde349b4fb7cbdca` suffix `e475567997dcfa52`
- Namespace/scope/version: `inventory_filter` / `page_gzip_json_v1` / v7
- Cache HIT: `True`
- Blob: `{"type": "bytes", "present": true, "size_with_magic": 640617, "starts_with_ifgz1": true, "gzip_payload_bytes": 640611, "valid_ifgz1": true, "gunzip_ms": 18.685, "uncompressed_json_bytes": 13812337}`

## 5. Redis GET Results

- PING median: **342.02 ms**
- redis-py GET median: **508.812 ms** (p95=900.474, max=1374.81)
- django safe_cache_get median: **1074.604 ms**
- extract median: **0.154 ms**
- Throughput (redis-py): `{"n": 30, "min": 0.444, "mean": 1.119, "median": 1.2, "p95": 1.282, "max": 1.373}`

## 6. Connection Reuse Results

- Reused GET median: **793.62 ms**
- New connection+ping median: **1691.993 ms**
- New total connect+GET median: **4019.178 ms**

## 7. Payload Size vs Latency

| Bytes | Median ms | P95 ms | Median MB/s |
|---:|---:|---:|---:|
| 65,536 | 449.354 | 495.492 | 0.139 |
| 262,144 | 418.939 | 489.532 | 0.597 |
| 524,288 | 454.496 | 501.572 | 1.1 |
| 655,360 | 468.607 | 511.712 | 1.333 |
| 1,048,576 | 502.551 | 716.379 | 1.99 |
| 2,097,152 | 704.783 | 844.186 | 2.838 |

## 8. Concurrency Results

| Conc | OK | Err | Median ms | P95 ms | Aggregate MB/s |
|---:|---:|---:|---:|---:|---:|
| 1 | 40 | 0 | 2251.913 | 3442.085 | 0.261 |
| 2 | 40 | 0 | 2189.414 | 2642.89 | 0.547 |
| 5 | 40 | 0 | 2270.31 | 2727.665 | 1.256 |
| 10 | 40 | 0 | 2602.471 | 3243.605 | 2.026 |

## 9. Internal Redis vs Public Redis

```json
{
  "on_railway": false,
  "internal_configured": true,
  "public_configured": true,
  "internal": {
    "ok": false,
    "endpoint": {
      "present": true,
      "scheme": "redis",
      "tls": false,
      "host_kind": "railway_internal",
      "host_suffix": "redis.railway.internal",
      "port": 6379,
      "has_password": true,
      "path": "/"
    },
    "skipped": true,
    "reason": "REDIS_INTERNAL_URL not reachable from non-Railway runtime (private DNS)"
  },
  "public": {
    "ok": true,
    "endpoint": {
      "present": true,
      "scheme": "redis",
      "tls": false,
      "host_kind": "railway_public_proxy",
      "host_suffix": "proxy.rlwy.net",
      "port": 45692,
      "has_password": true,
      "path": "/"
    },
    "ping": {
      "label": "PING",
      "latency_ms": {
        "n": 20,
        "min": 197.145,
        "mean": 230.804,
        "median": 217.425,
        "p95": 288.064,
        "p99": 302.241,
        "max": 305.785,
        "stdev": 33.332,
        "samples_ms": [
          201.486,
          203.076,
          230.382,
          266.582,
          204.553,
          197.145,
          212.036,
          252.905,
          203.283,
          277.431,
          220.127,
          197.697,
          208.533,
          232.058,
          214.723,
          287.132,
          241.598,
          259.15,
          200.388,
          305.785
        ]
      }
    },
    "get_64kib": {
      "label": "public_64kib_GET",
      "payload_bytes": 65536,
      "latency_ms": {
        "n": 20,
        "min": 267.244,
        "mean": 353.787,
        "median": 350.638,
        "p95": 431.0,
        "p99": 450.046,
        "max": 454.808,
        "stdev": 49.554,
        "samples_ms": [
          454.808,
          429.747,
          303.398,
          369.125,
          327.156,
          338.942,
          362.529,
          336.994,
          369.291,
          363.29,
          332.413,
          313.969,
          326.989,
          267.244,
          395.32,
          269.338,
          402.038,
          419.261,
          331.56,
          362.334
        ]
      },
      "throughput_mbs": {
        "n": 20,
        "min": 0.137,
        "mean": 0.18,
        "median": 0.178,
        "p95": 0.232,
        "max": 0.234
      }
    }
  }
}
```

## 10. TLS Analysis

```json
{
  "tls_enabled": false,
  "endpoint": {
    "present": true,
    "scheme": "redis",
    "tls": false,
    "host_kind": "railway_public_proxy",
    "host_suffix": "proxy.rlwy.net",
    "port": 45692,
    "has_password": true,
    "path": "/"
  },
  "note": "Resolved Redis URL scheme is not TLS (rediss). Handshake overhead N/A for this endpoint."
}
```

## 11. django-redis vs redis-py

```json
{
  "safe_cache_get_minus_redis_py_median": 569.685,
  "django_client_get_minus_redis_py_median": 198.858
}
```

## 12. Redis Server-side Measurements

```json
{
  "server": {
    "redis_version": "8.2.9",
    "os": "Linux 6.12.12+bpo-cloud-amd64 x86_64",
    "tcp_port": 6379,
    "uptime_in_seconds": 781894
  },
  "stats": {
    "total_connections_received": 63,
    "total_commands_processed": 2529,
    "instantaneous_ops_per_sec": 2,
    "total_net_input_bytes": 489079735,
    "total_net_output_bytes": 5367533798,
    "instantaneous_input_kbps": 0.04,
    "instantaneous_output_kbps": 0.4
  },
  "commandstats_get": {
    "calls": 1618,
    "usec": 4544610,
    "usec_per_call": 2808.78,
    "rejected_calls": 2,
    "failed_calls": 0
  },
  "latency_latest": [],
  "slowlog_top5": [
    {
      "id": 158,
      "duration_us": 11593,
      "command": "GET"
    },
    {
      "id": 157,
      "duration_us": 11130,
      "command": "GET"
    },
    {
      "id": 156,
      "duration_us": 14880,
      "command": "GET"
    },
    {
      "id": 155,
      "duration_us": 18009,
      "command": "GET"
    },
    {
      "id": 154,
      "duration_us": 18203,
      "command": "GET"
    }
  ]
}
```

## 13. Railway → Redis vs Railway → Neon

- Neon SELECT 1 reused median: **203.01 ms**
- Neon connect median: **1512.222 ms**
- Inventory COUNT median: **214.193 ms**
- Redis PING median: **342.02 ms**
- Redis large GET median: **508.812 ms**

## 14. Bottleneck Analysis

```json
{
  "T1_connection_acquisition": {
    "reused": "amortized ~0 (pool)",
    "new_connection_median_ms": 1691.993
  },
  "T2_T3_redis_get_and_transfer_ms": {
    "n": 30,
    "min": 444.993,
    "mean": 577.207,
    "median": 508.812,
    "p95": 900.474,
    "p99": 1287.299,
    "max": 1374.81,
    "stdev": 188.751,
    "samples_ms": [
      479.043,
      503.351,
      525.133,
      494.026,
      488.317,
      643.57,
      444.993,
      509.439,
      596.029,
      602.269,
      507.146,
      497.727,
      508.184,
      491.989,
      498.759,
      501.401,
      502.167,
      540.698,
      490.887,
      1073.047,
      511.244,
      498.191,
      522.353,
      474.656,
      531.334,
      689.552,
      615.431,
      591.885,
      608.586,
      1374.81
    ]
  },
  "T4_extract_ms": {
    "n": 30,
    "min": 0.027,
    "mean": 0.17,
    "median": 0.154,
    "p95": 0.351,
    "p99": 0.461,
    "max": 0.472,
    "stdev": 0.103,
    "samples_ms": [
      0.472,
      0.033,
      0.202,
      0.434,
      0.146,
      0.146,
      0.149,
      0.154,
      0.144,
      0.027,
      0.24,
      0.15,
      0.23,
      0.155,
      0.251,
      0.145,
      0.05,
      0.213,
      0.148,
      0.029,
      0.175,
      0.213,
      0.184,
      0.164,
      0.201,
      0.137,
      0.245,
      0.027,
      0.029,
      0.218
    ]
  },
  "T5_response_construction": "not re-measured here (Phase 3: ~0.2ms for direct gzip)",
  "note": "T2/T3 cannot be split further without packet capture; GET time includes server+network+transfer"
}
```

- Decision key medians: `{"redis_ping": 342.02, "redis_py_get": 508.812, "django_safe_cache_get": 1074.604, "extract": 0.154, "reused_get": 793.62, "new_connection_total": 4019.178, "neon_select1_reuse": 203.01, "payload_bytes": 640617}`

## 15. Recommendation

1. Re-run this exact script inside Railway (`railway run` / one-off service) before Phase 5.
2. Ensure connection pooling/reuse remains enabled (avoid per-request Redis connects).
3. Do not change cache format/keys/TTL based on this phase alone.

## 16. What NOT to Optimize Yet

- Python JSON rendering / DRF (Phase 3 already removed this from gzip HIT path)
- Redis cache format / IFGZ1 / gzip level / TTL / keys
- PostgreSQL indexes / PostGIS / materialized views (unless Neon RTT proves primary)
- Frontend / page_size policy

---

**Phase 4 investigation complete — no production behavior changed.**

Raw results: `benchmark_inventory_filter_phase4_redis_network_results.json`
