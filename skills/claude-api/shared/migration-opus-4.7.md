## Migrating to Opus 4.7

> **Model ID `claude-opus-4-7` is authoritative as written here.** When the user asks to migrate to Opus 4.7, write `model="claude-opus-4-7"` exactly. Do **not** WebFetch to verify - this guide is the source of truth for migration target IDs. The corresponding entry exists in `shared/models.md`.

Claude Opus 4.7 was Anthropic's most capable model at its launch and is now the previous-generation Opus (Opus 4.8 is current - see Migrating to Opus 4.8 below). It is highly autonomous and performs exceptionally well on long-horizon agentic work, knowledge work, vision tasks, and memory tasks. This section summarizes everything that was new at the 4.7 launch and remains the layered breaking-change path for callers coming from Opus 4.6 or older. It is layered on top of the 4.6 migration above - if the caller is jumping from Opus 4.5 or older, apply the 4.6 changes first, then this section, then the 4.8 section.

**TL;DR for someone already on Opus 4.6:** update the model ID to `claude-opus-4-7`, strip any remaining `budget_tokens` and sampling parameters (both 400 on Opus 4.7), give `max_tokens` extra headroom and re-baseline with `count_tokens()` against the new model, opt back into `thinking.display: "summarized"` if reasoning is surfaced to users, and re-tune `effort` - it matters more on 4.7 than on any prior Opus.

### Breaking changes (will 400 on Opus 4.7)

**Extended thinking removed.**

`thinking: {type: "enabled", budget_tokens: N}` is no longer supported on Claude Opus 4.7 or later models and returns a 400 error. Switch to adaptive thinking (`thinking: {type: "adaptive"}`) and use the effort parameter to control thinking depth. Adaptive thinking is **off by default** on Claude Opus 4.7: requests with no `thinking` field run without thinking, matching Opus 4.6 behavior. Set `thinking: {type: "adaptive"}` explicitly to enable it.

```python
# Before (Opus 4.6)
client.messages.create(
    model="claude-opus-4-6",
    max_tokens=64000,
    thinking={"type": "enabled", "budget_tokens": 32000},
    messages=[{"role": "user", "content": "..."}],
)

# After (Opus 4.7)
client.messages.create(
    model="claude-opus-4-7",
    max_tokens=64000,
    thinking={"type": "adaptive"},
    output_config={"effort": "high"},  # or "max", "xhigh", "medium", "low"
    messages=[{"role": "user", "content": "..."}],
)
```

If the caller wasn't using extended thinking, no change is required - thinking is off by default, or can be set explicitly with `thinking={"type": "disabled"}`.

Delete `budget_tokens` plumbing entirely. For the replacement `effort` value, see **Choosing an effort level on Opus 4.7** below - there is no exact 1:1 mapping from `budget_tokens`.

**Sampling parameters removed.**

The `temperature`, `top_p`, and `top_k` parameters are no longer accepted on Claude Opus 4.7. Requests that include them return a 400 error. Remove these fields from your request payloads. Prompting is the recommended way to guide model behavior on Claude Opus 4.7. If you were using `temperature = 0` for determinism, note that it never guaranteed identical outputs on prior models.

```python
# Before - errors on Opus 4.7
client.messages.create(temperature=0.7, top_p=0.9, ...)

# After
client.messages.create(...)  # no sampling params
```

- **If the intent was determinism** - use `effort: "low"` with a tighter prompt.
- **If the intent was creative variance** - the prompt replacement depends on the use case; **ask the user** how they want variance elicited. If you can't ask, add a use-case-appropriate instruction along the lines of *"choose something off-distribution and interesting"* - e.g. for text generation, *"Vary your phrasing and structure across responses"*; for frontend/design, use the propose-4-directions approach under **Design and frontend coding** below.

### Choosing an effort level on Opus 4.7

`budget_tokens` controlled how much to *think*; `effort` controls how much to think *and* act, so there is no exact 1:1 mapping. **Use `xhigh` for best results in coding and agentic use cases, and a minimum of `high` for most intelligence-sensitive use cases.** Experiment with other levels to further tune token usage and intelligence:

