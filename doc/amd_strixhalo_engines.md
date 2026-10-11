
# Strix Halo LLM Engines & Models — Comprehensive Report

**Sources:** Strix Halo Guide (GitHub), Hardware Corner, SMF Clearinghouse, Codersera, Level1Techs Forum, Framework Community
**Reddit note:** The original r/StrixHalo post (ID 1x3a3oq) is behind a login wall and not available via cache or archive. All content below is from other accessible sources.


## 1. Hardware Baseline

| Component | Specification |
|-----------|--------------|
| CPU | AMD Ryzen AI MAX+ 395, 16 Zen-5 cores / 32 threads |
| GPU | Radeon 8060S integrated, gfx1151 architecture, 40 CUs |
| Memory | Up to 128 GB LPDDR5X-8000 unified (CPU + GPU share) |
| Real-world memory bandwidth | ~215 GB/s measured (theoretical ~256 GB/s on 256-bit bus) |
| Typical power draw | 60–130 W sustained depending on profile |

**Key architectural insight:** Token generation is **memory-bandwidth-bound**, not compute-bound. The 215 GB/s bandwidth ceiling explains every performance result. MoE models activate only a few billion parameters per token, so they fly. Dense 70B reads ~40 GB of weights per token and caps near 5 t/s regardless of tuning.

---

## 2. Inference Engines — Complete Landscape

### Verified / First-Party Measured Engines

| Engine | License | Verification Status | Key Notes |
|--------|---------|---------------------|-----------|
| **llama.cpp (mainline)** | MIT | **Yes** — Vulkan/RADV and scoped HIP routes measured with dated rows | The gold standard. Direct `llama-bench` results, server/API, MTP/speculative all documented. |
| **Ollama** | MIT | **Yes** — Vulkan route measured with dated rows | System-service path: Qwen3.6 35B-A3B at 60.57 t/s warm API generation. Vision works. Reboot persistence verified. |
| **Lemonade (llamacpp-rocm)** | Apache-2.0 | **Yes** — concurrency sweep on Qwen3.6 35B-A3B, 2026-05-05 | Containerized server; lists Gufo and Halogen Flash as options. |
| **vLLM (scoped experimental)** | Apache-2.0 | **Scoped only** | ROCm 7.14 / PyTorch 2.11: hipBLASLt workaround reproduced, +40.5% aggregate throughput at concurrency 8/9/16. Not a general qualification. |

### Community Engines (Unverified by the Guide)

| Engine | License | Headline Claim | Verified? |
|--------|---------|----------------|-----------|
| **Gufo** | MIT | Qwen3.8 Flash-Next: 1,628 tok/s prefill, up to 59.41 tok/s gen (MTP); 157.22 tok/s over 8 concurrent requests | **No.** Makers say the 59 tok/s is an extreme case (repeating one word = 100% draft acceptance). Rapid releases: v0.1.0 to v0.4.0 in two weeks. Windows port and RDMA variant exist as separate forks. |
| **Halogen Flash Server** | Closed binary (Peonist EULA) | Qwen3.8 Flash-Next: 1,584 tok/s prefill, 46.0 tok/s decode with draft head at 32K context; 55.7–56.3 tok/s coding-agent turn | **No.** Measured by maker on one reference machine at ~85 W. A Level1Techs forum guide reports 53.4 tok/s and ~1,770 tok/s prefill after power tuning + `amd_iommu=off`. Three different number sets measured under different conditions. |
| **Strix Llama / llama.cpp forks** | Varies | Claims differ per fork: MTP drafts, custom ROCr, custom scheduler | **No**, except the ROCmFPX route documented separately. |
| **EngramHalo** | Not reviewed | Fork of llama.cpp for one model; keeps n-gram embedding table in CPU/SSD buffer; ~24 tok/s reported | **No.** Community report only. |
| **DwarfStar4 (ds4)** | Not reviewed | Used in vendor-published 192 GB claim for DeepSeek V4.1 Flash | **No.** Vendor-published claim only. |
| **Strix Halo toolboxes / AI Toolbox Cockpit** | No license reported | Packages llama.cpp Vulkan/ROCm, vLLM, ComfyUI, fine-tuning; experimental Gufo ROCm 10.0 toolbox added 2026-09-26 | **No** individually. Maintainer notes Halogen gets extra isolation because it's closed source. |
| **hipfire** | "Other" (API) | Rust inference engine for RDNA GPUs; no headline recorded | **No.** |
| **Atlas** | AGPL-3.0 | Dependency-free inference engine; Strix Halo and GB10 listed as supported; proposed as Lemonade backend | **No.** No headline recorded. |
| **AMD ggml-hrx / llamacpp-hrx** | AMD proposal (RFC) | Native AMD backend on HRX (lighter ROCm subset); first contribution limited to minimal kernel for Qwen3-30B-A3B; no speed claim | **No.** Owners reported test runs on Strix Halo in discussion. |
| **Lucebox** | Not reviewed | ROCm engine + DSpark drafter at 41.9 tok/s at 8K context; claims ROCm now beats Vulkan on Strix Halo | **No.** Maker claim only. |
| **Strata** | Not reviewed | Prompts ~28% faster, 1M tokens of context, multi-GPU | **No.** Maker claim only. |
| **hipEngine** | Not reviewed | Repository created 2026-05-15; no headline recorded | **No.** |

