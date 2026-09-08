# Phase 4 — Inventory Filter Neon ↔ App Transfer Investigation

**Status:** Investigation only. No production code, Neon, Railway, DB, or API changes.

**Measurement caveat:** Railway CLI was **not authenticated** (`railway login` required), so in-container Railway→Neon timings could **not** be collected. All DB timing samples below are **local Windows (India) → Neon `ap-southeast-1`**. Railway region was inferred from public HTTP headers only.

Artifacts:

- `tools/investigate_inventory_filter_phase4_transfer.py`
- `tools/investigate_inventory_filter_phase4_results.json`
- `tools/investigate_inventory_filter_phase4_cursor_split.json`
- `tools/investigate_inventory_filter_phase4_followup.json`

---

## A. Environment

| Item | Value |
|------|-------|
| Railway region | **`sin1` (Singapore)** — inferred from production `GET /health/` headers `x-railway-edge: sin1` / `x-hikari-trace: sin1.*`. `RAILWAY_REGION` env could not be read (CLI unauthorized). |
| Neon region | **`ap-southeast-1`** (Singapore) — from DB host suffix / Neon pageserver metadata |
| DB driver (runtime) | **psycopg3 `3.3.3`** (`psycopg.Connection`). `psycopg2-binary` is also installed (`2.9.12`); Django 6 used psycopg3 at runtime. |
| Django version | **6.0** |
| Connection endpoint | **pooled** (`pooler` token in hostname) |
| Pooler | **Neon pooler (PgBouncer-style)** — pooled endpoint; client TLS to pooler |
| CONN_MAX_AGE | **60** (settings default; `DB_CONN_MAX_AGE` unset) |
| SSL | Client `sslmode=require`. Server `SHOW ssl=off` (TLS terminated at pooler). |
| App-side pool | None beyond Django `CONN_MAX_AGE` |
| Selected width | `pg_column_size` avg **~148–159 B/row** (Phase 3 narrow `.values()` + `quantity_value`) |

**Geography note:** Railway edge `sin1` and Neon `ap-southeast-1` appear **already colocated in Singapore**. Local bench machine is **not**.

---

## B. Network latency

Measured from **local Windows → Neon** (existing connection unless noted).

| Test | Min | Median | P95 | Max |
|------|----:|-------:|----:|----:|
| SELECT 1 | 94.8 | **117.9** | 407.8 | 509.0 |
| connection establishment | 848.3 | **1160.4** | 3227.7 | 3754.3 |
| SELECT 1 after connect | 111.0 | 158.8 | 199.5 | 199.6 |
| SELECT 1 same connection | 100.1 | 160.0 | 212.9 | 225.4 |
| Neon TCP connect | 78.7 | **92.7** | 123.6 | 123.9 |
| Railway HTTPS `/health/` (local→sin1 edge) | 1049 | **1138** | 1562 | 1803 |

Connection reuse with `CONN_MAX_AGE=60`: **confirmed** (same underlying connection object across 3 ensure_connection cycles).

**Interpretation:** ~100 ms RTT local→Neon. Connection setup (~1 s) matters for cold connects, **not** for multi-second warm fetches.

Railway in-container `SELECT 1` RTT: **not measured** (CLI unauthorized).

---

## C. Raw database fetch

### Critical measurement note

With the **default (unnamed) cursor**, psycopg receives the **full result during `execute()`**. `fetchall()` is mostly local unpack (~ms). Transfer time therefore appears in **`execute_ms`**, not `fetch_ms`.

Server-side (named) cursors move transfer into `fetch*` (requires an open transaction; worked against current Neon pooled endpoint in this bench).

### Unnamed cursor (Django-equivalent path) — median of 5

| page_size | PG EXPLAIN | Execute (≈transfer) | Fetch (local) | Total | ≈MB/s (pg bytes/execute) | Rows/s | Notes |
|----------:|-----------:|--------------------:|--------------:|------:|-------------------------:|-------:|-------|
| 5k | 3.0 | **607** | 3.0 | **610** | ~1.17 | ~8.2k | payload ~0.71 MiB |
| 15k | 9.5 | **3239** | 9.7 | **3248** | ~0.67 | ~4.6k | payload ~2.18 MiB |
| 25k | 18.5 | **5500** | 18.1 | **5517** | ~0.69 | ~4.5k | payload ~3.80 MiB |
| 50k | 46.2 | **2603** | 71.9 | **2665** | ~2.84 | ~18.8k | high run-to-run variance |

### Named cursor (transfer isolated in fetch) — median of 5

| page_size | Named execute | Named fetch (transfer) | Total | ≈MB/s on fetch |
|----------:|--------------:|-----------------------:|------:|---------------:|
| 5k | 286 | **523** | 809 | ~1.35 |
| 15k | 373 | **3044** | 3321 | ~0.72 |
| 25k | 268 | **4443** | 4711 | ~0.86 |
| 50k | 279 | **1928** | 2207 | ~3.83 |

RSS stayed modest (~127 MB class in earlier sweep); 50k was monitored and completed without an unbounded memory spike beyond normal materialization.

---

## D. Raw vs Django values()

Follow-up (4 samples each; medians):

| page_size | Raw total med | Django values() med | Difference |
|----------:|--------------:|--------------------:|-----------:|
| 5k | 269 | 388 | **+119** |
| 15k | 5331 | 6138 | **+807** |
| 25k | 15974 | 13153 | **-2821** (noise / WAN variance) |
| 50k | 6032 | 10053 | **+4021** |