| Level | Use when | Notes |
| --- | --- | --- |
| `max` | Intelligence-demanding tasks worth testing at the ceiling | Can deliver gains in some use cases but may show diminishing returns from increased token usage; can be prone to overthinking |
| `xhigh` | **Most coding and agentic use cases** | The best setting for these; used as the default in Claude Code |
| `high` | Intelligence-sensitive use cases generally | Balances token usage and intelligence; recommended minimum for most intelligence-sensitive work |
| `medium` | Cost-sensitive use cases that need to reduce token usage while trading off intelligence | |
| `low` | Short, scoped tasks and latency-sensitive workloads that are not intelligence-sensitive | |

### Silent default changes (no error, but behavior differs)

**Thinking content omitted by default.**

Thinking blocks still appear in the response stream on Claude Opus 4.7, but their `thinking` field is empty unless you explicitly opt in. This is a silent change from Claude Opus 4.6, where the default was to return summarized thinking text. To restore summarized thinking content on Claude Opus 4.7, set `thinking.display` to `"summarized"`. **The block-field name is unchanged** - it is still `block.thinking` on a `thinking`-type block; do not rename it.

**Detect this:** any code that reads `block.thinking` (or equivalent) from a `thinking`-type block and renders it in a UI, log, or trace. **The fix is the request parameter, not the response handling** - add `display: "summarized"` to the `thinking` parameter:

```python
thinking={"type": "adaptive", "display": "summarized"}  # "display" is new on Opus 4.7; values: "omitted" (default) | "summarized"
```

The default is `"omitted"` on Claude Opus 4.7. If thinking content was never surfaced anywhere, no change needed. If your product streams reasoning to users, the new default appears as a long pause before output begins; set `display: "summarized"` to restore visible progress during thinking.

**Updated token counting.**

Claude Opus 4.7 and Claude Opus 4.6 count tokens differently. The same input text produces a higher token count on Claude Opus 4.7 than on Claude Opus 4.6, and `/v1/messages/count_tokens` will return a different number of tokens for Claude Opus 4.7 than it did for Claude Opus 4.6. The token efficiency of Claude Opus 4.7 can vary by workload shape. Prompting interventions, `task_budget`, and `effort` can help control costs and ensure appropriate token usage. Keep in mind that these controls may trade off model intelligence. **Update your `max_tokens` parameters to give additional headroom, including compaction triggers.** Claude Opus 4.7 provides a 1M context window at standard API pricing with no long-context premium.

What else to check:

- Client-side token estimators (tiktoken-style approximations) calibrated against 4.6
- Cost calculators that multiply tokens by a fixed per-token rate
- Rate-limit retry thresholds keyed to measured token counts

Re-baseline by re-running `client.messages.count_tokens()` against `claude-opus-4-7` on a representative sample of the caller's prompts. Do not apply a blanket multiplier. For cost-sensitive workloads, consider reducing `effort` by one level (e.g. `high` -> `medium`). For agentic loops, consider adopting Task Budgets (below).

### New feature: Task Budgets (beta)

Opus 4.7 introduces **task budgets** - tell Claude how many tokens it has for a full agentic loop (thinking + tool calls + final output). The model sees a running countdown and uses it to prioritize work and wrap up gracefully as the budget is consumed.

This is a **suggestion the model is aware of**, not a hard cap. It is distinct from `max_tokens`, which remains the enforced per-response limit and is *not* surfaced to the model. Use `task_budget` when you want the model to self-moderate; use `max_tokens` as a hard ceiling to cap usage.

Requires beta header `task-budgets-2026-03-13`:

```python
client.beta.messages.create(
    betas=["task-budgets-2026-03-13"],
    model="claude-opus-4-7",
    max_tokens=64000,
    thinking={"type": "adaptive"},
    output_config={
        "effort": "high",
        "task_budget": {"type": "tokens", "total": 128000},
    },
    messages=[...],
)
```

Set a generous budget for open-ended agentic tasks and tighten it for latency-sensitive ones. **Minimum `task_budget.total` is 20,000 tokens.** If the budget is too restrictive for the task, the model may complete it less thoroughly, referencing its budget as the constraint. **Do not add `task_budget` during a migration unless you are sure the budget value is right** - if you can run the workload and measure, do so; otherwise ask the user for the value rather than guessing. This is the primary lever for offsetting the token-counting shift on agentic workloads.