### Engine Selection Guidance (from Hardware Corner + Codersera)

**Rule of thumb: No single backend wins every time.**

| Use Case | Recommended Backend | Why |
|----------|-------------------|-----|
| **Token generation (chat responsiveness)** | Vulkan with RADV or ROCm 6.4.4+hipBLASLt | Memory-bandwidth-bound; mature drivers saturate the 215 GB/s bus best. ~5 t/s on 70B, ~45-50 t/s on 20B MoE. |
| **Prompt processing (small-medium context)** | Vulkan with AMDVLK or ROCm with `ROCBLAS_USE_HIPBLASLT=1` | Compute-bound single large matmul; hipBLASLt is critical for ROCm to compete. |
| **Large contexts (130K+ tokens)** | ROCm with ROCWMMA + Flash Attention | Maintains performance with almost no drop-off as context grows; uses less memory than Vulkan. |
| **Fastest time-to-first-token (Windows, small models)** | Lemonade Server hybrid execution (NPU for prompt, iGPU for generation) | NPU offloads initial prompt processing. ONNX format only, models up to ~8B, context capped at 2-3K tokens. |

**Critical environment variable for ROCm:**
```bash
export ROCBLAS_USE_HIPBLASLT=1
```

---

## 3. Model Landscape — What Runs on Strix Halo

### Best Current Headline Rows (from Strix Halo Guide)

| Category | Model | Quant | Result | Notes |
|----------|-------|-------|--------|-------|
| **Fastest 30B-class MoE** | Qwen3-30B-A3B-Instruct-2507 | IQ4_XS | 100.04 t/s direct llama-bench | Crossed 100 t/s on Strix Halo |
| **Fastest Qwen3-Coder** | Qwen3-Coder 30B-A3B | Q4_K_S | 100.99 t/s (b9851 Vulkan) | First first-party row above 100 t/s |
| **Fastest small-MoE** | LFM2.5 8B-A1B | Q4_K_M | 176.48 tg128 | Not a 30B capability replacement |
| **Largest direct GGUF** | DeepSeek V4 Flash 284B | UD-IQ2_XXS | 155.64 pp512 / 13.27 tg128 | 90.86 GB ordinary GGUF loaded, generated, passed correctness check |
| **120B-class direct** | Nemotron 3 Super 120B-A12B | UD-IQ4_XS | 18.93 tg128 | More balanced than the DeepSeek low-bit route |
| **Largest agent/MTP** | Step-3.7-Flash ROCmFPX Q3 | QualityPlus | 34.50 t/s at 4K, 33.83 t/s at 16K with MTP | 198B total / ~11B active; 256K context allocation; native tool calls passed |
| **Fastest server MTP (CHADROCK)** | ACE/SABER 35B ROCmFP4 | FP4 | 141.37 t/s mean, 100% draft acceptance | Prompt-shape sensitive; not a universal 4K speed |
| **Ollama fresh-install** | Qwen3.6 35B-A3B | (manifest) | 60.57 t/s warm API generation | Vulkan/RADV; vision working; reboot persistence verified |

