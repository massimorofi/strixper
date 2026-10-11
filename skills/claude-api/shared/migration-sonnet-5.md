## Migrating to Claude Sonnet 5

> **Model ID `claude-sonnet-5` is authoritative as written here.** When the user asks to migrate to Claude Sonnet 5, write `model="claude-sonnet-5"` exactly. Do **not** WebFetch to verify - this guide is the source of truth for migration target IDs. The corresponding entry exists in `shared/models.md`.

Claude Sonnet 5 substantially improves on Sonnet 4.6 for coding and agentic work, reaching what was previously Opus-tier quality on many tasks. Its API surface aligns with Opus 4.7/4.8: manual extended thinking is removed (adaptive or disabled only, adaptive is the default), and non-default sampling parameters are rejected. This section is layered on top of the Sonnet 4.6 migration above - if the caller is jumping from Sonnet 4.5 or older, apply the 4.6 changes first, then this one.

**TL;DR for someone already on Sonnet 4.6:** swap the model ID to `claude-sonnet-5`. Replace any remaining `thinking: {type: "enabled", budget_tokens: N}` with `thinking: {type: "adaptive"}` (the transitional escape hatch is gone - it now 400s), and note that omitting `thinking` now runs adaptive (4.6 ran thinking-off). Strip non-default `temperature`/`top_p`/`top_k`. Re-run `count_tokens()` against `claude-sonnet-5` - the new tokenizer produces ~30% more tokens for the same text, so token-budgeted limits and cost baselines shift (per-token pricing is also lower than Sonnet 4.6: $2/$10 vs $3/$15 per MTok). `effort` defaults to `high`, the same as Sonnet 4.6 - raise to `xhigh` for the hardest coding and agentic tasks (Claude Sonnet 5 supports the full `low`/`medium`/`high`/`xhigh`/`max` range), and give `max_tokens` headroom at `xhigh`/`max` (the new tokenizer means a Sonnet-4.6-tuned `max_tokens` may truncate equivalent output). Then re-tune prompts: Claude Sonnet 5 interprets instructions more literally than 4.6 - holdover style/tone directives now apply at face value; it is more agentic by default and reaches for tools and self-verification loops more readily (with thinking disabled it is less tool-eager - add an explicit nudge); it gives better in-progress updates by default (drop forced "summarize every N tool calls" scaffolding); and code-review harnesses with conservative-reporting instructions may see lower recall (tell it to report everything and filter downstream).

### Breaking changes (will 400 on Claude Sonnet 5)

These bring the Sonnet line onto the same request surface as Opus 4.7/4.8. See the **Per-SDK Syntax Reference** above for the language-specific spelling of each.

**1. Extended thinking removed - adaptive only.** `thinking: {type: "enabled", budget_tokens: N}` returns a 400. The transitional escape hatch that still worked on Sonnet 4.6 is gone. Use adaptive thinking with an effort hint:

```python
# Before - deprecated on Sonnet 4.6, now errors on Claude Sonnet 5
thinking={"type": "enabled", "budget_tokens": 10000}

# After
thinking={"type": "adaptive"},
output_config={"effort": "high"},  # or "xhigh" for the hardest coding/agentic tasks
```

To turn thinking off entirely, set `thinking: {type: "disabled"}` - but see *Adaptive vs. disabled* below before doing so.

**2. Sampling parameters rejected.** Setting `temperature`, `top_p`, or `top_k` to a non-default value returns a 400; omitting the parameter, or passing its default, is still accepted. The safest migration is to omit them entirely and steer with prompting. If the caller was relying on `temperature=0` for determinism, note in the migration comment that it never guaranteed identical outputs.

```python
# Before
client.messages.create(model="claude-sonnet-4-6", temperature=0.2, ...)

# After - omit entirely
client.messages.create(model="claude-sonnet-5", ...)
```

**3. Bedrock only: forced `tool_choice` requires `thinking: {type: "disabled"}`.** On Amazon Bedrock, pass `thinking: {type: "disabled"}` alongside `tool_choice: {type: "tool", name: ...}` or `tool_choice: {type: "any"}`. The Claude API and Vertex AI do not require this.