### Capability improvements

**High-resolution vision.** Opus 4.7 is the first Claude model with high-resolution image support. Maximum image resolution is **2576 pixels on the long edge** (up from 1568px on Opus 4.6 and prior). This unlocks gains on vision-heavy workloads, especially computer use and screenshot/artifact/document understanding. Coordinates returned by the model now map 1:1 to actual image pixels, so no scale-factor math is needed.

High-res support is **automatic on Opus 4.7** - no beta header, no client-side opt-in required. The model accepts larger inputs and returns pixel-accurate coordinates out of the box.

**Token cost.** Full-resolution images on Opus 4.7 can use up to ~3× more image tokens than on prior models (up to ~4784 tokens per image, vs. the previous ~1,600-token cap). If the extra fidelity isn't needed, downsample client-side before sending to control cost - but **do not add downsampling by default during a migration**. If you're not sure whether the pipeline needs the fidelity, ask the user rather than guessing. Use `count_tokens()` on representative images on Opus 4.7 to re-baseline before reacting to any measured cost shift.

Beyond resolution, Opus 4.7 also improves on low-level perception (pointing, measuring, counting) and natural-image bounding-box localization and detection.

**Knowledge work.** Meaningful gains on tasks where the model visually verifies its own output - `.docx` redlining, `.pptx` editing, and programmatic chart/figure analysis (e.g. pixel-level data transcription via image-processing libraries). If prompts have scaffolding like *"double-check the slide layout before returning"*, try removing it and re-baselining.

**Memory.** Opus 4.7 is better at writing and using file-system-based memory. If an agent maintains a scratchpad, notes file, or structured memory store across turns, that agent should improve at jotting down notes to itself and leveraging its notes in future tasks.

**User-facing progress updates.** Opus 4.7 provides more regular, higher-quality interim updates during long agentic traces. If the system prompt has scaffolding like *"After every 3 tool calls, summarize progress"*, try removing it to avoid excessive user-facing text. If the length or contents of Opus 4.7's updates are not well-calibrated to your use case, explicitly describe what these updates should look like in the prompt and provide examples.

### Real-time cybersecurity safeguards

Requests that involve prohibited or high-risk topics may lead to refusals.

### Fast Mode: Claude Opus 5.5 / Claude Opus 5 / Opus 4.8 only

Fast mode is available on Claude Opus 5.5, Claude Opus 5, and Opus 4.8. Only surface this if the caller's code actually uses fast mode (e.g. `model="claude-opus-4-6-fast"`, or `speed="fast"` on an unsupported model); if the word "fast" does not appear in the code, say nothing about Fast Mode.

When you see `model="claude-opus-4-6-fast"` (or any retired `-fast` model string), **the migration edit is** to move the fast-mode traffic onto Claude Opus 5.5, the current fast-capable default, at $8 / $40 per MTok (Claude Opus 5 and Opus 4.8 also work if the caller is staying on that tier):

```python
# Request fast mode on Claude Opus 5.5.
client.beta.messages.create(
    model="claude-opus-5-5", max_tokens=4096,
    speed="fast", betas=["fast-mode-2026-02-01"],
    messages=[...],
)
```

That is: switch the model to Claude Opus 5.5 (or Claude Opus 5 or Opus 4.8) and request fast mode the supported way, using the beta `client.beta.messages....` endpoint, the `fast-mode-2026-02-01` beta flag, and `speed="fast"` as a top-level request parameter (per-language form in SKILL.md § Fast Mode). Opus 4.7 fast mode has also been removed, so do not land on Opus 4.7 either. Do **not** leave the code on a retired `-fast` model string - the failure mode differs by version: `claude-opus-4-6-fast` is retired and the API **silently falls back** to standard Opus 4.6 (no error - the caller loses fast-mode speed without noticing); `claude-opus-4-7-fast` and `speed="fast"` on Opus 4.7 instead return an **API error** (hard failure - requests break outright rather than degrading). Either way, migrate to a supported fast-mode model (Claude Opus 5.5 by default) now.

### Behavioral shifts (prompt-tunable)

