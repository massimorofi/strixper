# Halogen Strix Halo Operations Dashboard — Technical Requirements & Architectural Specification

This document is the **complete, current** specification for the Halogen Strix Halo
Operations Dashboard: a web dashboard that monitors a running **Halogen LLM
Server** alongside host/hardware telemetry on an **AMD Strix Halo (Ryzen AI Max+
395 / Radeon 8060S)** machine, and runs an on-demand fine-tuning compliance audit.

It is written so the application can be **rebuilt from scratch** and match the
shipped behavior exactly. Where the original draft and the shipped app diverged,
this document reflects the **shipped app**.

---

## 1. System Overview & Architecture

### 1.1 Architecture Blueprint

```
+-----------------------------------------------------------------------------------+
|                                 BROWSER FRONTEND                                   |
|   +------------------------------------+ +------------------------------------+    |
|   |    Tab 1: Live Server Metrics      | |   Tab 2: Strix Halo Fine-Tuning     |    |
|   |  - 4 KPI cards (each w/ info btn)  | |  - System summary banner            |    |
|   |  - Session statistics (min/avg/max)| |  - 8-check compliance audit table   |    |
|   |  - 4 time-series charts (info btn) | |    (PASS / WARN / FAIL)             |    |
|   +------------------------------------+ +------------------------------------+    |
+------------------------------------------^----------------------------------------+
| REST API (JSON over HTTP)
+------------------------------------------v----------------------------------------+
|                                  PYTHON BACKEND                                    |
|                          (FastAPI / Uvicorn + asyncio)                             |
|                                                                                    |
|  HalogenClient   -> httpx  -> Halogen /health /metrics /v1/models                   |
|  SystemMonitor   -> sysfs / procfs (GTT, VRAM, RAM, CPU)                            |
|  RocmMonitor     -> `rocm-smi` subprocess (GPU util %, VRAM)                        |
|  TuningChecker   -> shell commands (uname, tuned-adm, lspci, ls, ...)               |
|  StatsTracker    -> running min/avg/max accumulators (session-wide)                 |
+------------------------------------------^----------------------------------------+
|
+-----------------------+-----------------------+
| HTTP (Port 8731)                              | Local Shell / Sysfs / procfs
v                                               v
+---------------------------+                   +---------------------------+
|   Halogen Engine Server   |                   |    Linux Host / Kernel    |
+---------------------------+                   +---------------------------+
```

### 1.2 Core Capabilities

1. **Live Metrics Polling** — an async backend poller reads Halogen's `/health`,
   `/metrics`, `/v1/models` at a configurable interval (default **5 s**) and
   merges them with local hardware telemetry into one snapshot.
2. **Local Hardware Telemetry** — GTT/VRAM from amdgpu `sysfs`, RAM/CPU from
   `procfs`.
3. **GPU Monitoring via `rocm-smi`** — a short-lived `rocm-smi` subprocess per
   poll supplies **GPU compute utilization** and **VRAM usage**, which raw sysfs
   does not expose (and reports higher than sysfs because it counts
   driver-reserved allocations).
4. **Session Statistics** — running **min / avg / max** for the key series,
   accumulated **since server startup**, with a **Reset** button to zero the
   counters and restart the clock.
5. **Automated Fine-Tuning Compliance Check** — an on-demand audit (8 checks) of
   kernel, IOMMU, boot params, GTT size, rocm-smi, firmware, TuneD profile and
   udev rules, each reported PASS / WARN / FAIL with its measured impact.
6. **In-context documentation** — every chart and every top KPI card carries an
   **info button** that opens a modal explaining the measures shown there.

---

## 2. Technology Stack