**Not a request-shape error, but handle it: cybersecurity safeguards.** Claude Sonnet 5 is substantially more cyber-capable than Sonnet 4.6, so - like Opus 4.7/4.8 - requests touching prohibited or high-risk topics may be refused. Handle it as a content outcome (see the `refusal` stop-reason guidance in the Claude Fable 5.1 section if the caller needs a fallback path).

**Unchanged from Sonnet 4.6:** assistant-turn prefills still return a 400 (use `output_config.format` or a system-prompt instruction); the 1M-token context window, the 128k max-output ceiling, prompt caching, batch processing, the Files API, PDF support, vision, and the full server- and client-side tool set all carry over.

### Silent default change: adaptive thinking on when `thinking` is omitted

On Sonnet 4.6, a request with no `thinking` field runs **without** thinking. On Claude Sonnet 5, the same request runs with **adaptive thinking**. This is not an error - but callers who never set `thinking` will now see thinking output (and spend thinking tokens) where they didn't before. `max_tokens` is a hard limit on total output (thinking + response text), so a workload that ran thinking-off on Sonnet 4.6 by omission may now truncate. Either set `thinking: {type: "disabled"}` explicitly to keep the old behavior, or revisit `max_tokens` to leave room for thinking.

### Silent default change: `thinking.display` defaults to `"omitted"`

`thinking.display` defaults to `"omitted"` on Claude Sonnet 5 (matching Opus 4.7/4.8 and Claude Fable 5.1); on Sonnet 4.6 it defaulted to `"summarized"`. With the default, `thinking` blocks stream with empty text - to a streaming UI this looks like a long pause before output. Combined with the adaptive-on-by-default change above, a Sonnet 4.6 caller who omits `thinking` entirely now gets adaptive thinking *and* empty-text thinking blocks. If you stream reasoning to users, set `thinking: {type: "adaptive", display: "summarized"}` explicitly. `display` controls visibility only - thinking happens and is billed the same under every setting.

### New tokenizer (~30% more tokens)

Claude Sonnet 5 uses the same new tokenizer as Opus 4.7/4.8. The same input text produces approximately 30% more tokens than on Sonnet 4.6. No request/response shape changes and no code edits are required, but **everything measured or budgeted in tokens shifts**: `usage` fields and `count_tokens()` results for the same text are higher, the 1M context window holds less text, and a `max_tokens` limit tuned for Sonnet 4.6 may truncate equivalent output. Per-token pricing is $2/$10 per MTok (Sonnet 4.6 is $3/$15), so the cost of an equivalent request differs in both directions: more tokens at a lower rate. Re-run `count_tokens()` against `claude-sonnet-5` rather than reusing counts measured against earlier models, and re-baseline cost dashboards before reacting to measured shifts.

### Choosing an effort level on Claude Sonnet 5

`effort` defaults to `high` when not set (same as Sonnet 4.6 and Opus 4.8). Claude Sonnet 5 supports the full `low`/`medium`/`high`/`xhigh`/`max` range - the first Sonnet-tier model with `xhigh`. **Keep the `high` default for most work and raise to `xhigh` for the hardest coding and agentic tasks**:

| Level    | When to use on Claude Sonnet 5 |
| -------- | ----- |
| `max`    | Tasks needing the absolute highest capability with no token constraint. Can deliver gains in some use cases but may show diminishing returns and is sometimes prone to overthinking - test before committing |
| `xhigh`  | The hardest coding and agentic use cases - the recommended setting for those |
| `high`   | The default; balances token usage and intelligence for most use cases |
| `medium` | Cost-saving step-down from the default - comparable to Sonnet 4.6 at `high` |
| `low`    | Short, scoped tasks and latency-sensitive workloads that aren't intelligence-sensitive (chat, simple lookups) |

As a rough cross-model mapping when migrating: Claude Sonnet 5 at `medium` is comparable in intelligence to Sonnet 4.6 at `high`, and Claude Sonnet 5 at `high` is comparable to Sonnet 4.6 at `max`. When benchmarking, match by observed thinking length rather than effort name.

Claude Sonnet 5 **respects effort levels strictly, especially at the low end**. At `low` and `medium` it scopes its work to what was asked rather than going above and beyond - good for latency and cost, but on moderately complex tasks at `low` there is some risk of under-thinking. If you observe shallow reasoning on complex problems, **raise effort to `high` or `xhigh` rather than prompting around it**. If you must keep effort at `low` for latency, add targeted guidance:

> *"This task involves multi-step reasoning. Think carefully through the problem before responding."*

**Leave `max_tokens` headroom at `xhigh`/`max`.** Set a large output token budget (up to the 128k cap, unchanged from Sonnet 4.6) so the model has room for thinking and tool calls. On long tasks, adaptive thinking can use a large share of the budget; if the budget is tight you may see a response that is almost entirely thinking followed by a truncated answer and `stop_reason: "max_tokens"` - raise `max_tokens` or drop to `medium`. Because Claude Sonnet 5 uses the new tokenizer (~30% more tokens for the same text), `max_tokens` limits tuned for Sonnet 4.6 may truncate equivalent output.

### Adaptive vs. disabled thinking

Leave adaptive thinking on. Claude Sonnet 5 calibrates thinking spend to task complexity; the small added latency is usually worth the quality gain. If the caller was running Sonnet 4.6 with thinking off, **try adaptive + `effort: "low"` first** rather than `thinking: {type: "disabled"}`.

The triggering behavior for adaptive thinking is steerable. If the model emits thinking blocks more often than wanted (which can happen with large or complex system prompts), prompt it directly - and measure the effect on quality:

> *"Thinking adds latency and should only be used when it will meaningfully improve answer quality, typically for problems that require multi-step reasoning. When in doubt, respond directly."*

Conversely, if you're running hard workloads at `medium` and seeing under-thinking, the first lever is to raise effort; if you need finer control, prompt for it directly.

### Capability improvements

**Coding and agentic tasks.** The largest gains over Sonnet 4.6 are in coding and agentic tasks. Claude Sonnet 5 performs well out of the box on existing Sonnet 4.6 prompts.

**High-resolution vision.** Claude Sonnet 5 is the first Sonnet-tier model with high-resolution image support: maximum **2576 pixels on the long edge** (up from 1568px on Sonnet 4.6). High-res images can use up to ~3× more image tokens than on Sonnet 4.6 (4784 vs 1568 tokens per image at the limit) - if the added fidelity isn't needed, downsample before sending to control token costs. No beta header or opt-in required.

**Computer use.** Supports the `computer_20251124` tool version (beta header `computer-use-2025-11-24`). Capability works across resolutions up to the 2576px / 3.75MP maximum; sending screenshots at **1080p** provides a good balance of performance and cost. For particularly cost-sensitive workloads, **720p** or **1366×768** are lower-cost options with strong performance. Test to find the ideal settings for the use case; experimenting with `effort` can also help tune behavior.

### Behavioral shifts (prompt-tunable)

None of these break code, but prompts tuned for Sonnet 4.6 may land differently. Claude Sonnet 5 follows instructions closely, so small explicit directives close the gap.

**Response length and verbosity.** Claude Sonnet 5 calibrates response length to task complexity rather than defaulting to a fixed verbosity - usually shorter on simple lookups, longer on open-ended analysis. If a product depends on a particular verbosity, tune the prompt. To decrease verbosity:

> *"Provide concise, focused responses. Skip non-essential context, and keep examples minimal."*

If you see specific kinds of verbosity (e.g. over-explaining), add targeted instructions to prevent them. Positive examples showing the desired concision tend to be more effective than telling the model what not to do.

**Tool use triggering.** Claude Sonnet 5 is more agentic than Sonnet 4.6 by default and will reach for tools and run self-verification loops more readily. **With thinking disabled**, the model is less likely to reach for tools or consider searching - if the harness relies on tool calls with thinking off, add an explicit nudge in the system prompt. `effort` is also a lever: `high` and `xhigh` show substantially more tool usage in agentic search and coding. For scenarios where you want more tool use, also explicitly instruct when and how to use the tools (e.g. if web-search is under-used, describe in the prompt why and how it should be called).

**User-facing progress updates.** Claude Sonnet 5 provides regular, higher-quality updates to the user throughout long agentic traces by default. If the harness has scaffolding to force interim status messages ("After every 3 tool calls, summarize progress"), **try removing it**. If the length or content of the updates isn't well-calibrated to the use case, describe what they should look like in the prompt and provide an example.