**Finding:** After Phase 3, `.values()` is **not** the dominant multi-second cost. Most wall time is already paid getting bytes from Neon. Django dict conversion adds on the order of **~0.1–1+ s** depending on page size/variance — secondary to transfer from this client.

---

## E. Fetch-size experiment (named cursor, page_size=15k)

| Fetch size | Time med (total) | Throughput (MB/s on fetch) | rows/s (fetch) |
|-----------:|-----------------:|---------------------------:|---------------:|
| 100 | **15753** | 0.14 | 968 |
| 500 | 3499 | 0.68 | 4671 |
| 1,000 | 2074 | 1.21 | 8333 |
| 5,000 | **1009** | 3.00 | 20684 |

**Finding:** Over ~100 ms RTT, small `fetchmany` sizes multiply round-trips (15k/100 ≈ 150 RTTs ≈ 15 s) and **materially hurt**. Larger batches help **on this WAN path**. Production Django uses unnamed cursors (single buffered transfer), so this is diagnostic of latency sensitivity, not a production code change.

---

## F. Cold vs warm

- Warm `SELECT 1` ≈ 100–160 ms; cold **connection establishment** ≈ **1.2 s median**.
- Large-page **fetch totals remain multi-second on warm connections**.
- Cold vs warm for large pages is dominated by **transfer variance**, not connect setup.

⇒ Multi-second latency is **transfer-bound / RTT-sensitive**, not connection-establishment-bound, once a connection exists.

---

## G. Network vs application (15k, evidence-based)

Using medians from cursor-split + follow-up + Phase 4 serialize timings:

| Component | Approx ms | Share of ~6.3 s page path |
|-----------|----------:|--------------------------:|
| PostgreSQL execution (EXPLAIN) | ~10 | **~0.2%** |
| transfer/fetch (unnamed execute / named fetch) | ~3200–5300 | **~80–85%** |
| Python processing (values overhead + serialize/group) | ~800–950 | **~13–15%** |
| response rendering (JSON + gzip) | ~60 | **~1%** |

Rounded estimate:

```
PostgreSQL execution: ~0%
transfer/fetch:       ~84%
Python processing:    ~14%
response rendering:   ~2%
```

---

## Size split (DB vs HTTP)

| page_size | PG `pg_column_size` sum | Python repr approx | JSON | gzip |
|----------:|------------------------:|-------------------:|-----:|-----:|
| 5k | 0.71 MiB (742,080 B) | (see JSON artifact) | ~MB-class | ~0.1× JSON |
| 15k | **2.18 MiB** | larger than PG bytes | larger | ≪ JSON |
| 25k | 3.80 MiB | | | |
| 50k | 7.39 MiB | | | |

Only required Phase 3 columns + annotated `quantity_value` are selected (core fields, not full 30 quantity columns).

---

## H. Root cause

```
PRIMARY BOTTLENECK:
low Neon→client row-transfer throughput amplified by high client↔Neon RTT
(~100 ms from local India to Neon ap-southeast-1); PG execution is milliseconds.
psycopg unnamed-cursor buffering attributes that transfer time to execute(), not fetchall().
```

**Not** primary:

- PostgreSQL CPU / missing indexes (EXPLAIN 3–46 ms)
- Django model materialization (already removed)
- CONN_MAX_AGE reuse failure (reuse works)
- SSL setup on warm fetches
- Railway↔Neon **region mismatch** (both appear Singapore)

---

## I. Recommended next action

### P0 — Measure the same suite **inside Railway**
- **Expected benefit:** Determines whether production still sees multi-second transfer (unlikely if truly colocated) or only local WAN does.
- **Risk:** None if read-only script via `railway run` / one-off.
- **Complexity:** Low (requires `railway login`).
- **Evidence:** CLI unauthorized blocked in-container RTT/throughput; headers only prove edge `sin1`.

### P1 — Keep UX page sizes ≤15k (already recommended)
- **Expected benefit:** Caps transfer volume (~2 MiB DB payload vs ~7 MiB at 50k).
- **Risk:** None (API already allows up to 50k).
- **Complexity:** Frontend only / guidance.
- **Evidence:** Transfer time scales with bytes over high-RTT paths; PG still ms.

### P2 — Only if Railway in-container still shows multi-second transfer: protocol/fetch experiments
- **Expected benefit:** Possible improvement via binary/COPY, compression, or driver tuning — **unproven on Railway**.
- **Risk:** Medium (behavior/compatibility with Neon pooler).
- **Complexity:** Medium.
- **Evidence:** Named `fetchmany` size strongly RTT-sensitive locally; unnamed path already batches as one transfer.

**Do not** move Railway or Neon regions based on local-India timings alone — regions already look aligned.

---

## J. Final decision

```
NEXT STEP: NO INFRASTRUCTURE CHANGE JUSTIFIED
```

**Reason:** Measured multi-second gap is **WAN transfer from a distant client to Neon Singapore**, while Railway (`sin1`) and Neon (`ap-southeast-1`) appear **already colocated**. Moving infra would not address the measured local bottleneck and is not justified until Railway in-container timings prove a remaining production transfer problem.

---

## Method notes

1. No production settings, endpoints, pooler mode, indexes, or API contracts were changed.
2. Secrets/credentials/full hostnames were redacted; Neon internal IDs exist in raw JSON diagnostics — do not publish that file externally.
3. High variance on 25k/50k is expected on contended WAN; medians/P95 used rather than single runs.
