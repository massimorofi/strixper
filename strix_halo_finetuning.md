# AMD Strix Halo — Linux Setup & Tuning Guide for Local LLM Inference

*Compiled 2026-10-03. Sources: strix-halo-toolboxes.com (kyuz0 / Donato Capitella), Gygeek/Framework-strix-halo-llm-setup, AMD developer playbooks, pablo-ross/strix-halo-gmktec-evo-x2, local-llm-benchmarks.dev, terminal-bench-mini, and GitHub issue #66 (Lars Urban IOMMU benchmark). Reddit r/StrixHalo was unreachable (403) and could not be included.*

> **How to read this guide.** Numbers are tied to the source and date that measured them. Where sources disagree, the canonical (most recent, best-verified) recommendation is given and the discrepancy is flagged inline. Treat single-source figures as representative best-cases, not guarantees.

---

## 1. Executive Summary

The short version, for someone who just wants it to work fast:

- **Hardware reality:** Strix Halo (Ryzen AI Max+ 395, `gfx1151`) has ~**208 GB/s sustained** memory bandwidth on its 256-bit LPDDR5x bus. LLM **decode is memory-bandwidth-bound** — no software backend can beat that ceiling. Therefore **MoE models with few active parameters massively outperform dense models** of similar size. This is the single most important fact in this entire guide.
- **OS:** Fedora 43/44 or Ubuntu 25.10 with a **mainline kernel 6.18.x** (6.16.9 is the hard minimum; **6.17 broke the ROCm KFD ABI** — see §3).
- **Critical firmware check:** `linux-firmware 20251125` **breaks ROCm** on Strix Halo. Downgrade to `20251111`.
- **Kernel boot params:** `amd_iommu=off amdgpu.gttsize=126976 ttm.pages_limit=32505856` (exposes ~124 GB unified memory to the GPU; IOMMU off is worth **5–12%** but disables the NPU).
- **Power:** `tuned-adm profile accelerator-performance` (+5–8% prefill) and BIOS **TDP 85W** (+19% vs 55W).
- **Engine:** **llama.cpp** with `-fa 1 --no-mmap -ngl 999` is the workhorse. **ROCm 10.0** or **Vulkan (RADV)** backends are within ~1.6% on decode — backend choice matters far less than model choice.
- **Model:** **Qwen3.8-Flash-Next** (MoE, ~3B active) at **UD-Q4_K_XL** is the standout — top speed *and* top agentic quality (100% pass@1 on terminal-bench-mini). For raw decode speed, **Qwen3.6-35B-A3B** hits ~91 t/s.
- **Quantization:** **Q4_K_XL** (Unsloth UD quants) is the sweet spot for 128 GB. Use **imatrix-calibrated** quants for coding/agent work.
- **Speculative decoding (MTP):** worth **1.3–1.7×** on decode. Use `--spec-draft-n-max 3` with **default** `p-min 0.00` (aggressive beats conservative here).
- **Fine-tuning:** LoRA/QLoRA works well up to 12B; **27B full fine-tuning is impossible** on 128 GB (needs ~324 GB). Use QLoRA for 27B.

---

## 2. The Platform: What Strix Halo Actually Is

| Component | Spec | Why it matters for LLMs |
|---|---|---|
| CPU | Zen 5, 16 cores / 32 threads | Prefill offload, CPU threads become the bottleneck at 6+ concurrent models |
| iGPU | RDNA 3.5 Radeon 8060S, 40 CUs (`gfx1151`) | The compute engine for ROCm/Vulkan inference |
| Memory | Up to 128 GB LPDDR5x-8000, unified | Lets you run 70B–235B models that won't fit on consumer GPUs |
| Bandwidth | ~256 GB/s theoretical, **~208 GB/s sustained** | **The hard ceiling on decode speed.** Everything else is optimization around this. |
| NPU | XDNA 2 | Useful for CV/small models, but **disabled when IOMMU is off** |

**VRAM vs GTT — a common confusion.** Tools like `rocm-smi` show ~1 GB of "VRAM" — that's just the display framebuffer. The actual compute pool is **GTT (Graphics Translation Table)**, ~115–128 GB, configured via kernel parameters, *not* the BIOS UMA framebuffer setting. Check `cat /sys/class/drm/card*/device/mem_info_gtt_total` to see what you actually have.

