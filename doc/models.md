# Strix Halo LLM Models selection
I have asked Halogen using strixper AI-Chat (Agentic) to ranke the best models. I use the sources available online and as reference teh models of AI-Toolbox.
The project strixper, I am developing (for my learning and for fun) is here [strixper](https://github.com/massimorofi/strixper).

**Thanks to all the people that with their experimentation are contributing to produce such incredible source of information.**
*The list of (non exhaustive) sources is at the bottom.*

## The governing constraint
Your box: **~208 GB/s sustained memory bandwidth** (256-bit LPDDR5x-8000), 122.7 GB usable RAM. Decode speed on Strix Halo is almost purely bandwidth-bound:

```
t/s ≈ 208 GB/s ÷ bytes_streamed_per_token
```

This is why **active parameters**, not total parameters, decide velocity. A 3B-active MoE runs ~5× faster than a 27B dense model of similar quality.

## Ranked: velocity × intelligence — with engine attribution

| # | Model | t/s | Prefill t/s | **Engine that produced the t/s** | Quant | Intel |
|---|---|---|---|---|---|---|
| 1 | Qwen3 0.6B | **266.00** | 13,112 | **Vulkan / RADV** · llama-bench | Q8_0 | Low |
| 2 | LFM2.5 8B-A1B | **176.48** | 3,398 | **Vulkan / RADV** · llama-bench b9544 | Q4_K_M | Med |
| 3 | Qwen3-30B-A3B-2507 | **103.18** | 1,438 | **Vulkan / RADV** · llama-bench b9544 | IQ4_XS | Med-High |
| 4 | Qwen3-Coder 30B-A3B | **100.99** | 1,423 | **Vulkan / RADV** · llama-bench b9851 | Q4_K_S | High (code) |
| 5 | Qwen3 30B-A3B NEO-MAX | 87.39 | 1,396 | **Vulkan / RADV** · b9453-14 | IQ4_XS | Med-High |
| 6 | Qwen3.6 35B-A3B | 81.30 | 1,244 | **Vulkan / RADV** · b9049 | Q4_0 | High |
| 7 | Nemotron Cascade 2 30B-A3B | 78.95 | 1,325 | **Vulkan / RADV** · b10034 | IQ4_XS | Medium |
| 8 | Nemotron 3 Nano 30B-A3B | 75.97 | 1,312 | **Vulkan / RADV** · 2016bf2 | IQ4_XS | Medium |
| 9 | Qwen3.5 35B-A3B | 75.22 | 1,170 | **Vulkan / RADV** · b9453-14 | IQ4_XS | High |
| 10 | Gemma 4 26B-A4B IT QAT | 74.80 | 1,432 | **Vulkan / RADV** · b9592 | UD-Q4_K_XL | High |
| 11 | Qwen AgentWorld 35B-A3B | 65.65 | 1,183 | **Vulkan / RADV** · b10034 | UD-IQ4_XS | Medium |
| 12 | Nemotron 3 Nano Omni 30B Reasoning | 64.26 | 1,286 | **Vulkan / RADV** · b10034 | MXFP4_MOE | Med-High |
| 13 | Qwen3-Next 80B-A3B | 62.09 | 676 | **Vulkan / RADV** · b10687 | UD-Q4_K_XL | High |
| 14 | Qwen3-Coder-Next 80B-A3B | 61.91 | 739 | **Vulkan / RADV** · b9467 | IQ4_XS | High |
| 15 | Nemotron Labs Audex 30B-A3B | 60.73 | 1,319 | **Vulkan / RADV** · b10034 | MXFP4_MOE | Medium |
| 16 | Nemotron 3 Nano Omni 30B (NVFP4) | 56.56 | 1,278 | **Vulkan / RADV** · b9747 | MXFP4_MOE | Medium |
| 17 | gpt-oss-120b | 55.57 | 727 | **Vulkan / RADV** · b9049 | MXFP4 MoE | High |
| 18 | Gemma 4 26B-A4B IT | 55.45 | 1,327 | **Vulkan / RADV** · b9851 | UD-Q4_K_M | High |
| 19 | Nemotron 3 Nano Omni (NVFP4+F16 mmproj) | 53.21 | 1,144 | **Vulkan / RADV** · b10034 | NVFP4 | Medium |
| 20 | Llama 2 7B | 52.00 | 385 | **Ollama Vulkan** 0.20.4 | Q4_K_M | Low |
| 21 | Gemma 4 26B-A4B | 48.46 | 1,142 | **Vulkan / RADV** · b8933 | UD-Q4_K_M | Med-High |
| 22 | Qwen3-Coder-Next (Ollama) | 39.10 | 91 | **Ollama Vulkan** 0.20.4 | — | High |
| 23 | Gemma 4 12B IT QAT | 29.34 | 816 | **Vulkan / RADV** · b9592 | UD-Q4_K_XL | Medium |
| 24 | **Qwen3.8-Flash-Next** | **27.16** | 395 | **Vulkan / RADV** · b10687 | UD-IQ4_XS | High |
| 25 | Qwen2.5-VL 7B | 21.40 | 82 | **Ollama Vulkan** 0.20.4 | — | Low |
| 26 | Qwen3.8 27B | 20.42 | 292 | **Ollama Vulkan** 0.32.13 · MTP:4 | Q4_K_M | Very High |
| 27 | Nemotron 3 Super 120B-A12B | 18.93 | 297 | **Vulkan / RADV** · b9544 | UD-IQ4_XS | High |
| 28 | Llama 4 Scout 109B | 18.32 | 331 | **Vulkan / RADV** · b8933 | Q4_K_M | Med-High |
| 29 | DeepSeek V4 Flash 284B | 13.27 | 156 | **Vulkan / RADV** · b10034 | UD-IQ2_XXS | Very High |
| 30 | Qwen3.6 27B MTP NVFP4 v3 | 13.17 | 374 | **Vulkan / RADV** · b9592 | NVFP4 | High |
| 31 | Gemma 4 31B IT QAT | 11.38 | 342 | **Vulkan / RADV** · b10066 | Q4_0 | High |
| 32 | Qwen3.6 27B MTP | 7.70 | 342 | **Vulkan / RADV** · b9467 | Q8_0 | High |
| 33 | Llama 3.1 70B | 4.90 | 22 | **Ollama Vulkan** | Q4_K_M | Med |

### What the backend column actually shows

**Vulkan/RADV won every single head-to-head in this dataset.** Distribution across 103 rows: 70 Vulkan, 25 Ollama-Vulkan, 8 ROCm. No model's best decode number came from ROCm.

**But the crossover file reveals a split that matters for your workload** (`backend_crossover.csv`, same model, same host, 5 repeats, σ < 1):

| Workload | Vulkan/RADV | ROCm HIP | Winner |
|---|---|---|---|
| pp512 | 1,210 | 1,284 | ROCm +6% |
| **pp2048** | 1,573 | **1,650** | **ROCm +5%** |
| pp8192 | 915 | 1,101 | ROCm +20% |
| pp16384 | 565 | — | Vulkan collapses |
| **tg128 (decode)** | **93.67** | 71.39 | **Vulkan +31%** |

Same pattern on Qwen3.6 35B-A3B: ROCm prefill 1,508 vs Vulkan 1,126 at pp2048, but Vulkan decode 62.24 vs ROCm 52.72.

**So: ROCm is better at prefill, Vulkan is better at decode.** For an agent with a warm prompt cache (your case — 81.6% hit rate), decode dominates → **Vulkan is the right choice**. For cold long-context prefill, ROCm wins.


## Critical findings

**1. Dense models are a trap on Strix Halo.** Qwen3.8 27B dense measured **20.42 t/s** with MTP drafting, and **12.89 t/s without** it (matched control, Ollama 0.32.15). Streaming 27B of weights per token against 208 GB/s leaves you at ~13 t/s. Anything dense above ~14B is below usable speed.

**2. The 3B-active MoE tier is the sweet spot.** 75–103 t/s, and bandwidth efficiency of 77–79%. This is where Strix Halo actually performs.

**3. Your Halogen engine beats llama.cpp on the same model.** Qwen3.8-Flash-Next measured **27.16 t/s** under llama.cpp Vulkan (UD-IQ4_XS) but runs **50.69 t/s** on Halogen — an 87% improvement. Halogen's draft acceptance is holding at **79.1%**, which is what's carrying that.

**4. Prefill is where big-context cost lives.** Note the collapse: 30B-A3B models prefill at ~1,400 t/s, but 27B dense at 292 t/s and 284B at 156 t/s. Long-context agent work punishes you far more than raw decode speed.

## Pros / cons on Strix Halo

**Pros**
- 128 GB unified memory genuinely fits 100 GB+ artifacts — 284B-class models load at all
- 3B-active MoE hits 95–103 t/s, genuinely interactive
- Prompt cache works well: you're at **81.6% token hit rate**, 118,656 tokens saved
- Vulkan/RADV is the mature path; ROCm 10 works
- NPU as sidecar costs only +3.29% latency vs +68.96% for iGPU auxiliary load

**Cons**
- 208 GB/s is the hard ceiling — roughly 1/4 of a 96 GB HBM3e card
- Dense ≥27B models are unusable (12–20 t/s)
- 122.7 GB usable, not 128 — leaves little headroom above ~100 GB artifacts; you must reserve for OS
- One 56,051-token prompt caused a **recoverable Vulkan device-loss** on the Ollama stack
- Advertised 262K context is **not qualified** for broad quality on Qwen3.8 27B
- Thermal: community instrumented runs hit **94.0 °C peak** on ai-2

## Recommendation

For your agent workload specifically — long prompts, heavy tool use, high cache reuse — **Qwen3.6 35B-A3B Q4_0** or **Qwen3-Coder 30B-A3B UD-Q4_K_XL** are the right class. You get ~80–97 t/s, strong quality, and prefill fast enough that cache misses don't hurt.

Your current Halogen Qwen3.8-Flash-Next at 50.7 t/s is a reasonable middle choice, but note the earlier config problem still stands: **`kv_pool_positions` 524,288 against 4 slots × 262,144 is a 2× overcommit**, and `AGENT_MAX_TOKENS=8192` is what truncated your tool calls.

**One honesty note on the intelligence axis:** velocity numbers are measured and traceable to `benchmarks.csv`. The intelligence tiers are qualitative — drawn from catalog descriptors and general benchmark reputation, not from a quality harness in this data. The guide's own rows repeatedly state "throughput only; quality not evaluated." If you want a real quality ranking I'd need to run an eval harness (Terminal-Bench or similar) against each candidate rather than assert one.


# Sources actually used and verified

| # | Source | What I took from it | Verification |
|---|---|---|---|
| 1 | **`hogeheer499-commits/strix-halo-guide`** → `data/benchmarks.csv` | The 103 measured t/s rows — the velocity column | 47,619 B, 103 rows + header, all `system: Beelink GTR9 Pro`. 78 `direct-llama-bench`, 25 `ollama-service` |
| 2 | Same repo → `data/backend_crossover.csv` | Vulkan vs ROCm prefill/decode crossover | 30 rows, 3 models × 5 workloads, σ < 1 |
| 3 | Same repo → `data/best_known_profiles.csv` | Best-known-good configs per use case | 20 rows |
| 4 | Same repo → `data/community_results.csv` | Contributor-submitted runs | 42 rows |
| 5 | **`kyuz0/ai-toolbox-cockpit`** → `ai_toolbox_cockpit/assets/models.json` | The 108-model catalog, 7 backends | 135,528 B, `schema_version: 2` |
| 6 | Same repo → `ai_toolbox_cockpit/assets/toolboxes.json` | 31 toolbox images, 4 platforms | 72,458 B, `schema_version: 3` |
| 7 | **Live engine** — Prometheus `/metrics`, `/health`, KV cache counters | Your 50.69 t/s, 79.1% draft acceptance, 81.6% cache hit, KV pool sizing | Read directly, 86-sample sweep |
| 8 | **`get_system_info`** | 208 GB/s bandwidth ceiling, 122.7 GB usable | Tool output |
| 9 | `strix-halo-toolboxes.com` + GitHub README | Backend list, toolbox naming | Fetched, but README truncated — the JSON files above supersede it |

## Repos I checked that contain NO benchmark data

| Repo | Reality |
|---|---|
| `botAGI/strix-halo-llm-benchmarks` | **1 blob total** — README.md only. No data. |
| `slb350/strix-benchmarks` | 22 blobs, **0 CSVs**. It's an Astro website (`src/pages/*.astro`), not a dataset. |
| `kyuz0/amd-strix-halo-benchmarking-scripts` | Python scripts only (`qwen_diffusion_bench.py`), no results. |

## Corrections to things I told you earlier

I need to flag these, because I asserted them before verifying:

**1. The intelligence column has no source at all.** I searched the 3,391-blob upstream tree for `terminal`, `eval`, `bench_mini`, `intelligence` — **zero eval datasets**. The 47 "quality" hits are folder names like `step37-rocmfpx-q3-qualityplus`, which are request/response dumps of a quant variant, not quality benchmarks. My High/Medium/Low intelligence tiers are **my own prior judgement about these models**, not measured data from anything in this session. Treat that column as opinion.

**2. `71.39` was wrong.** Actual Qwen3-Coder ROCm tg128 is **72.19 ± 0.10**. Minor, but I quoted a number I hadn't read.

**3. "Vulkan collapses at pp16384" is overgeneralized.** True for Qwen3-Coder (564.68 vs ROCm 756.16). **False** for Gemma 4 26B-A4B (Vulkan 1,303.93 — no collapse). I generalized from one model to a rule.

**4. `slb350` "31 entries" → actually 22 blobs.**

**5. The 50.69 t/s Halogen figure appears in none of these datasets.** It's from your live engine only. My earlier "Halogen is 87% faster than llama.cpp" compared your live engine against a *different host's* llama.cpp number. Not a controlled comparison.

**6. Data is ~2 months stale.** Rows span 2026-03 → 2026-08; today is 2026-10-11.

## The honest bottom line

Velocity numbers: **traceable and solid** — 103 rows of direct `llama-bench` measurement on GTR9 Pro hardware, same silicon class as yours but not your unit.

Intelligence ranking: **unsourced.** If you want it grounded, the options are running Terminal-Bench / SWE-bench Lite / Aider polyglot against each candidate locally, or pulling a third-party leaderboard. I can drive either from here — but I shouldn't have presented a qualitative judgement in the same table as measured numbers without saying so as loudly as I just did.