These don't break anything, but prompts tuned for Opus 4.6 may land differently. Opus 4.7 is more steerable than 4.6, so small prompt nudges usually close the gap.

**More literal instruction following.** Claude Opus 4.7 interprets prompts more literally and explicitly than Claude Opus 4.6, particularly at lower effort levels. It will not silently generalize an instruction from one item to another, and it will not infer requests you didn't make. The upside of this literalism is precision and less thrash. It generally performs better for API use cases with carefully tuned prompts, structured extraction, and pipelines where you want predictable behavior. A prompt and harness review may be especially helpful for migration to Claude Opus 4.7.

**Verbosity calibrates to task complexity.** Opus 4.7 scales response length to how complex it judges the task to be, rather than defaulting to a fixed verbosity - shorter answers on simple lookups, much longer on open-ended analysis. If the product depends on a particular length or style, tune the prompt explicitly. To reduce verbosity:

> *"Provide concise, focused responses. Skip non-essential context, and keep examples minimal."*

If you see specific kinds of over-verbosity (e.g. over-explaining), add instructions targeting those. Positive examples showing the desired level of concision tend to be more effective than negative examples or instructions telling the model what not to do. Do **not** assume existing "be concise" instructions should be removed - test first.

**Tone and writing style.** Opus 4.7 is more direct and opinionated, with less validation-forward phrasing and fewer emoji than Opus 4.6's warmer style. As with any new model, prose style on long-form writing may shift. If the product relies on a specific voice, re-evaluate style prompts against the new baseline. If a warmer or more conversational voice is wanted, specify it:

> *"Use a warm, collaborative tone. Acknowledge the user's framing before answering."*

**`effort` matters more than on any prior Opus.** Opus 4.7 respects `effort` levels more strictly, especially at the low end. At `low` and `medium` it scopes work to what was asked rather than going above and beyond - good for latency and cost, but on moderate tasks at `low` there is some risk of under-thinking.

- If shallow reasoning shows up on complex problems, raise `effort` to `high` or `xhigh` rather than prompting around it.
- If `effort` must stay `low` for latency, add targeted guidance: *"This task involves multi-step reasoning. Think carefully through the problem before responding."*
- **At `xhigh` or `max`, set a large `max_tokens`** so the model has room to think and act across tool calls and subagents. Start at 64K and tune from there. (`xhigh` is a new effort level on Opus 4.7, between `high` and `max`.)

Adaptive-thinking triggering is also steerable. If the model thinks more often than wanted - which can happen with large or complex system prompts - add: *"Thinking adds latency and should only be used when it will meaningfully improve answer quality - typically for problems that require multi-step reasoning. When in doubt, respond directly."*

**Uses tools less often by default.** Opus 4.7 tends to use tools less often than 4.6 and to use reasoning more. This produces better results in most cases, but for products that rely on tools (search/retrieval, function-calling, computer-use steps), it can drop tool-use rate. Two levers:

- **Raise `effort`** - `high` or `xhigh` show substantially more tool usage in agentic search and coding, and are especially useful for knowledge work.
- **Prompt for it** - be explicit in tool descriptions or the system prompt about when and how to use the tool, and encourage the model to err on the side of using it more often:

> *"When the answer depends on information not present in the conversation, you MUST call the `search` tool before answering - do not answer from prior knowledge."*

**Fewer subagents by default.** Opus 4.7 tends to spawn fewer subagents than 4.6. This is steerable - give explicit guidance on when delegation is desirable. For a coding agent, for example:

> *"Do NOT spawn a subagent for work you can complete directly in a single response (e.g. refactoring a function you can already see). Spawn multiple subagents in the same turn when fanning out across items or reading multiple files."*

**Design and frontend coding.** Opus 4.7 has stronger design instincts than 4.6, with a consistent default house style: warm cream/off-white backgrounds (around `#F4F1EA`), serif display type (Georgia, Fraunces, Playfair), italic word-accents, and a terracotta/amber accent. This reads well for editorial, hospitality, and portfolio briefs, but will feel off for dashboards, dev tools, fintech, healthcare, or enterprise apps - and it appears in slide decks as well as web UIs.

The default is persistent. Generic instructions ("don't use cream," "make it clean and minimal") tend to shift the model to a different fixed palette rather than producing variety. Two approaches work reliably:

1. **Specify a concrete alternative.** The model follows explicit specs precisely - give exact hex values, typefaces, and layout constraints.
2. **Have the model propose options before building.** This breaks the default and gives the user control:

   > *"Before building, propose 4 distinct visual directions tailored to this brief (each as: bg hex / accent hex / typeface - one-line rationale). Ask the user to pick one, then implement only that direction."*

If the caller previously relied on `temperature` for design variety, use approach (2) - it produces meaningfully different directions across runs.

Opus 4.7 also requires less frontend-design prompting than previous models to avoid generic "AI slop" aesthetics. Where earlier models needed a lengthy anti-slop snippet, Opus 4.7 generates distinctive, creative frontends with a much shorter nudge. This snippet works well alongside the variety approaches above:

> *"NEVER use generic AI-generated aesthetics like overused font families (Inter, Roboto, Arial, system fonts), cliched color schemes (particularly purple gradients on white or dark backgrounds), predictable layouts and component patterns, and cookie-cutter design that lacks context-specific character. Use unique fonts, cohesive colors and themes, and animations for effects and micro-interactions."*

**Interactive coding products.** Opus 4.7's token usage and behavior can differ between autonomous, asynchronous coding agents with a single user turn and interactive, synchronous coding agents with multiple user turns. Specifically, it tends to use more tokens in interactive settings, primarily because it reasons more after user turns. This can improve long-horizon coherence, instruction following, and coding capabilities in long interactive coding sessions, but also comes with more token usage. To maximize both performance and token efficiency in coding products, use `effort: "xhigh"` or `"high"`, add autonomous features (like an auto mode), and reduce the number of human interactions required from users.

When limiting required user interactions, specify the task, intent, and relevant constraints upfront in the first human turn. Well-specified, clear, and accurate task descriptions upfront help maximize autonomy and intelligence while minimizing extra token usage after user turns - because Opus 4.7 is more autonomous than prior models, this usage pattern helps to maximize performance. In contrast, ambiguous or underspecified prompts conveyed progressively over multiple user turns tend to reduce token efficiency and sometimes performance.

**Code review.** Opus 4.7 is meaningfully better at finding bugs than prior models, with both higher recall and precision. However, if a code-review harness was tuned for an earlier model, it may initially show *lower* recall - this is likely a harness effect, not a capability regression. When a review prompt says "only report high-severity issues," "be conservative," or "don't nitpick," Opus 4.7 follows that instruction more faithfully than earlier models did: it investigates just as thoroughly, identifies the bugs, and then declines to report findings it judges to be below the stated bar. Precision rises, but measured recall can fall even though underlying bug-finding has improved.

Recommended prompt language:

> *"Report every issue you find, including ones you are uncertain about or consider low-severity. Do not filter for importance or confidence at this stage - a separate verification step will do that. Your goal here is coverage: it is better to surface a finding that later gets filtered out than to silently drop a bug. For each finding, include your confidence level and an estimated severity so a downstream filter can rank them."*

This can be used without an actual second step, but moving confidence filtering out of the finding step often helps. If the harness has a separate verification/dedup/ranking stage, tell the model explicitly that its job at the finding stage is coverage, not filtering. If single-pass self-filtering is wanted, be concrete about the bar rather than using qualitative terms like "important" - e.g. *"report any bugs that could cause incorrect behavior, a test failure, or a misleading result; only omit nits like pure style or naming preferences."* Iterate on prompts against a subset of evals to validate recall or F1 gains.

**Computer use.** Computer use works across resolutions up to the new 2576px / 3.75MP maximum. Sending images at **1080p** provides a good balance of performance and cost. For particularly cost-sensitive workloads, **720p** or **1366×768** are lower-cost options with strong performance. Test to find the ideal settings for the use case; experimenting with `effort` can also help tune behavior.

---


## Opus 4.7 Migration Checklist

Every item is tagged: **`[BLOCKS]`** items cause a 400 error, infinite loop, silent truncation, or empty output if missed - apply these as code edits, not as suggestions. **`[TUNE]`** items are quality/cost adjustments - surface them to the user as recommendations.