* **Backend:** Python 3.11+, **FastAPI**, **Uvicorn** (async, non-blocking).
* **HTTP client:** `httpx` (async) for Halogen endpoints.
* **Config:** `python-dotenv` (`.env` file).
* **Frontend:** React 18 + **Vite**.
* **Styling:** **Tailwind CSS** (utility classes, CSS custom-property theming).
* **Data fetching:** **TanStack Query** (`@tanstack/react-query`) with `refetchInterval`.
* **Charts:** **Recharts** (line / area / step charts).
* **Icons:** **lucide-react**.

---

## 3. Backend API Specifications

### 3.1 Environment Configuration

Read from environment variables / a `.env` file (`backend/app/config.py`):

| Variable | Default | Meaning |
| --- | --- | --- |
| `HALOGEN_HOST` | `http://127.0.0.1:8731` | Halogen server base address |
| `POLL_INTERVAL_SECONDS` | `5` | Background poll cadence (seconds) |
| `BIND_HOST` | `0.0.0.0` | Backend bind host |
| `BIND_PORT` | `8000` | Backend bind port |

Numeric values are validated: a non-positive or unparsable `POLL_INTERVAL_SECONDS`
falls back to `5`.

### 3.2 Endpoints Summary

All endpoints are mounted under the `/api/v1` prefix.

| Endpoint | Method | Trigger | Description |
| --- | --- | --- | --- |
| `/api/v1/live-status` | `GET` | Every poll (default 5 s) | Aggregated Halogen + hardware + session-stats snapshot |
| `/api/v1/tuning-check` | `GET` | On-demand (button) | Runs the 8-check compliance audit |
| `/api/v1/system-info` | `GET` | On-demand | Hardware baseline for the Tab 2 banner |
| `/api/v1/config` | `GET` | On-demand | Read current runtime settings |
| `/api/v1/config` | `POST` | On-demand | Update runtime settings (poll interval) |
| `/api/v1/stats/reset` | `POST` | On-demand (button) | Zero the session statistics and restart the clock |
| `/api/v1/healthz` | `GET` | On-demand | Backend's own liveness (independent of Halogen) |

Interactive API docs are served at `http://<host>:8000/docs`.

### 3.3 Detailed API Contracts

#### `GET /api/v1/live-status`

Returns the most recent snapshot produced by the background poller (computed on
first request if not yet available).

**JSON Response Schema:**

```json
{
  "timestamp": "2026-10-03T22:18:46Z",
  "connected": true,
  "halogen": {
    "status": "ok",
    "model": "halogen-qwen3.8-flash-next",
    "busy": false,
    "slots": 4,
    "in_flight": 2,
    "queued": 0,
    "context_size": 262144,
    "kv_pool_positions": 524288.0,
    "kv_cache_usage_ratio": 0.849609,
    "prompt_tokens_per_sec": 611.13,
    "predicted_tokens_per_sec": 48.15,
    "prompt_tokens_total": 1362385.0,
    "tokens_predicted_total": 247778.0,
    "prompt_tokens_cached_total": 27223757.0,
    "draft_acceptance_rate": 0.7261
  },
  "hardware": {
    "gpu": {
      "gtt_total_gb": 96.0,
      "gtt_used_gb": 28.15,
      "gtt_usage_pct": 29.32,
      "vram_total_mb": 512.0,
      "vram_used_mb": 377.7
    },
    "memory": {
      "ram_total_gb": 122.69,
      "ram_used_gb": 38.21,
      "ram_usage_pct": 31.15
    },
    "cpu": {
      "usage_pct": 8.84,
      "active_threads": 32
    },
    "rocm": {
      "available": true,
      "gpu_util_pct": 96.0,
      "vram_used_gb": 0.37,
      "vram_total_gb": 0.5,
      "vram_usage_pct": 73.76
    }
  },
  "stats": {
    "prompt_tps": { "min": 245.97, "max": 1377.06, "avg": 800.13, "count": 19 },
    "decode_tps": { "min": 30.15, "max": 75.84, "avg": 56.38, "count": 20 },
    "gtt_used_gb": { "min": 28.15, "max": 28.15, "avg": 28.15, "count": 21 },
    "gtt_pct": { "min": 29.32, "max": 29.32, "avg": 29.32, "count": 21 },
    "vram_pct": { "min": 73.79, "max": 73.79, "avg": 73.79, "count": 21 },
    "gpu_util_pct": { "min": 13.0, "max": 99.0, "avg": 61.2, "count": 18 },
    "cpu_pct": { "min": 1.07, "max": 12.4, "avg": 4.1, "count": 20 },
    "draft_acceptance": { "min": 72.41, "max": 72.42, "avg": 72.41, "count": 21 },
    "started_at": "2026-10-03T22:18:46Z",
    "samples": 21
  }
}
```

