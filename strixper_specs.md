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
   `/metrics`, `/v1/models` and `/cache` at a configurable interval (default
   **5 s**) and merges them with local hardware telemetry into one snapshot.
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
8. **LLM-Runner** — a tab to create, run and locally store the docker command
   used to launch a containerized LLM inference server. Configurations are kept
   in a JSON file on disk, listed for selection, editable, and each can be
   started with a button that streams the container's live output; a running
   container can be stopped on demand. See §3.3 (runner endpoints), §4.6
   (`RunnerStore`) and §5.5 (Tab 4).

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
| `/api/v1/count-tokens` | `POST` | On-demand (widget) | Count the input tokens of a prompt (proxies Halogen) |
| `/api/v1/chat` | `POST` | On-demand (AI-Chat tab) | Streaming chat completion (proxies Halogen, 4 API styles) |
| `/api/v1/runner/configs` | `GET` | On-demand (LLM-Runner tab) | List stored docker run configurations |
| `/api/v1/runner/configs/{id}` | `GET` | On-demand | Fetch one stored configuration |
| `/api/v1/runner/configs` | `POST` | On-demand (LLM-Runner tab) | Create a new run configuration |
| `/api/v1/runner/configs/{id}` | `PUT` | On-demand (LLM-Runner tab) | Edit an existing configuration |
| `/api/v1/runner/configs/{id}` | `DELETE` | On-demand (LLM-Runner tab) | Delete a configuration |
| `/api/v1/runner/configs/{id}/run` | `POST` | On-demand (LLM-Runner tab) | Start the rendered docker command (SSE stream) |
| `/api/v1/runner/preview` | `POST` | On-demand (LLM-Runner tab) | Render a command template against its parameters, without running |
| `/api/v1/runner/runs/{run_id}/stop` | `POST` | On-demand (LLM-Runner tab) | Stop a running docker command |
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

**`POST /api/v1/runner/configs/{id}/run`** — renders the stored
`docker_command` against the configuration's parameter values, launches the
resulting command as a shell subprocess, and streams its merged
stdout/stderr back as `text/event-stream` (same SSE framing as
`/api/v1/chat`):

| Event | Data | Meaning |
| --- | --- | --- |
| `started` | `{"run_id": "…", "command": "…"}` | Process launched |
| `stdout` | `{"text": "…"}` | An output chunk (stdout + stderr merged) |
| `exit` | `{"exit_code": 0}` | Process terminated |
| `error` | `{"message": "…"}` | Failed to start / stream failure |

The `command` in the `started` event is the **rendered** command — the
template with every `{placeholder}` already replaced — so what actually ran
is always visible in the stream.

An optional body `{ "values": { "port": "9000", … } }` overrides individual
parameter values for this run only; nothing is written back to the store.
Override keys must be parameters the configuration already declares — an
unknown key is a `400` rather than a silent no-op, so a typo cannot quietly
leave the stored value in place. `400` is also returned if a parameter the
template needs ends up blank.

The subprocess is launched attached to a **pseudo-terminal (PTY)** so the
command sees a real TTY. This is required for TTY-allocating docker flags
(`-t` / `-it`); without a PTY Docker aborts with *"cannot attach stdin to a
TTY-enabled container because stdin is not a terminal"*. The PTY emits CRLF,
which the backend normalises to LF before streaming. The run is assigned a
`run_id` (sent in the `started` event). The process is terminated
automatically when the client disconnects. `404` if the config is unknown;
`502` if the command cannot be launched.

**`POST /api/v1/runner/runs/{run_id}/stop`** — sends `SIGTERM` to the running
process (escalating to `SIGKILL` after a 5 s grace period). Returns
`{ "stopped": "<run_id>" }`, or `404` if the run is not active.

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
| `tokens_predicted_total` | `llamacpp:tokens_predicted_total` |
| `prompt_tokens_cached_total` | `halogen:prompt_tokens_cached_total` |
| `draft_acceptance_rate` | `halogen:draft_tokens_accepted_total / halogen:draft_tokens_total` (rounded 4dp) |
| `cache` | `GET /cache` payload passed through verbatim (empty `{}` if unavailable) |

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
`stats` (StatsTracker), `model_name` (served model id, refreshed from `/health`
on every poll and used by `/api/v1/count-tokens`).

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
* Tab switcher (Live Server Metrics / Strix Halo Fine-Tuning / AI-Chat /
  LLM-Runner). The URL hash selects the initial tab (`#live`, `#tuning`, `#chat`
  or `#runner`); any other value falls back to `live`.

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

* **API selector** — `Chat Completions` (default), `Anthropic Messages`,
  `Responses`, `Text Completions`. Changing it switches the upstream Halogen
  endpoint used for subsequent turns (see the mapping in §3.3). The current
  style and its upstream path are shown in the header subtitle.
* **Max tokens** — numeric input (1–32,768), sent as `max_tokens`.
* **Thinking** — checkbox (default on). Unchecking sends `thinking: false` so
  the model suppresses its chain-of-thought.
* **Clear conversation** — erases the message list and any error banner.

**Message list (middle).** User turns render as right-aligned bubbles; assistant
turns as left-aligned bubbles with an avatar. Each assistant bubble contains:

* A **collapsible reasoning block** (collapsed by default) showing the model's
  chain-of-thought with its character count.
