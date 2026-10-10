# Halogen Strix Halo Operations Dashboard
![AMD Strix Halo Logo](AMD_Strix_Halo_logo.png)

A real-time web dashboard for monitoring a running **Halogen LLM Server** alongside
host and hardware telemetry on an **AMD Strix Halo** (Ryzen AI Max+ 395 / Radeon
8060S) machine, with an on-demand fine-tuning compliance audit and a built-in
streaming **AI chat**.

Built from `strixper_specs.md`, using `Halogen_REST_API.md` for the Halogen
endpoint contracts and `strix_halo_finetuning.md` for the tuning recommendations.

## Table of Contents

- [Quick Start](#quick-start)
- [Prerequisites](#prerequisites)
- [What it does](#what-it-does)
- [Architecture](#architecture)
- [Local Development and Run Modes](#local-development-and-run-modes)
- [Configuration](#configuration)
- [Changing ports](#changing-ports-port-conflicts)
- [API](#api)
- [Agentic AI Chat](#agentic-ai-chat)
- [Docker quick start](#docker-quick-start)
- [Docker Deployment Details](#docker-deployment-details)
- [Project layout](#project-layout)
- [Tuning checks](#notes-on-the-tuning-checks)
- [Credits](#credits)

## Quick Start

Choose Docker for a self-contained dashboard image, or run the frontend and
backend directly for development.

### Run with Docker

From the repository root, build the image and start Strixper:

```bash
./build_img.sh
./strixper.sh start
```

Open **http://localhost:8000** on the host. To access it from another device,
use the host's LAN IP, for example `http://192.168.1.172:8000`. Stop Strixper
and its configured LLM engine containers together with:

```bash
./strixper.sh stop
```

The start script uses host networking, the Docker socket, a persistent
`strixper-data` volume, and host GPU devices/groups for ROCm telemetry and the
LLM Runner. See [Docker Deployment Details](#docker-deployment-details) before
exposing the unauthenticated dashboard to a network.

### Run locally

With Python 3.11+ and Node.js 20+ installed:

```bash
./run.sh
```

Open **http://localhost:5173**. The development server runs the API on port
8000 and the Vite frontend on port 5173. Stop both local processes with
**Ctrl+C** or `./stop.sh`. Locally, GPU telemetry requires host ROCm tools such
as `rocm-smi` to be installed.

## Prerequisites

### Dashboard

- **Docker mode:** Linux Docker Engine, permission to use the Docker daemon,
  and the host's `/dev/kfd` and `/dev/dri` devices. The `video` and `render`
  host groups must exist; `strixper.sh` resolves their numeric GIDs.
- **Local mode:** Python 3.11 or newer, Node.js 20 or newer, npm, and the
  dependencies installed by `run.sh`.
- **AMD GPU telemetry:** an AMDGPU-enabled Linux host. The Docker image is
  based on AMD's ROCm 7.2.2 Ubuntu 24.04 image and includes `rocm-smi`; runtime
  device access is supplied by the Docker start script.
- **Network:** TCP port 8000 for the backend (and 5173 for the local Vite
  development server) must be available. Allow the selected port through the
  host firewall for LAN access.

### LLM inference engine

The dashboard does not include an inference engine. It monitors and supervises
one that runs separately, and it is engine-agnostic about which one: Halogen,
GUFO, or a `llama.cpp` `llama-server` all work, as long as the container
exposes an HTTP endpoint the dashboard can reach.

What each engine provides differs, and the dashboard degrades gracefully rather
than failing:

| Engine | Metrics namespace | `/cache` | `/v1/messages/count_tokens` |
| --- | --- | --- | --- |
| **Halogen** | `halogen:` + `llamacpp:` | Yes | Yes |
| **GUFO** | `llamacpp:` only | No | Yes |
| **llama.cpp** | `llamacpp:` only | No | Not provided |

Throughput, KV-cache usage, and token totals come from the `llamacpp:` metrics
that all three can expose, so those charts work on any engine when metrics are
enabled. **Stock `llama-server` disables `/metrics` by default**; its launch
command must include `--metrics` (as the bundled ROCm10 Gemma configuration
does). The `halogen:` counters and the `/cache` endpoint are Halogen-only and
read as absent (`None` / `{}`) elsewhere — the dashboard degrades to the
available fields rather than erroring.

One asymmetry worth knowing: GUFO reports `llamacpp:prompt_tokens_cached_total`,
but the dashboard reads cached-token counts from `halogen:prompt_tokens_cached_total`.
On GUFO that field therefore shows as unavailable even though the number exists.

Configure a runnable engine in the **LLM-Runner** tab. See
[strixper_prerequisites.md](./strixper_prerequisites.md) for the host, kernel,
firmware, GPU driver, and engine/model setup checklist.
Runner commands must give each container a unique Docker `--name`; Strixper
uses that name to adopt, monitor, and reliably stop each engine.

## What it does

- **Live Server Metrics** — provides one selectable tab per running engine and
  polls that engine's `/health`, `/metrics` and
  `/v1/models` and reads local sysfs/procfs telemetry, rendering KPI cards and
  time-series charts (token throughput, KV pool, queue depth, host memory). The
  top **Throughput** card shows the **session average** prefill/decode rate
  rather than the instantaneous gauge, because the per-second throughput gauge
  reads 0 whenever the engine is idle.
- **Parallel LLM engines** — start multiple distinct Runner configurations at
  once. Each running engine has its own live console and metrics snapshot;
  stopping one leaves the others running. The chat engine selector targets a
  specific run.
- **GPU Monitoring (rocm-smi)** — reads live GPU compute utilization and VRAM
  usage via `rocm-smi` and plots them alongside GTT and RAM in the memory chart.
  rocm-smi is the authoritative source: it reports higher VRAM usage than raw
  sysfs because it counts driver-reserved allocations.
- **Session Statistics** — a min / avg / max table (prompt & decode token rate,
  GTT / VRAM usage, GPU utilization, CPU utilization, draft acceptance)
  accumulated for the selected engine since it was first polled (the default
  target falls back to server-start time), with a **Reset statistics** button
  that resets every engine's counters and restarts their clocks. For throughput,
  utilization, and acceptance series (prompt/decode t/s, GPU utilization,
  CPU utilization, draft acceptance) **zero readings are excluded** from both
  the average and the minimum — a 0 means "idle / not reporting yet", not a
  real measurement of zero.
- **Prompt Cache & Token Counter** — a card with Halogen's prompt-cache telemetry
  (hit rate, tokens saved, stores/evictions, pool usage) and a widget that counts
  the exact token cost of any prompt before you send it.
- **AI Chat** — a streaming chat tab with an engine selector for choosing among
  running engines, and two modes. **Plain** is a classic
  one-shot chat: type a request, watch the answer stream in token-by-token, and
  expand the model's chain-of-thought under each reply. Switch between
  Halogen's four inference API styles (**Chat Completions**, **Anthropic
  Messages**, **Responses**, **Text Completions**) to compare them, toggle the
  model's thinking on/off, and cap the response length. **Agent** turns the
  same box into a tool-using assistant: it can inspect live metrics, run the
  tuning audit, and (only if you explicitly allow it) start and stop engines.
  See [Agentic AI Chat](#agentic-ai-chat).
- **In-context Help** — every chart and every top KPI card has an **info button**
  (ⓘ, top-right corner) that opens a detailed explanation of the measures shown
  there.
- **Strix Halo Fine-Tuning** — runs a shell-based compliance audit (kernel,
  IOMMU, GTT/TTM boot params, GTT size, rocm-smi, firmware, TuneD profile, udev
  rules) and shows a PASS / WARN / FAIL table with the measured impact of each
  item.

## Architecture

```
Browser (React + Vite + Tailwind + TanStack Query + Recharts)
        |  REST (JSON over HTTP) + SSE (streaming chat)
FastAPI backend (asyncio)
  - HalogenClient   -> httpx -> Halogen /health /metrics /v1/models /cache
                    -> POST /v1/chat/completions /v1/messages /v1/responses /v1/completions
  - SystemMonitor   -> sysfs / procfs (GTT, RAM, CPU)
  - TuningChecker   -> shell commands (uname, tuned-adm, ls, ...)
        |
Halogen engine + Linux host
```

## Local Development and Run Modes

Pick the mode that matches how you want to use it.

### 1. Development (local only, hot reload)

```bash
./run.sh
```

Creates the Python venv, installs backend and frontend dependencies, and runs
the FastAPI backend (`:8000`) and the Vite dev server (`:5173`) concurrently.
Open **http://localhost:5173**. Frontend edits hot-reload; restart the script to
pick up backend changes.

### 2. Production, single port (local only)

Build the frontend once, then serve everything (UI + API) from FastAPI on one
port:

```bash
cd frontend && npm install && npm run build && cd ..
cd backend && .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Open **http://localhost:8000**.

### 3. Exposed on the local network (LAN)

Same as mode 2, but bind to all interfaces so other devices on your network can
reach it:

```bash
cd frontend && npm install && npm run build && cd ..
cd backend && .venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Find this machine's LAN address:

```bash
hostname -I        # e.g. 192.168.1.172  (ignore the 172.17.x Docker bridge)
```

Other devices open **http://192.168.1.172:8000** (substitute your own IP).

If a firewall is active, open the port:

```bash
# ufw
sudo ufw allow 8000/tcp
# firewalld
sudo firewall-cmd --permanent --add-port=8000/tcp && sudo firewall-cmd --reload
```

> **Security:** the dashboard has **no authentication** and CORS is `*`. This is
> fine on a trusted home LAN, but anyone on the network can view the metrics and
> reach the Halogen API. On an untrusted network, keep it bound to `127.0.0.1`
> and reach it over an SSH tunnel instead (mode 4).

### 4. Remote access over an SSH tunnel (secure)

Run the dashboard bound to localhost only (mode 2), then forward the port from
another machine:

```bash
ssh -L 8000:localhost:8000 briggen@192.168.1.172
```

Open **http://localhost:8000** on your local machine. Nothing is exposed to the
network beyond SSH itself.

### Stopping the servers

If you started with `./run.sh`, just press **Ctrl+C** in that terminal — it shuts
down both the backend and the frontend.

To stop them from another terminal, or if they were detached:

```bash
./stop.sh
```

This finds the processes listening on the backend (`8000`) and frontend (`5173`)
ports and terminates them (SIGTERM, then SIGKILL after 5s). If you changed the
ports, pass them through so the script looks at the right ones:

```bash
BIND_PORT=8010 FRONTEND_PORT=5174 ./stop.sh
```

To check what is still listening:

```bash
ss -tlnp | grep -E ':(8000|5173)'
```

## Configuration

Copy `backend/.env.example` to `backend/.env` and adjust:

| Variable | Default | Meaning |
| --- | --- | --- |
| `HALOGEN_HOST` | `http://127.0.0.1:8731` | Halogen server base address |
| `POLL_INTERVAL_SECONDS` | `5` | Background poll cadence |
| `BIND_HOST` | `0.0.0.0` | Backend bind host |
| `BIND_PORT` | `8000` | Backend bind port |
| `AGENT_MAX_TURNS` | `25` | Max agent turns before the run is aborted |
| `AGENT_MAX_TOKENS` | `8192` | Per-model-call output cap (see the note on tool-call truncation) |
| `AGENT_INSTRUCTIONS` | built-in | Optional override of the agent system prompt |
| `AGENT_WORKSPACE` | `backend/agent_workspace` | Where the full-access tier writes files |
| `AGENT_TLS_VERIFY` | `1` | TLS verification for `fetch_url` / `search_web` |

The polling interval can also be changed at runtime from the dashboard's
auto-refresh selector (synced to the backend via `POST /api/v1/config`).

## Changing ports (port conflicts)

Two ports are involved:

| Component | Default | Controlled by |
| --- | --- | --- |
| Backend (uvicorn) | `8000` | `BIND_PORT` env / `--port` flag |
| Frontend (Vite dev) | `5173` | `FRONTEND_PORT` env / `--port` flag |

**With `run.sh` (dev)** — set both as environment variables:

```bash
BIND_PORT=8010 FRONTEND_PORT=5174 ./run.sh
```

`run.sh` also points the Vite `/api` proxy at the new backend port automatically,
so the frontend keeps talking to the backend without extra config.

**Backend only (production single-port):**

```bash
cd backend && .venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8010
```

**Frontend dev server only (without `run.sh`):**

```bash
cd frontend
BACKEND_URL="http://127.0.0.1:8010" npm run dev -- --host --port 5174
```

`BACKEND_URL` tells the Vite proxy where the backend lives — set it whenever the
backend is **not** on the default `8000`.

**Permanent defaults:** set `BIND_HOST` / `BIND_PORT` in `backend/.env`. For the
frontend, edit `server.port` and the proxy `target` in `frontend/vite.config.js`.

**Docker:** the dashboard runs with `--network=host`, so `BIND_PORT` *is*
the host port. Change it when recreating the container with
`BIND_PORT=8010 ./strixper.sh recreate` (see the Docker quick start below).

**Finding a free port:**

```bash
ss -tlnp | grep LISTEN          # see what's already taken
```

## API

| Endpoint | Method | Description |
| --- | --- | --- |
| `/api/v1/live-status` | GET | Aggregated Halogen + hardware snapshot (includes `stats`) |
| `/api/v1/tuning-check` | GET | On-demand Strix Halo compliance audit |
| `/api/v1/system-info` | GET | Hardware baseline for the summary banner |
| `/api/v1/config` | GET / POST | Read / update runtime settings |
| `/api/v1/stats/reset` | POST | Reset session statistics to zero |
| `/api/v1/count-tokens` | POST | Count the input tokens of a prompt (proxies Halogen) |
| `/api/v1/chat` | POST | Streaming chat completion (4 API styles, SSE) |
| `/api/v1/healthz` | GET | Backend liveness |

Interactive API docs: `http://localhost:8000/docs`.

### AI Chat (`POST /api/v1/chat`)

The chat tab sends the conversation to Halogen and streams the reply back as
Server-Sent Events. One request body works across all four upstream API styles:

```json
{
  "messages": [{ "role": "user", "content": "Hello!" }],
  "api": "chat",
  "stream": true,
  "thinking": true,
  "max_tokens": 1024
}
```

`api` selects the Halogen endpoint: `chat` (`/v1/chat/completions`), `messages`
(`/v1/messages`), `responses` (`/v1/responses`), or `completions`
(`/v1/completions`). `thinking: false` suppresses the model's chain-of-thought.
The stream is normalized to `reasoning`, `delta`, `done` and `error` events so
the UI is identical regardless of the chosen style. Set `stream: false` for a
single JSON response instead.

Assistant replies render as GitHub-flavoured Markdown, including tables and
fenced code blocks. Use **Source** on a reply to switch between the rendered
view and its original Markdown text.

Your conversation is **not** cleared when you switch tabs — the chat stays
mounted in the background, so you can check the metrics mid-chat and come back
to it. It is also saved locally, so it survives a page reload. Use **Clear
conversation** (eraser icon) to start over.

## Agentic AI Chat

Toggle **Agent** in the AI Chat toolbar and the tab stops sending one-shot
completions. Instead, each request runs an
[OpenAI Agents SDK](https://openai.github.io/openai-agents-python/) loop
against your local engine: the model decides which dashboard tools to call,
reads the results, and keeps going until it can answer. Nothing is sent off the
machine — the SDK talks to the engine's OpenAI-compatible endpoint, and no
OpenAI account or API key is involved.

The reply streams exactly like Plain mode (text plus optional chain-of-thought),
with one addition: a collapsible **Actions** panel under the answer lists every
tool call and a summary of what came back.

### Tools

The agent has nine read-only tools, always available:

| Tool | What it returns |
| --- | --- |
| `get_live_status` | Aggregated engine + hardware snapshot |
| `get_engine_health` | Engine `/health` |
| `get_engine_metrics` | Engine `/metrics` as parsed key/value pairs |
| `get_cache_stats` | Prompt-cache telemetry |
| `run_tuning_check` | Full Strix Halo compliance audit |
| `get_system_info` | Hardware baseline |
| `count_prompt_tokens` | Exact token cost of a prompt |
| `list_runner_configs` | Saved LLM-Runner configurations |
| `get_active_run` | The engine run currently tracked, if any |

Two more tools are exposed **only** when you tick **Allow actions**:

| Tool | What it does |
| --- | --- |
| `start_engine` | Launches a saved run configuration |
| `stop_engine` | Stops the currently active run |

**Allow actions is destructive.** Starting and stopping engines goes through the
Docker socket, which is root-equivalent on the host. The toggle is off by
default, is required per request, and the agent simply cannot see those two
tools while it is off — this is not a suggestion in the prompt, it is enforced
by which tools are registered.

### Full access: research and execution

Ticking **Full access** adds seven tools that go outside the machine's own
telemetry — the agent can browse, search, run code, and write files:

| Tool | What it does |
| --- | --- |
| `fetch_url` | Fetches any HTTP(S) page and returns readable text (or raw body for APIs) |
| `search_web` | Web search (Brave → DuckDuckGo → Bing), returns result titles and links |
| `run_shell` | Runs a bash command, returns exit code, stdout, stderr |
| `run_python` | Writes the snippet to the workspace and runs it under `python3` |
| `write_file` | Writes or appends a file in the workspace |
| `read_file` | Reads a file |
| `list_directory` | Lists a directory |

This is what turns "I cannot browse that URL" into an actual research run: the
model fetches a page, notices the RSS feed is short, writes a parser, runs it,
and reconciles the difference.

**Read the security note before turning this on.** The dashboard container has
`/var/run/docker.sock` mounted so the LLM-Runner can manage engine containers.
Anything that can write to that socket can create a privileged container, which
is **root on your host**. `run_shell` does not add a privilege boundary — it
removes one. The tool's destructive-command guard (`rm -rf /`, `mkfs`,
`dd of=/dev/…`, `shutdown`, and friends) is accident insurance against a model
doing something dumb, not a sandbox against one doing something clever.

Full access implies Allow actions: an agent that can run shell commands can
already do anything the two engine-control tools can do, so withholding them
would only be confusing.

Generated files live in a single workspace directory (`AGENT_WORKSPACE`,
default `/app/backend/agent_workspace`, mounted as the `strixper-agent-ws`
volume so it survives restarts). Relative paths in the file tools resolve there.

TLS verification is on for `fetch_url` and `search_web`. Set
`AGENT_TLS_VERIFY=0` only if you sit behind a TLS-terminating proxy whose CA
is not in the container trust store.

`search_web` scrapes public search engines — Brave, then DuckDuckGo, then Bing —
and stops as soon as one fills the requested count. None of them is an API: all
are undocumented HTML, so a redesign can break a parser, and Brave rate-limits
(HTTP 429) under heavy use. The chain absorbs that. `fetch_url` does not depend
on any of it — give the agent a URL directly and it is fine.

### Endpoints

| Endpoint | Method | Description |
| --- | --- | --- |
| `/api/v1/agent/chat` | POST | Streaming agentic chat (SSE) |
| `/api/v1/agent/tools` | GET | Tool inventory for the current permission set |

Request body:

```json
{
  "messages": [{ "role": "user", "content": "Why is throughput low?" }],
  "mode": "agent",
  "allow_actions": false,
  "full_access": false,
  "max_turns": 8,
  "max_tokens": 2048,
  "temperature": 0.2,
  "thinking": true
}
```

Set `"mode": "plain"` to have the same endpoint fall through to the normal
one-shot chat handler.

The SSE stream reuses the Plain-mode vocabulary and adds one event:

| Event | Payload | Meaning |
| --- | --- | --- |
| `reasoning` | `{"text": ...}` | Chain-of-thought chunk |
| `delta` | `{"text": ...}` | Answer chunk |
| `tool` | `{"phase": "call", "name": ..., "detail": ...}` | Tool invoked |
| `tool` | `{"phase": "result", "name": ..., "output": ...}` | Tool returned |
| `done` | `{"finish_reason", "usage", "timings"}` | Turn finished |
| `error` | `{"message": ...}` | Aborted (max turns, engine down, ...) |

Tool output is clipped before it reaches the model so a chatty endpoint cannot
blow up the context window. `AGENT_MAX_TURNS` bounds how many model↔tool
round-trips a single request may take.

Agent mode does not expose the **Max tokens** control. A `write_file` call puts
the entire file body inside a single tool-call JSON object, so a small output cap
cuts those arguments off mid-string and the call fails to parse — which is what
happened at the default 1024. Agent mode uses `AGENT_MAX_TOKENS` (8192) instead,
which comfortably covers scripts of a few thousand lines.

### Customising the instructions

`AGENT_INSTRUCTIONS` replaces the built-in system prompt wholesale. The default
describes the dashboard, the Strix Halo context, and when to reach for each
tool; override it only if you want a different voice or a narrower remit.

## Docker quick start

For the shortest path see [Quick Start](#quick-start). This section documents
the Docker runtime, options, and operational behavior in more detail.

The dashboard supervises LLM engine containers through the host's Docker
daemon (the LLM-Runner) and reaches them on `127.0.0.1`, so the container
needs the **Docker socket mounted** and **host networking** — a plain
`-p 8000:8000` bridge mapping is not enough.

**Prerequisites:** Docker installed, and your user in the `docker` group
(`docker ps` works without `sudo`).

### Build the image

```bash
./build_img.sh
```

Produces `strixper-dashboard:latest`. Pass a tag to override
(`./build_img.sh strixper:v2`), or `NO_CACHE=1 ./build_img.sh` to
rebuild from scratch.

### Start the container

```bash
./strixper.sh start
```

| Flag | Why it is needed |
| --- | --- |
| `--network=host` | The dashboard talks to engine containers on `127.0.0.1`; host networking also serves the UI on the LAN at `http://<this-host>:8000` |
| `-v /var/run/docker.sock:/var/run/docker.sock` | The LLM-Runner starts/stops engine containers through the host daemon |
| `-v /etc/group:/etc/group:ro` | Engine commands resolve the host `render` group GID (`getent group render`) |
| `-v strixper-data:/app/backend/data` | Persists your LLM-Runner configs and the remembered engine target across recreations |
| `-v strixper-agent-ws:/app/backend/agent_workspace` | Persists files the agent creates when **Full access** is enabled |
| `--device=/dev/kfd` and `--device=/dev/dri` | Give ROCm SMI access to the host GPU devices |
| `--group-add ...` | Allow the container to access GPU devices using the host `video` and `render` group IDs |

Open **http://192.168.1.172:8000** (your LAN IP — `hostname -I`) from
any device on the network. If a firewall is active, open the port first:

```bash
sudo ufw allow 8000/tcp
```

### Stop the container

```bash
./strixper.sh stop
```

The script asks the backend to stop all registered LLM-Runner engines before
stopping the dashboard. If the dashboard was already stopped, the script
starts it briefly so it can adopt and stop configured engines left running.
A graceful backend/container shutdown also attempts to stop every registered
run, but a forced process kill can prevent that cleanup. The wrapper is still
recommended because it calls the stop-all API and sweeps configured container
names as a fallback if that API is unavailable. Only Strixper-managed runs and
containers named by saved Runner configurations are stopped; unrelated Docker
containers are left alone.

Other useful actions:

```bash
./strixper.sh status
./strixper.sh restart
./build_img.sh && ./strixper.sh recreate
```

`recreate` stops all managed engines, removes the dashboard container, then
creates it from the selected image while preserving the `strixper-data` volume.
Set `STRIXPER_IMAGE` to select a different image tag, or `BIND_PORT` when
creating the container to use a different listening port.

The Docker container reads Runner configs from the **`strixper-data` volume**.
The local, gitignored `backend/data/llm_runner_configs.json` is not mounted into
that volume, so editing it on the host does not update an existing container.
Create or edit configurations in the LLM-Runner UI (or through its API); if
you already have a config in a separate local JSON file, import it through the
API or create it in the UI.

After running `./strixper.sh stop`, remove only the dashboard container with
`docker rm strixper`; the named `strixper-data` volume remains available for
the next start. To permanently delete the saved Runner configs and engine
target, remove that volume separately with `docker volume rm strixper-data`.

### Check it

```bash
docker logs -f strixper                       # backend logs
curl http://127.0.0.1:8000/api/v1/healthz    # -> {"status":"ok"}
docker ps                                     # dashboard + engine containers
```

### Notes

- **ROCm diagnostics and GPU metrics:** the runtime image uses AMD's
  version-pinned ROCm 7.2.2 Ubuntu base, which includes `rocm-smi`. Pass
  `/dev/kfd` and `/dev/dri` as shown above for it to read GPU data. The
  diagnostic's kernel and boot-parameter checks observe the host kernel;
  package-manager and TuneD checks run inside the container and may not
  reflect host firmware or TuneD state.
- **Changing the port:** run `BIND_PORT=8090 ./strixper.sh recreate`. With
  host networking, the container binds that port directly on the host.
- **Runtime configuration:** use the dashboard settings to change the
  Halogen engine address and polling interval. The wrapper passes `BIND_HOST`
  and `BIND_PORT` when it creates the dashboard container; other startup
  environment variables are not currently forwarded by `strixper.sh`.

### Docker Deployment Details

The runtime image uses `rocm/dev-ubuntu-24.04:7.2.2` and serves both the
production React build and the FastAPI API from one process. Its frontend is
built in a separate Node.js stage. The runtime also includes the Docker CLI,
Python backend dependencies, and ROCm SMI from AMD's base image.

Use `./build_img.sh` to build `strixper-dashboard:latest`. `./strixper.sh`
manages the dashboard container with these actions:

| Command | Behavior |
| --- | --- |
| `./strixper.sh start` | Start the existing dashboard container or create it from the selected image |
| `./strixper.sh stop` | Stop all registered LLM-Runner runs, stop any remaining containers named by saved Runner configurations, then stop Strixper |
| `./strixper.sh restart` | Stop the managed stack and start the dashboard again |
| `./strixper.sh recreate` | Stop managed engines, remove/recreate the dashboard container, and preserve its data volume |
| `./strixper.sh status` | Show dashboard status and all running Runner engines |

Use this wrapper rather than `docker stop strixper` when stopping the stack:
a direct Docker stop does not call the Runner API or stop its engine containers.
The wrapper can also start a stopped dashboard briefly to discover a saved,
configured engine container that outlived the dashboard.

To select another image tag when creating/recreating the dashboard, set
`STRIXPER_IMAGE`, for example:

```bash
STRIXPER_IMAGE=your-dockerhub-user/strixper:latest ./strixper.sh recreate
```

`BIND_PORT` controls the port when the script creates a new container. The
script stores Runner configs and the remembered engine target in the
`strixper-data` Docker volume; deleting that volume removes this saved data.

**Security:** the dashboard has no authentication, and mounting
`/var/run/docker.sock` grants control over the host Docker daemon (effectively
root-equivalent access). Use only on a trusted network; do not expose the port
directly to the public internet. Host networking makes the service reachable
on the host's interfaces, subject to firewall rules.

## Project layout

```
backend/
  app/
    main.py                 FastAPI app + lifespan + static serving
    config.py               env settings
    routers/                live, tuning, config, tokens, chat, agent endpoints
    services/
      halogen_client.py     async Halogen HTTP client + Prometheus parser
      system_monitor.py     sysfs/procfs telemetry
      tuning_checker.py     compliance audit + system info
      live_service.py       background poller + shared runtime state
      agent_tools.py        tool functions exposed to the agent
frontend/
  src/
    App.jsx                 tabs, theme, polling, history buffer
    api.js                  fetch helpers + streamChat / streamAgentChat SSE clients
    components/             TopBar, StatTile, Meter, StatusBadge, LiveTab,
                            TuningTab, AIChatTab
    charts/                 Throughput, KV pool, Queue, Memory charts
Dockerfile
run.sh
```

## Notes on the tuning checks

The audit encodes the recommendations from the Strix Halo tuning guide:

- **Kernel >= 6.16.9** (6.18.x recommended; 6.17 broke the ROCm KFD ABI).
- **`amd_iommu=off`** in the kernel cmdline (+5-12% memory reads; disables NPU).
- **GTT/TTM boot params** (`amdgpu.gttsize`, `ttm.pages_limit`) for ~124 GB pool.
- **rocm-smi installed & working** — required for live GPU utilization and VRAM
  metrics; without it the dashboard falls back to sysfs only.
- **Firmware not `20251125`** (that build breaks ROCm; 20251111 validated).
- **TuneD `accelerator-performance`** profile (+5-8% prefill).
- **udev** render-group read/write on `/dev/kfd` and `/dev/dri/renderD*`.

Status colors (green PASS / yellow WARN / red FAIL) always ship with an icon and
label, never color alone.

## Credits

Strixper builds on the work of the open-source projects and documentation used
to run, tune, and monitor AMD Strix Halo systems. Special thanks to:

- [kyuz0/amd-strix-halo-toolboxes](https://github.com/kyuz0/amd-strix-halo-toolboxes)
  for Strix Halo ROCm tooling and practical system guidance.
- [peonist-ai/halogen-flash-server](https://github.com/peonist-ai/halogen-flash-server)
  for the Halogen inference server.
- [gufo-org/gufo](https://github.com/gufo-org/gufo)
  for the GUFO project and related Strix Halo work.

Thanks also to the [OpenAI Agents SDK](https://openai.github.io/openai-agents-python/)
team, whose Python SDK powers the agentic chat, and to the maintainers of React,
Vite, FastAPI, Uvicorn, TanStack Query, Tailwind CSS, Recharts, lucide-react,
Docker, and ROCm, and to the authors of the Halogen API and Strix Halo tuning
references used in this project.
