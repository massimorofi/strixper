# Halogen Strix Halo Operations Dashboard — Technical Requirements & Architectural Specification

This document is the **complete, current** specification for the Halogen Strix Halo
Operations Dashboard: a web dashboard that monitors a running **Halogen LLM
Server** alongside host/hardware telemetry on an **AMD Strix Halo (Ryzen AI Max+
395 / Radeon 8060S)** machine, and runs an on-demand fine-tuning compliance audit.

It is written so the application can be **rebuilt from scratch** and match the
shipped behavior exactly. Where the original draft and the shipped app diverged,
this document reflects the **shipped app**.

> **On the word "Halogen".** The product is named after the engine it was built
> for, and the endpoint contracts below are Halogen's. In practice the dashboard
> is engine-agnostic: it monitors whatever HTTP server the engine target points
> at, and GUFO and stock `llama.cpp` `llama-server` both work. Where a feature
> genuinely depends on a Halogen-only endpoint (`/cache`, the `halogen:` metric
> keys, `/v1/messages/count_tokens`), that is called out at the point of use.
> Where the dashboard reads the `llamacpp:` namespace, it works on all three.
> See §4.1 for the namespace breakdown and §4.6 for engine configuration.

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
|   +------------------------------------+                                           |
|   |    Tab 3: AI-Chat                   |  +------------------------------------+   |
|   |  - Streaming chat (4 API styles)    |  |   Tab 4: LLM-Runner               |   |
|   |  - Collapsible chain-of-thought     |  |  - Create/edit docker run configs   |   |
|   |  - Per-turn token / speed stats     |  |  - Run container, live output       |   |
|   +------------------------------------+  |  - Store configs locally (JSON)     |   |
|                                           +------------------------------------+   |
+------------------------------------------^----------------------------------------+
| REST API (JSON over HTTP) + SSE (text/event-stream for chat)
+------------------------------------------v----------------------------------------+
|                                  PYTHON BACKEND                                    |
|                          (FastAPI / Uvicorn + asyncio)                             |
|                                                                                    |
|  HalogenClient   -> httpx  -> Halogen /health /metrics /v1/models /cache            |
|                 -> POST /v1/messages/count_tokens, /v1/chat/completions,            |
|                    /v1/messages, /v1/responses, /v1/completions                     |
|  SystemMonitor   -> sysfs / procfs (GTT, VRAM, RAM, CPU)                            |
|  RocmMonitor     -> `rocm-smi` subprocess (GPU util %, VRAM)                        |
|  TuningChecker   -> shell commands (uname, tuned-adm, lspci, ls, ...)               |
|  StatsTracker    -> running min/avg/max accumulators (session-wide)                 |
|  AgentOrchestrator (OpenAI Agents SDK) -> same Halogen endpoint, tool loop          |
|                    -> dashboard tools: live status, metrics, cache, tuning, tokens    |
|                    -> gated actions: start/stop engine (via docker socket)           |
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

1. **Live Metrics Polling** — each managed engine is polled at the UI cadence
   through its own `/health`, `/metrics`, `/v1/models` and `/cache` endpoints;
   each snapshot is merged with shared local hardware telemetry. The default
   engine target remains available when no managed run is selected.
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
7. **AI Chat** — a streaming chat tab that sends the conversation to Halogen and
   renders the reply token-by-token, with the model's chain-of-thought collapsed
   under each answer. The user can switch between Halogen's four inference API
   styles (OpenAI Chat Completions, Anthropic Messages, OpenAI Responses, and
   raw Text Completions) from the UI to compare them, toggle the model's
   thinking on/off, and cap the response length. See §3.3 (`POST /api/v1/chat`)
   and §5.4 (Tab 3).
8. **Agentic AI Chat** — the same chat tab, in **Agent** mode, runs a
   tool-using loop built on the OpenAI Agents SDK. The model inspects the
   machine through dashboard tools (live status, metrics, cache, tuning audit,
   token counting, runner configs) and iterates until it can answer. Capabilities
   come in three tiers, each ticked explicitly: read-only (always), **Allow
   actions** (start/stop an engine), and **Full access** (web fetch/search,
   shell, Python execution, file I/O). See §3.3
   (`POST /api/v1/agent/chat`), §4.11 (`agent_tools`) and §4.12
   (`agent_exec_tools`).
9. **Parallel LLM engines** — multiple distinct Runner configurations can run
   concurrently. Each run has its own run ID, console stream, metrics polling,
   and engine URL; stopping one does not stop the others. The stack stop script
   stops all registered runs before stopping Strixper.
10. **LLM-Runner** — a tab to create, run and locally store the docker command
    used to launch a containerized LLM inference server. Configurations are kept
    in a JSON file on disk, listed for selection, editable, and started with
    per-engine console tabs; each run can be stopped on demand. See §3.3
    (runner endpoints), §4.6 (`RunnerStore`) and §5.5 (Tab 4).
11. **Pluggable extensions** — agent capabilities are not hard-coded. Skills are
    drop-in folders (`SKILL.md` + resources) discovered from `SKILLS_DIR` with no
    rebuild, and MCP servers are registered, tested and enabled from the AI Chat
    **Extensions** panel, with their configuration persisted in the data volume.
    Both are granted one of the three access tiers and only become visible to the
    agent when the request satisfies that tier. See §3.3 (Agent extension APIs),
    §4.13 (`skill_loader`) and §4.14 (`mcp_manager`).

---

## 2. Technology Stack

* **Backend:** Python 3.11+ for local development; the Docker runtime is Python
  3.12, with **FastAPI** and **Uvicorn** (async, non-blocking).
* **HTTP client:** `httpx` (async) for Halogen endpoints.
* **Config:** `python-dotenv` (`.env` file).
* **Agentic chat:** `openai-agents` (OpenAI Agents SDK, Python), driven through
  Halogen's OpenAI-compatible endpoint — no OpenAI account required.
* **Agent extensions:** `PyYAML` for safe Agent Skill manifest parsing and the
  `mcp` Python SDK for MCP stdio clients.
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
| `HALOGEN_HOST` | `http://127.0.0.1:8731` | **Initial** engine address only — superseded at runtime by the engine target (see §4.9) |
| `POLL_INTERVAL_SECONDS` | `5` | Background poll cadence (seconds) |
| `BIND_HOST` | `0.0.0.0` | Backend bind host |
| `BIND_PORT` | `8000` | Backend bind port |
| `RUNNER_STORE_PATH` | `backend/data/llm_runner_configs.json` | LLM-Runner config store (see §4.6) |
| `RUNNER_STOP_TIMEOUT` | `5` | Seconds `docker stop` waits before force-killing (see §4.8) |
| `RUNNER_CONSOLE_BYTES` | `2097152` | Console history kept for late-joining viewers (see §4.10) |
| `RUNNER_SUBSCRIBER_QUEUE` | `2000` | Per-viewer output queue depth (see §4.10) |
| `RUNNER_ADOPT_TAIL` | `2000` | Lines of `docker logs` pulled when adopting a container |
| `RUNNER_RELEASE_WAIT` | `20` | Seconds to wait for a stopped container's port/GPU to free |
| `RUNNER_STOP_SETTLE` | `15` | Seconds a stop waits for the output pump to record the exit |
| `RUNNER_SSE_HEARTBEAT` | `15` | Seconds of silence before a console stream sends `ping` |
| `RUNNER_RECONCILE` | `20` | Seconds between scans for an untracked engine container |
| `ENGINE_TARGET_PATH` | `backend/data/engine_target.json` | Remembered engine target (see §4.9) |
| `DEFAULT_MODEL` | `halogen-qwen3.8-flash-next` | Model id assumed before any engine reports one |
| `AGENT_MAX_TURNS` | `25` | Max model↔tool round-trips per agentic request (see §3.3) |
| `AGENT_MAX_TOKENS` | `8192` | Per-model-call output cap for the agent loop (see §3.3) |
| `AGENT_INSTRUCTIONS` | built-in | Optional replacement for the agent system prompt (see §3.3) |
| `AGENT_WORKSPACE` | `backend/agent_workspace` | Full-access working directory (see §4.12) |
| `AGENT_TLS_VERIFY` | `1` | TLS verification for `fetch_url` / `search_web` (see §4.12) |
| `SKILLS_DIR` | `<repository>/skills` (Docker: `/app/skills`) | Read-only root containing drop-in Agent Skill folders |
| `STRIXPER_DATA_DIR` | `backend/data` | Persistent runtime data root; MCP config is stored here |
| `MCP_SERVERS_DIR` | `<repository>/mcp-servers` (Docker: `/mcp-servers`) | Per-server MCP package root; server-ID subdirectories and shared `bin/` are added to child process `PATH` |
| `MCP_ADMIN_TOKEN` | unset (management disabled) | Required high-entropy token for MCP management endpoints; supplied as `X-MCP-Admin-Token` |
| `MCP_CALL_TIMEOUT_SECONDS` | `30` | MCP tool-call timeout |

`HALOGEN_HOST` is easy to over-read: it is where the dashboard starts
looking, not where it keeps looking. As soon as an engine is started through
the LLM-Runner (or the target is set by hand), that becomes the address of
record and it is remembered across restarts. The `.env` value is reached
only on a fresh install with no saved target.

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
| `/api/v1/count-tokens` | `POST` | On-demand (widget) | Count the input tokens of a prompt (proxies Halogen) |
| `/api/v1/chat` | `POST` | On-demand (AI-Chat tab) | Streaming chat completion (proxies Halogen, 4 API styles) |
| `/api/v1/agent/chat` | `POST` | On-demand (AI-Chat tab, Agent mode) | Agentic tool-driven chat (SSE) |
| `/api/v1/agent/tools` | `GET` | On-demand | Tool inventory for the current permission set |
| `/api/v1/agent/skills` | `GET` | AI-Chat Extensions / agent request | Rescan installed skills and return valid skills plus manifest errors |
| `/api/v1/agent/mcp-servers` | `GET` | AI-Chat Extensions | Protected MCP config/runtime status (requires `X-MCP-Admin-Token`) |
| `/api/v1/agent/mcp-servers` | `POST` | AI-Chat Extensions | Protected create; enabling launches the configured stdio command |
| `/api/v1/agent/mcp-servers/{id}` | `PUT` / `DELETE` | AI-Chat Extensions | Protected update/delete; closes the previous process/session |
| `/api/v1/agent/mcp-servers/test` | `POST` | AI-Chat Extensions | Protected temporary connection/tool-list test; always closes the session |
| `/api/v1/runner/configs` | `GET` | On-demand (LLM-Runner tab) | List stored docker run configurations |
| `/api/v1/runner/configs/{id}` | `GET` | On-demand | Fetch one stored configuration |
| `/api/v1/runner/configs` | `POST` | On-demand (LLM-Runner tab) | Create a new run configuration |
| `/api/v1/runner/configs/{id}` | `PUT` | On-demand (LLM-Runner tab) | Edit an existing configuration |
| `/api/v1/runner/configs/{id}` | `DELETE` | On-demand (LLM-Runner tab) | Delete a configuration |
| `/api/v1/runner/configs/{id}/run` | `POST` | On-demand (LLM-Runner tab) | Start the rendered docker command (returns JSON) |
| `/api/v1/runner/active` | `GET` | Compatibility | Most recently started run, or `null` |
| `/api/v1/runner/runs` | `GET` | Polled ~3 s (Runner, Chat) | All currently running engine runs |
| `/api/v1/runner/runs/{run_id}/live-status` | `GET` | Polled (Live Metrics) | Per-engine metrics and shared host telemetry |
| `/api/v1/runner/preview` | `POST` | On-demand (LLM-Runner tab) | Render a command template against its parameters, without running |
| `/api/v1/runner/runs/{run_id}/stream` | `GET` | Per viewer (LLM-Runner tab) | Attach to the live console (SSE) |
| `/api/v1/runner/runs/{run_id}/stop` | `POST` | On-demand (LLM-Runner tab) | Stop a running docker command |
| `/api/v1/runner/stop-all` | `POST` | Stack shutdown | Stop every managed engine run |
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
    "draft_acceptance_rate": 0.7261,
    "cache": {
      "entries": 24,
      "bytes": 2793351648,
      "last_entry_bytes": 116389652,
      "max_entries": 24,
      "hits": 62,
      "misses": 9,
      "stores": 135,
      "evicted": 2,
      "prompt_tokens_saved": 4019426,
      "store_ms_total": 3666.5,
      "restore_ms_total": 344.9,
      "last_store_ms": 0.0,
      "last_restore_ms": 0.0,
      "refused": 0,
      "rows_copied": 2,
      "rows_copied_ms": 0.3,
      "superseded": 52,
      "composable_context": { "chunks": 0, "bytes": 0, "stored": 0, "composed": 0, "evicted": 0, "composed_tokens": 0 },
      "disk": { "on": false, "records": 0, "lineages": 0, "bytes": 0, "budget_bytes": 0, "hits": 0, "misses": 0, "persisted": 0, "branches": 0, "evicted": 0, "skipped": 0, "restore_ms_total": 0.0, "restored_bytes": 0 },
      "tapped": 29,
      "full_hits": 3,
      "pool": { "waiting_for_room": 0, "waiting_s": 0.0, "relocated": 0, "cold_resorts": 0, "positions": 524288, "used": 288512, "busy_regions": 0, "held_regions": 7, "room_clamped": 0, "moved": 2, "packed": 0, "taken_over": 0, "usage_ratio": 0.5503 },
      "dropped": 19,
      "hit_rate": 0.8784,
      "token_hit_rate": 0.9652,
      "snapshot_places": ["system_end", "last_user_start", "history_end"]
    }
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
    "cache_hit_rate": { "min": 87.7, "max": 88.6, "avg": 88.0, "count": 21 },
    "cache_token_hit_rate": { "min": 96.5, "max": 96.7, "avg": 96.5, "count": 21 },
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

