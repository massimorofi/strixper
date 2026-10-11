# Strix Halo LLM Models selection
I have asked Halogen using strixper AI-Chat (Agentic) to ranke the best models. I use the sources available online and as reference teh models of AI-Toolbox.

## The governing constraint
Your box: **~208 GB/s sustained memory bandwidth** (256-bit LPDDR5x-8000), 122.7 GB usable RAM. Decode speed on Strix Halo is almost purely bandwidth-bound:

```
t/s ≈ 208 GB/s ÷ bytes_streamed_per_token
```

This is why **active parameters**, not total parameters, decide velocity. A 3B-active MoE runs ~5× faster than a 27B dense model of similar quality.

## Ranked: velocity × intelligence

| # | Model | Active | Measured t/s | Prefill t/s | Intel | Verdict |
|---|---|---|---|---|---|---|
| 1 | **Qwen3-Coder 30B-A3B** UD-Q4_K_XL | 3B | **96.8** | 1,320 | High (code) | **Best overall dev route** |
| 2 | **Qwen3-30B-A3B-2507** IQ4_XS | 3B | **103.2** | 1,438 | Med-High | Fastest usable general model |
| 3 | **Qwen3.6 35B-A3B** Q4_0 | 3B | **81.3** | 1,244 | High | Best speed/quality balance |
| 4 | **Gemma 4 26B-A4B IT QAT** | 4B | **74.8** | 1,432 | High | Best prefill of the tier |
| 5 | **Nemotron 3 Nano 30B-A3B** | 3B | 76.0 | 1,312 | Medium | Solid, underrated |
| 6 | **Qwen3.5 35B-A3B** | 3B | 75.2 | 1,170 | High | Safe all-rounder |
| 7 | **gpt-oss-120b** MXFP4 | 5.1B | 55.6 | 727 | High | 71% BW eff., good |
| 8 | **Qwen3-Next 80B-A3B** | 3B | 62.1 | 676 | High | Weak prefill, odd |
| 9 | **Qwen3.8-Flash-Next** (Halogen) | ~6B | **50.7** | 1,275 | High | **Your current engine** |
| 10 | **Qwen3.8 27B dense** Q4_K_M | 27B | **20.4** | 292 | Very High | Too slow to use |
| 11 | **Nemotron 3 Super 120B-A12B** | 12B | 18.4 | 295 | High | Capacity proof only |
| 12 | **DeepSeek V4 Flash 284B** IQ2 | 57B | **13.3** | 156 | Very High | Unusable interactively |

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
