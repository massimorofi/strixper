# Halogen Strix Halo Operations Dashboard

A real-time web dashboard for monitoring a running **Halogen LLM Server** alongside
host and hardware telemetry on an **AMD Strix Halo** (Ryzen AI Max+ 395 / Radeon
8060S) machine, with an on-demand fine-tuning compliance audit.

Built from `strixper_specs.md`, using `halogen_api.md` for the Halogen endpoint
contracts and `strix_halo_finetuning.md` for the tuning recommendations.

## What it does

- **Live Server Metrics** — polls Halogen's `/health`, `/metrics` and `/v1/models`
  and reads local sysfs/procfs telemetry, rendering KPI cards and time-series
  charts (token throughput, KV pool, queue depth, host memory). The top
  **Throughput** card shows the **session average** prefill/decode rate rather
  than the instantaneous gauge, because Halogen's per-second throughput gauge
  reads 0 whenever the engine is idle.
- **GPU Monitoring (rocm-smi)** — reads live GPU compute utilization and VRAM
  usage via `rocm-smi` and plots them alongside GTT and RAM in the memory chart.
  rocm-smi is the authoritative source: it reports higher VRAM usage than raw
  sysfs because it counts driver-reserved allocations.
- **Session Statistics** — a min / avg / max table (prompt & decode token rate,
  GTT / VRAM usage, GPU utilization, CPU utilization, draft acceptance)
  accumulated **since the server started**, with a **Reset statistics** button to
  zero the counters and restart the clock. For the throughput, utilization and
  acceptance series (prompt/decode t/s, GPU utilization, CPU utilization, draft
  acceptance) **zero readings are excluded** from both the average and the
  minimum — a 0 means "idle / not reporting yet", not a real measurement of zero.
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
        |  REST (JSON over HTTP)
FastAPI backend (asyncio)
  - HalogenClient   -> httpx -> Halogen /health /metrics /v1/models
  - SystemMonitor   -> sysfs / procfs (GTT, RAM, CPU)
  - TuningChecker   -> shell commands (uname, tuned-adm, ls, ...)
        |
Halogen engine + Linux host
```

## Running the dashboard

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

**Docker:** the *host* side of `-p` is what can conflict — change it freely:

```bash
docker run -d -p 8010:8000 --name strixper strixper-dashboard   # host 8010 -> container 8000
```

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
| `/api/v1/healthz` | GET | Backend liveness |

Interactive API docs: `http://localhost:8000/docs`.

## Docker

The image is multi-stage: stage 1 builds the React frontend, stage 2 runs the
FastAPI backend which serves the built UI and the API on port 8000.

**Build the image:**

```bash
docker build -t strixper-dashboard .
```

**Run it (local only):**

```bash
docker run -p 127.0.0.1:8000:8000 strixper-dashboard
```

**Run it exposed on the LAN:**

```bash
docker run -d -p 8000:8000 --name strixper strixper-dashboard
```

Then open **http://192.168.1.172:8000** (your LAN IP) from any device.

**With configuration** (point at a remote Halogen, change the port, etc.):

```bash
docker run -d -p 8000:8000 \
  -e HALOGEN_HOST=http://192.168.1.50:8731 \
  -e POLL_INTERVAL_SECONDS=5 \
  strixper-dashboard
```

Or use an env file:

```bash
docker run -d -p 8000:8000 --env-file backend/.env strixper-dashboard
```

**Rebuild after code changes:**

```bash
docker build -t strixper-dashboard .
docker rm -f strixper && docker run -d -p 8000:8000 --name strixper strixper-dashboard
```

**Compose (optional)** — `docker-compose.yml`:

```yaml
services:
  dashboard:
    build: .
    ports:
      - "8000:8000"
    env_file:
      - backend/.env
    restart: unless-stopped
```

```bash
docker compose up -d --build
```

> Note: `-p 8000:8000` publishes on all interfaces (LAN-exposed); prefix with
> `127.0.0.1:` to keep it local-only. The container reads `BIND_HOST`/`BIND_PORT`
> but the host port mapping is what actually controls LAN reachability.

## Project layout

```
backend/
  app/
    main.py                 FastAPI app + lifespan + static serving
    config.py               env settings
    routers/                live, tuning, config endpoints
    services/
      halogen_client.py     async Halogen HTTP client + Prometheus parser
      system_monitor.py     sysfs/procfs telemetry
      tuning_checker.py     compliance audit + system info
      live_service.py       background poller + shared runtime state
frontend/
  src/
    App.jsx                 tabs, theme, polling, history buffer
    components/             TopBar, StatTile, Meter, StatusBadge, LiveTab, TuningTab
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