`GET` returns
`{ "poll_interval_seconds": <float>, "halogen_host": "<url>", "engine_config_id": <string|null>, "engine_config_name": <string|null> }`.

`POST` accepts a JSON body in which either or both fields may appear:

```json
{ "poll_interval_seconds": 10, "halogen_host": "http://127.0.0.1:18080" }
```

`poll_interval_seconds` is validated to `0.5 … 300`. On a successful update the
poller is woken immediately (`state.wake.set()`) so the new cadence takes effect
without waiting out the old interval.

`halogen_host` repoints the dashboard at a different engine — see §4.9 for the
mechanism and its persistence. It is validated (http/https scheme and a real
host required) and a rejected address is a `400` carrying the reason, not a
silent no-op. Setting it by hand clears `engine_config_id` /
`engine_config_name`, since a hand-picked address has no originating
configuration.

#### `POST /api/v1/stats/reset`

Zeros the running min/avg/max accumulators and restarts the session clock.
Returns the fresh (empty) stats snapshot.

#### `POST /api/v1/count-tokens`

Counts the input tokens of a prompt **without generating**, by proxying Halogen's
Anthropic-compatible `POST /v1/messages/count_tokens`. Useful for checking the
exact token cost (including chat-template framing) before sending a request.

**Request body:**

```json
{
  "text": "The user prompt to count",
  "system": "Optional system prompt"
}
```

`text` is required, non-blank, up to 2,000,000 characters. `system` is optional.

**JSON Response Schema:**

```json
{
  "input_tokens": 65,
  "model": "halogen-qwen3.8-flash-next",
  "chars": 60,
  "system_chars": 0
}
```

Notes:
* `input_tokens` is the token count **including** the chat-template overhead, not
  just the raw characters. Short inputs are dominated by template framing
  (e.g. a 25-character prompt counts as 59 tokens).
* The model id is taken from the live `/health` snapshot (`state.model_name`),
  refreshed every poll — Halogen requires a `model` field and rejects the request
  with HTTP 500 if it is missing.
* `422` if `text` is blank or missing (Pydantic validation).
* `502` if Halogen is unreachable or returns an error / unexpected payload.

#### `POST /api/v1/chat`

Sends a conversation to Halogen and streams the assistant reply back. This is the
endpoint behind the **AI-Chat** tab. It fronts Halogen's four inference endpoints
behind **one normalized interface**, so the frontend renders identically no
matter which upstream API style is chosen.

**Request body:**

```json
{
  "messages": [
    { "role": "user", "content": "What is the capital of France?" }
  ],
  "api": "chat",
  "stream": true,
  "thinking": true,
  "max_tokens": 1024,
  "temperature": 0.7
}
```

| Field | Type | Default | Meaning |
| --- | --- | --- | --- |
| `messages` | array | required | 1–200 turns; each `{role, content}`, `role ∈ {user, assistant, system}`, `content` 1–100,000 chars, non-blank |
| `api` | string | `"chat"` | Upstream style: `chat` \| `messages` \| `responses` \| `completions` |
| `stream` | bool | `true` | `true` = SSE stream, `false` = single JSON object |
| `thinking` | bool | `true` | `false` suppresses the model's chain-of-thought |
| `max_tokens` | int | `null` | 1–32,768; omitted = server default |
| `temperature` | float | `null` | 0.0–2.0; omitted = server default |

**Upstream API mapping.** The `api` field selects which Halogen endpoint is called
and how the payload is shaped:

| `api` | Halogen endpoint | Payload shape |
| --- | --- | --- |
| `chat` | `POST /v1/chat/completions` | `{model, messages, stream, …}` (OpenAI) |
| `messages` | `POST /v1/messages` | system turns hoisted to a top-level `system` string; `anthropic-version: 2023-06-01` header |
| `responses` | `POST /v1/responses` | `{model, input: <last user turn>, stream, max_output_tokens, …}` |
| `completions` | `POST /v1/completions` | `{model, prompt: <last user turn>, stream, …}` (raw text) |

**Thinking control.** Halogen always emits chain-of-thought by default. When
`thinking: false`, the backend injects the appropriate suppression per style:
`reasoning_effort: "none"` for `chat`/`completions`,
`reasoning: {effort: "none"}` for `responses`, and
`thinking: {type: "disabled"}` for `messages`. All four were verified to stop
emitting reasoning with these fields.

**Streaming response (`stream: true`).** `Content-Type: text/event-stream`, with
`Cache-Control: no-cache`, `Connection: keep-alive` and `X-Accel-Buffering: no`.
The backend **normalizes** every upstream event into one of four events so the UI
is style-independent:

| Event | Data | Meaning |
| --- | --- | --- |
| `reasoning` | `{"text": "…"}` | A chain-of-thought chunk |
| `delta` | `{"text": "…"}` | An answer-text chunk |
| `done` | `{"finish_reason": "…", "usage": {…}, "timings": {…}}` | Terminal event |
| `error` | `{"message": "…"}` | Failure mid-stream (Halogen down / bad request) |

Each upstream style is translated by a dedicated extractor:

| `api` | reasoning source | answer source | done signal |
| --- | --- | --- | --- |
| `chat` | `choices[0].delta.reasoning_content` | `choices[0].delta.content` | `choices[0].finish_reason` |
| `messages` | `content_block_delta.thinking_delta` | `content_block_delta.text_delta` | `message_delta.stop_reason` |
| `responses` | `response.reasoning_text.delta` | `response.output_text.delta` | `response.completed` |
| `completions` | *(none — inline in raw text)* | `choices[0].text` | `choices[0].finish_reason` |

> **Note on `completions`:** the raw text-completion style has no separate
> reasoning channel. The model embeds its thinking inline between literal
> `<|im_start|>think … <|im_end|>` markers. The frontend splits this
> client-side (`createThinkSplitter` in `api.js`) so the UI still shows the
> chain-of-thought collapsed separately. The other three styles deliver
> reasoning on its own event and need no client-side splitting.

**Non-streaming response (`stream: false`).** A single JSON object in the common
shape:

```json
{
  "content": "The capital of France is Paris.",
  "reasoning": "We need to answer briefly…",
  "finish_reason": "stop",
  "model": "halogen-qwen3.8-flash-next",
  "usage": { "prompt_tokens": 54, "completion_tokens": 16, "total_tokens": 70 },
  "timings": { "predicted_per_second": 27.9, "predicted_ms": 573.3, "cache_n": 54 }
}
```

Errors: `422` on invalid body (Pydantic). `502` if Halogen is unreachable or
returns an error (for `stream: false`; for `stream: true` the failure surfaces as
an `error` SSE event instead).

#### `POST /api/v1/agent/chat`

Agentic chat. Instead of forwarding one request to Halogen, the backend runs an
**OpenAI Agents SDK** loop: the model is given dashboard tools, chooses among
them, reads the results, and continues until it produces a final answer. The
client sees one streamed turn, exactly like `POST /api/v1/chat`, plus tool
events.

Request:

```json
{
  "messages": [
    { "role": "user", "content": "Why is my decode throughput low?" }
  ],
  "mode": "agent",
  "allow_actions": false,
  "full_access": false,
  "max_turns": 8,
  "max_tokens": 2048,
  "temperature": 0.2,
  "thinking": true,
  "instructions": ""
}
```

| Field | Default | Meaning |
| --- | --- | --- |
| `messages` | required | Full conversation history, sent every turn (no server-side session) |
| `mode` | `agent` | `agent` runs the SDK loop; `plain` delegates to the `POST /api/v1/chat` handler |
| `allow_actions` | `false` | When true, the destructive tools (`start_engine`, `stop_engine`) are registered |
| `full_access` | `false` | When true, the research/execution tools (§4.12) are registered **and** `allow_actions` is forced on |
| `max_turns` | `AGENT_MAX_TURNS` (25) | Hard cap on model↔tool round-trips |
| `max_tokens` | `AGENT_MAX_TOKENS` (8192) | Per-call response cap. Not exposed in the agent UI — see the truncation note below |
| `temperature` | `0.2` | Low by default; a tool-calling loop drifts badly when creative |
| `thinking` | `true` | `false` sends `reasoning.effort = "none"` so Halogen suppresses chain-of-thought |
| `instructions` | empty | Per-request system-prompt override; falls back to `AGENT_INSTRUCTIONS`, then the built-in prompt |

Response is `text/event-stream`:

| Event | Payload | Notes |
| --- | --- | --- |
| `reasoning` | `{"text": "..."}` | Chain-of-thought delta (only when thinking is on) |
| `delta` | `{"text": "..."}` | Answer delta |
| `tool` | `{"phase": "call", "name": "...", "detail": "..."}` | The model invoked a tool |
| `tool` | `{"phase": "result", "name": "...", "output": "..."}` | The tool returned |
| `done` | `{"finish_reason", "usage", "timings"}` | Turn finished; `usage` is the sum across all turns |
| `error` | `{"message": "..."}` | Aborted — max turns exceeded, engine unreachable, ... |

The `reasoning` / `delta` / `done` / `error` vocabulary is identical to
`POST /api/v1/chat`, so a client that works there works here; `tool` is the one
addition and unknown events are ignored by the shared SSE reader.

**Two SDK behaviors worth recording.** Both were found empirically and are the
reason the code looks the way it does:

1. **`include_usage` must be set explicitly.** The SDK only adds
   `stream_options={"include_usage": true}` when it recognises a genuine OpenAI
   endpoint. Halogen is not recognised, so without
   `ModelSettings(include_usage=True)` the usage block never arrives and the
   token stats render blank.
2. **Thinking control lives in `reasoning.effort`, not `reasoning_effort`.**
   `ModelSettings` is a pydantic dataclass; a flat `reasoning_effort=` kwarg is
   silently discarded and thinking stays on. The nested
   `Reasoning(effort="none")` shape is what reaches the wire.

`RunResultStreaming` exposes no `final_usage`; the aggregate is computed from
`result.raw_responses`, each of which carries a `.usage`.

**Output cap and tool-call truncation.** A tool call is itself generated text: the
model emits the whole JSON object, including the full `content` string of a
`write_file` call, before the call executes. With a small `max_tokens` that JSON is
cut off mid-string, the arguments fail to parse, and the model sees an opaque tool
error. The default is therefore `AGENT_MAX_TOKENS` (8192) rather than the 1024 the
plain-mode UI defaults to, and the agent UI does not expose the control at all —
a user lowering it to tune answer length would silently break file writing.
`max_tokens` remains settable per request for callers that know what they are doing.

Errors: `422` on invalid body. Mid-stream failures surface as an `error` event
rather than an HTTP status, because the stream has already started.

#### `GET /api/v1/agent/tools`

Returns the tool inventory for the current permission set:

```json
{
  "allow_actions": false,
  "tools": [
    {
      "name": "get_live_status",
      "description": "Aggregated Halogen + hardware snapshot",
      "destructive": false
    }
  ]
}
```

Useful for confirming which tools an agent turn will actually see. `start_engine`
and `stop_engine` appear only with `?allow_actions=true`. With
`?full_access=true` the seven research/execution tools of §4.12 join them, and the
response also carries `full_access` and a `tier` label
(`read-only` / `actions` / `full`). The bounded skill tools are also listed.
MCP tools appear only when their server is enabled, connected, and its configured
minimum access level is met by the current request.

#### Agent extension APIs

