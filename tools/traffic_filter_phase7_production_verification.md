# Traffic Filter Phase 7 — Production Deployment & Railway Cache HIT Verification

**Diagnosis only — no production API behavior changed.**  
**`TRAFFIC_FILTER_PERF_DEBUG` was not left enabled** (could not be set: Railway CLI unauthorized).

Base URL: `https://nhitramsbackend-production.up.railway.app`  
Endpoint: `POST /traffic_filter/`

---

## Executive answer

| Question | Answer |
|---|---|
| Which commit is expected / in git `origin/main`? | **`da582cb8b70e440f2ad51aeb93dd1a2ad95c7938`** — *Optimized traffic endpoint* (2026-09-10 12:11:57 +0530) |
| Exact Railway-deployed SHA? | **Not confirmed via Railway API** (CLI `Unauthorized`) |
| Phase 3 deployed? | **YES (strongly evidenced)** — live responses match TFGZ1 sizes; code present on `origin/main` |
| Phase 5 deployed? | **YES (strongly evidenced)** — `get_many` / `tfgz1_ns_v1` / `traffic_filter:data:gzip_json_v1:*` present on `origin/main`; live gzip bytes **stable** |
| Is Railway serving TFGZ1 HIT? | **YES (behavioral proof)** — repeated identical requests return **identical** gzip `Content-Length` |
| Is another traffic_filter code optimization required before understanding the remaining ~2 s? | **Not yet** — first finish Railway log/`server_total_ms` capture with auth; remaining latency is on the live HIT path vs Phase 4A in-process (~300 ms) |

**Root cause of Phase 6 (~5.3–5.8 s + varying gzip sizes):**  
Phases 3/5 were **not on Railway** during Phase 6. Git still pointed at older pickle `cached_filter_response` behavior until `da582cb` landed. Re-serializing + re-gzipping each request explains varying `Content-Length`.

**Current state (post-`da582cb`):**  
HIT appears live (stable `7158` / `138452` bytes), but external TTFB is still **~1.8–3.1 s** (warm), not the ~300–700 ms in-process Phase 4A/5 profile. That residual gap is the Phase 8 investigation target — **not** another TFGZ1 redesign.

---

## 1. Deployed version

### Expected (git)

- `origin/main` / local `HEAD`: **`da582cb8b70e440f2ad51aeb93dd1a2ad95c7938`**
- Message: `Optimized traffic endpoint`
- Includes:
  - `nhit/caching/traffic_filter_cache.py` (TFGZ1 + Phase 5 `get_many`)
  - `traffic/views.py` → `get_cached_traffic_filter_response`
  - `safe_cache_get_many` in `nhit/caching/service.py`
  - `traffic/services/traffic_filter_perf.py` + `TRAFFIC_FILTER_PERF_DEBUG` setting
  - tests / phase tooling

### Prior commit (what Phase 6 almost certainly hit)

- Before `da582cb`, `traffic/views.py` used:

```python
return cached_filter_response(
    namespace=CACHE_NS_TRAFFIC_FILTER,
    payload=data,
    builder=build_response,
)
```

- That path pickles nested Python, then DRF + middleware gzip → **non-stable gzip sizes** (matches Phase 6).

### Railway SHA match

| Item | Status |
|---|---|
| Expected commit | `da582cb…` |
| Railway reported deploy SHA | **Unavailable** — `railway whoami` / `railway status` → `Unauthorized. Please login with railway login` |
| Behavioral match to `da582cb` | **Strong** (see §6–§8) |

---

## 2. Cache backend (safe metadata)

From **deployed source** (`origin/main` `nhit/settings.py` + constants) and local settings shape (no secrets):

| Field | Value |
|---|---|
| Cache enabled when Redis URL resolved | `NHIT_CACHE_ENABLED = bool(_REDIS_URL)` |
| Backend class | `django_redis.cache.RedisCache` |
| Key prefix | `nhit` |
| `CACHE_TTL_FILTER` | **300** |
| Redis URL selection | On Railway runtime (`RAILWAY_*` set): prefer `REDIS_INTERNAL_URL`, else `REDIS_URL` |
| `TRAFFIC_FILTER_PERF_DEBUG` (code default) | **false** |
| Local probe `TRAFFIC_FILTER_PERF_DEBUG` | false |
| Local `NHIT_REDIS_USING_INTERNAL` | false (expected off Railway) |

**Railway live env values** (whether `REDIS_INTERNAL_URL` is set, replica count, exact debug flag): **not readable** without Railway auth.

---

## 3. Safe Redis / process identity

| Item | Finding |
|---|---|
| Redis configured in code path | **yes** (django-redis when URL present) |
| Multi-worker | Dockerfile / entrypoint default **`GUNICORN_WORKERS=3`** (same container, same env → same Redis URL) |
| Multi-replica | **Unknown** without Railway dashboard/CLI |
| Worker A vs Worker B different Redis | **No evidence** in code; all workers share process env. Cannot prove live env identity without Railway access |
| Local Redis vs Railway Redis | Local HIT probe against configured Redis returned **MISS** while Railway serves stable TFGZ1 sizes → local client is **not** a reliable window into Railway’s warm keys (different DB/instance/prefix visibility or Railway-only population) |