**More literal instruction following.** Claude Sonnet 5 interprets prompts literally and explicitly, particularly at lower effort levels. It does not silently generalize an instruction from one item to another, and it does not infer requests that weren't made. The upside is precision - better for carefully tuned prompts, structured extraction, and pipelines that need predictable behavior. If an instruction should apply broadly, **state the scope explicitly** ("Apply this formatting to every section, not just the first one"). The same literalism means style/tone directives carried over from Sonnet 4.6 may now over-apply - re-baseline holdover lines like "be concise" before keeping them.

**Tone and writing style.** Prose style on long-form writing may shift. If a product relies on a specific voice, re-evaluate style prompts against the new baseline. For a warmer or more conversational voice:

> *"Use a warm, collaborative tone. Acknowledge the user's framing before answering."*

Because `temperature`/`top_p`/`top_k` are not accepted on Claude Sonnet 5, callers who previously relied on `temperature` for stylistic variety must use system-prompt instructions instead.

**Code review harnesses.** A review harness tuned for an earlier model may initially see lower recall on Claude Sonnet 5. This is likely a harness effect, not a capability regression: when a review prompt says "only report high-severity issues" / "be conservative" / "don't nitpick," Claude Sonnet 5 follows that instruction more faithfully than earlier models did - it investigates just as thoroughly, identifies the bugs, and then doesn't report findings it judges below the stated bar. Precision typically rises, but measured recall can fall even though underlying bug-finding ability has improved. Recommended prompt language:

> *"Report every issue you find, including ones you are uncertain about or consider low-severity. Do not filter for importance or confidence at this stage - a separate verification step will do that. Your goal here is coverage: it is better to surface a finding that later gets filtered out than to silently drop a real bug. For each finding, include your confidence level and an estimated severity so a downstream filter can rank them."*

This works even without an actual second step, but moving confidence filtering out of the finding stage often helps. If you do want single-pass self-filtering, be concrete about where the bar is rather than using qualitative terms like "important" - e.g. "report any bugs that could cause incorrect behavior, a test failure, or a misleading result; only omit nits like pure style or naming preferences." Iterate against a subset of evals to validate recall/F1 gains.

**Design and frontend defaults.** Claude Sonnet 5 may settle into a consistent default visual style on open-ended frontend and design briefs. Generic instructions ("don't use that color," "make it clean and minimal") tend to shift it to a different fixed palette rather than producing variety. Two approaches work reliably: **specify a concrete alternative** (the model follows explicit specs precisely - give the palette, typography, layout, and spacing), or **have the model propose options before building** (e.g. "Before building, propose 4 distinct visual directions tailored to this brief - bg hex / accent hex / typeface plus a one-line rationale - ask the user to pick one, then implement only that direction"). Because `temperature` isn't accepted on Claude Sonnet 5, the propose-then-pick approach is the recommended way to get meaningfully different design directions across runs. To steer away from generic AI-aesthetic patterns, a short directive in the system prompt also helps:

> *"NEVER use generic AI-generated aesthetics like overused font families (Inter, Roboto, Arial, system fonts), cliched color schemes (particularly purple gradients on white or dark backgrounds), predictable layouts and component patterns, and cookie-cutter design that lacks context-specific character. Use unique fonts, cohesive colors and themes, and animations for effects and micro-interactions."*

**Interactive coding products.** Token usage and behavior can differ between autonomous, asynchronous coding agents (single user turn) and interactive, synchronous coding agents (multiple user turns). To maximize both performance and token efficiency, use `effort: "xhigh"` or `"high"`, add autonomous features like an auto mode, and reduce the number of human interactions required. Specify task, intent, and constraints upfront in the first turn - well-specified initial prompts maximize autonomy and intelligence while minimizing extra token usage after user turns; ambiguous or progressively-revealed prompts tend to reduce token efficiency and sometimes performance.

### Claude Sonnet 5 Migration Checklist

Every item is tagged: **`[BLOCKS]`** items cause a 400 error or truncated output if missed; **`[TUNE]`** items are quality/cost adjustments - surface them to the user as recommendations.