Agent capabilities are resolved per request from three sources — built-in
dashboard tools, skills, and MCP tools — and each source is gated by one of the
three access tiers. The tier is a property of the *request* (`allow_actions`,
`full_access`) and of each *extension* (`minimum_access`); an extension is only
registered when the request meets or exceeds what the extension needs.

| Capability source | `read-only` | `actions` | `full_access` |
| --- | --- | --- | --- |
| Built-in dashboard tools (§4.11) | ✔ | ✔ | ✔ |
| `start_engine` / `stop_engine` | ✘ | ✔ | ✔ |
| Research + execution tools (§4.12) | ✘ | ✘ | ✔ |
| Skill tools (`list_skills`, `load_skill`, `read_skill_resource`) | ✔ | ✔ | ✔ |
| MCP tools | Only if `minimum_access = read-only` | `read-only` or `actions` | Any tier |

`full_access` implies `allow_actions`; requesting it forces the action tier on.
A skill therefore never widens what the request already allows — skills read files
from disk but cannot execute anything, and an MCP server's reach is whatever its
own process can do, which is why `minimum_access` is mandatory rather than a hint.

`GET /api/v1/agent/skills` rescans `SKILLS_DIR` and returns each valid skill's
name/description plus manifest or validation errors. Skill directories contain a
standard `SKILL.md`; they are not imported as Python. The agent receives only
the skill index initially and loads full instructions or bounded, path-confined
resources on demand with `list_skills`, `load_skill`, and
`read_skill_resource`.

**`SKILL.md` validation.** The loader is deliberately strict so a malformed
folder surfaces as a reported error instead of a half-loaded skill. Frontmatter
must open with `---` on the first line and be terminated; it is parsed as YAML and
only these keys are accepted: `name`, `description`, `license`, `compatibility`,
`metadata`, `allowed-tools`. Unknown keys are rejected. `license`,
`compatibility` and `allowed-tools` must be strings and `metadata` must be a
mapping. `name` must match `^[a-z0-9]+(?:-[a-z0-9]+)*$` and must equal the
containing directory name, and `description` is required. Size ceilings: 8 KiB
of frontmatter (`MAX_FRONTMATTER_BYTES`), 100 KB per `SKILL.md`
(`MAX_SKILL_FILE_BYTES`), 64 KB per resource returned through
`read_skill_resource` (`MAX_RESOURCE_BYTES`), and 1024 characters for the
description. Resource paths are resolved against the skill folder and rejected if
they escape it.

MCP management (`GET/POST /api/v1/agent/mcp-servers`,
`PUT/DELETE /api/v1/agent/mcp-servers/{id}`, and
`POST /api/v1/agent/mcp-servers/test`) requires `MCP_ADMIN_TOKEN` and the
`X-MCP-Admin-Token` header. If no token is configured the management API
returns `503` and cannot be enabled from the browser. The stdio-only config
store is versioned JSON under `STRIXPER_DATA_DIR/mcp_servers.json`, atomically
written with restrictive file permissions, and contains secret references
rather than credential values. Each server is connected lazily when an
eligible agent request first needs it; failed servers are reported in status
without preventing unrelated tools from working. Application shutdown closes
all MCP sessions and subprocesses. Because the connection is lazy, a freshly
recreated container reports every server `disconnected` with zero tools until the
first chat request or a **Test connection** opens it; the next status call shows
it `connected`. That is expected, not a failure.

**Operational limits.** The manager refuses runaway configurations rather than
degrading silently:

| Constant | Value | Applies to |
| --- | --- | --- |
| `MCP_START_TIMEOUT_SECONDS` | 20 s | stdio open, session create, `initialize`, `list_tools` |
| `MCP_CALL_TIMEOUT_SECONDS` | 30 s (configurable) | one tool call |
| `MCP_MAX_SERVERS` | 32 | registered servers |
| `MCP_MAX_TOOLS_PER_SERVER` | 64 | tools advertised by one server |
| `MCP_MAX_TOOLS_TOTAL` | 256 | MCP tools registered across all servers |
| `MCP_MAX_TOOL_INPUT_BYTES` | 100 000 | serialized tool arguments |
| `MCP_MAX_TOOL_OUTPUT_BYTES` | 32 000 | tool result text (byte-truncated with a `\n[truncated]` marker) |
| — | 32 000 | converted JSON schema per tool |
| — | 1 024 | tool description shown to the model |

**Tool naming.** Each MCP tool is registered as `mcp_{server_id}_{tool_name}`
with every character outside `[A-Za-z0-9_-]` replaced by `_`. If the result is
longer than 64 characters it is truncated to 55 characters plus `_` plus the
first 8 hex characters of the SHA-256 of the full name, so names stay valid for
the OpenAI tool schema and collisions remain distinguishable. A normalized
collision inside one server is rejected with an explicit error rather than
shadowing a tool.

**MCP SDK field compatibility.** The installed `mcp` Python SDK exposes
snake_case attributes (`input_schema`, `structured_content`, `is_error`,
`mime_type`) while the protocol itself uses camelCase. The adapter reads the
snake_case name first and falls back to camelCase, so it works with either
generation of the SDK. A missing `is_error` is treated as an error and raised
rather than assumed success. Non-text content blocks are replaced with
`[Non-text MCP content omitted: <mime|type>]` instead of being passed to the
model as raw data.

**Locking.** A per-server `call_lock` serializes tool calls against one server so
a slow or stateful server never sees concurrent requests. Configuration edits use
`_server_update_lock()`, which takes the global lock and then the per-server
lock, so editing one server does not churn the others. If a configuration change
is detected while a call is in flight, the call fails with "MCP server
configuration changed; retry this tool call" rather than executing against a
stale session.

**Child-process environment.** The stdio child does **not** inherit the backend
environment. Only `PATH`, `HOME`, `TMPDIR`, `TMP`, `TEMP`, `LANG`, `LC_ALL`,
`TZ`, `SSL_CERT_FILE` and `SSL_CERT_DIR` are passed through; `MCP_ADMIN_TOKEN`
and every other backend variable (including inference credentials) is withheld.
Values declared through `secretRef` are layered on top of that minimal set.

`MCP_SERVERS_DIR` is the single install root for stdio MCP packages. Each
server is installed under a child directory matching its Strixper server ID;
the manager prepends that child's `bin`, `.venv/bin`, and `node_modules/.bin`,
plus the shared `MCP_SERVERS_DIR/bin`, to the child process `PATH`. The Docker launcher mounts
the persistent `strixper-mcp-servers` volume at `/mcp-servers`; the image
seeds its bundled packages into a new volume. Tieline is pinned at
`mcp-servers/tieline/package-lock.json`, and
`mcp-servers/tieline/strixper-mcp-config.json` seeds default config only when
the persistent MCP config is missing. Legacy Tieline `npx` entries are
migrated to the installed per-server executable during backend initialization.

The Docker launcher separately mounts the repository read-only at
`/workspace/strixper` and supplies `TIELINE_WORKSPACE` to the Tieline server.
The repository's `.tieline/` workspace and compiled code-topology artifact
remain at the repository root so code-context queries see the actual project.
An empty contract has no accepted product-intent records until Capabilities,
Stories, and Acceptance Criteria are authored. Because Tieline exposes both
read and planning-write tools and the manager gates access per server, its
default minimum access is `actions`; the AI Chat **Allow actions** switch also
enables Strixper's engine start/stop tools.

An MCP stdio server command runs with the backend user's privileges. It is
treated as trusted executable code, not sandboxed; do not enable servers you
do not trust. `MCP_ADMIN_TOKEN` protects management operations only, not the
rest of the dashboard API. The token is sent over HTTP by the browser; use
localhost, an SSH tunnel, or HTTPS for MCP administration. Do not expose the
dashboard to untrusted networks.

**Risk summary.**

| Risk | Mitigation in the implementation |
| --- | --- |
| Prompt injection via skill content | Skill text is data, not system policy. It cannot grant permissions, and executable capability always comes from a separately gated tool. Keep the skills directory under your control. |
| Arbitrary code execution through MCP | Explicit admin-token-gated enablement, an explicit trust warning in the UI, and the `minimum_access` tier. Not sandboxed; not covered by the shell deny-list. |
| Secret leakage | Only environment-variable *references* are stored. Config, status, and error payloads are redacted; resolved values are never logged. |
| Malformed or hostile MCP schemas | Schemas are validated and recursively sanitized (unsupported keywords such as `default` removed), input and output are byte-bounded, and failures are reported explicitly. |
| Tool-name collisions | MCP tools are namespaced `mcp_{server}_{tool}` with normalized names and explicit collision detection. Skills add no callable tools. |
| Hangs and resource exhaustion | Connect and call timeouts, per-server call serialization, caps on servers/tools/total tools, size ceilings, and orderly shutdown of every child process. |
| Management API exposure | Standard dashboard network limits apply. Administer over localhost/tunnel/HTTPS only. |

#### LLM-Runner endpoints

The LLM-Runner tab lets the user **create, run and store** the docker command
used to launch a containerized LLM inference server. Configurations are stored
locally as a JSON list on the backend disk (see §4.6), so they survive restarts.

**Configuration object** (all seven fields always present):

```json
{
  "id": "a9be92b171ae",
  "name": "Qwen3 local inference",
  "docker_command": "docker run -v {models}/w.hgn:/models/w.hgn:ro -p 0.0.0.0:{port}:{port} {image}",
  "description": "optional free-text note",
  "parameters": [
    { "name": "models", "label": "Models directory", "value": "/srv/models" },
    { "name": "port", "label": "API port", "value": "8731" },
    { "name": "image", "label": "Docker image", "value": "my-llm-image:latest" }
  ],
  "created_at": "2026-10-09T18:37:58Z",
  "updated_at": "2026-10-09T18:37:58Z"
}
```

`docker_command` is a **template**: wherever a value should be variable the
command references a parameter as `{parameter_name}`, and the parameter's
`value` is substituted in at run time (see §4.7). A configuration with an
empty `parameters` list is a plain, unparameterised command and behaves
exactly as before.

**Parameters.** Each parameter has `name` (1–60 chars, an identifier —
letters, digits and underscore, not starting with a digit), `label`
(optional, ≤ 120; falls back to the name) and `value` (≤ 4,000 chars).
Up to 40 parameters per configuration. Names are unique case-insensitively.
On every create and update the store checks that **every placeholder in the
command has a matching parameter**, so a saved configuration always renders
into a complete command — `400` naming the missing ones otherwise. Parameters
the command does not reference are allowed (useful while editing); the UI
lists them as a hint.

`GET /api/v1/runner/configs` → `{ "configs": [ <configuration>, … ] }`.

`GET /api/v1/runner/configs/{id}` → the single configuration, or `404`.

`POST /api/v1/runner/configs` — body `{ name, docker_command, description?, parameters? }`.
`name` (1–120 chars) and `docker_command` (1–10,000 chars) are required and
non-blank; `description` is optional (≤ 1,000 chars); `parameters` is
optional (defaults to an empty list). Names are unique (case-insensitive).
Returns the created configuration. `400` on a blank field, a duplicate name,
a malformed parameter, or a placeholder with no matching parameter.

`PUT /api/v1/runner/configs/{id}` — body accepts any subset of
`{ name, docker_command, description, parameters }`; omitted fields are left
unchanged (sending `parameters: []` clears the list). Returns the updated
configuration. `400` on validation / duplicate name, `404` if the id is
unknown. A rejected update leaves the stored configuration completely
untouched — the whole resulting state is validated before anything is written.

`DELETE /api/v1/runner/configs/{id}` → `{ "deleted": "<id>" }`, or `404`.

**`POST /runner/preview`** — body `{ docker_command, parameters? }`. Renders
the template against the parameters and returns `{ "command": "…" }` without
saving or running anything, so the UI can show exactly what a run would
launch through the same code path the run itself uses. `400` if a referenced
parameter is missing or blank.

**A run is a server-side resource, not a browser connection.** Starting and
watching are separate calls, so any number of browsers can follow the same
engine, and closing one never affects the run. See §4.10 for the service
that owns this.

**`POST /api/v1/runner/configs/{id}/run`** — renders the stored
`docker_command` against the configuration's parameter values, launches it,
and returns the run summary as JSON immediately. It does **not** stream;
the console comes from `GET …/stream`.

If another engine is already running it is stopped first, and the stop is
**awaited** — the new container usually wants the same GPU and often the
same published port, and launching into a still-held port is exactly the
failure that reads as a mysterious crash-on-start. The new configuration is
validated (rendered, container name extracted) *before* anything running is
touched, so a typo in the new config cannot take down a working engine.

Body: optional `{ "values": { "port": "9000", … } }` overrides individual
parameter values for this run only; nothing is written back to the store.
Override keys must be parameters the configuration already declares — an
unknown key is a `400` rather than a silent no-op, so a typo cannot quietly
leave the stored value in place. `400` is also returned if a parameter the
template needs ends up blank. `404` if the config is unknown; `502` if the
command cannot be launched.