Notes:
* `connected` is `false` when Halogen's `/health` is unreachable; in that case
  `halogen` is `{"status": "unreachable", "error": "..."}`.
* `hardware.rocm` is `{"available": false, "error": "..."}` when `rocm-smi` is
  missing, times out, or returns unparsable output.
* `stats` is the session-wide accumulator (see §4.5). Each series has
  `min / max / avg / count`.

#### `GET /api/v1/tuning-check`

Runs all 8 compliance checks concurrently and returns them in a fixed order.

**JSON Response Schema:**

```json
{
  "evaluated_at": "2026-10-03T22:18:46Z",
  "checks": [
    {
      "id": "kernel_version",
      "name": "Kernel Version Verification",
      "command": "uname -r",
      "expected": ">= 6.16.9 (Mainline 6.18.x recommended)",
      "actual": "6.18.7-generic",
      "status": "PASS",
      "impact": "Required for >64GB unified memory support. Kernel 6.17 broke the ROCm KFD ABI -- use 6.18.x mainline."
    }
    /* ... 7 more checks, see §4.4 ... */
  ]
}
```

Each check object always has the seven fields: `id`, `name`, `command`,
`expected`, `actual`, `status` (`PASS` | `WARN` | `FAIL`), `impact`.

#### `GET /api/v1/system-info`

Hardware baseline for the Tab 2 summary banner.

```json
{
  "cpu": "AMD Ryzen AI MAX+ 395 w/ Radeon 8060S (16 Cores / 32 Threads)",
  "gpu": "Advanced Micro Devices, Inc. [AMD/ATI] Device 1586",
  "bandwidth": "~208 GB/s sustained (256-bit LPDDR5x-8000)"
}
```

CPU model and thread count are read from `/proc/cpuinfo`; GPU name from
`lspci -d 1002:` (first AMD device).

#### `GET /api/v1/config` / `POST /api/v1/config`

`GET` returns `{ "poll_interval_seconds": <float>, "halogen_host": "<url>" }`.

`POST` accepts a JSON body:

```json
{ "poll_interval_seconds": 10 }
```

`poll_interval_seconds` is validated to `0.5 … 300`. On a successful update the
poller is woken immediately (`state.wake.set()`) so the new cadence takes effect
without waiting out the old interval.

#### `POST /api/v1/stats/reset`

Zeros the running min/avg/max accumulators and restarts the session clock.
Returns the fresh (empty) stats snapshot.

#### `GET /api/v1/healthz`

Returns `{ "status": "ok" }`. Independent of Halogen reachability.

---

## 4. Backend Services

### 4.1 `HalogenClient` (`services/halogen_client.py`)

Async `httpx` client for the three Halogen endpoints.

* `get_health()` → `GET /health` (required; raises on failure).
* `get_metrics()` → `GET /metrics`, parsed by `parse_prometheus()` into
  `{metric_name: float}` (comment lines and unparsable lines skipped).
* `get_models()` → `GET /v1/models`.
* `snapshot()` fetches all three concurrently (`asyncio.gather`); `/metrics`
  and `/v1/models` are best-effort (a failure yields `{}`), `/health` is
  required.