**The bandwidth math.** A 17.66 GiB model decoding at ~11 t/s implies ~208 GB/s effective read bandwidth — i.e. the bus is saturated. Decode speed ≈ (bandwidth) / (bytes of active weights per token). So to go faster you must either **shrink the weights** (lower quant) or **reduce active parameters per token** (MoE). This is why a 30B MoE with 3B active generates *faster* than a 7B dense model.

---

## 3. OS, Kernel & BIOS

### 3.1 Distro & kernel

- **Recommended:** Fedora 43/44 or Ubuntu 25.10 with **mainline kernel 6.18.x** (reference rigs run 6.18.5 / 6.18.7).
- **Minimum:** kernel **6.16.9+** for full >64 GB unified memory access (older kernels cap visible memory at ~15 GB).
- **⚠ Kernel 6.17 KFD ABI break (2026-03):** kernel 6.17 changed the KFD kernel/userspace ABI and **breaks old ROCm 7.0-rc distrobox containers** (segfault in `libhsa-runtime64.so`). If you run 6.17+, you must use a **native ROCm 7.2 host build**, not the old container.
- Install mainline kernels on Ubuntu: `sudo add-apt-repository ppa:cappelikan/ppa && sudo apt install mainline && sudo mainline --install 6.18.7`.

### 3.2 BIOS settings