Response — the run summary:

```json
{
  "run_id": "cc7c704e4814",
  "config_id": "10082f82ee6a",
  "config_name": "Halogen-Qwen3.8-flash-next",
  "container_name": "ai-toolbox-cockpit-halogen-server-334446c6",
  "engine_url": "http://127.0.0.1:8731",
  "started_at": "2026-10-09T21:57:14Z",
  "status": "running",
  "exit_code": null,
  "adopted": false,
  "engine_switch_error": null
}
```

`container_name` is the value of the command's `--name` flag, or `null`
when the command has none. `engine_url` is the address the dashboard has
just been repointed at, or `null` when the configuration declares no
`port` parameter and so left the target alone (see §4.9).
`engine_switch_error` carries the reason the repoint failed, when it did —
the run itself is fine in that case, so it is reported rather than left for
the user to infer from a stale live view.

**`GET /api/v1/runner/active`** — `{ "active": <run summary> }`, or
`{ "active": null }` when nothing is running. Cheap enough to poll, which
is how the UI keeps its Run/Stop buttons honest when a run exits on its own.

**`GET /api/v1/runner/runs/{run_id}/stream`** — attaches a viewer to the
live run as `text/event-stream` (same SSE framing as `/api/v1/chat`):

| Event | Data | Meaning |
| --- | --- | --- |
| `state` | `{"run_id": "…", "status": "…", "exit_code": null, "backlog": "…"}` | Current state plus everything already produced |
| `stdout` | `{"text": "…"}` | A new output chunk (stdout + stderr merged) |
| `ping` | `{}` | Keepalive during a quiet stretch |
| `exit` | `{"exit_code": 0}` | Process terminated |
| `error` | `{"message": "…"}` | Stream failure |

The viewer subscribes **before** the backlog is read, with no `await`
between, so nothing produced in that gap is lost or repeated. A late joiner
gets the whole buffered console in the `backlog` field rather than an empty
window — which is the whole point of a run that outlives the browser that
started it.

Disconnecting detaches that one viewer and nothing else. **The run keeps
going.** Stopping it is the Stop button's job, not the connection's.
`404` if the run is not the active one.

**`POST /api/v1/runner/runs/{run_id}/stop`** — stops the run by stopping
its **container** through docker (`docker stop --time N`), then reaps the
local `docker run` client process. Returns
`{ "stopped": "<run_id>", "method": "docker_stop" }`, or `404` if the run
is not active.

`method` reports what actually did it: `docker_stop` (graceful stop
succeeded), `docker_kill` (the container ignored the graceful stop and was
force-killed), `process` (no container handle, so only the client was
killed), or `already_exited`.

Stopping goes through docker rather than signalling the client because
`docker run -it` attaches the CLI to a pseudo-terminal: killing that client
does **not** stop the container, which keeps running with nothing attached,
holding the GPU and the published port. `docker stop` sends SIGTERM to the
container's PID 1 and escalates to SIGKILL after `N` seconds
(`RUNNER_STOP_TIMEOUT`, default 5). A command with no `--name` gives no
handle to stop by, so it falls back to killing the client only — include
`--name` for a reliable stop.

#### `GET /api/v1/healthz`

Returns `{ "status": "ok" }`. Independent of Halogen reachability.

---

## 4. Backend Services

### 4.1 `HalogenClient` (`services/halogen_client.py`)

Async `httpx` client for the Halogen endpoints.

* `get_health()` → `GET /health` (required; raises on failure).
* `get_metrics()` → `GET /metrics`, parsed by `parse_prometheus()` into
  `{metric_name: float}` (comment lines and unparsable lines skipped).
* `get_models()` → `GET /v1/models`.
* `get_cache()` → `GET /cache` — Halogen-specific prompt-cache counters
  (hits/misses, stores/evictions, `prompt_tokens_saved`, store/restore latency,
  pool usage, disk-tier state).
* `count_tokens(messages, model, system=None, tools=None)` →
  `POST /v1/messages/count_tokens` — returns `{ "input_tokens": <int> }`.
  The `model` field is required by Halogen; omitting it yields HTTP 500.
* `post_json(path, payload, headers=None)` → generic blocking JSON `POST` with a
  long chat timeout (300 s read / 10 s connect), `raise_for_status()`, returns
  the parsed dict. Used by the chat proxy for `stream: false` requests.
* `stream_sse(path, payload, headers=None)` → async generator that POSTs and
  iterates the upstream SSE stream line-by-line, keeping only `data:` lines,
  skipping blanks and the `[DONE]` sentinel, and yielding each `json.loads()`
  chunk (unparsable lines skipped). Used for `stream: true` requests.
* `chat_completion(messages, model, max_tokens=None, temperature=None, **extra)`
  → blocking `POST /v1/chat/completions`.
* `chat_completion_stream(...)` → same payload, yields parsed SSE chunks.
* `snapshot()` fetches `/health`, `/metrics`, `/v1/models` and `/cache`
  concurrently (`asyncio.gather`); `/metrics`, `/v1/models` and `/cache` are
  best-effort (a failure yields `{}`), `/health` is required.

`build_halogen_state(health, metrics, models, cache)` maps the raw Halogen
payloads to the `halogen` section:

| Output field | Source |
| --- | --- |
| `status`, `model`, `busy`, `slots`, `in_flight`, `queued` | `/health` |
| `context_size` | `/health.context`, else `/v1/models[0].max_model_len` / `.context_length` |
| `kv_pool_positions` | `halogen:kv_pool_positions` (else `/health`) |
| `kv_cache_usage_ratio` | `llamacpp:kv_cache_usage_ratio` |
| `prompt_tokens_per_sec` | `llamacpp:prompt_tokens_seconds` |
| `predicted_tokens_per_sec` | `llamacpp:predicted_tokens_seconds` |
| `prompt_tokens_total` | `llamacpp:prompt_tokens_total` |
| `prompt_seconds_total` | `llamacpp:prompt_seconds_total` |
| `tokens_predicted_total` | `llamacpp:tokens_predicted_total` |
| `prompt_tokens_cached_total` | `halogen:prompt_tokens_cached_total` |
| `draft_acceptance_rate` | `halogen:draft_tokens_accepted_total / halogen:draft_tokens_total` (rounded 4dp) |
| `cache` | `GET /cache` payload passed through verbatim (empty `{}` if unavailable) |

**Metric namespaces are not uniform across engines.** The `llamacpp:` keys come
from `llama.cpp`'s own Prometheus exporter and are therefore available under
GUFO and `llama-server` when metrics are enabled; stock `llama-server` disables
the `/metrics` endpoint by default and requires `--metrics`. The ROCm10 Gemma
Runner configuration includes this flag. The `halogen:` keys and the `/cache`
endpoint are Halogen-specific. The mapping above reflects this: the throughput,
KV-usage, and token-total fields read from `llamacpp:` and work on engines
exposing metrics, while `kv_pool_positions`, `prompt_tokens_cached_total`,
`draft_acceptance_rate`, and `cache` depend on Halogen and read as absent
(`None` / `{}`) elsewhere. Nothing in the merge raises on a missing key, so a
non-Halogen engine yields a partially populated snapshot rather than an
error.

Verified against the shipped binaries rather than assumed:

| | `halogen:` keys | `llamacpp:` keys | `/cache` | `/v1/messages/count_tokens` |
| --- | --- | --- | --- | --- |
| Halogen | Yes | Yes | Yes | Yes |
| GUFO | No | Yes | No | Yes |
| `llama-server` (with `--metrics`) | No | Yes | No | No |

(Confirmed by inspecting the shipped binaries: the route strings compiled into
`gufo` include `/v1/messages/count_tokens`; the `llama-server` build in the
`rocm-10.0` toolbox does not expose it.)

GUFO's exporter emits `llamacpp:prompt_tokens_cached_total` and
`llamacpp:spec_decode_num_*` for its speculative decoding. The mapping above
reads the equivalent figures from `halogen:prompt_tokens_cached_total` and
`halogen:draft_tokens_*`, so on GUFO those two fields show as unavailable even
though the underlying counters exist under different names. Adding a fallback to
the `llamacpp:` names would restore them; until then the dashboard simply omits
cached-token and draft-acceptance figures for non-Halogen engines.

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
  `vram_pct`, `gpu_util_pct`, `cpu_pct`, `draft_acceptance`,
  `cache_hit_rate`, `cache_token_hit_rate`.