`build_halogen_state(health, metrics, models)` maps the raw Prometheus metrics to
the `halogen` section:

| Output field | Source |
| --- | --- |
| `status`, `model`, `busy`, `slots`, `in_flight`, `queued` | `/health` |
| `context_size` | `/health.context`, else `/v1/models[0].max_model_len` / `.context_length` |
| `kv_pool_positions` | `halogen:kv_pool_positions` (else `/health`) |
| `kv_cache_usage_ratio` | `llamacpp:kv_cache_usage_ratio` |
| `prompt_tokens_per_sec` | `llamacpp:prompt_tokens_seconds` |
| `predicted_tokens_per_sec` | `llamacpp:predicted_tokens_seconds` |
| `prompt_tokens_total` | `llamacpp:prompt_tokens_total` |
| `tokens_predicted_total` | `llamacpp:tokens_predicted_total` |
| `prompt_tokens_cached_total` | `halogen:prompt_tokens_cached_total` |
| `draft_acceptance_rate` | `halogen:draft_tokens_accepted_total / halogen:draft_tokens_total` (rounded 4dp) |

### 4.2 `SystemMonitor` (`services/system_monitor.py`)

Cheap filesystem reads, safe to call from the event loop.

* **GPU (amdgpu sysfs):** reads `mem_info_gtt_total`, `mem_info_gtt_used`,
  `mem_info_vram_total`, `mem_info_vram_used` from
  `/sys/class/drm/card*/device/` (first card with a value). Converts bytes →
  GB (`/ 1024³`), computes `gtt_usage_pct`.
* **RAM (procfs):** reads `/proc/meminfo`; `used = MemTotal − MemAvailable`
  (falls back to `MemFree`); reports total/used GB and `ram_usage_pct`.
* **CPU (procfs):** reads the aggregate `cpu ` line of `/proc/stat`; computes
  busy% as the delta between successive reads (`Δbusy / Δtotal`), so the first
  read returns `0.0`. Reports `active_threads = os.cpu_count()`.

### 4.3 `RocmMonitor` (`services/rocm_monitor.py`)

Runs `rocm-smi --showuse --showmeminfo vram --json` as a short-lived subprocess
(5 s timeout) on each poll. Parses the first `cardN` entry:

* `gpu_util_pct` ← `"GPU use (%)"`
* `vram_used_gb` ← `"VRAM Total Used Memory (B)" / 1024³`
* `vram_total_gb` ← `"VRAM Total Memory (B)" / 1024³`
* `vram_usage_pct` = used / total × 100

Any failure (binary missing → `FileNotFoundError`, timeout, non-zero exit,
unparsable JSON, no card) returns `{"available": false, "error": "..."}` so the
poll loop never breaks.

### 4.4 `TuningChecker` (`services/tuning_checker.py`)

Each check is an async function returning the standard 7-field result dict. All
shell commands run via `asyncio.create_subprocess_shell` with a timeout; the
audit never crashes (a crashing check is reported as `FAIL`).

**Checks, in the fixed display order:**

| # | id | Command | PASS condition |
| --- | --- | --- | --- |
| 1 | `kernel_version` | `uname -r` | kernel ≥ 6.16.9 |
| 2 | `iommu_status` | `cat /proc/cmdline` | contains `amd_iommu=off` |
| 3 | `boot_params` | `cat /proc/cmdline` | has both `amdgpu.gttsize=` and `ttm.pages_limit=` (one only → WARN) |
| 4 | `gtt_size` | `cat /sys/class/drm/card*/device/mem_info_gtt_total` | ≥ 120 GiB (≥ 64 GiB → WARN) |
| 5 | `rocm_smi` | `rocm-smi --showuse --json` | installed, exit 0, returns GPU data |
| 6 | `firmware_check` | `rpm -qa \| grep linux-firmware \|\| dpkg -l \| grep linux-firmware` | not `20251125` |
| 7 | `tuned_profile` | `tuned-adm active` | contains `accelerator-performance` |
| 8 | `udev_rules` | `ls -l /dev/kfd /dev/dri/renderD*` | render group has `rw` |