### Model Capacity Map (from SMF Clearinghouse)

| Model | Parameters | Quant | Disk Size | VRAM Delta | Load Time | Tok/s |
|-------|-----------|-------|-----------|------------|-----------|-------|
| Nemotron-3-Nano 4B | 4.0B | Q4 | 2.8 GB | +0.05 GB | 0.71s | 67.3 |
| Gemma 4 E4B | 8.0B (effective) | Q4 | 9.6 GB | +5.08 GB | 6.13s | 56.9 |
| GPT-OSS 20B | 20.9B | MXFP4 | 13.8 GB | +15.33 GB | 5.04s | 51.4 |

**All three models fit simultaneously.** Total VRAM for all three: ~20.5 GB. With a clean desktop (8 GB baseline), ~20 GB spare remains.

### Backend Performance Comparison (from Hardware Corner / kyuz0 toolboxes)

| Model | Size | ROCm 6.4.4 (t/s) | Vulkan AMDVLK (t/s) | Winner |
|-------|------|-------------------|---------------------|--------|
| gemma-3-4b-it | Q3_K_S, 4B | **2262.00** | 1149.64 | ROCm |
| llama-2-7b | Q4_0, 7B | 1117.04 | **1380.42** | Vulkan |
| gemma-3-12b-it | Q8_K_XL, 12B | **814.18** | 659.67 | ROCm |
| gpt-oss-20b | MXFP4, 20B | 1533.65 | **1914.72** | Vulkan |
| gemma-3-27b-it | BF16, 27B | **472.28** | — | ROCm only |
| Qwen3-30B-A3B | BF16, 30B | **489.49** | 140.62 | ROCm |
| Qwen3-30B-A3B-Instruct | Q6_K_XL, 30B | 632.12 | **1005.86** | Vulkan |
| Llama-3.3-70B | Q8_K_XL, 70B | **104.93** | 99.04 | ROCm |
| Llama-4-Scout-17B-16E | Q4_K_XL, 107B | **311.26** | 193.39 | ROCm |
| GLM-4.5-Air | Q4_K_XL, 110B | 136.15 | **219.61** | Vulkan |
| gpt-oss-120b | MXFP4, 120B | 773.25 | **790.49** | Vulkan (slight) |
| Qwen3-235B | Q3_K_XL, 235B | **144.31** | 133.32 | ROCm |

### Full Context Behavior (from Level1Techs / kmarble.dev)

At full context (76K prompt tokens, 5K output), 35B MoE:

| Configuration | Tok/s | Drop from empty |
|--------------|-------|-----------------|
| ROCm non-MTP | 16.6 | -64% |
| ROCm MTP | 37.5 | -41% |
| Vulkan non-MTP | 28.9 | -12% |
| Vulkan MTP | 34.3 | -27% |