* **`IGNORE_ZERO_KEYS = ("prompt_tps", "decode_tps", "gpu_util_pct", "cpu_pct", "draft_acceptance")`**
  — these discard zero readings from **both the average and the minimum** (a 0
  means the engine/metric wasn't reporting activity). The memory series
  (`gtt_used_gb`, `gtt_pct`, `vram_pct`) and the cache hit-rate series keep
  their true minimum including any zeros.
* `draft_acceptance`, `cache_hit_rate` and `cache_token_hit_rate` are recorded
  as **percentages** (the raw ratio × 100), so their min/avg/max are in %.
* `record(sample)` adds one sample; `snapshot()` returns
  `{key: {min, max, avg, count}, started_at, samples}`; `reset()` zeroes
  everything and restarts `started_at`.

**`RuntimeState`** — `poll_interval`, `latest`, `wake` (asyncio.Event),
`halogen` (HalogenClient), `monitor` (SystemMonitor), `rocm` (RocmMonitor),
`stats` (StatsTracker), `model_name` (served model id, used by
`/api/v1/count-tokens` and by the chat's `model` field).

`model_name` is seeded from the persisted engine target at startup, falling
back to `DEFAULT_MODEL`, and refreshed from `/health` on every poll that
reports a `model`. The `/health` value wins when present — the engine is
authoritative — but a run configuration's `served_model_name` is applied
immediately on switch, so an engine that does not report its model in
`/health` still gets the right id from the first request rather than
inheriting whatever the previous engine used.

**`build_live_snapshot(state)`** — polls Halogen and rocm **concurrently**
(`asyncio.gather(..., return_exceptions=True)`), reads local hardware, attaches
`hardware.rocm`, records the sample into `stats`, and returns the combined
snapshot (§3.3).

**`poll_loop(state)`** — loops: build snapshot → wait on `wake` with a timeout of
`max(poll_interval, 0.5)` → clear `wake`. `managed_poller` is an async context
manager that starts the loop on startup and cancels it + closes the Halogen client
on shutdown.

### 4.6 `RunnerStore` (`services/runner_store.py`)

Persists the LLM-Runner docker configurations to a JSON file on disk.

* **Storage path:** `backend/data/llm_runner_configs.json` by default; override
  with the `RUNNER_STORE_PATH` environment variable (used by tests / alt layouts).
* **Atomic writes:** each mutation writes a temp file then `os.replace`s it over
  the target, so a crash mid-write cannot corrupt the store. A missing or corrupt
  file is treated as an empty list and rewritten on the next successful mutation.
* **Concurrency:** all mutations take an `asyncio.Lock`.
* **Validation:** `name` (1–120) and `docker_command` (1–10,000) required and
  non-blank; `description` optional (≤ 1,000). Names are unique case-insensitive
  (`RunnerStoreError` on a duplicate). Each config gets a 12-hex-char `id` and
  `created_at` / `updated_at` timestamps.
* **Template validation:** on create *and* update the stored command and
  parameter list are checked together (`validate_template`) so no configuration
  can be saved with a `{placeholder}` that has no matching parameter.
* **Update ordering:** `update` resolves the complete resulting configuration,
  validates it, and only then assigns — a payload rejected by validation leaves
  the in-memory copy as it was, not just the file on disk.
* **Load-time migration:** configurations written before `parameters` existed
  have no such key; `_load` normalises it to `[]`. A stored `parameters` list
  that no longer validates (a hand-edited file, say) is dropped to `[]` rather
  than making the whole store unreadable.
* **Read once, at construction.** `_load()` is called from `__init__` and never
  again; there is no reload, watcher, or mtime check. The in-memory list is the
  source of truth for the process lifetime, and every mutation `_persist()`s the
  whole list over the file. Two consequences worth knowing before hand-editing
  `llm_runner_configs.json`:
  - Edits made while the backend is running are invisible until restart. The API
    keeps reporting the set the process booted with.
  - Worse, the next create/update/delete overwrites the file with the
    in-memory list, silently discarding whatever was hand-added. A third
    engine appended to the file by hand disappears the moment a config is saved
    from the UI.
  Edit the file with the backend stopped, or use the LLM-Runner UI.
* **Docker data is volume-backed.** `strixper.sh` mounts the named
  `strixper-data` volume at `/app/backend/data`; the host checkout's ignored
  `backend/data/llm_runner_configs.json` is not mounted. Host-side changes to
  that file therefore do not appear in Docker. Create configs in the UI/API or
  explicitly import them into the volume-backed store.
* `list()`, `get(id)`, `create(...)`, `update(id, ...)`, `delete(id)`.

The router (`routers/runner.py`) holds one lazily-created store instance and a
dict of active runs (`run_id -> subprocess handle`) so runs can be stopped by id.

### 4.7 Parameter templates (`services/params.py`)

The substitution layer shared by the store (validation) and the run path
(rendering).

* **Syntax.** Placeholders are `{parameter_name}`, matched by
  `\{([A-Za-z_][A-Za-z0-9_]*)\}` — only identifier-shaped braces count, so a
  stray `{` in a JSON literal or format string is left alone.
* **Values are inserted verbatim**, with no shell quoting. The command template
  is already raw shell and may legitimately contain `$(...)` substitutions, so
  quoting would get in the way. The consequence is that a parameter feeding a
  path or argument must not contain shell-significant characters it doesn't
  mean; the non-blank check on render catches the common case of an unset value.
* **Limits.** Name ≤ 60 chars, label ≤ 120, value ≤ 4,000, at most 40
  parameters per configuration.
* **Functions.** `normalise_parameters(raw)` validates and reduces a payload to
  `{name, label, value}` rows (empty label → name); `find_placeholders(cmd)`,
  `missing_parameters(cmd, params)`, `unused_parameters(cmd, params)` for
  author-time feedback; `validate_template(cmd, params)` raises on any unfilled
  placeholder; `merge_values(params, overrides)` applies run-time overrides,
  rejecting unknown keys; `render_command(cmd, params)` produces the final
  string, raising if a referenced value is blank.
* All failures raise `ParamError` (a `ValueError`), which the router surfaces as
  `400` and the store re-raises as `RunnerStoreError`.

`frontend/src/params.js` mirrors the read-only helpers (`findPlaceholders`,
`missingParameters`, `unusedParameters`, `renderTemplate`) plus `validateRows`
for per-row form feedback, so the form can give live warnings. The backend stays
the authority: it re-validates on save and at run time.

### 4.8 `docker_control` (`services/docker_control.py`)

Stopping a run means stopping the **container**, not the process that launched
it. With `docker run -it` the CLI is attached to a pseudo-terminal, and a
signal to that client tears down its own stream handling without reliably
reaching the container — the container survives, still holding the GPU and the
published port, with nothing attached to it. Every stop path therefore goes
through the docker CLI.

* **`extract_container_name(command)`** reads the `--name` value out of the
  *rendered* command (`--name foo` and `--name=foo` both match), so parameter
  substitution has already happened. An unrendered `{placeholder}` is skipped:
  it would name a container that cannot exist. `None` when the command has no
  `--name`, which leaves no handle to stop by.
* **`stop_container(name, timeout)`** — `docker stop --time N`, graceful
  SIGTERM to the container's PID 1 with docker escalating to SIGKILL after
  `N` seconds. **`kill_container(name)`** — `docker kill`, immediate.
  **`container_is_running(name)`** — `docker inspect -f {{.State.Running}}`.
  Each returns a boolean rather than raising, so the caller can treat a failed
  docker call as "try the next strategy".
* **Timeouts.** `RUNNER_STOP_TIMEOUT` (default 5 s) is how long docker waits
  for the container before force-killing it; keep it short so the Stop button
  stays responsive against a server that ignores SIGTERM. Every docker CLI call
  additionally carries `DOCKER_CLI_GUARD` (25 s) of headroom on our own wait,
  so a wedged docker daemon surfaces as a failed stop rather than a hung
  request.
* **Teardown is idempotent.** `ActiveRun.stop()` in the run registry guards
  each run with its own `asyncio.Lock` and records `_halted`/`_halt_method`,
  so repeated stops are safe and the second caller gets the first caller's
  `halt_method` back. Nothing stops a run on browser disconnect any more —
  see §4.10.
* **Fallback chain.** `docker stop` → `docker kill` → kill the client process.
  A run command without a Docker `--name` is rejected before launch; this is
  required so Strixper can reliably track, adopt, and stop every engine.
* **`list_running_names()`** — `docker ps --format {{.Names}}`, `[]` on any
  failure. One call rather than one `inspect` per configuration, because
  discovery runs on a timer.
* **`inspect_started_at(name)`** — `docker inspect -f {{.State.StartedAt}}`.
  Used for adopted runs: the container was already up, so docker's own clock
  is the honest start time rather than the moment we noticed it.

### 4.9 `engine_target` (`services/engine_target.py`)

The default engine target used by the compatibility live-status endpoint and
manual target editor. Managed engines have independent per-run targets.

**Why this exists.** `HALOGEN_HOST` is read once at process launch, so the
dashboard could not follow Runner-launched engines on other ports. The default
target remains runtime state, while each managed run also stores its own
engine URL so multiple engines can be polled without changing one another's
target.

* **Two different addresses, deliberately not conflated.**
  - `bind` (a *configuration parameter*, rendered into `-p {bind}:{port}`)
    decides who may reach the container from outside: `127.0.0.1` for
    local-only, `0.0.0.0` for the LAN.
  - the **engine target** decides where *this backend* connects to reach the
    engine. It is always loopback — the backend supervises containers on its
    own host, and routing to a local service via an outward-facing address
    just adds a failure mode.

  Note that a docker command may contain two bind-ish things. GUFO's
  `--host 0.0.0.0` is the server binding *inside* the container and must
  stay `0.0.0.0` for the published port to work at all; only the `-p`
  host-published address is parameterised.

* **Auto-switch on run start.** A configuration that declares a `port`
  parameter is treated as a network server, so launching one repoints the
  dashboard at `http://127.0.0.1:{port}` and records the originating
  `config_id` / `config_name`. A configuration with no `port` leaves the
  target alone. The switch happens in the request handler, immediately
  after the run launches successfully — a run that failed to start cannot
  leave the dashboard pointed at nothing.

* **The served model travels with the target.** The target also stores
  `model_name`, taken from the configuration's `served_model_name`
  parameter. This is not decoration: the chat sends a `model` field on
  every request, and an engine that does not report its model in `/health`
  would otherwise leave the chat asking for whatever the *previous* engine
  served. `/health` still overrides it when the engine reports one — the
  engine is authoritative, the config value is the floor. A manual
  `POST /config {"halogen_host": …}` with no model supplied keeps the
  current one rather than blanking the persisted value.

* **Stop does not revert.** Per the chosen semantics, stopping a run leaves
  the target where it is. If that engine is gone the live view honestly
  shows "Connection Lost"; starting another configuration switches again.
  `POST /config {"halogen_host": …}` is the manual escape hatch.

* **Persistence is best-effort.** The switch has already taken effect in
  memory before anything is written, so an unwritable
  `engine_target.json` logs a warning and the run proceeds. On startup a
  missing, unreadable, or corrupt target file falls back to `HALOGEN_HOST`
  — the same philosophy as the config store: a bad file must never wedge
  the app.

* **`normalise_base_url`** requires an http/https scheme and a non-empty
  host, and strips the trailing slash so callers can safely append
  `/health`. It raises `EngineTargetError` rather than guessing, so a typo
  surfaces as a `400` instead of a silently unreachable client.

* **Why mutating `base_url` is safe.** `HalogenClient` builds every
  request URL from `self.base_url` at call time
  (`f"{self.base_url}/health"`), not at construction, so reassignment
  takes effect on the next request with nothing to rebuild. httpx pools
  connections by origin and caches no responses, so old pooled
  connections to the previous engine simply idle out. All the in-memory
  mutations in `set_engine_target` happen with no `await` between them, so
  no other coroutine can observe a half-updated target.

**Consequence worth knowing:** `chat.py` and `tokens.py` read the same
`state.halogen` client. Repointing the target redirects the chat proxy and
the token counter too — which is the intended behaviour, but it means the
target engine must actually speak the API those tabs use.

### 4.10 `run_registry` (`services/run_registry.py`)

Owns the one live engine run as a server-side resource.

**Why this exists.** A run used to be a property of one HTTP stream: its
reader lived inside the SSE generator, and the generator's `finally`
stopped the container. That made two ordinary things impossible. A second
browser could not see a run another browser had started — its Stop button
was missing and its console was empty. And closing a browser killed an
engine the user still wanted up, because "nobody is watching" was being read
as "nobody wants this running".

So the run lives here instead, owned by the backend. A background pump reads
the container's output from the moment the run starts, keeps a rolling
backlog, and fans it out to any number of viewers. Viewers come and go; the
run does not care. It is stopped only by an explicit stop.

* **Two ways a run begins.**
  - **Started** — we launch `docker run` ourselves under a PTY and read the
    PTY master. Preferred, because two things reach the client's stderr and
    *never* appear in `docker logs`: image-pull progress, and daemon-side
    flag errors (`manifest for xyz not found`, a bad device path). That is
    precisely the output an operator needs when a start goes wrong.
  - **Adopted** — a container is already running, started before this
    backend process existed or by another tool. There is no terminal on it,
    so the console comes from `docker logs -f --tail N`. Same interface to
    the caller, same stop path, `adopted: true` in the summary.

* **The pump is the sole writer of terminal state.** Everything that ends a
  run — a crash, an exit, a stop, the container dying on its own — arrives
  as EOF on the read side, so exactly one place decides a run is over and
  records `status: "exited"` with its `exit_code`. `stop()` asks docker to
  stop the container and then *awaits* the pump under `asyncio.shield`, so
  a cancelled HTTP request cannot kill the pump and leave the run stuck
  reporting "running". If the pump is wedged past `RUNNER_STOP_SETTLE`,
  the stop settles the state itself.

* **Detached (`-d`) configurations.** EOF on our own PTY does not
  necessarily mean the run ended — a detached run's client exits straight
  away while the container keeps serving. On EOF the registry checks whether
  the container is still up and, if so, swaps the console source to
  `docker logs -f` rather than declaring the run dead.

* **Fan-out and backpressure.** Each viewer gets a bounded
  `asyncio.Queue(RUNNER_SUBSCRIBER_QUEUE)`. When a viewer's queue is full
  the buffer **drops that queue's oldest chunk** and counts it
  (`dropped_chunks` / `dropped_bytes`), rather than evicting the viewer.
  The distinction matters: Chrome throttles a backgrounded tab to roughly
  one timer tick a second, and a viewer dropped on the first lag would
  never see the rest of the run. Shedding old lines costs a little history;
  eviction loses the console entirely.

* **The console buffer is capped by bytes, not lines**
  (`RUNNER_CONSOLE_BYTES`, default 2 MiB ≈ eight hours of a busy engine's
  access logging). A line-count cap is not a bound at all — chunk sizes
  vary, and one large read could carry the whole budget.

* **The backlog/subscribe race is closed by ordering.** The viewer
  subscribes *before* the backlog snapshot is taken, with no `await`
  between, so anything produced in between lands in the live stream with a
  sequence number above the backlog's. Nothing is lost, nothing is
  duplicated.

* **No locks around the buffer or the subscriber set, deliberately.** Every
  mutating method there is fully synchronous with no `await` inside it, so
  each is atomic with respect to other tasks on the event loop. Each run has
  its own stop lock; the registry serializes start/adopt operations while
  leaving already-running engines alone.

* **Independent concurrent runs.** Starting a run does not stop any existing
  engine. A duplicate container name is rejected; otherwise each engine
  receives its own run ID, output pump, console buffer, stop lock and metrics
  target. Configurations that publish the same host port still conflict at
  Docker/network level and need distinct ports.

* **Reconcile, not a one-shot scan.** Adoption runs at startup *and* on a
  timer (`RUNNER_RECONCILE`, default 20 s), including while other runs are
  active, so discovering one engine never blocks adoption of another. A
  startup-only scan has a hole in it: if the backend restarts while
  `docker run` is still pulling an image, the container does not exist
  yet, nothing adopts it, and the dashboard stays blind to it forever. The
  loop turns adoption from a snapshot into a property the system keeps.

* **Discovery matches on the rendered container name.** Each configuration's
  command is rendered exactly as a real start would render it, the
  `--name` is read out of that result, and docker is asked whether that
  container is up (`docker ps`). Matching against what would actually run,
  rather than a stored string that may have drifted, is the point.

* **Graceful backend shutdown stops every managed run.** The registry stops
  all tracked engine containers concurrently before releasing console pumps.
  `strixper.sh stop` calls the stop-all API first and sweeps saved container
  names as a recovery path. After an ungraceful process kill, startup discovery
  can still adopt any surviving configured containers.

* **Deleting a running configuration is refused** with `409`. It would
  leave a live container with nothing describing it. Stop the run first.

### 4.11 `agent_tools` (`services/agent_tools.py`)

Builds the tool set handed to the agent for a single request.

* `build_tools(state, allow_actions, run_id=None)` → `list[FunctionTool]`.
  A **new list is built per request**, not once at import: the SDK binds the
  enabled/disabled decision and the closure over `state` at decoration time, so
  a module-level list would freeze the permission set forever.

**Read-only tools (always present):**

| Tool | Backing |
| --- | --- |
| `get_live_status` | `build_run_snapshot` for selected run, otherwise default live snapshot |
| `get_engine_health` | Selected run's Halogen client `get_health` |
| `get_engine_metrics` | Selected run's Halogen client `get_metrics` |
| `get_cache_stats` | Selected run's Halogen client `get_cache` |
| `run_tuning_check` | `tuning_checker.run_all_checks` |
| `get_system_info` | `tuning_checker.get_system_info` |
| `count_prompt_tokens` | Selected run's Halogen client `count_tokens` |
| `list_runner_configs` | `runner_store` via `runner.get_store` |
| `get_active_run` | `run_registry.registry` |

**Action tools (registered only when `allow_actions` is true):**

| Tool | Backing | Effect |
| --- | --- | --- |
| `start_engine` | `runner.launch_config(state, config_id)` | Starts a saved run configuration |
| `stop_engine` | `registry.stop(run_id)` | Stops only the requested run |

`stop_engine` looks up the exact `run_id` before stopping; it does not stop
other concurrent engines.

**Every tool returns a JSON string, clipped.** `_clip()` serialises the payload
and truncates it. Raw `/metrics` output is several tens of kilobytes; feeding it
to the model unbounded would consume the context window in one call. Clipping
keeps context growth predictable.

**Why `launch_config` was extracted from `run_config` (§3.3).** The docker-launch
logic previously lived inside the HTTP handler, which meant the only way to start
an engine was to make an HTTP request. `launch_config(state, config_id,
values=None)` is the transport-free core; `run_config` is now a thin wrapper that
maps `ConfigNotFound` → 404, `ValueError` → 400, `RuntimeError` → 502. The
agent calls `launch_config` directly. Behavior of the HTTP endpoint is unchanged.

### 4.12 `agent_exec_tools` (`services/agent_exec_tools.py`)

The **full-access** tier: web research, shell, Python execution, and file I/O.
Registered only when the request sets `full_access`, which also forces
`allow_actions` on.

* `build_exec_tools(state, workspace)` → `list[FunctionTool]`. Same per-request
  construction rule as §4.11 — the workspace is closed over at decoration time.

| Tool | What it does | Bounds |
| --- | --- | --- |
| `fetch_url(url, as_text=True)` | HTTP(S) GET, follows redirects, returns `{status, final_url, content_type, body}` | 20 s timeout, 400 KB cap, HTML→text by default |
| `search_web(query, max_results=8)` | Web search via Brave → DuckDuckGo → Bing; returns `{query, count, results:[{title,url}]}` | 20 s per engine, 1–20 results |
| `run_shell(command, timeout_seconds=120)` | `bash -c` via `asyncio.create_subprocess_exec`, returns `{exit_code, stdout, stderr}` | 120 s default, 900 s max |
| `run_python(code, timeout_seconds=120)` | Writes `{workspace}/agent_{sha256[:12]}.py`, runs it under `python3` | same timeouts, `stdin=DEVNULL` |
| `write_file(path, content, append=False)` | Writes/appends in the workspace | — |
| `read_file(path, max_bytes=60000)` | Reads a file | 60 KB |
| `list_directory(path=".")` | Lists entries with type and size | — |

**The guard is not a sandbox.** `_DENIED_PATTERNS` rejects `rm -rf /`, `mkfs`,
`dd … of=/dev/…`, `shutdown`/`reboot`/`halt`/`poweroff`, `> /dev/sdX`, and
`chmod -R 777 /`. It catches a model doing something catastrophic by accident.
It does nothing about the fact that the container holds `docker.sock`, and it is
deliberately not sold to the user as protection. The real control is the checkbox.

**Why the deny-list is not worth extending.** Every pattern added is a false sense
of security proportional to how complete it looks, and it is not complete:
`python -c "import os; os.system(...)"` bypasses it entirely, and the model has
`run_python`. The guard exists because `rm -rf /` is the one thing a model will
occasionally do unprompted while chasing a permissions error.

**`html_to_text()`** strips comments, `script`/`style`/`noscript`/`svg`/`iframe`,
turns closing block tags into newlines, removes the remaining tags, unescapes
entities, and collapses whitespace line by line. Crude, and adequate for an LLM
reading a page. Comment stripping runs *after* the script/style removal: a JS
block containing `-->` would otherwise break the `</script>` boundary and leak
code into the text.

**`_resolve(path, workspace)`** resolves relative paths inside the workspace and
returns `os.path.realpath`. It is not a jail — an absolute path goes wherever the
process can go. It exists so relative paths behave sensibly, not to confine.

**Output clipping.** Every tool result passes through `_clip()` at 12 KB. A
`cat` of a large file or a verbose build must not blow out the context window.

**Workspace persistence.** `AGENT_WORKSPACE` (default
`/app/backend/agent_workspace`) is mounted as the `strixper-agent-ws` named
volume in `strixper.sh`, so generated scripts and downloads survive a container
recreate. It is gitignored and `.dockerignore`d.

**`search_web` is fragile by construction.** It scrapes search-engine HTML with
a regex; there is no API involved. It walks **Brave → DuckDuckGo → Bing**,
deduplicating by URL, and reports which engines failed. Each engine is tried in
order and the first one to fill the requested count wins, so a rate-limit or a
broken parser on one engine costs a retry, not the request.

Two practical notes from testing:

* **Brave rate-limits.** It returns HTTP 429 under repeated use from the same
  address. The fallback chain covers this; a single-engine implementation would
  not have.
* **Redirect wrappers.** DuckDuckGo returns `//duckduckgo.com/l/?uddg=<encoded>`
  and Bing wraps its targets similarly. `_unwrap_redirect()` decodes the real
  destination so the agent can pass the URL straight to `fetch_url`.

Bing was tried first and returned generic "Strix" matches (security tools,
Wikipedia) regardless of the technical query, which is why it sits last.
Startpage returns a "privacy please" interstitial and Searx `format=json`
returns empty. `fetch_url` does not depend on any of it — give the agent a URL
directly and it is fine.

**TLS is verified by default.** `AGENT_TLS_VERIFY=0` disables it for deployments
behind a TLS-terminating proxy whose CA is absent from the container trust store.
Verified working with verification on against Bing, example.com, and a self-hosted
site, from both the host venv and inside the built image.

---

## 5. Frontend Dashboard Specification

### 5.1 Top Bar

* App name: **Halogen Strix Halo Operations Dashboard**.
* Connection status indicator (green = connected, red = lost), driven by
  `data.connected`.
* **Engine target chip** — shows the address the dashboard is currently
  reading from, plus the name of the LLM-Runner configuration it came from
  (e.g. `⬢ http://127.0.0.1:18080 · GUFO`). Clicking it opens an inline
  editor (input + save/cancel, Enter saves, Escape closes) that POSTs
  `halogen_host` to `/api/v1/config`; a rejected address is shown inline
  rather than silently ignored. The chip is the manual override for engines
  started outside the LLM-Runner — the normal path is the automatic switch
  on run start (see §4.9).
* Auto-refresh selector: `1s`, `5s` (default), `10s`, `30s`, `Off`. Changing it
  updates the local `refetchInterval` **and** POSTs the new
  `poll_interval_seconds` to `/api/v1/config`.
* Manual refresh button.
* Theme toggle (dark default; persisted to `localStorage` under
  `strixper-theme`).
* Tab switcher (Live Server Metrics / Strix Halo Fine-Tuning / AI-Chat /
  LLM-Runner). The URL hash selects the initial tab (`#live`, `#tuning`, `#chat`
  or `#runner`); any other value falls back to `live`.

### 5.2 Tab 1: Live Server Metrics

Layout, top to bottom:

When Runner engines are running, a tab strip selects the engine whose
independent live metrics and history are shown. Host CPU, memory, and GPU
telemetry is shared because all engines use the same machine. With no managed
run, this page displays the configured default engine target.

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
ten tracked series (prompt/decode t/s, GTT GB/%, VRAM %, GPU util %, CPU %,
draft acceptance %, cache hit rate %, cache token hit rate %), with a
**"Reset statistics"** button (calls `POST /api/v1/stats/reset`, then
refetches). Shows "since {started_at} · N samples".

**D. Prompt Cache + Token Counter (2-up grid).**

* **Prompt Cache card** (`CacheStatsCard`) — Halogen prompt-cache telemetry from
  `GET /cache` (surfaced as `halogen.cache`): request hit rate, token hit rate,
  prompt tokens saved, cache entries (live/max), hits, misses, stores, evicted,
  pool usage, cumulative store/restore latency, and refused stores. Header shows
  whether the disk cache tier is on. Has an info modal explaining each measure.
  Renders `—` for any field missing (e.g. when `/cache` is unavailable).
* **Token Counter** (`TokenCounter`) — a textarea + **"Count tokens"** button
  (⌘/Ctrl+Enter also submits) that calls `POST /api/v1/count-tokens` and shows
  the resulting input tokens, character count, and chars/token ratio. Shows an
  inline error on failure.

**E. Dynamic charts (2-up grid).** Each chart card has an **info button**
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

### 5.4 Tab 3: AI-Chat

A classic streaming chat interface (`components/AIChatTab.jsx`) backed by
`POST /api/v1/chat` (§3.3). The whole tab is a single card with three regions:
a controls bar, a scrolling message list, and a composer.

**Controls bar (top).**

* **Engine selector** — lists all running LLM engine runs. Each chat request
  includes the selected `run_id`; if that run stops during a conversation the
  backend rejects the stale selection instead of silently sending the request
  to another engine.
* **Agent** — checkbox (default on). Off, the tab is the classic one-shot chat
  described below. On, every request goes to `POST /api/v1/agent/chat` and the
  agentic loop takes over (§3.3). The API selector is hidden in Agent mode: the
  SDK owns request routing, so the four-style switch no longer applies.
* **Engine selector** — lists each running LLM-Runner run. The selected `run_id`
  is included in plain and agent requests. If that run stops, the backend
  rejects the stale ID instead of silently switching the request to another
  engine.
* **Allow actions** — shown only when Agent is on. Tick it and the agent is
  additionally given `start_engine` and `stop_engine`. Off by default. This is
  enforced by which tools are registered server-side, not by asking the model to
  behave.
* **API selector** — `Chat Completions` (default), `Anthropic Messages`,
  `Responses`, `Text Completions`. Changing it switches the upstream Halogen
  endpoint used for subsequent turns (see the mapping in §3.3). The current
  style and its upstream path are shown in the header subtitle. Hidden in
  Agent mode.
* **Max tokens** — numeric input (1–32,768), sent as `max_tokens`. Hidden in
  Agent mode: the agentic loop uses `AGENT_MAX_TOKENS` instead, because a low
  value truncates `write_file` arguments (§3.3).
* **Thinking** — checkbox (default on). Unchecking sends `thinking: false` so
  the model suppresses its chain-of-thought.
* **Clear conversation** — erases the message list and any error banner.
* **Conversation / Extensions sub-navigation** — Extensions shows the
  discovered skill folders and validation errors, plus the MCP server manager.
  Skills are installed by dropping a folder into `SKILLS_DIR` and rescanning.
  The MCP view requires the administrator token, keeps it in component memory
  only, supports stdio server CRUD/test/status, and never displays resolved
  secret values. Enabling a stdio command requires an explicit warning
  confirmation.

**Message list (middle).** User turns render as right-aligned bubbles; assistant
turns as left-aligned bubbles with an avatar. Each assistant bubble contains:

* A **collapsible reasoning block** (collapsed by default) showing the model's
  chain-of-thought with its character count.
* In Agent mode, a **collapsible Actions panel** (collapsed by default, styled
  like the reasoning block) listing each tool call and a one-line summary of its
  result, in call order.
* The **answer**, rendered as GitHub-flavoured Markdown (including tables and
  fenced code blocks) and streamed live with a blinking caret while generating.
  After generation, a **Source** toggle switches between rendered Markdown and
  the original text. Raw HTML is not enabled in Markdown replies.
* A **per-turn stats line** (after completion): input/output token counts,
  prefill and decode speeds when supplied by the engine, wall-clock seconds,
  and `finish_reason`. In Agent mode the tool-call count is appended.

While a turn is in flight, the composer's Send button becomes a **Stop** button
that aborts the stream via `AbortController`. Errors surface as a red banner
above the composer.

**Composer (bottom).** A textarea with **Enter to send** / **Shift+Enter for a
newline**. Empty input disables Send.

**Client-side streaming.** `streamChat()` in `api.js` uses `fetch` +
`ReadableStream` (not `EventSource`, which cannot POST) and parses the SSE
frames, dispatching `onReasoning` / `onDelta` / `onDone` / `onError` callbacks.
`streamAgentChat()` is the Agent-mode counterpart: same reader, plus an
`onTool` callback for the `tool` events.

**Chat preferences.** Mode, Allow-actions, max tokens, thinking, and API style
are persisted under a separate `localStorage` key (`strixper-chat-settings`)
from the transcript (`strixper-chat`), so **Clear conversation** empties the
messages without resetting how the tab is configured.
For the `completions` style, `createThinkSplitter()` incrementally splits the
raw text on the `<|im_start|>…<|im_end|>` markers so reasoning and
answer render separately, matching the other three styles.

**Conversation persistence.** Switching tabs must **not** discard the chat. The
chat tab is therefore kept mounted at all times and merely hidden with a CSS
`hidden` class when another tab is selected — unmounting it would destroy the
message list, the draft, and any stream in flight. The conversation is also
mirrored to `localStorage` (key `strixper-chat`, capped at the last 100 turns)
so it survives a page reload; restored messages are forced to
`streaming: false`, since a reload interrupts any stream that was running.
**Clear conversation** empties both the list and the stored copy. Because a
hidden element has no layout, the auto-scroll-to-bottom effect also runs when
the tab becomes active again, not only when messages change.

### 5.5 Tab 4: LLM-Runner

A docker run manager (`components/RunnerTab.jsx`) backed by the
`/api/v1/runner/*` endpoints (§3.3). Three functions: **create**, **run** and
**store** the docker command that launches a containerized LLM inference
server. Multiple distinct configurations can run concurrently; each run has an
independent registry entry, console stream, engine URL, and metrics snapshot.
A responsive grid — on `lg`+ screens the config list takes one of three
columns and the form/console take the other two; below `lg` everything stacks to
a single column:

* **Left — Saved configurations.** A heading with a **New** button (opens a
  blank form). Below it, a list of stored configs, each showing its name, its
  description (if any), the docker command template (truncated, full template in
  the `title` tooltip) and — when the config has them — a wrapped row of
  `name=value` chips, one per parameter, each carrying the parameter's label in
  its `title`. Per-config actions: **Run** (▶, primary), **Stop** (■),
  **Edit** (pencil) and **Delete** (trash). While a config is running it shows
  a pulsing `RUNNING` badge, its **Run** and **Delete** buttons are disabled,
  and its **Stop** is enabled; on every other card **Stop** is disabled and
  **Run** is enabled. Edit is always available.

  **Stop lives on the card, not only in the console, and is disabled rather
  than hidden when idle.** A browser that merely *opens* the page — having
  started nothing — must still be able to see which engine is live and stop
  it. Disabled rather than hidden keeps the button positions identical across
  every card so the layout never jumps.
* **Right — Form + Run console.**
  * **Form** (`ConfigForm`): `Name` (≤ 120), `Docker command` (monospace
    textarea, `spellCheck={false}`), the **Run parameters** editor, and optional
    `Description` (≤ 1000). Create mode posts a new config; editing an existing
    one (via the Edit button) pre-fills the form and PUTs the changes. Submit is
    disabled until both name and command are non-blank and no parameter row is
    invalid. An inline error banner shows validation failures (e.g. a duplicate
    name). Editing shows a Cancel control (header `X` and a footer button) that
    reverts to a blank form.
  * **Run parameters editor** (`ParametersEditor`): a list of rows, each with
    `name` (monospace), `Label (optional)` and `value` (monospace) inputs plus a
    **Remove** (trash) button; an **Add parameter** button sits in the section
    header and is disabled at the 40-parameter cap. Rows can be added and removed
    in any order. Each row validates its own name (identifier-shaped, not
    duplicated case-insensitively) and shows the problem inline in red under that
    row. With no parameters the section shows a dashed hint explaining the
    `{name}` syntax.
  * **Live template feedback.** As the command and parameters are edited, the
    form reports: placeholders used by the command with no matching parameter
    (amber warning, listing them — this is what the backend rejects on save), and
    defined parameters the command never references (neutral hint). Both update
    on every keystroke.
  * **Rendered command preview** (`CommandPreview`): a collapsed disclosure that
    expands to show the concrete command the current template + parameter values
    produce, so the exact command line can be checked before running. Hidden when
    the template can't be rendered (blank values).
  * **Run console:** a fixed-height (~420 px) scrolling monospace pane with a
    blinking caret at the end while running. The header shows the run status
    (`Idle` / `Running` (spinner) / `Exited (code)` — green on 0, red
    otherwise), the running config name, its container name, the engine URL the
    dashboard is reading from, and an `adopted` marker when the engine was
    already running rather than launched here. Runtime/stream errors render as
    an inline red banner inside the console.

    **Each running engine gets a console tab.** The UI attaches to every run
    by ID, so a browser that opens onto already-running engines fills each
    console from its backlog instead of showing an empty pane. It does not
    wait for a Run click in that browser.

    Output chunks are buffered in a ref and flushed on a fixed ~100 ms beat
    rather than per chunk, and the rendered string is capped at ~500 KB.
    Appending to a React state string on every chunk is O(length) each time
    and re-renders the whole `<pre>`; at an engine's full logging rate with
    a multi-megabyte backlog that janks visibly. The cap keeps both memory
    and re-render cost flat regardless of how long the run goes.

Like the chat tab, the runner is **kept mounted** (hidden with a CSS `hidden`
class) when another tab is selected, so switching tabs never interrupts a
running container's output stream. The console auto-scrolls to the bottom on new
output and when the tab is re-shown.

**Run state is read, not held.** The tab polls
`GET /api/v1/runner/runs` every ~3 s and derives each configuration's Run/Stop
button state from the complete running set, rather than tracking "did I press
Run" in local state. That makes the buttons correct in a browser that did not
start the runs.

**Client-side streaming.** A shared `readSSE(res, onEvent)` helper in
`api.js` splits the byte stream on blank-line frame boundaries and
dispatches each complete frame; both the runner console and the chat proxy
use it. Around it: `startRunnerRun(configId, values)` (POST, returns the
run summary), `fetchRunnerRuns()` (GET, all running runs),
`fetchRunnerLiveStatus(runId)` (GET, that run's engine snapshot),
`openRunnerStream(runId, handlers)` (GET SSE, dispatching `onState` /
`onStdout` / `onExit` / `onError`), and `stopRunnerRun(runId)`.
`EventSource` is not used anywhere because it cannot POST.

### 5.6 Theme & Color

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

### 5.7 Formatting helpers (`format.js`)

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
      tokens.py             POST /count-tokens (Halogen count_tokens proxy)
      chat.py               POST /chat (streaming chat proxy, 4 API styles)
      agent.py               agent chat, tools, skills, protected MCP APIs
      runner.py             LLM-Runner: config CRUD + start/watch/stop + preview
    services/
      docker_control.py     stop/kill/inspect the container a run owns
      engine_target.py      which engine the dashboard reads; resolve + persist
      halogen_client.py     async Halogen HTTP client + Prometheus parser
      system_monitor.py     sysfs/procfs telemetry (GTT/VRAM/RAM/CPU)
      rocm_monitor.py       GPU util + VRAM via `rocm-smi`
      tuning_checker.py     8-check compliance audit + system info
      live_service.py       MetricStats / StatsTracker / RuntimeState / poll loop
      run_registry.py       the live run: pump, fan-out, adoption, reconcile
      runner_store.py       JSON-persisted store for LLM-Runner docker configs
      params.py             {name} template validation + command rendering
      agent_tools.py        tool functions exposed to the agent (read-only + gated)
      agent_exec_tools.py   full-access tier: web, shell, python, file I/O
      skill_loader.py       safe SKILL.md discovery and bounded resource loading
      mcp_manager.py        persisted MCP config, stdio process/session lifecycle
    data/
      llm_runner_configs.json   runtime store (gitignored)
      engine_target.json        remembered engine target (gitignored)
      mcp_servers.json          persisted MCP server store (gitignored)
  tests/
    test_agent_extensions.py  skill + MCP contract tests (discovery, path escape,
                            secret redaction, PATH resolution, legacy migration,
                            access enforcement, SDK field compatibility)
frontend/
  src/
    App.jsx                 tabs, theme, polling, 120-sample history buffer
    api.js                  fetch helpers (live/config/chat and runner APIs, including fetchRunnerRuns and fetchRunnerLiveStatus) + the shared readSSE frame parser and sendJSON
    theme.js                chart + status color tokens (light/dark)
    format.js               number/percent/time formatters
    params.js               client-side {name} template helpers (placeholders, missing/unused, render, row validation)
    components/
      TopBar.jsx            connection, refresh, theme, tabs
      StatTile.jsx          KPI card with info button
      Meter.jsx             progress bar
      StatusBadge.jsx       PASS/WARN/FAIL badge
      LiveTab.jsx           Tab 1 (KPI cards, stats, cache + token counter, charts)
      StatsSummary.jsx      min/avg/max table + reset button
      CacheStatsCard.jsx    Halogen prompt-cache telemetry card (GET /cache)
      TokenCounter.jsx      on-demand prompt token counter (POST /count-tokens)
      TuningTab.jsx         Tab 2 (banner + audit table)
      AIChatTab.jsx         Tab 3 (streaming chat, reasoning, agent mode, tool steps)
      AgentExtensions.jsx   AI Chat skill discovery and protected MCP manager
      RunnerTab.jsx         Tab 4 (create/run/store docker run configs)
    charts/
      ChartCard.jsx         chart wrapper with info button
      InfoModal.jsx         portal modal (backdrop / X / Escape to close)
      ChartTooltip.jsx      shared tooltip
      ThroughputChart.jsx   Chart A
      MemoryChart.jsx       Chart B
      KvPoolChart.jsx       Chart C
      QueueChart.jsx        Chart D
Dockerfile
mcp-servers/
  README.md                   install-root convention (one dir per server)
  tieline/                    pinned Node MCP server + default Strixper config
skills/
  research-review/SKILL.md example drop-in research workflow
.mcp.json                     repo-root MCP registration for dev agents
.tieline/                     Tieline code-context workspace (compiled topology)
run.sh
stop.sh
build_img.sh
strixper.sh
docker-entrypoint.sh
```

---

## 7. Critical Metric Semantics (do not regress)

These were discovered empirically against the live Halogen engine and drive
several design decisions:

1. **`llamacpp:prompt_tokens_seconds` / `predicted_tokens_seconds` are
   "since last scrape" gauges.** They report a real rate only in the brief window
   right after a request completes, then **decay to 0** while the engine is idle.
   For a single user, most polls read 0. **Consequence:** the top Throughput card
   shows a session average. Prefill statistics use deltas of cumulative
   `prompt_tokens_total` / `prompt_seconds_total` after each completed request,
   because sampling the brief prefill gauge misses short requests. These
   counters are frozen during a request and advance at request completion, so
   they are not a source of instantaneous in-progress throughput.
2. **`prompt_tokens_total` / `tokens_predicted_total` counters are frozen during
   a request** and only advance at request **completion**. Decode retains its
   “since last scrape” gauge; cumulative counter differences are not used as an
   instantaneous decode rate.
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
7. **Routers** — `live`, `tuning`, `config_routes`, `stats_routes`, `tokens`,
    `chat`, `agent`, `runner`, `healthz`.
8. **Frontend scaffold** — Vite + React + Tailwind + TanStack Query + Recharts +
   lucide-react; theme tokens; formatters; `api.js`.
9. **Tab 1** — 4 KPI cards (each with info modal), secondary counters,
   `StatsSummary` with reset, 4 charts (each with info modal), 120-sample
   history buffer.
10. **Tab 2** — system banner + audit table with PASS/WARN/FAIL badges.
11. **Tab 3 (AI-Chat)** — `streamChat` SSE client + `createThinkSplitter` in
    `api.js`; `AIChatTab.jsx` with message list, collapsible reasoning, API
    style switch, thinking toggle, max-tokens input, and stop/clear controls.
12. **Agentic chat** — `agent_tools.py` (read-only tools + `allow_actions`
    gated actions, JSON-clipped returns), `agent.py` router
    (`POST /api/v1/agent/chat`, `GET /api/v1/agent/tools`), `AGENT_MAX_TURNS`
    and `AGENT_INSTRUCTIONS` in `config.py`, `openai-agents` in
    `requirements.txt`, and `streamAgentChat()` in `api.js`. Extract
    `launch_config` / `ConfigNotFound` from `run_config` in `runner.py` so the
    agent and the HTTP route share one launch path. Add the **Agent** and
    **Allow actions** toggles plus the collapsible Actions panel to
    `AIChatTab.jsx`, and persist chat preferences under
    `strixper-chat-settings`.
13. **Full-access tier** — `agent_exec_tools.py` (`fetch_url`, `search_web`,
    `run_shell`, `run_python`, `write_file`, `read_file`, `list_directory`),
    `AGENT_WORKSPACE` and `AGENT_TLS_VERIFY` in `config.py`, the `full_access`
    request field and `_resolve_tools()` tier selection in `agent.py`, the
    `FULL_ACCESS_ADDENDUM` appended to the system prompt, the third **Full
    access** checkbox in `AIChatTab.jsx`, and the `strixper-agent-ws` volume in
    `strixper.sh`. `full_access` forces `allow_actions` on.
14. **Tab 4 (LLM-Runner)** — `RunnerStore` (JSON-persisted config CRUD) +
    `runner.py` router (config CRUD, concurrent runs, per-run SSE output,
    per-run metrics and stop-all); `RunnerTab.jsx` with the config list,
    create/edit form, and one live console tab per running engine. AI-Chat
    selects a running engine and sends its run ID for both plain and agent
    requests. Keep the tabs mounted so runs and streams survive tab switches.
15. **Packaging and deployment** — multi-stage `Dockerfile`: Node 22 builds
    the frontend, AMD `rocm/dev-ubuntu-24.04:7.2.2` supplies ROCm SMI and the
    Python 3.12 runtime, and FastAPI serves the production frontend. Include
    the Docker CLI, healthcheck, and `docker-entrypoint.sh`.
16. **Container lifecycle wrapper** — `strixper.sh` builds no images itself;
    it starts/stops/restarts/recreates/status-checks the dashboard and manages
    configured engine containers. It passes host networking, Docker socket,
    persistent `strixper-data` volume, `/dev/kfd`, `/dev/dri`, and host
    `video`/`render` group IDs. Stop must request engine shutdown through the
    Runner stop-all API before stopping Strixper, including discovery of
    configured engines after an ungraceful dashboard shutdown. Graceful backend
    shutdown must also stop all registered engine containers.
17. **Local workflow and docs** — `run.sh` starts backend + Vite for
    development; `stop.sh` stops those local processes. README documents
    prerequisites, local and Docker quick starts, LAN/security behavior,
    configuration, and credits.
18. **Verify** — `cd frontend && npm run build`; validate backend syntax and
    routes; confirm concurrent starts, per-run console/metrics, chat engine
    routing, individual stop, and stop-all. For Docker acceptance, build and
    run the container; confirm its healthcheck, frontend assets, ROCm SMI/GPU
    access, and managed shutdown.
19. **Skills** — `skill_loader.py` (strict `SKILL.md` validation, size ceilings,
    path-confined resource reads, `list_skills` / `load_skill` /
    `read_skill_resource`), `SKILLS_DIR` in `config.py`, `GET /api/v1/agent/skills`,
    the skill tools registered in every tier, and the example
    `skills/research-review/SKILL.md`. Skills are discovered at request time, so
    adding a folder requires no rebuild — only a rescan from the UI.
20. **MCP extensions** — `mcp_manager.py` (versioned atomic store, secret
    references, lazy connect, per-server call/update locks, install-root `PATH`
    composition, schema sanitization, SDK field compatibility, caps and
    timeouts), the admin-token-guarded CRUD/test endpoints in `agent.py`,
    `MCP_ADMIN_TOKEN` / `MCP_SERVERS_DIR` / `MCP_CALL_TIMEOUT_SECONDS` in
    `config.py`, the `mcp-server-runtime` Docker stage plus the
    `strixper-mcp-servers` volume, the `mcp-servers/` install root with its
    README, and the **Extensions** sub-nav in AI Chat.
21. **Extension tests** — `backend/tests/test_agent_extensions.py` covers skill
    discovery, path-escape rejection, manifest loading, secret persistence and
    redaction, non-inheritance of `MCP_ADMIN_TOKEN` by the child process,
    install-root `PATH` resolution, legacy `npx` migration, disable/delete,
    access-tier enforcement, duplicate and invalid IDs, recursive `default`
    removal from schemas, tool wrapping, snake_case SDK fields, and the
    management-disabled / token-header paths.

---

### 8.1 Testing strategy for extensions

Extension code is untrusted-input handling, so the tests assert the *contract*
rather than the happy path:

* **Discovery is total.** Every valid folder is listed and every invalid folder
  produces a specific error naming the file and the reason. A single bad skill
  must not hide the good ones.
* **Path confinement is tested with an actual escape attempt**, not by inspection.
* **Secrets never round-trip through the API.** Write a `secretRef`, read the
  store back, assert the value is present in the file and absent from the
  response body.
* **Environment isolation is asserted from the child's point of view**: a tool
  that echoes `PATH` proves the install-root prefix arrived, and a probe for
  `MCP_ADMIN_TOKEN` proves it did not.
* **Migration is tested against the pre-migration shape** so an upgrade cannot
  silently drop a working server.
* **Tier enforcement is tested per tier**, including the negative case: a tool at
  `actions` must be absent from a `read-only` request.
* **Schema sanitization** is tested with a schema containing `default` — the
  keyword that a real engine (GUFO) rejects — and asserts the converted schema
  is accepted recursively.

Run them with `cd backend && .venv/bin/python -m pytest -q`. They need no
network, no GPU, and no running engine.

---

### 8.2 Deferred / future extensions

Recorded so they are not rediscovered as "missing":

* **Hot reload of `skills/`.** Skills are rescanned per request today; a file
  watcher would remove even the rescan step. Not implemented because the rescan
  is cheap and explicit refresh is easier to reason about.
* **Skill marketplace / git install.** Pulling skills from a remote repository
  with signature verification. Requires a trust story that does not exist yet.
* **MCP Streamable HTTP transport.** The manager is stdio-only. Remote servers
  need authentication, TLS, and reconnect semantics that stdio inherits from the
  process boundary and HTTP does not.
* **Per-tool allow/deny lists.** `minimum_access` is per server today; a
  fine-grained per-tool policy is the natural next step once there is demand.
* **Skill-declared tools.** Skills currently carry instructions only. Letting a
  skill declare an executable tool would turn it into a plugin and would need
  the same trust model as MCP.

---

## 9. Docker Deployment and Lifecycle Contract

### 9.1 Image build and runtime

The root `Dockerfile` is a multi-stage production image:

1. `node:22-alpine` builds the React frontend into `frontend/dist`.
2. `node:22-bookworm-slim` (`mcp-server-runtime`) installs each bundled Node.js
   MCP server under `/mcp-servers` with `npm ci --omit=dev`. Keeping this in its
   own stage means the Node toolchain and its build-time cache never reach the
   runtime image; only the resulting `/mcp-servers` tree and the Node runtime
   under `/usr/local` are copied out.
3. `rocm/dev-ubuntu-24.04:7.2.2` is the runtime base. It provides Ubuntu 24.04,
   Python 3.12, ROCm userspace including `rocm-smi`, and the utilities needed
   by the backend. Python dependencies are installed in `/opt/venv`.
4. The runtime copies the Docker CLI from `docker:cli` and the two
   `mcp-server-runtime` outputs (`/usr/local`, `/mcp-servers`), installs
   `backend/requirements.txt`, copies the backend and frontend build, and uses
   `docker-entrypoint.sh` to seed the initial MCP config if needed and `exec`
   Uvicorn as PID 1.

The image binds `BIND_HOST=0.0.0.0`, `BIND_PORT=8000` by default. Its Docker
healthcheck polls `/api/v1/healthz`; this is backend liveness and does not imply
that a Halogen engine is running or reachable.

**Volume seeding caveat.** `/mcp-servers` is backed by the named volume
`strixper-mcp-servers`. Docker populates a *fresh* volume from the image
content, but a volume that already contains data is never refreshed by a newer
image. After changing a bundled MCP package, remove the volume
(`docker volume rm strixper-mcp-servers`) or the container will keep running the
old tree.

### 9.2 Host runtime requirements

The dashboard container relies on host facilities and must be created with:

* `--network=host` so dashboard-managed inference containers published on
  loopback remain reachable at `127.0.0.1`, and the dashboard is reachable on
  the host's LAN interfaces.
* `/var/run/docker.sock` mounted into the container for the LLM-Runner Docker
  CLI. Access to this socket grants broad control over the host Docker daemon.
* `/dev/kfd` and `/dev/dri` devices and the host numeric `video` and `render`
  group IDs so `rocm-smi` can query the GPU.
* `/etc/group` read-only for engine commands that resolve the host `render`
  group.
* The named `strixper-agent-ws` volume mounted at `/app/backend/agent_workspace`
  so the full-access tier's downloads and generated scripts survive a recreate.
* The named `strixper-data` volume mounted at `/app/backend/data` to preserve
  Runner configs, the remembered engine target, and non-secret MCP server
  configuration across container removal.
* The named `strixper-mcp-servers` volume mounted at `/mcp-servers` for
  per-server MCP packages and binaries. Tieline is installed beneath
  `/mcp-servers/tieline`; install future server packages under a subdirectory
  matching their configured MCP server ID.
* The repository `skills/` directory mounted read-only at `/app/skills`.
  Add a child folder with `SKILL.md` on the host and rescan in AI Chat →
  Extensions to discover it without rebuilding the image.
* The repository root mounted read-only at `/workspace/strixper` for Tieline's
  local code-context MCP server; `TIELINE_WORKSPACE` points to this mount.
* `MCP_ADMIN_TOKEN` passed from the host environment (or `MCP_ENV_FILE`
  supplied to `strixper.sh`) when enabling protected MCP administration.

The provided `strixper.sh` resolves host GPU group IDs and sets these runtime
options when creating the container. With host networking, Docker port
publishing (`-p`) is not used; configure the host firewall separately for LAN
access. The HTTP dashboard has no authentication and should not be exposed to
untrusted networks.

### 9.3 `strixper.sh` lifecycle actions

The repository-level `strixper.sh` is the supported Docker stack controller:

| Action | Contract |
| --- | --- |
| `start` | Start the existing named container, or create it from `STRIXPER_IMAGE` (default `strixper-dashboard:latest`). Wait for `/api/v1/healthz`. |
| `stop` | Ensure the backend is available, stop all Runner runs using the Runner API, render saved config commands to discover their `--name` container names, stop any still-running matching containers, then stop Strixper. |
| `restart` | Perform managed shutdown followed by start. |
| `recreate` | Perform managed shutdown, remove the dashboard container, and recreate it while retaining `strixper-data`. |
| `status` | Report container status and every currently running engine. |

If Strixper was already stopped, `stop` starts it temporarily so startup
discovery can adopt running containers whose names match saved configurations.
Failures to stop managed engines are surfaced, and the dashboard is left
running rather than hiding an incomplete shutdown.

The wrapper manages configured Runner engine containers, not arbitrary Docker
containers. A container started manually or through a config that was removed
before shutdown may not be discoverable from the saved configuration and is
outside this cleanup contract. A graceful direct Docker stop also triggers
backend cleanup, but use `./strixper.sh stop` for the explicit API call and
container-name recovery sweep.

### 9.4 Local development versus Docker operation

`run.sh` remains the local development workflow: it runs FastAPI and the Vite
development server concurrently. `stop.sh` stops those local processes only;
it does not stop Docker-managed inference containers. Use `strixper.sh` for the
Docker dashboard and its configured engine lifecycle.

---

## 10. Credits

This project builds on open-source software and community documentation.
Special thanks to:

* [kyuz0/amd-strix-halo-toolboxes](https://github.com/kyuz0/amd-strix-halo-toolboxes)
  for Strix Halo ROCm tooling and system guidance.
* [peonist-ai/halogen-flash-server](https://github.com/peonist-ai/halogen-flash-server)
  for the Halogen inference server.
* [gufo-org/gufo](https://github.com/gufo-org/gufo)
  for the GUFO project and related Strix Halo work.

Additional thanks to the [OpenAI Agents SDK](https://openai.github.io/openai-agents-python/)
team, whose Python SDK powers the agentic chat, and to the React, Vite,
FastAPI, Uvicorn, TanStack Query, Tailwind CSS, Recharts, lucide-react,
Docker, and ROCm communities, and to the authors of the Halogen API and
Strix Halo tuning references used by this project.