Key constants: `BROKEN_FIRMWARE = "20251125"`, `MIN_KERNEL = (6, 16, 9)`,
`MIN_GTT_BYTES = 120 GiB`, `EXPECTED_TTM_PAGES = 32505856`.

`run_all_checks()` runs all checks concurrently (`asyncio.gather`) and returns
`{ "evaluated_at": <iso>, "checks": [ ... ] }`.

`get_system_info()` returns the `{cpu, gpu, bandwidth}` banner.

### 4.5 `LiveService` (`services/live_service.py`)

Holds the shared runtime state and the background poller.

**`MetricStats`** — running min/max/mean for one series. Has an `ignore_zero`
flag: when set, a reading of `0` is skipped entirely (not counted, not summed,
not considered for min/max). Used for metrics where `0` means "idle / not
reporting yet", not a real zero.

**`StatsTracker`** — accumulates session-wide stats.

* Tracked keys: `prompt_tps`, `decode_tps`, `gtt_used_gb`, `gtt_pct`,
  `vram_pct`, `gpu_util_pct`, `cpu_pct`, `draft_acceptance`.
* **`IGNORE_ZERO_KEYS = ("prompt_tps", "decode_tps", "gpu_util_pct", "cpu_pct", "draft_acceptance")`**
  — these discard zero readings from **both the average and the minimum** (a 0
  means the engine/metric wasn't reporting activity). The memory series
  (`gtt_used_gb`, `gtt_pct`, `vram_pct`) keep their true minimum including any
  zeros.
* `draft_acceptance` is recorded as a **percentage** (the raw
  `draft_acceptance_rate` ratio × 100), so its min/avg/max are in %.
* `record(sample)` adds one sample; `snapshot()` returns
  `{key: {min, max, avg, count}, started_at, samples}`; `reset()` zeroes
  everything and restarts `started_at`.

**`RuntimeState`** — `poll_interval`, `latest`, `wake` (asyncio.Event),
`halogen` (HalogenClient), `monitor` (SystemMonitor), `rocm` (RocmMonitor),
`stats` (StatsTracker).

**`build_live_snapshot(state)`** — polls Halogen and rocm **concurrently**
(`asyncio.gather(..., return_exceptions=True)`), reads local hardware, attaches
`hardware.rocm`, records the sample into `stats`, and returns the combined
snapshot (§3.3).

**`poll_loop(state)`** — loops: build snapshot → wait on `wake` with a timeout of
`max(poll_interval, 0.5)` → clear `wake`. `managed_poller` is an async context
manager that starts the loop on startup and cancels it + closes the Halogen client
on shutdown.

---

## 5. Frontend Dashboard Specification

### 5.1 Top Bar

* App name: **Halogen Strix Halo Operations Dashboard**.
* Connection status indicator (green = connected, red = lost), driven by
  `data.connected`.
* Auto-refresh selector: `1s`, `5s` (default), `10s`, `30s`, `Off`. Changing it
  updates the local `refetchInterval` **and** POSTs the new
  `poll_interval_seconds` to `/api/v1/config`.
* Manual refresh button.
* Theme toggle (dark default; persisted to `localStorage` under
  `strixper-theme`).
* Tab switcher (Live / Tuning); `#tuning` in the URL opens Tab 2.

### 5.2 Tab 1: Live Server Metrics

Layout, top to bottom:

**A. Primary KPI cards (4-up grid).** Each card has an **info button** (top-right
`ⓘ`) that opens a modal explaining the measure.

1. **Model Loaded** — model name + context window (tokens).
2. **In-Flight & Slots** — `in_flight / slots busy`, with `queued` in the sub.
3. **KV Cache Pool** — `kv_cache_usage_ratio` as a % with a progress meter
   (turns critical > 90%), plus total positions.
4. **Throughput (session avg)** — **session-average** prefill (`stats.prompt_tps.avg`)
   and decode (`stats.decode_tps.avg`) t/s, plus draft acceptance %.
   *This deliberately shows the session average, not the instantaneous gauge,
   because Halogen's `prompt_tokens_seconds` gauge decays to 0 whenever the engine
   is idle (see §7).*

**B. Secondary counters row** — prompt tokens total, generated tokens total,
cached prompt tokens, CPU % + thread count.

**C. Session Statistics** (`StatsSummary`) — a min / avg / max table over the
seven tracked series, with a **"Reset statistics"** button (calls
`POST /api/v1/stats/reset`, then refetches). Shows "since {started_at} · N
samples".

**D. Dynamic charts (2-up grid).** Each chart card has an **info button**
(top-right `ⓘ`) opening a modal that explains the measures in that chart.

* **Chart A — Token Throughput** (line): Prompt t/s (blue) vs Decode t/s
  (orange). Single t/s axis.
* **Chart B — Host Memory & GPU Utilization** (line, 0–100% axis): GTT used
  (blue), RAM used (orange), VRAM used (aqua), GPU util (violet).
* **Chart C — KV Pool Utilization** (area, 0–100%): KV used %.
* **Chart D — Queue Depth** (step line): queued requests (integer).

The frontend keeps a rolling history buffer of the last **120 samples**
(`MAX_SAMPLES`) built from each `live-status` response.

### 5.3 Tab 2: Strix Halo Configuration & Fine-Tuning

* **System Summary Banner** — CPU, GPU, memory bandwidth (from
  `GET /api/v1/system-info`).
* **"Run System Diagnostics"** button — triggers `GET /api/v1/tuning-check`.
* **Audit Checks Table** — one row per check with a status badge
  (`PASS` green / `WARN` yellow / `FAIL` red), the command, current value,
  expected value, and impact. Status is always conveyed by **icon + label**,
  never color alone.

### 5.4 Theme & Color

Chart colors are hex (not CSS vars) because Recharts writes SVG presentation
attributes. Both modes were validated with the dataviz palette validator
(light surface `#fcfcfb`, dark surface `#1a1a19` — all checks pass).

| Role | Light | Dark |
| --- | --- | --- |
| blue (series 1) | `#2a78d6` | `#3987e5` |
| orange (series 2) | `#eb6834` | `#d95926` |
| aqua (series 3) | `#1baf7a` | `#199e70` |
| violet (series 4) | `#4a3aa7` | `#9085e9` |

Status colors: `PASS #0ca30c`, `WARN #fab219`, `FAIL #d03b3b`.

### 5.5 Formatting helpers (`format.js`)

`fmtInt` (thousands separators), `fmtNum(v, digits)`, `fmtPct(v, digits)`,
`fmtTime(iso)` (en-GB time). Null values render as `—`.

---

## 6. Project Layout

```
backend/
  app/
    main.py                 FastAPI app + lifespan + static serving
    config.py               env settings
    routers/
      live.py               GET /live-status
      tuning.py             GET /tuning-check, GET /system-info
      config_routes.py      GET/POST /config
      stats_routes.py       POST /stats/reset
    services/
      halogen_client.py     async Halogen HTTP client + Prometheus parser
      system_monitor.py     sysfs/procfs telemetry (GTT/VRAM/RAM/CPU)
      rocm_monitor.py       GPU util + VRAM via `rocm-smi`
      tuning_checker.py     8-check compliance audit + system info
      live_service.py       MetricStats / StatsTracker / RuntimeState / poll loop
frontend/
  src/
    App.jsx                 tabs, theme, polling, 120-sample history buffer
    api.js                  fetch helpers (fetchLiveStatus, postConfig, resetStats)
    theme.js                chart + status color tokens (light/dark)
    format.js               number/percent/time formatters
    components/
      TopBar.jsx            connection, refresh, theme, tabs
      StatTile.jsx          KPI card with info button
      Meter.jsx             progress bar
      StatusBadge.jsx       PASS/WARN/FAIL badge
      LiveTab.jsx           Tab 1 (KPI cards, stats, charts)
      StatsSummary.jsx      min/avg/max table + reset button
      TuningTab.jsx         Tab 2 (banner + audit table)
    charts/
      ChartCard.jsx         chart wrapper with info button
      InfoModal.jsx         portal modal (backdrop / X / Escape to close)
      ChartTooltip.jsx      shared tooltip
      ThroughputChart.jsx   Chart A
      MemoryChart.jsx       Chart B
      KvPoolChart.jsx       Chart C
      QueueChart.jsx        Chart D
Dockerfile
run.sh
stop.sh
```

---

## 7. Critical Metric Semantics (do not regress)

These were discovered empirically against the live Halogen engine and drive
several design decisions:

1. **`llamacpp:prompt_tokens_seconds` / `predicted_tokens_seconds` are
   "since last scrape" gauges.** They report a real rate only in the brief window
   right after a request completes, then **decay to 0** while the engine is idle.
   For a single user, most polls read 0. **Consequence:** the top Throughput card
   shows the **session average**, not this instantaneous gauge.
2. **`prompt_tokens_total` / `tokens_predicted_total` counters are frozen during
   a request** and only advance at request **completion**. Differencing them
   mid-request reads 0 — do not compute live throughput from them.
3. **GTT ≠ VRAM.** GTT (`mem_info_gtt`) is the large unified pool (~96 GB here,
   ~29% used as a static baseline). VRAM (rocm-smi) is a small dedicated
   carve-out (~512 MB, ~73% used). They are different memory pools; the
   discrepancy is expected.
4. **rocm-smi reads VRAM higher than raw sysfs** because it counts
   driver-reserved allocations. rocm-smi is the authoritative source for GPU
   compute utilization (sysfs has none).
5. **Zero readings mean "idle / not reporting", not a measured zero.** Hence
   throughput and utilization series exclude zeros from their average and minimum
   (§4.5).

---

## 8. Implementation Checklist (build order)

1. **Backend scaffold** — FastAPI app (`main.py`), `config.py`, routers under
   `routers/`, CORS `*`, static mount of `frontend/dist` when present.
2. **HalogenClient** — async httpx client + `parse_prometheus` +
   `build_halogen_state`.
3. **SystemMonitor** — sysfs GTT/VRAM, procfs RAM/CPU.
4. **RocmMonitor** — `rocm-smi` subprocess → GPU util + VRAM, graceful
   unavailable marker.
5. **LiveService** — `MetricStats` (with `ignore_zero`), `StatsTracker`
   (session min/avg/max + reset), `RuntimeState`, `build_live_snapshot`,
   `poll_loop`, `managed_poller`.
6. **TuningChecker** — the 8 checks + `run_all_checks` + `get_system_info`.
7. **Routers** — `live`, `tuning`, `config_routes`, `stats_routes`, `healthz`.
8. **Frontend scaffold** — Vite + React + Tailwind + TanStack Query + Recharts +
   lucide-react; theme tokens; formatters; `api.js`.
9. **Tab 1** — 4 KPI cards (each with info modal), secondary counters,
   `StatsSummary` with reset, 4 charts (each with info modal), 120-sample
   history buffer.
10. **Tab 2** — system banner + audit table with PASS/WARN/FAIL badges.
11. **Packaging** — `Dockerfile` (multi-stage: build frontend, serve from
    FastAPI), `run.sh` (dev: backend + Vite concurrently), `stop.sh`.
12. **Verify** — `cd frontend && npm run build`; restart backend; confirm
    live-status, stats reset, tuning checks, and all info modals render.