- [ ] **[BLOCKS]** Update the `model=` string to `claude-sonnet-5`
- [ ] **[BLOCKS]** Replace `thinking: {type: "enabled", budget_tokens: N}` with `thinking: {type: "adaptive"}` + `output_config.effort` - the Sonnet 4.6 transitional escape hatch is gone
- [ ] **[BLOCKS]** Strip `temperature`, `top_p`, `top_k` from request construction (use system-prompt instructions for tone/variety instead)
- [ ] **[BLOCKS]** Bedrock only: pass `thinking: {type: "disabled"}` alongside forced `tool_choice` (`{type: "tool"}` / `{type: "any"}`) - not required on the Claude API or Vertex AI
- [ ] **[BLOCKS]** At `effort: "xhigh"` or `"max"`: set a large `max_tokens` (up to 128k, unchanged from Sonnet 4.6) so the model has room for thinking and tool calls - Sonnet-4.6-tuned limits may truncate equivalent output under the new tokenizer (symptom: `stop_reason: "max_tokens"`)
- [ ] **[TUNE]** Thinking-field omitted: adaptive is now the default (4.6 ran thinking-off) - either set `thinking: {type: "disabled"}` to preserve the old behavior, or revisit `max_tokens` for the added thinking spend
- [ ] **[TUNE]** `thinking.display` defaults to `"omitted"` (4.6 defaulted to `"summarized"`): if you stream reasoning to users, set `thinking: {type: "adaptive", display: "summarized"}` explicitly - the default streams empty-text thinking blocks (long pause before output)
- [ ] **[TUNE]** New tokenizer: re-run `count_tokens()` against `claude-sonnet-5` (~30% more tokens for the same text); revisit `max_tokens` and compaction triggers sized close to expected output length; re-baseline cost dashboards before reacting (per-token pricing is lower than Sonnet 4.6: $2/$10 vs $3/$15 per MTok)
- [ ] **[TUNE]** Effort: keep the `high` default; raise to `xhigh` for the hardest coding/agentic tasks; `medium` is a cost-saving step-down (~ Sonnet 4.6 at `high`); reserve `low` for short, latency-sensitive, non-intelligence-sensitive tasks. If shallow reasoning shows up at `low`/`medium`, raise effort rather than prompting around it
- [ ] **[TUNE]** Thinking-off callers: try `thinking: {type: "adaptive"}` + `effort: "low"` instead of `disabled`; if `disabled` must stay, add an explicit tool-triggering nudge (the model is less tool-eager with thinking off)
- [ ] **[TUNE]** Tool usage: more agentic than 4.6 by default (reaches for tools and self-verification more readily) - `effort` is a lever (`high`/`xhigh` for more tool use); add explicit when/how triggering instructions for under-used tools
- [ ] **[TUNE]** Drop forced progress-update scaffolding ("after every N tool calls, summarize") - the default updates are higher quality; describe the desired update shape if it still needs tuning
- [ ] **[TUNE]** Re-baseline holdover style/tone/scope directives - instructions are followed literally; state the scope explicitly when one should apply broadly
- [ ] **[TUNE]** Verbosity-sensitive routes: tune response length via prompt (positive examples > "don't" instructions)
- [ ] **[TUNE]** Code-review harnesses with conservative-reporting instructions ("only high-severity", "don't nitpick"): switch to a coverage-first prompt (report everything with confidence + severity) and filter downstream - measured recall can otherwise fall even though bug-finding improved
- [ ] **[TUNE]** Open-ended frontend/design briefs: specify a concrete spec, or have the model propose 3-4 visual directions and pick one (the recommended substitute for `temperature`-driven variety)
- [ ] **[TUNE]** Interactive coding products: use `effort: "xhigh"`/`"high"`, add autonomous features (e.g. auto mode), and put task/intent/constraints in the first turn
- [ ] **[TUNE]** Vision-heavy / computer-use pipelines: leave images at native resolution up to 2576px long edge for the accuracy gain (downsample to control image-token cost if fidelity isn't needed); for computer use, 1080p screenshots are a good performance/cost balance with `computer_20251124`
- [ ] **[TUNE]** Security workloads: add handling for safeguard refusals (cyber-capable topics may now be declined where Sonnet 4.6 answered)

---