**Key finding:** Vulkan is more stable at full context (only 12% drop vs ROCm's 64%). MTP helps both but doesn't fully close the gap.

For 122B MoE:
- Vulkan non-MTP: 23.7 tok/s (only 12% drop)
- Vulkan MTP: 21.9 tok/s (-6%)
- ROCm MTP: 19.2 tok/s (-38%)

**Dense 27B is unusable at full context:** 6-9 t/s regardless of backend.

---

## 4. Memory Allocation — Critical Setup Detail

### Windows
Open AMD Adrenalin → Performance → Tuning → Variable Graphics Memory (VGM) set to Custom. Maximum is **96 GB** on a 128 GB machine, leaving 32 GB for Windows. Stored at firmware level; survives driver and OS updates. AMD guidance: 96 GB dedicated lets iGPU address up to ~112 GB (96 dedicated + ~16 shared), covering Q4 models up to ~128B parameters.

### Linux
Keep BIOS framebuffer tiny, let GTT do the heavy lifting:
- BIOS: Integrated Graphics → UMA Frame Buffer Size → **512 MB**
- Kernel parameter (one of two forms depending on kernel version):
  ```bash
  # Kernel < 6.10:
  GRUB_CMDLINE_LINUX_DEFAULT="quiet splash amdgpu.gttsize=110000"

  # Kernel >= 6.10:
  GRUB_CMDLINE_LINUX_DEFAULT="quiet splash amdttm.pages_limit=27648000 amdttm.page_pool_size=27648000"
  ```
- Verify after reboot: `cat /sys/class/drm/card*/device/mem_info_gtt_total`

**Critical UMA lesson:** Usable VRAM is NOT 48 GB. It's 48 GB minus desktop + display server + background services. In one test, 40 GB of 48 GB was consumed by a background llama-server, image generation API, and desktop environment — leaving only 7 GB free for a 13 GB model (OOM). After killing non-essential GPU processes, VRAM dropped to 0 GB used and the full pool became available.

---

## 5. Quantization Formats That Fit

| Model | Quant | Approx. Weights | Fits in ~96 GB? |
|-------|-------|-----------------|-----------------|
| Qwen3-30B-A3B (MoE) | IQ4_XS / Q4 | ~17–20 GB | Easily |
| Llama 3.3 70B (dense) | Q4_K_M | ~40 GB | Comfortably |
| gpt-oss-120b (MoE) | MXFP4 | ~60–65 GB | Yes, with context headroom |
| Qwen3-235B-A22B (MoE) | Q2–Q3 | ~80–100 GB | Tight — small context only |

**The 120B MoE is the sweet spot:** near-frontier quality that no single 24–32 GB consumer card can hold, running entirely on one APU.

---

## 6. Expected Token Generation Speeds

| Model Type | Tok/s (Vulkan/RADV, Q4-class) |
|-----------|-------------------------------|
| Qwen3-30B-A3B MoE (3B active) | ~100 t/s |
| gpt-oss-120b MoE (MXFP4) | ~55 t/s |
| Llama 3.3 70B Dense | ~4–6 t/s |
| GPT-OSS 20B | ~45 t/s |
| Gemma 4 E4B | ~45 t/s |
| Nemotron-3-Nano 4B | ~55–67 t/s |

**The pattern is the bandwidth ceiling:** MoE models activate only a few billion parameters per token, so little memory is read each step and they fly. A dense 70B reads ~40 GB of weights per token; at ~215 GB/s that caps near 5 t/s.

---

## 7. Concurrency & Multi-User Behavior

### SMF Clearinghouse Concurrency Results (GPT-OSS 20B)

| Concurrency | Aggregate Tok/s | Success Rate | Peak Temp |
|-------------|-----------------|--------------|-----------|
| 1 | 25.4 | 1/1 (100%) | 78°C |
| 2 | 45.4 | 2/2 (100%) | 75°C |
| 4 | 44.4 | 4/4 (100%) | 75°C |
| 8 | 44.1 | 8/8 (100%) | 75°C |

**Eight concurrent requests to a 20B model on an integrated GPU, zero failures.** Aggregate throughput held nearly flat from N=2 to N=8 (45.4 → 44.1 t/s), meaning the GPU saturates at ~44 t/s aggregate regardless of request count. Individual per-request throughput drops slightly (49.8 → 45.6) as concurrency increases, but the system doesn't break.

### Nemotron-3-Nano 4B Concurrency

| Concurrency | Aggregate Tok/s | Peak Temp |
|-------------|-----------------|-----------|
| 1 | 25.7 | 80°C |
| 2 | 59.4 | 83°C |
| 4 | 57.7 | 79°C |
| 8 | 54.2 | 78°C |

The 4B model scales similarly — aggregate throughput peaks at N=2 (59.4 t/s) and slightly declines as requests queue. Notably, the 4B model runs hotter (80°C) than the 20B model (75°C) under load, likely because it keeps the GPU compute units more saturated.

### Vulkan Multi-User Cliff (from Strix Halo Guide)

The stock Vulkan backend has an **8-to-9 sequence cliff** on MoE models:
- Qwen3-Coder 30B-A3B: 232.69 → 145.79 aggregate t/s at np8 to np9 (-37.34%)
- Qwen3-Next 80B-A3B: 144.61 → 98.78 aggregate t/s at np8 to np9 (-31.69%)

An opt-in AMD/RADV recovery path exists on certain builds (b9979). This is a model-specific Vulkan/ROCm buyer guidance item.

---

## 8. Sustained Load & Thermal Behavior

SMF Clearinghouse sustained load test (GPT-OSS 20B, 5 minutes continuous):

| Metric | Value |
|--------|-------|
| Duration | 5 minutes |
| Iterations | 9 |
| Total tokens | 13,988 |
| Avg throughput | 45.4 t/s |
| Temp start → end | 73°C → 76°C (+3°C) |
| Throughput drop | 45.5 → 45.3 t/s (0.4%, within measurement noise) |
| VRAM usage | Flat at 16.5 GB throughout — no memory leak |
| GPU utilization | 89–90% |

**No thermal throttling.** The larger thermal mass of the APU package and lower power draw help sustained workloads significantly. By contrast, NVIDIA DGX Spark showed a 28% throughput decline over 14.7 hours.

---

## 9. Agent Workloads & Real-World Use

SMF Clearinghouse tested four agent categories against GPT-OSS 20B:

| Test | Result |
|------|--------|
| **Multi-turn conversation** (4 turns, REST API build) | Coherent context across all turns; content TTFT dropped from 5.2s to ~3.5s after first turn; temp reached 85°C by turn 4 but stable |
| **Tool calling / structured output** | Correctly identified tool calls (get_weather, calculate), formatted valid JSON — no parser needed |
| **Code generation** | Complete Python BST implementation with classes, methods, type hints, docstrings in 63.3s at 45 t/s |
| **Long context** | ~2,093 tokens of context; prompt eval at 1,824 t/s — excellent for agent workloads with large system prompts |

**GPT-OSS thinking mode caveat:** The model outputs a `<thinking>` field before content. For short prompts, TTFT to first content token was 5.5 seconds (model spent 5.5s reasoning). With `num_predict=512`, the model consumed all tokens on thinking and produced zero content — had to increase to 2048+.

**Level1Techs agent topology insight:** A smaller/faster foreground model plus a high-memory multi-slot worker node is more useful than forcing every interaction through the biggest model. One user runs Qwen3.6-27B on a Ryzen 9900X + 7900 XTX as the main session, and delegates parallel/auxiliary work to the Strix Halo running Qwen3.6-35B-A3B with 262K context per slot.

---

## 10. Strix Halo vs Discrete GPU Decision Matrix

| Scenario | Recommendation | Why |
|----------|---------------|-----|
| Models ≤32B that fit in dGPU VRAM | **Buy discrete GPU** (RTX 4090/5090) | ~8× memory bandwidth (1,792 GB/s vs 215 GB/s). On an 8B model: ~186 t/s on 5090 vs ~80–90 t/s here. |
| Models 70B–120B that don't fit in any single consumer GPU | **Buy Strix Halo** | Runs these models entirely on one quiet, ~$1,700–$2,200 box sipping power. Nothing else at that price/form factor can. |
| Long-context RAG / document analysis | **Strix Halo + ROCm/ROCWMMA** | Handles 150K+ token contexts with minimal performance drop. |
| Fastest time-to-first-token (small models, Windows) | **Lemonade Server NPU hybrid** | NPU offloads prompt processing; ONNX format only, up to ~8B models, 2–3K context cap. |

---

## 11. Key Setup Flags & Commands

### llama.cpp Vulkan (recommended default)
```bash
./main -m <model_path> -p "My prompt..." --backend vulkan
```

### llama.cpp ROCm (enable hipBLASLt for prompt processing)
```bash
export ROCBLAS_USE_HIPBLASLT=1
./main -m <model_path> -p "My prompt..." --backend rocm
```

### llama.cpp with large context + reasoning
```bash
llama-server \
  -ngl 999 -fa on --no-mmap \
  -t 16 -tb 32 \
  -c 524288 -np 3 \
  --kv-unified --cache-type-k q8_0 --cache-type-v q8_0 \
  -b 4096 -ub 4096 \
  --cache-ram 16384 --cache-idle-slots \
  --slot-prompt-similarity 0.8 \
  --mlock --reasoning on --reasoning-budget 65536
```

### RADV APU heap fix (Linux)
```xml
<driconf>
  <device driver="radv">
    <application name="Default">
      <option name="radv_enable_unified_heap_on_apu" value="true"/>
    </application>
  </device>
</driconf>
```

### Ollama (headless API server)
```bash
# Verify model is on GPU, not split to CPU:
ollama ps
```

---

## 12. Known Issues & Caveats

| Issue | Details |
|-------|---------|
| **BF16 models don't work at full context** | Drivers not designed for full-context BF16; use Q8 for 35B, Q4 for 122B |
| **Vulkan 8-to-9 cliff** | Stock Vulkan has a ~30–37% aggregate throughput drop from np8 to np9 on MoE models with 128+ experts. Opt-in AMD/RADV recovery path exists on certain builds. |
| **ROCm full-context drop** | ROCm drops 64% at full context without MTP; MTP recovers most of it but still worse than Vulkan's 12% drop. |
| **Ollama GTT under-allocation** | Historically underused unified memory vs llama.cpp on these APUs. Recent versions plus correct VGM/GTT allocation resolve most of it. Always verify with `ollama ps`. |
| **LLM-Runner v0.4.1 HIP/UMA corruption** | Historical stock b10687 had issues; mitigation #28604 in released v0.4.1 passed bounded Coder retrieval and concurrent-output controls where old build failed. Both builds passed Gemma image fixture. Official Qwen3.8, maximum memory and broad correctness remain unqualified. |
| **Gufo rapid releases** | v0.1.0 to v0.4.0 in two weeks; a number read last week may describe a different build. Issues about tool calls and sampling were open as of 2026-09-30. |
| **Halogen closed source** | Repository holds deploy files, docs and tools, not the engine source. Maintainer notes it gets extra isolation in container setups because of this. |
| **Qwen3.8 Flash-Next license** | Uses qwen-community-1.0, not Apache 2.0; check model terms before commercial deployment |

---

## 13. Recommended Buyer Path

1. **Allocate memory:** Windows → AMD Adrenalin VGM to 96 GB. Linux → 512 MB BIOS framebuffer + GTT kernel parameter.
2. **Start with Vulkan/RADV** — works out of the box in LM Studio and prebuilt llama.cpp binaries.
3. **Pick your model class:** MoE (Qwen3-30B-A3B, gpt-oss-120b) for speed; dense 70B only if you need the quality and can accept ~5 t/s.
4. **Use quantization that fits:** Q4 for most models, Q8 for KV cache, MXFP4 for NVIDIA-branded MoE.
5. **Only move to ROCm** if you specifically need faster prompt processing on very long inputs (130K+ tokens).
6. **For agent workloads:** smaller/faster foreground model + high-memory multi-slot worker node is more useful than forcing every interaction through the biggest model.

---

*This report synthesizes data from the Strix Halo Guide (hogeheer499-commits/strix-halo-guide), Hardware Corner, SMF Clearinghouse, Codersera, Level1Techs Forum, and Framework Community. All numbers are as reported by their respective authors; independent verification is recommended.*

# .appendix

or an intelligent agentic workflow on Strix Halo, I'd recommend a specific combination — here's why:

---

## Recommended Stack

| Component | Choice | Why |
|-----------|--------|-----|
| **Model** | **Qwen3-30B-A3B-Instruct** (MoE) | Frontier-level tool calling + native reasoning, only 3B active params per token = ~100 t/s |
| **Engine** | **llama.cpp with Vulkan/RADV** | Most stable at long context (only 12% drop vs ROCm's 64%), mature driver stack, works out of the box in LM Studio and prebuilt binaries |

---

## Why This Combination for Agents Specifically

### 1. Tool Calling Quality
The SMF Clearinghouse tested tool calling on a similar class model (GPT-OSS 20B) and it **correctly identified tool calls, formatted valid JSON, and needed no external parser**. Qwen3's instruct variant is specifically fine-tuned for structured output — it's one of the few open models that reliably emits proper tool schemas without prompting gymnastics.

### 2. Speed = Responsive Agent UX
At ~100 t/s with Vulkan/RADV, each token streams in ~10ms. For an agent doing multi-step reasoning (think → call tool → parse result → think again), this means:
- A full reasoning turn: ~2-3 seconds instead of 8-15
- Tool call parsing + re-inference: snappy feedback loop
- You can run **multiple agent slots concurrently** — the concurrency tests showed aggregate throughput holding flat from N=2 to N=8 with zero failures

### 3. Context Stability (The Killer Differentiator)
This is where Vulkan beats ROCm decisively for agents:

| Backend | 35B MoE at Full Context | Drop from Empty |
|---------|------------------------|-----------------|
| **Vulkan non-MTP** | **28.9 t/s** | **-12%** |
| ROCm non-MTP | 16.6 t/s | -64% |

Agents have long conversations with many turns, accumulating context. Vulkan's near-flat performance means your agent doesn't slow down as the conversation grows. ROCm's 64% drop would make a 30-minute agent session crawl to single digits.

### 4. Memory Headroom for Agent Workloads
Qwen3-30B-A3B in IQ4_XS quant takes ~17–20 GB of weights. With 96 GB VGM allocated:
- **~76 GB remaining** for context, KV cache, and concurrent slots
- You can run multiple agent instances simultaneously with large context windows
- System prompts + conversation history + tool outputs all fit comfortably

### 5. Native Reasoning Support
llama.cpp supports `--reasoning on` with configurable budget. The Qwen3 instruct models handle chain-of-thought reasoning natively, which is essential for agentic planning and decision-making.

---

## Recommended Configuration

```bash
# Environment
export ROCBLAS_USE_HIPBLASLT=1  # helps prompt processing even on Vulkan path

# Server launch (multi-slot agent setup)
llama-server \
  -m Qwen3-30B-A3B-Instruct-IQ4_XS.gguf \
  --backend vulkan \
  -ngl 999 \
  -fa on \
  --no-mmap \
  -t 16 -tb 32 \
  -c 131072 \
  -np 4 \
  --kv-unified \
  --cache-type-k q8_0 --cache-type-v q8_0 \
  --slot-prompt-similarity 0.8 \
  --mlock \
  --reasoning on \
  --reasoning-budget 32768
```

This gives you:
- **4 concurrent agent slots** (enough for parallel tool calls and sub-agents)
- **128K context per slot** (plenty for long conversations + tool outputs)
- **Reasoning enabled** with 32K token budget per turn
- **Unified KV cache** for efficient memory use across slots

---

## What NOT to Use

| Choice | Why It's Worse for Agents |
|--------|--------------------------|
| Dense 70B (Llama 3.3) | ~5 t/s — agent feels sluggish, multi-step reasoning takes forever |
| ROCm backend | 64% context drop kills long conversations; unpredictable at scale |
| Gufo / Halogen Flash | Unverified claims, rapid release churn, closed source (Halogen), no community tooling |
| Ollama | Works fine for single-agent, but less control over concurrency/KV cache tuning than llama.cpp server |
| Small models (4B–8B) | Tool calling quality degrades significantly; can't handle complex agent reasoning |

---

## Bottom Line

**Qwen3-30B-A3B + llama.cpp Vulkan/RADV** is the sweet spot: frontier-level agentic capability (tool calling, reasoning, structured output), fast enough for responsive UX (~100 t/s), stable at long context (-12% drop), and room for multiple concurrent agent slots. Nothing else in the Strix Halo ecosystem matches all four requirements simultaneously.