---

## 4–5. Phase 3 / Phase 5 on `origin/main`

| Feature | Present on `da582cb`? |
|---|---|
| `nhit/caching/traffic_filter_cache.py` | yes |
| Magic `TFGZ1\n` + gzip(JSON) | yes |
| Scope `gzip_json_v1` | yes |
| Data key `traffic_filter:data:gzip_json_v1:{digest}` | yes |
| Entry `{kind: tfgz1_ns_v1, ns_v, blob}` | yes |
| `safe_cache_get_many([version_key, data_key])` | yes |
| `response_path=direct_gzip_bytes` | yes |
| Views wired to helper + `Accept-Encoding` | yes |

**Phase 3 deployed = YES (behavioral + git)**  
**Phase 5 deployed = YES (behavioral + git)**  
Exact Railway SHA still unverified via platform API.

---

## 6. Live request series (gzip stability = HIT proxy)

Payload (primary):

```json
{
  "chainage_start": 0,
  "chainage_end": 1381,
  "date": "2026-05-20",
  "direction": ["All"],
  "project_name": ["Abu Road to Swaroopganj"]
}
```

`Accept-Encoding: gzip`

### Series A — 5 identical requests

| # | status | total_ms | Content-Encoding | Content-Length / wire |
|---|---:|---:|---|---:|
| 1 | 200 | 3095 | gzip | **7158** |
| 2 | 200 | 4051 | gzip | **7158** |
| 3 | 200 | 2317 | gzip | **7158** |
| 4 | 200 | 2928 | gzip | **7158** |
| 5 | 200 | 2386 | gzip | **7158** |

- **unique Content-Lengths: {7158} — identical_all = true**
- median ≈ **2928 ms**

### Series B — 8 warm session requests (later)

- unique CL: **{7158}**
- median total ≈ **1833 ms** (min ≈ 1791, max ≈ 2383)
- transfer ≪ TTFB

### Large payload (Chittorgarh) — 5 requests

- unique CL: **{138452}** — identical
- median total ≈ **2229 ms**
- uncompressed expectation ~7.5 MB matches prior phases

### Contrast with Phase 6 (pre-`da582cb`)

- Phase 6 primary wire samples varied ~**7160–7258** across identical requests
- Phase 6 warm TTFB ~**5.3–5.8 s**
- That pattern matches **pickle HIT / MISS + re-gzip**, not TFGZ1 direct bytes

**Conclusion:** Railway is now serving a **stable cached gzip blob** → consistent with **`direct_gzip_bytes` TFGZ1 HIT**.

---

## 7. HIT / MISS status

| Signal | Result |
|---|---|
| gzip Content-Length stable across repeats | **HIT-like (confirmed)** |
| Size matches prior TFGZ1 artifacts (7158 / 138452) | **yes** |
| `TRAFFIC_FILTER_PERF` log `cache=HIT` | **Not captured** (no Railway log access) |
| `response_path=direct_gzip_bytes` from logs | **Not captured** |
| `redis_round_trips=1` from logs | **Not captured** (code on `da582cb` implements it) |
| DB queries = 0 on HIT | **Not captured from logs**; expected by code on HIT |

**Inferred status: HIT (TFGZ1)** with high confidence from wire-byte identity.  
**Log-proven status: unavailable** until Railway auth + temporary `TRAFFIC_FILTER_PERF_DEBUG=true`.

---

## 8. Cache key consistency (deterministic)

For the primary payload (validated serializer data), local code computes:

| Field | Safe value |
|---|---|
| namespace | `traffic_filter` |
| scope | `gzip_json_v1` |
| digest suffix | `…09f0d361c344` |
| data key suffix | `…3e13947f980df16c846af6a5ad5ac8d775ae29dd379a09f0d361c344` |
| magic | `TFGZ1\n` |

Keys are deterministic from payload+scope. Full values / Redis contents not logged.

---

## 9. Namespace version / invalidation

### Code callers of `invalidate_traffic_filter_cache()` / `invalidate_all_filter_caches()`

Production/runtime call sites on `origin/main` (excluding tests/tools):

- **Definitions only** in `nhit/caching/invalidation.py`
- **No model signals**, admin hooks, or request middleware found that continuously bump `traffic_filter`

So: **no evidence of continuous automatic invalidation** from application code paths reviewed.

### Live namespace_version stability

- Cannot read `ns_v` from Railway logs without debug.
- **Proxy:** stable gzip bytes across many minutes of repeats ⇒ entry remains valid for current namespace version (version not bouncing every request).

If `ns_v` mismatched every time, each request would rebuild and gzip sizes would likely vary again (as in Phase 6).

---

## 10. `TRAFFIC_FILTER_PERF_DEBUG` (Steps 3–4, 11–12)