* The **answer text**, streamed in live with a blinking caret while generating.
* A **per-turn stats line** (after completion): input/output token counts,
  decode speed (tok/s), wall-clock seconds, and `finish_reason`.

While a turn is in flight, the composer's Send button becomes a **Stop** button
that aborts the stream via `AbortController`. Errors surface as a red banner
above the composer.

**Composer (bottom).** A textarea with **Enter to send** / **Shift+Enter for a
newline**. Empty input disables Send.

**Client-side streaming.** `streamChat()` in `api.js` uses `fetch` +
`ReadableStream` (not `EventSource`, which cannot POST) and parses the SSE
frames, dispatching `onReasoning` / `onDelta` / `onDone` / `onError` callbacks.
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
server. A responsive grid — on `lg`+ screens the config list takes one of three
columns and the form/console take the other two; below `lg` everything stacks to
a single column:

* **Left — Saved configurations.** A heading with a **New** button (opens a
  blank form). Below it, a list of stored configs, each showing its name, its
  description (if any), the docker command template (truncated, full template in
  the `title` tooltip) and — when the config has them — a wrapped row of
  `name=value` chips, one per parameter, each carrying the parameter's label in
  its `title`. Per-config actions: **Run** (▶, primary), **Edit** (pencil) and
  **Delete** (trash, right-aligned). While a config is running it shows a
  pulsing `RUNNING` badge and its **Run** and **Delete** buttons are disabled
  (Edit stays available). The empty state shows a terminal icon with a short
  prompt to create a configuration.
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
  * **Run console:** a fixed-height (~420 px) scrolling monospace pane. Pressing
    **Run** starts the configured command and streams its live output in,
    appending chunks as they arrive with a blinking caret at the end while
    running. The header shows the run status (`Idle` / `Running` (spinner) /
    `Exited (code)` — green on 0, red otherwise) and a **Stop** button while
    running. The footer shows the finish time and exit code after completion.
    Runtime/stream errors render as an inline red banner inside the console.

Like the chat tab, the runner is **kept mounted** (hidden with a CSS `hidden`
class) when another tab is selected, so switching tabs never interrupts a
running container's output stream. The console auto-scrolls to the bottom on new
output and when the tab is re-shown.

**Client-side streaming.** `streamRunnerRun()` in `api.js` uses `fetch` with a
`ReadableStream` reader (an `EventSource` cannot POST) and parses the SSE frames,
dispatching `onStarted` / `onStdout` / `onExit` / `onError` callbacks. **Stop**
aborts the in-flight `AbortController` *and* calls
`POST /api/v1/runner/runs/{run_id}/stop`; the `run_id` is captured from the
`started` event.

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
      runner.py             LLM-Runner: config CRUD + run (SSE) + stop + preview
    services/
      halogen_client.py     async Halogen HTTP client + Prometheus parser
      system_monitor.py     sysfs/procfs telemetry (GTT/VRAM/RAM/CPU)
      rocm_monitor.py       GPU util + VRAM via `rocm-smi`
      tuning_checker.py     8-check compliance audit + system info
      live_service.py       MetricStats / StatsTracker / RuntimeState / poll loop
      runner_store.py       JSON-persisted store for LLM-Runner docker configs
      params.py             {name} template validation + command rendering
    data/
      llm_runner_configs.json   runtime store (gitignored)
frontend/
  src/
    App.jsx                 tabs, theme, polling, 120-sample history buffer
    api.js                  fetch helpers (fetchLiveStatus, postConfig, resetStats, countTokens, streamChat, createThinkSplitter, and the runner client: fetchRunnerConfigs, createRunnerConfig, updateRunnerConfig, deleteRunnerConfig, previewRunnerCommand, streamRunnerRun, stopRunnerRun, sendJSON)
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
      AIChatTab.jsx         Tab 3 (streaming chat, reasoning, API style switch)
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
7. **Routers** — `live`, `tuning`, `config_routes`, `stats_routes`, `tokens`,
    `chat`, `runner`, `healthz`.
8. **Frontend scaffold** — Vite + React + Tailwind + TanStack Query + Recharts +
   lucide-react; theme tokens; formatters; `api.js`.
9. **Tab 1** — 4 KPI cards (each with info modal), secondary counters,
   `StatsSummary` with reset, 4 charts (each with info modal), 120-sample
   history buffer.
10. **Tab 2** — system banner + audit table with PASS/WARN/FAIL badges.
11. **Tab 3 (AI-Chat)** — `streamChat` SSE client + `createThinkSplitter` in
    `api.js`; `AIChatTab.jsx` with message list, collapsible reasoning, API
    style switch, thinking toggle, max-tokens input, and stop/clear controls.
12. **Tab 4 (LLM-Runner)** — `RunnerStore` (JSON-persisted config CRUD) +
    `runner.py` router (config CRUD, run with SSE output stream, stop);
    `RunnerTab.jsx` with the config list, create/edit form, and live run
    console with stop control. Keep the tab mounted so a run survives tab
    switches.
13. **Packaging** — `Dockerfile` (multi-stage: build frontend, serve from
    FastAPI), `run.sh` (dev: backend + Vite concurrently), `stop.sh`.
14. **Verify** — `cd frontend && npm run build`; restart backend; confirm
    live-status, stats reset, tuning checks, all info modals, and the
    LLM-Runner create/run/stop flow render.