1. **Integrated Graphics → UMA Frame Buffer Size → 512 MB** (or 2 GB if that's the BIOS minimum — fine, only ~1.5 GB GTT headroom lost). This is the *display* framebuffer only; compute memory is GTT, set separately in the kernel.
2. **Disable IOMMU** — worth ~6% memory-read improvement (community; Lars Urban's three-way test measured **5–12%**). **Caveat: this disables the XDNA 2 NPU and removes DMA-attack protection.** Re-enable if you need the NPU or VFIO passthrough.
3. **TDP → 85 W** — claimed **+19% vs 55 W**, with diminishing returns above.

BIOS menu locations vary by manufacturer (tested on Framework Desktop).

### 3.3 Kernel boot parameters (the load-bearing part)

Append to `GRUB_CMDLINE_LINUX` in `/etc/default/grub`:

```
amd_iommu=off amdgpu.gttsize=126976 ttm.pages_limit=32505856
```

- `amdgpu.gttsize=126976` ≈ 124 GB GTT (the GPU's addressable compute pool).
- `ttm.pages_limit=32505856` × 4 KiB ≈ 124 GB pinned-memory ceiling.
- Then `sudo grub2-mkconfig -o /boot/grub2/grub.cfg && sudo reboot` (or `sudo update-grub` on Debian/Ubuntu).

**⚠ Discrepancy across the project's own docs:** the central site uses `126976 / 32505856` (~124 GB); the LLM-Fine-tuning README uses `131072 / 33554432` (128 GiB); the Gygeek guide uses `117760` (~115 GB). **Use the central ~124 GB values** — leaving a safety margin below total RAM avoids system thrashing.

**⚠ `amd_iommu=pt` does NOT work** on this platform — the kernel logs `AMD-Vi: Unknown option - 'pt'` and silently falls back to Translated mode. Real passthrough is `iommu=pt` (no `amd_` prefix). But the verified-best config is simply `amd_iommu=off` (Lars Urban, 2026-05-15: off 692.36 pp512 vs Translated 658.04 vs iommu=pt 652.45 t/s).

### 3.4 systemd-boot variant (AMD Ryzen AI Halo Debian-based images)

Use the `amd-ttm` tool instead of the two kernel params:

```bash
sudo apt install pipx && pipx ensurepath && pipx install amd-debug-tools
sudo amd-ttm --set 124        # sets TTM pages limit to 32505856 = 124.00 GB
```

Set `amd_iommu=off` via `/etc/kernel/cmdline` (**not** `/boot/loader/entries/`, which gets regenerated), then `sudo kernel-install add "$(uname -r)" /boot/vmlinuz-$(uname -r) /boot/initrd.img-$(uname -r)` and reboot.

### 3.5 ⚠ Critical firmware warning

`linux-firmware` version **20251125 critically breaks ROCm** on Strix Halo (instability, crashes, arbitrary failures). AMD recalled it but many distros (including Fedora) still ship it.

```bash
rpm -qa | grep linux-firmware        # if it shows 20251125:
# download the 20251111 rpms from kojipkgs.fedoraproject.org
sudo dnf downgrade ./*.rpm
sudo dracut -f --kver <kernel-you-boot>
reboot
```

Validated on kernel 6.18.4-200.fc43. **`dracut -f` must target the kernel you actually boot.**

### 3.6 Power & desktop

```bash
sudo dnf install tuned            # or apt install tuned
sudo systemctl enable --now tuned
sudo tuned-adm profile accelerator-performance
tuned-adm active                  # verify
```

The `accelerator-performance` profile disables high-latency CPU STOP (C-)states → **+5–8% prompt processing**. Recommended on all host variants.

For maximum free memory, disable the desktop: `sudo systemctl set-default multi-user.target && sudo reboot` (restore with `graphical.target`).

---

## 4. Drivers & ROCm

### 4.1 ROCm version landscape (Oct 2026)

| ROCm | Status | Use |
|---|---|---|
| **10.0** | **Stable** (Fedora 44 base) | AMD's supported `gfx1151` package set; used by main llama.cpp / ds4 / vLLM toolboxes |
| 7.2.3 / 7.2.4 | Mature, widely used | Native host builds, custom/experimental, IOMMU benchmark |
| 7.14 | Referenced | EngramHalo upstream validation, R9700 `gfx1201` images |
| 7 nightly (TheRock) | Bleeding edge | Fine-tuning toolbox base |
| 6.4.x | Legacy / excluded | Outdated — do not use for new setups |

AMD nightly stream: `nightly.repo.amd.com` (TheRock). Docs: `rocm.docs.amd.com`.

### 4.2 Install ROCm on the host (native)

```bash
# AMD apt repo
wget https://repo.radeon.com/rocm/rocm.gpg.key -O - | sudo gpg --dearmor -o /etc/apt/keyrings/rocm.gpg
echo "deb [arch=amd64 signed-by=/etc/apt/keyrings/rocm.gpg] https://repo.radeon.com/rocm/apt/latest ubuntu main" | sudo tee /etc/apt/sources.list.d/rocm.list
sudo apt update && sudo apt install rocm-hip-runtime rocm-hip-sdk
# For ROCm 7.2 native build specifically: install rocm-hip-runtime-dev hipblas-dev
export PATH=/opt/rocm/bin:$PATH
```

### 4.3 GPU access / udev rules (critical)

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

### 4.4 Verify the install

```bash
rocminfo | grep -E 'Agent|Name|Marketing'   # expect CPU Agent + gfx1151 GPU Agent
rocm-smi                                    # should show Radeon 8060S
cat /sys/class/drm/card*/device/mem_info_gtt_total   # ~124 GB
```

### 4.5 Vulkan stack

The `vulkan-radv` path uses **Mesa RADV**. It is the **most compatible** backend on Strix Halo and is the official stable recommendation for "just works" inference. For max compatibility across models, start with `vulkan-radv`; for max performance, `rocm-10.0`.

---

## 5. Inference Engines

### 5.1 Comparison table

| Engine | Backend | Strengths | Maturity | Best for |
|---|---|---|---|---|
| **llama.cpp** | ROCm 10.0 / Vulkan RADV | The workhorse. GGUF, flash attention, MTP spec-decode, RPC clustering. `-fa 1 --no-mmap -ngl 999` mandatory. | Stable | General chat, coding, serving |
| **llama.cpp-perf** (Nathanw1014 fork) | Vulkan | Best raw decode numbers in Oct 2026 dataset (91 t/s on 35B-A3B) | Experimental | Max decode throughput |
| **Gufo** | ROCm | Standalone engine; top prefill (1624 t/s) + top agentic quality with Qwen3.8-Flash-Next | Stable/experimental | High-quality agentic + long context |
| **DwarfStar / ds4** (antirez) | ROCm 10.0 | Native C engine for DeepSeek V4 Flash; OpenAI + Anthropic API; KV disk cache; pipeline parallelism | Stable | DeepSeek V4, extreme Q2 quants |
| **vLLM** | ROCm 10.0 + RCCL | High-throughput serving, TP=2 clustering over Ethernet/RDMA. Needs AITER patches; broad AITER toggle must stay OFF. | Functional, least batteries-included | Multi-user serving, clustering |
| **LM Studio** | ROCm | GUI, easy | Stable | Beginners, local GUI |
| **Ollama** | ROCm | Simplest CLI/API serving | Stable | Quick local model management |
| **Lemonade** | NPU/ROCm | AMD's local model server, used across playbooks | Stable | AMD-integrated workflows |

### 5.2 Backend verdict: ROCm vs Vulkan

**Measured (Qwen3.8-27B, pablo-ross 2026-08):** ROCm pp512=347.3 / tg128=10.88 vs Vulkan(RADV) pp512=288.1 / tg128=11.06 — **only 1.6% decode difference**, Vulkan worse at prefill. The popular claim that "Vulkan is 2× faster than ROCm" **did not reproduce on stock builds** (it came from a specialized fork). Community reports conflict (one user: Vulkan better long-context, ROCm better short-context; another: ROCm 6.4.4 fastest). **Conclusion: pick the model and quant first; the backend is a second-order concern.** Default to `vulkan-radv` for compatibility, `rocm-10.0` for performance.

### 5.3 Required llama.cpp flags on Strix Halo

```bash
llama-server -m MODEL.gguf --host 0.0.0.0 --port 8080 \
  -ngl 999 -fa 1 --no-mmap -c 8192
```

- `-fa 1` — flash attention (prevents crashes).
- `--no-mmap` — prevents crashes/slowdowns on GPU backends. **Exception:** the EngramHalo toolbox *requires* `-lm mmap --lazy-mode on` and must NOT use `--no-mmap`.
- `-ngl 999` — offload all layers to GPU.
- For `llama-bench`, use `-mmp 0` instead of `--no-mmap`.

---

## 6. Models: Performance & Quality

### 6.1 Speed (tokens/sec) on Strix Halo

| Model | Quant | Engine | Prefill (pp) | Decode (tg) | Date / source |
|---|---|---|---|---|---|
| Llama-2-7B | Q4_K_M | ROCm 7 RC | pp512 **871.77** | tg128 **43.83** | pablo-ross 2025-11-04 |
| Qwen3-Coder-30B-A3B | Q4_K_M | ROCm | large-prompt 46.81 | **~71** | pablo-ross 2025-11-04 |
| Qwen3.6-35B-A3B | UD-Q4_K_XL | llama.cpp-perf/Vulkan | **1634.5** | **91.44** | local-llm-benchmarks 2026 |
| Qwen3.8-Flash-Next | UD-Q4_K_XL | Gufo/ROCm | **1624.6** | **46.54** | local-llm-benchmarks 2026 |
| Qwen3.8-Flash-Next | UD-Q4_K_XL + MTP | strix-llama | 1207 (pp2048), 1055 @32k | **43.9** | kyuz0 2026 |
| Qwen3-Coder-Next (80B/3B) | UD-Q4_K_XL | ROCm | large 256.54 | ~35 (under load) | pablo-ross 2026-07-08 |
| DeepSeek-V4-Flash-0731 | IQ2_XXS | Gufo | — | **32.95** | local-llm-benchmarks 2026 |
| Qwen3.8-27B (dense) | Q4_K_M | ROCm 7.2.4 | pp512 347.3 / pp4096 332.7 | **10.88** | pablo-ross 2026-08-17 |
| LFM2.5-1.2B | BF16 | — | **2287–2339** | — | local-llm-benchmarks 2026 |

*Reference (NOT Strix Halo):* Radeon AI PRO R9700 (`gfx1201`, discrete) runs the same models ~2.5–3× faster (e.g. Qwen3.6-35B-A3B 3808 pp / 148–175 tg) — confirming Strix Halo is bandwidth-bound, not compute-bound.

**Key observations:**
- A 30B MoE (3B active) generated **faster** (71 t/s) than a 7B dense model (43.8 t/s) — active-parameter count beats total size.
- The dense Qwen3.8-27B (10.88 t/s) was **rejected for production** despite good benchmark scores — it moves all 27B params per token vs 3B active for the MoE → ~4× slower decode.
- Decode degrades with context depth (e.g. Qwen3.8-Flash-Next 46.5 t/s at depth 0 → ~39.7 t/s at 32k).

### 6.2 Quality / intelligence (terminal-bench-mini, pass@1, 19 real terminal tasks, terminus-2 agent, 2026-09-27)

| Model | Quant / engine | pass@1 |
|---|---|---|
| **Qwen3.8-Flash-Next** | UD-Q4_K_XL (Gufo, MTP-7) | **100%** (19/19) |
| DeepSeek-V4.1-Flash | Q2 (DwarfStar) | 94.7% |
| Qwen3.8-Flash-Next | W4B (halogen-flash) | 94.7% |
| DeepSeek-V4-Flash-0731 | UD-IQ2_XXS (Vulkan) | 94.7% |
| Qwen3.8-27B | Q4_0_ROCMI4 | 84.2% |
| Qwen3.8-Flash-Next | UD-IQ4_XS (EngramHalo) | 84.2% |
| GLM 5.3 Flash | Q2 | 73.7% |
| Qwen3.6-27B | UD-Q8_K_XL | 63.2% |
| Muse-Glimmer-30B | — | 57.9% |
| Qwen3.6-35B-A3B | UD-Q4_K_XL | 57.9% |
| MiMo-V2.6-Flash-RL | MXFP4 | 36.8% |

**Qwen3.8-27B standalone eval:** SWE-bench Pro 61.7, LiveCodeBench v6 90.3, Terminal Bench 2.1 73.0 — quality was *not* the problem on this hardware; speed was.

### 6.3 Best model per use case

| Use case | Pick | Why |
|---|---|---|
| **Daily driver (chat + coding + agents)** | **Qwen3.8-Flash-Next UD-Q4_K_XL** | Top speed *and* 100% agentic pass@1; ~3B active keeps decode fast |
| **Max raw decode speed** | Qwen3.6-35B-A3B UD-Q4_K_XL | 91 t/s decode, but weaker agentic quality (57.9%) |
| **Max intelligence** | Qwen3.8-Flash-Next (Q4_K_XL) or DeepSeek-V4.1-Flash Q2 | 100% / 94.7% pass@1 |
| **DeepSeek-specific** | DeepSeek-V4 Flash via **DwarfStar/ds4** (imatrix `chat-v2-imatrix` quants) | imatrix preserves logic at extreme Q2 |
| **Long context** | Qwen3.8-Flash-Next (262K native) | Linear-attention prefill is ~8.6× faster than older baselines |
| **Lightweight / fast small** | LFM2.5-1.2B BF16 | 2287+ t/s prefill, tiny footprint |

> **Note:** terminal-bench-mini is a focused coding/terminal comparison, **not an overall model ranking**. A vision benchmark was in development as of the data date.

---

## 7. Quantization & KV Cache

### 7.1 Recommended quants

- **Unsloth UD quants** (`UD-Q4_K_XL`, `UD-Q8_K_XL`, `UD-IQ4_XS`, `UD-IQ3_XXS`, `UD-Q2_K_XL`) are the dominant recommended format for llama.cpp on Strix Halo.
- **Q4_K_XL is the sweet spot** for 128 GB — good quality, fits with large context.
- **imatrix-calibrated quants** (e.g. antirez `chat-v2-imatrix` for DeepSeek) preserve logic/instruction-following at extreme Q2 compression far better than uniform quants. **Prefer imatrix quants specifically for coding agents.**
- **MXFP4** used by GPT-OSS-20B / MiMo; **AWQ 4/8-bit** used in the vLLM path.

### 7.2 KV cache

- Quantizing the KV cache (`q8_0`, `q4_0/q4_1`) saves memory at long context with modest quality loss; requires flash attention (`-fa 1`).
- At 128 GB you generally have room to keep KV in higher precision for the context lengths you actually use.

### 7.3 VRAM planning

Use the estimator before committing to a model+context combo:

```bash
gguf-vram-estimator.py MODEL.gguf --contexts 32768
```

Examples: Llama-4-Scout-17B Q4_K_XL = 57.74 GiB model / 108.87 GiB at 1M context (but 1M prefill ≈ 1.5 hr). Qwen3-235B Q3_K_XL: 65K ctx = 110.75 GiB, 131K = 122.5 GiB, 262K = 146 GiB (**OOMs above ~130K on 128 GB**).

---

## 8. Performance Tuning

Ordered by measured impact:

| Tweak | Gain | How / notes |
|---|---|---|
| **MoE model choice** | 2–4× decode vs dense | The single biggest lever. Fewer active params/token. |
| **Lower quant** | Proportional to bytes saved | Q4_K_XL vs Q8: ~2× less weight traffic per token. |
| **`amd_iommu=off`** | **+5–12%** | Kernel param. Breaks NPU + DMA protection. |
| **BIOS TDP 85W** | **+19%** vs 55W | Diminishing returns above 85W. |
| **`tuned accelerator-performance`** | **+5–8% prefill** | Disables high-latency CPU C-states. |
| **MTP speculative decoding** | **1.3–1.7× decode** | `--spec-type draft-mtp --spec-draft-n-max 3`, **default `p-min 0.00`**, `-ngld 99`. |
| **Disable desktop** (`multi-user.target`) | Frees RAM/GPU | Kills DE + background processes. |
| **`--no-mmap` + `-fa 1`** | Avoids crashes/slowdowns | Mandatory on GPU backends. |

**MTP nuance (counterintuitive):** aggressive drafting with LOW acceptance (39.6%) **beat** conservative `p-min 0.60` (75.4% acceptance), because throughput tracks *tokens-accepted-per-verify-pass*, not acceptance percentage. Q4_K_M: 10.88 → 14.27 t/s (1.31×); Q8_0: 7.65 → 13.10 t/s (1.71×). The inflated "2.4×" figures floating around came from short generations with warm prompt-cache reuse.

**Multi-model serving:** pablo-ross runs 4–5 models concurrently on 128 GB (Qwen3-Coder-Next :8080, Bielik-11B :8081, Qwen2.5-7B :8082, Nomic Embed v2 :8083, DeepSeek-R1 :8084), each a separate `llama-server` under systemd, fronted by an **nginx OpenAI-compatible gateway** routing on the JSON `model` field. ~66–78 GB used. **CPU threads (32) become the bottleneck at 6+ models, before memory does** — plan thread budgets.

### 8.1 ⚠ Correctness warning (hard-won)

A llama.cpp build (commit `666f8898a`, Aug 2026) benchmarked **+22% faster** but **silently corrupted output** above ~1600 prompt tokens — mangled tokens, repetition loops, and cross-request KV leakage. **A fast benchmark is NOT a correctness test.** Before promoting any build: keep binary backups and run a long (~2000-token) exact-answer health probe. The fix was pinning to a known-good binary with an `LD_LIBRARY_PATH` prefix.

---

## 9. Fine-Tuning on Strix Halo

### 9.1 Feasibility & measured memory/time (Gemma-3, 2 epochs, max_length 512)

| Model | Full FT | LoRA | 8-bit LoRA | QLoRA |
|---|---|---|---|---|
| Gemma-3 1B | 19 GB / 2m52s | 15 GB / 2m | 13 GB / 8m | 13 GB / 9m |
| Gemma-3 4B | 46 GB / 9m | 30 GB / 5m | 21 GB / 41m | 13 GB / 9m |
| Gemma-3 12B | 115 GB / 25m | 67 GB / 13m | 43 GB / 2h38m | 26 GB / 23m |
| Gemma-3 27B | **OOM** | **OOM** | 32 GB unstable | **19 GB runs** |
| GPT-OSS-20B MXFP4 | — | 32–38 GB / ~1h | — | — |

**Memory rule of thumb:** Full FT needs ~**params × 16 bytes** (weights + gradients + 2× Adam states). 27B ≈ 324 GB → **impossible even with 2-node FSDP on 128 GB**. Use **LoRA/QLoRA for anything ≥ 12B**; QLoRA is the reliable path for 27B.

### 9.2 Tools & recipes

- **LLM Fine-tuning toolbox** (`kyuz0/amd-strix-halo-llm-finetuning`): Fedora + ROCm 7 nightly (TheRock) + Jupyter Lab. Supports QLoRA / LoRA / 8-bit LoRA / Full FT for Gemma-3, Qwen-3, GPT-OSS-20B.
- **Attention:** use `attn_implementation="eager"` for Gemma/Qwen **training** — FlashAttention-2 is inference-only.
- **GPT-OSS** needs `Mxfp4Config(dequantize=True)`.
- **Multi-node:** DDP (replicate, sync grads) for models that fit one node (1B/4B/12B LoRA/QLoRA); FSDP (shard) when too large. TLDR: **use DDP unless OOM, then FSDP.** Launcher: `start-finetuning-cluster.py` (TUI); `benchmark_configs.py` estimates memory for all combos.
- **Datasets:** HF repo ID or local JSONL with a `messages` column (HF Chat Template format).
- **GGUF merge:** for llama.cpp deployment, merge LoRA adapters into the GGUF after training.

---

## 10. Utilities & Applications

| Utility | Purpose | Install / use |
|---|---|---|
| **AI Toolbox Cockpit** | Unified TUI managing all backends (containers, models, servers) | `pipx install git+https://github.com/kyuz0/ai-toolbox-cockpit.git && ai-toolbox-cockpit` |
| **amd-debug-tools / amd-ttm** | Set TTM page limit on systemd-boot systems | `pipx install amd-debug-tools; sudo amd-ttm --set 124` |
| **tuned / tuned-adm** | CPU power profiles | `sudo tuned-adm profile accelerator-performance` |
| **gguf-vram-estimator.py** | Estimate VRAM incl. context overhead | `gguf-vram-estimator.py MODEL.gguf --contexts 32768` |
| **llama-bench / llama-batched-bench** | llama.cpp throughput | use `-mmp 0` not `--no-mmap` |
| **ds4-bench** | DwarfStar throughput | ships with ds4 toolbox |
| **rocm-smi / rocminfo** | GPU monitoring & enumeration | from ROCm |
| **sysfs GTT inspection** | Check actual GPU memory pool | `cat /sys/class/drm/card*/device/mem_info_gtt_total` / `_used` |
| **GPU workload watch** | systemd service auto-switching TuneD + Framework fan on LLM process detection | ships with toolboxes |
| **nginx** | OpenAI-compatible multi-model gateway (routes on JSON `model`) | config per §8 |
| **docker-relay** | nginx + FastAPI + Cloudflare Tunnel for external exposure without inbound ports | pablo-ross |
| **llm-watchdog** | systemd health-check timer | pablo-ross |
| **model_manager** | ComfyUI model downloader TUI | ComfyUI toolbox |
| **start-vllm / start-vllm-cluster** | vLLM TUI wizards | vLLM toolbox |
| **Mainline kernel installer** | Install newer kernels on Ubuntu | `ppa:cappelikan/ppa` |
| **ectool / fw-ectool** | Framework fan control | DHowett/framework-ec |

**Application layer (from AMD playbooks):** Open WebUI (chat), OpenHands + Lemonade (agents), Hermes Agent / OpenClaw (autonomous agents), n8n (workflow automation), LM Studio (GUI), ComfyUI + Z-Image Turbo (image gen). Filter playbooks by device / OS / memory / difficulty.

---

## 11. Recommended Turnkey Setups

### A. Daily driver — chat + coding + agents (best all-round)
- Fedora 43/44, kernel 6.18.x, `amd_iommu=off amdgpu.gttsize=126976 ttm.pages_limit=32505856`, linux-firmware 20251111, `accelerator-performance`.
- **Qwen3.8-Flash-Next UD-Q4_K_XL** via **Gufo/ROCm** with MTP (`--spec-draft-n-max 3`, default p-min).
- ~46 t/s decode, 1624 t/s prefill, 100% agentic pass@1.

### B. Max intelligence
- Same host. **Qwen3.8-Flash-Next Q4_K_XL** (100%) or **DeepSeek-V4.1-Flash Q2 imatrix** via **DwarfStar** (94.7%).
- Use imatrix-calibrated quants; keep context ≤ 130K on 128 GB.

### C. Fast workhorse (raw throughput)
- **Qwen3.6-35B-A3B UD-Q4_K_XL** via **llama.cpp-perf / Vulkan** — 91 t/s decode. Accept the lower agentic quality (57.9%) for speed-critical, simpler tasks.

### D. Multi-model serving / agents
- 4–5 `llama-server` instances on separate ports under systemd + **nginx OpenAI-compatible gateway**. Watch the 32-thread CPU ceiling at 6+ models. Use ROCm 10.0 native host build (not the old 7.0-rc container, due to the 6.17 KFD break).

---

## 12. Final Summary

1. **Physics first:** Strix Halo decode is bandwidth-bound (~208 GB/s). **MoE with few active params is the winning architecture** — it beats dense models of every size on decode.
2. **Setup is mostly kernel + firmware:** `amd_iommu=off` + GTT/TTM params + a non-broken `linux-firmware` + `accelerator-performance` profile + kernel 6.18.x. Get these right and everything else is gravy.
3. **Backend is second-order:** ROCm vs Vulkan differ by ~1.6% on decode. Pick `vulkan-radv` for compatibility, `rocm-10.0` for performance. Don't chase the "Vulkan 2× faster" myth — it didn't reproduce.
4. **Model choice is first-order:** Qwen3.8-Flash-Next (Q4_K_XL) is the current standout — fast *and* smart. Match quant (Q4_K_XL sweet spot) and use imatrix quants for agents.
5. **Tuning levers, ranked:** MoE > lower quant > IOMMU off (+5–12%) > TDP 85W (+19%) > tuned profile (+5–8% prefill) > MTP spec-decode (1.3–1.7×).
6. **Fine-tuning:** LoRA/QLoRA up to 12B is comfortable; 27B full FT is impossible on 128 GB — use QLoRA.
7. **Verify correctness, not just speed:** the worst failure mode (silent output corruption) hides behind a *faster* benchmark. Always run a long exact-answer health probe before trusting a build.
8. **Tooling:** the **AI Toolbox Cockpit** + the **kyuz0 toolbox ecosystem** is the most complete, maintained path; AMD's official playbooks were still a "Coming Soon" catalog at the time of research.

---

## 13. Sources

1. **strix-halo-toolboxes.com** (kyuz0 / Donato Capitella) — host config (kernel params, tuned, multi-user), six toolboxes (llama.cpp, ComfyUI, vLLM, Fine-tuning, DwarfStar/ds4, AI Toolbox Cockpit), firmware warning, ROCm landscape.
2. **kyuz0/amd-strix-halo-toolboxes** (GitHub, 1974★) — pre-built llama.cpp containers, ROCm 10.0 / Vulkan / Gufo / experimental forks, VRAM estimator, container engine notes.
3. **Gygeek/Framework-strix-halo-llm-setup** (GitHub, 113★) — BIOS/kernel/udev/ROCm/llama.cpp setup steps for Framework Desktop (note: single README, no scripts; some numbers outdated).
4. **pablo-ross/strix-halo-gmktec-evo-x2** (GitHub) — real per-model benchmarks, kernel 6.17 KFD ABI break, silent output-corruption bug, MTP tuning, multi-model production setup.
5. **local-llm-benchmarks.dev** (87 jobs) — prefill/decode t/s across models, quants, engines, context depths.
6. **terminal-bench-mini** (kyuz0.github.io, 21 models) — agentic coding pass@1 over 19 terminal tasks.
7. **GitHub issue #66** (Lars Urban / urbanswelt, 2026-05-15) — three-way IOMMU benchmark (off vs Translated vs iommu=pt).
8. **developer.amd.com/playbooks** (device=halo) — official playbook catalog (mostly "Coming Soon" as of 2026-10-03).
9. **Reddit r/StrixHalo** — *unreachable (403)*; community perspective drawn indirectly via GitHub issues and project docs instead.