| Action | Result |
|---|---|
| Enable on Railway | **Blocked** — Railway CLI unauthorized; browserless login started (`user_code` shown) but not completed in this session |
| Capture `TRAFFIC_FILTER_PERF` line | **Not done** |
| Compare `server_total_ms` vs external TTFB | **Not done** (no server timer) |
| Disable after test | **N/A** — flag was never successfully set on Railway |
| Left permanently true? | **No** |

### External timings available now (HIT-like)

| Metric | Primary warm (session median) | Large warm (median) |
|---|---:|---:|
| external total | ~1833 ms | ~2229 ms |
| external TTFB | ≈ total (transfer tiny for gzip) | ≈ total − tens–hundreds ms |
| gzip wire | 7158 | 138452 |

Without `server_total_ms`, we **cannot** split “app timer ~300 ms vs edge adds 1.5 s” vs “app timer itself ~2 s”.

---

## 11. Success criteria checklist

| # | Question | Answer |
|---|---|---|
| 1 | Which commit is deployed? | Expected **`da582cb`**; Railway SHA **unverified via platform** |
| 2 | Phase 3 deployed? | **Yes** (git + stable TFGZ1 sizes) |
| 3 | Phase 5 deployed? | **Yes** (git + stable sizes; `get_many` in source) |
| 4 | Which Redis backend? | **django-redis** when URL set; internal-vs-public **unknown on Railway** |
| 5 | Hitting Redis? | **Yes** (stable cached blob across requests) |
| 6 | TFGZ1 HIT or MISS? | **HIT (behavioral)** |
| 7 | `get_many()` used? | **In deployed source yes**; runtime log not captured |
| 8 | `redis_round_trips == 1`? | **In source on HIT yes**; log not captured |
| 9 | DB queries 0 on HIT? | **Expected by code**; log not captured |
| 10 | `response_path=direct_gzip_bytes`? | **Expected by code**; inferred from stable gzip wire |
| 11 | Repeated gzip bytes identical? | **Yes** (7158 / 138452) |
| 12 | Namespace version stable? | **Yes (inferred)** — no per-request rebuild signature |
| 13 | Repeated invalidation? | **No production callers found**; no live bounce signature |
| 14 | `server_total_ms` ≈ external TTFB? | **Unknown** — needs Railway perf log |

---

## 12. Root cause

### Phase 6 mystery (solved)

**PRIMARY:** Production was still on the **pre-Phase-3 pickle `cached_filter_response` path** until `da582cb` deployed.  
That explains:

- ~5.3–5.8 s TTFB reproduced by all clients  
- **varying** gzip `Content-Length` for identical JSON  
- gap vs local in-process Phase 4A/5 HIT (~300 ms)

### Phase 7 residual (open — do not optimize TFGZ1 yet)

After deploy, HIT is live but external warm TTFB remains **~1.8–3 s**, still above in-process ~300–700 ms.

Possible Phase 8 foci (measurement only until proven):

1. Enable `TRAFFIC_FILTER_PERF_DEBUG` briefly with Railway auth → read `server_total_ms`, `cache_get_ms` / lookup, DB count  
2. Confirm Railway uses **`REDIS_INTERNAL_URL`** (not public proxy) — safe yes/no only  
3. If `server_total_ms` ≈ external TTFB ≈ 2 s → Redis/network inside app path  
4. If `server_total_ms` ≈ 300 ms but external TTFB ≈ 2 s → Railway edge / platform / HTTP path outside the Django timer  

**Do not redesign TFGZ1 / keys / JSON / gzip / API contract** based on this residual alone.

---

## 13. Is another optimization required?

| Layer | Required now? |
|---|---|
| TFGZ1 / Phase 5 code redesign | **No** |
| Deploy Phase 3/5 | **Done** (`da582cb`) |
| Confirm HIT | **Done (behavioral)** |
| Railway perf log + Redis internal check | **Yes — next measurement** |
| Client/Postman-only theory | **Rejected** (Python/curl match) |

---

## 14. Multi-worker note

- Default **3 gunicorn workers** in one container share one env → one Redis configuration.  
- This does **not** by itself cause MISS loops.  
- Cross-replica mismatch remains untested without Railway topology info.

---

## 15. What was not changed

- No TFGZ1 / Redis key / JSON / gzip / DB / API contract changes in Phase 7  
- No Railway configuration changes (could not authenticate)  
- `TRAFFIC_FILTER_PERF_DEBUG` not left true  

---

## 16. Follow-up (blocked on auth)

When Railway CLI login is available:

```text
1. railway link / select nhitramsbackend-production
2. Confirm deploy SHA == da582cb…
3. Set TRAFFIC_FILTER_PERF_DEBUG=true (temporary)
4. One primary gzip request → capture TRAFFIC_FILTER_PERF line
5. Four more identical requests → prove cache=HIT, response_path=direct_gzip_bytes,
   redis_round_trips=1, db_query_count=0, identical gzip_bytes
6. Compare server_total_ms vs external TTFB
7. Set TRAFFIC_FILTER_PERF_DEBUG=false
```

Until then, Phase 7’s actionable conclusion stands: **Phase 3/5 are live and HITting; Phase 6 was pre-deploy; remaining multi-second TTFB needs server-side timing, not another cache format change.**
