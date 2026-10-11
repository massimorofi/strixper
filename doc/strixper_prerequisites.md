# Halogen on Strix Halo (Ubuntu) — Prerequisites

Everything required to run the **Halogen LLM engine** (and the Strixper dashboard)
on an **AMD Strix Halo** machine (Ryzen AI Max+ 395 / Radeon 8060S, `gfx1151`)
running **Ubuntu**. Compiled from `strix_halo_finetuning.md`, `halogen_api.md`,
`Halogen_REST_API.md`, `strixper_specs.md`, `start_halogen.sh` and the Dockerfile.

---

## 1. Hardware

| Component | Requirement |
|---|---|
| CPU | AMD Ryzen AI Max+ 395 (Zen 5, 16c/32t) |
| iGPU | Radeon 8060S, RDNA 3.5, 40 CUs (`gfx1151`) |
| RAM | Up to 128 GB LPDDR5x-8000 unified (~124 GB exposed as GTT pool) |
| Disk | Enough space for model files (~10–20 GB: checkpoint, overlay, vision tower, tokenizer) |

---

## 2. OS, Kernel & BIOS

- **Ubuntu 25.10** (or any Ubuntu where you can install a mainline kernel).
- **Kernel 6.18.x mainline** — hard minimum **6.16.9** (older kernels cap visible
  GPU memory at ~15 GB). **⚠ Kernel 6.17 broke the ROCm KFD ABI** — do not use.
  Install on Ubuntu:
  ```bash
  sudo add-apt-repository ppa:cappelikan/ppa && sudo apt install mainline
  sudo mainline --install 6.18.7
  ```
- **Kernel boot parameters** (append to `GRUB_CMDLINE_LINUX` in `/etc/default/grub`):
  ```
  amd_iommu=off amdgpu.gttsize=126976 ttm.pages_limit=32505856
  ```
  - `amdgpu.gttsize=126976` ≈ 124 GB GTT (GPU compute pool)
  - `ttm.pages_limit=32505856` × 4 KiB ≈ 124 GB pinned-memory ceiling
  - Then `sudo update-grub && sudo reboot`
  - `amd_iommu=off` gives +5–12% memory reads but **disables the XDNA 2 NPU**
    and DMA protection. `amd_iommu=pt` does **not** work on this platform.
- **BIOS settings:**
  1. Integrated Graphics → UMA Frame Buffer → **512 MB** (display only; compute memory is GTT)
  2. **Disable IOMMU**
  3. **TDP → 85 W** (+19% vs 55 W)

### ⚠ Firmware warning (critical)

`linux-firmware` version **20251125 breaks ROCm** on Strix Halo (crashes,
instability). Use **20251111** (validated). Check with:

```bash
dpkg -l | grep linux-firmware
```

If broken, downgrade the firmware package and rebuild the initramfs
(`sudo update-initramfs -u` on Ubuntu / `sudo dracut -f --kver <boot-kernel>`
on Fedora-based images), then reboot.

---

## 3. Drivers & ROCm stack

- **amdgpu kernel driver** (in-kernel; provided by the mainline kernel).
- **ROCm** (ROCm 10.0 stable recommended; 7.2.x also used). Install from AMD's apt repo:
  ```bash
  wget https://repo.radeon.com/rocm/rocm.gpg.key -O - | sudo gpg --dearmor -o /etc/apt/keyrings/rocm.gpg
  echo "deb [arch=amd64 signed-by=/etc/apt/keyrings/rocm.gpg] https://repo.radeon.com/rocm/apt/latest ubuntu main" | sudo tee /etc/apt/sources.list.d/rocm.list
  sudo apt update && sudo apt install rocm-hip-runtime rocm-hip-sdk
  export PATH=/opt/rocm/bin:$PATH
  ```
- **`rocm-smi`** — required by the dashboard for live GPU utilization/VRAM
  (falls back to sysfs only without it).
- **`rocminfo`** — verify enumeration: expect CPU agent + `gfx1151` GPU agent.
- **Vulkan stack (alternative)** — Mesa `vulkan-radv`; most compatible backend,
  within ~1.6% of ROCm on decode. Install `mesa-vulkan-drivers` if needed.

### GPU access / udev rules (critical)

Missing the `renderD` rule is the #1 cause of `HSA_STATUS_ERROR_OUT_OF_RESOURCES`.

```bash
sudo usermod -aG video,render $USER
sudo tee /etc/udev/rules.d/99-amd-kfd.rules <<'EOF'
SUBSYSTEM=="kfd", KERNEL=="kfd", GROUP="render", MODE="0666"
SUBSYSTEM=="drm", KERNEL=="card[0-9]*", MODE="0666"
SUBSYSTEM=="drm", KERNEL=="renderD[0-9]*", MODE="0666"
EOF
sudo udevadm control --reload-rules && sudo udevadm trigger
```

### Verify

```bash
rocminfo | grep -E 'Agent|Name|Marketing'
rocm-smi
cat /sys/class/drm/card*/device/mem_info_gtt_total   # ~124 GB
```

---

## 4. Power / system profile

```bash
sudo apt install tuned
sudo systemctl enable --now tuned
sudo tuned-adm profile accelerator-performance   # +5–8% prefill (disables C-states)
tuned-adm active
```

Optional: headless for max free RAM:
`sudo systemctl set-default multi-user.target && sudo reboot`

---

## 5. Docker (Halogen runs in a container)

- **Docker Engine** installed and current; user in the `docker` group
  (`sudo usermod -aG docker $USER`, re-login).