`[BLOCKS]` items prefixed with **"If..."** or **"At..."** are conditional. Before working through the list, **scan the file** for the conditions: does it surface thinking text to a UI/log? Does it set `output_config.effort` to `"x-high"` or `"max"`? Is it a security workload? Is it a multi-turn agentic loop? Apply only the items whose condition matches.

- [ ] **[BLOCKS]** Replace `thinking: {type: "enabled", budget_tokens: N}` with `thinking: {type: "adaptive"}` + `output_config.effort`; delete `budget_tokens` plumbing entirely
- [ ] **[BLOCKS]** Strip `temperature`, `top_p`, `top_k` from request construction
- [ ] **[BLOCKS]** If thinking content is surfaced to users or stored in logs: add `thinking.display: "summarized"` (otherwise the rendered text is empty)
- [ ] **[BLOCKS]** At `output_config.effort` of `xhigh` or `max`: set `max_tokens` >= 64000 (otherwise output truncates mid-thought)
- [ ] **[TUNE]** Give `max_tokens` and compaction triggers extra headroom; re-run `count_tokens()` against `claude-opus-4-7` on representative prompts to re-baseline (no blanket multiplier)
- [ ] **[TUNE]** Re-baseline cost and rate-limit dashboards *before* reacting to measured shifts
- [ ] **[TUNE]** Re-evaluate `effort` per route - use `xhigh` for coding/agentic and a minimum of `high` for most intelligence-sensitive work; it matters more on 4.7 than any prior Opus
- [ ] **[TUNE]** Multi-turn agentic loops: adopt the API-native Task Budgets (`output_config.task_budget`, beta `task-budgets-2026-03-13`, minimum 20k tokens) - this is for capping *cumulative* spend across a loop; per-turn depth is `effort`
- [ ] **[TUNE]** Check for ambiguous or underspecified instructions that relied on 4.6 generalizing intent, and update them to be clearer or more precise - 4.7 follows them literally
- [ ] **[TUNE]** Tool-use workloads: add explicit when/how-to-use guidance to tool descriptions (4.7 reaches for tools less often)
- [ ] **[TUNE]** Verbosity: test existing length instructions before changing them - 4.7 calibrates length to task complexity, so tune for the desired output rather than assuming a direction
- [ ] **[TUNE]** Remove forced-progress-update scaffolding (*"after every N tool calls..."*)
- [ ] **[TUNE]** Remove knowledge-work verification scaffolding (*"double-check the slide layout..."*) and re-baseline
- [ ] **[TUNE]** Add tone instruction if a warmer / more conversational voice is needed; re-evaluate style prompts on writing-heavy routes
- [ ] **[TUNE]** Subagent tool present: add explicit spawn / don't-spawn guidance
- [ ] **[TUNE]** Frontend/design output: specify a concrete palette/typeface, or have the model propose 4 visual directions before building (the default cream/serif house style is persistent)
- [ ] **[TUNE]** Interactive coding products: use `effort: "xhigh"` or `"high"`, add autonomous features (e.g. an auto mode) to reduce human interactions, and specify task/intent/constraints upfront in the first turn
- [ ] **[TUNE]** Code-review harnesses: remove or loosen "only report high-severity" / "be conservative" filters and have the model report every finding with confidence + severity; move filtering to a downstream step (4.7 follows severity filters more literally, which can depress measured recall)
- [ ] **[TUNE]** Vision-heavy pipelines (screenshots, charts, document understanding): leave images at native resolution up to 2576px long edge for the accuracy gain; remove any scale-factor math from coordinate handling (coords are now 1:1 with pixels). No beta header / opt-in needed - high-res is automatic on Opus 4.7.
- [ ] **[TUNE]** Computer-use pipelines: send screenshots at 1080p for a good performance/cost balance (720p or 1366×768 for cost-sensitive workloads); experiment with `effort` to tune behavior
- [ ] **[TUNE]** Cost-sensitive image pipelines: full-res images on 4.7 use up to ~4784 tokens vs ~1,600 on prior models (~3×). Downsampling client-side before upload avoids the increase, but **do not downsample by default** - if you're unsure whether fidelity is needed, ask the user. Re-baseline with `count_tokens()` on representative images before reacting to cost shifts.

---