- **Image:** `ghcr.io/peonist-ai/halogen-flash-server:latest`
- **Required container flags** (from `start_halogen.sh`):
  - `--device /dev/kfd` and `--device /dev/dri` (GPU passthrough)
  - `--group-add video` and `--group-add <render GID>`
  - `--security-opt seccomp=unconfined`
  - `--ipc=host`
  - `--ulimit memlock=-1:-1`
  - `--security-opt no-new-privileges`
  - `-p 0.0.0.0:8731:8731` (Halogen API port)
- **Note (kernel 6.17+):** old ROCm 7.0-rc distrobox/container images segfault
  on the broken KFD ABI — use native ROCm 7.2+ / ROCm 10.0 based images.

### Model files required (mounted read-only into the container)

| Host file | Container path | Purpose |
|---|---|---|
| `qwen38-flash-next-w4b.hgn` | `/models/qwen38-flash-next-w4b.hgn` | Main checkpoint (`HALOGEN_CHECKPOINT`) |
| `qwen38-flash-next-w4b.overlay-speed.hgn` | `/models/...overlay-speed.hgn` | Speed overlay (`HALOGEN_CK_OVERLAY`) |
| `qwen38-flash-next-vision.hgn` | `/models/qwen38-flash-next-vision.hgn` | Vision tower (`HALOGEN_VISION_TOWER`) |
| `tokenizer/chat_template.jinja` | `/models/tokenizer/chat_template.jinja` | Chat template |
| `tokenizer/generation_config.json` | `/models/tokenizer/...` | Generation config |
| `tokenizer/merges.txt` | `/models/tokenizer/merges.txt` | Tokenizer data |
| `tokenizer/tokenizer.json` | `/models/tokenizer/tokenizer.json` | Tokenizer data |
| `tokenizer/tokenizer_config.json` | `/models/tokenizer/tokenizer_config.json` | Tokenizer data |
| `tokenizer/vocab.json` | `/models/tokenizer/vocab.json` | Tokenizer data |

(On this machine they live under `/run/media/briggen/DEV/.aitoolbox/models/`.)

### Halogen environment variables

| Variable | Value | Meaning |
|---|---|---|
| `HALOGEN_CHECKPOINT` | `/models/qwen38-flash-next-w4b.hgn` | Model checkpoint |
| `HALOGEN_CK_OVERLAY` | `/models/qwen38-flash-next-w4b.overlay-speed.hgn` | Speed overlay |
| `HALOGEN_TOKENIZER` | `/models/tokenizer` | Tokenizer directory |
| `HALOGEN_API_PORT` | `8731` | API/REST port |
| `HALOGEN_CTX` | `262144` | Context length (262K) |
| `HALOGEN_KV_POOL_POSITIONS` | `524288` | KV pool size |
| `HALOGEN_KV_SLOTS` | `4` | Concurrent slots |
| `HALOGEN_PROMPT_CACHE` | `2` | Prompt cache mode |
| `HALOGEN_VISION_TOWER` | `/models/qwen38-flash-next-vision.hgn` | Vision model |

Start with `./start_halogen.sh`; verify at `http://127.0.0.1:8731/health`.

---

## 6. Dashboard prerequisites

- **Python 3.11+** (3.12 in Docker) with `venv` and `pip`.
- **Node.js 22 + npm** (frontend build; Docker uses `node:22-alpine`).
- **Python packages** (`backend/requirements.txt`): `fastapi`, `uvicorn[standard]`,
  `httpx`, `python-dotenv`.
- **Frontend packages** (npm): React 18, Vite 5, Tailwind CSS 3,
  `@tanstack/react-query`, Recharts, `lucide-react`.
- **Config:** copy `backend/.env.example` → `backend/.env`
  (`HALOGEN_HOST`, `POLL_INTERVAL_SECONDS`, `BIND_HOST`, `BIND_PORT`).
- **Ports:** `8731` (Halogen), `8000` (dashboard/API), `5173` (Vite dev only).
  Check conflicts with `ss -tlnp | grep LISTEN`.
- **Firewall (if LAN-exposed):** `sudo ufw allow 8000/tcp` (or firewalld).
  The dashboard has **no authentication** — prefer localhost binding or an SSH
  tunnel on untrusted networks.
- **Docker (optional, for the dashboard image):** `docker build -t strixper-dashboard .`
  then `docker run -p 8000:8000 strixper-dashboard` (or `docker compose up -d --build`).

---

## 7. Quick verification checklist

```bash
uname -r                                   # 6.18.x (NOT 6.17)
cat /proc/cmdline                          # amd_iommu=off amdgpu.gttsize=126976 ttm.pages_limit=32505856
dpkg -l | grep linux-firmware            # NOT 20251125 (use 20251111)
rocminfo | grep gfx1151                  # GPU agent visible
rocm-smi                                 # Radeon 8060S reported
cat /sys/class/drm/card*/device/mem_info_gtt_total   # ~124 GB
groups                                   # includes video, render
docker --version                         # Docker engine present
curl http://127.0.0.1:8731/health      # Halogen responds {"status":"ok",...}
curl http://127.0.0.1:8000/api/v1/healthz   # Dashboard backend alive
```

Or run the built-in audit from the dashboard's **Fine-Tuning** tab
(`GET /api/v1/tuning-check`), which checks kernel, IOMMU, boot params, GTT
size, rocm-smi, firmware, TuneD profile and udev rules.
