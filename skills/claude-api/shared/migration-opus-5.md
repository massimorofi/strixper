## Migrating to Claude Opus 5

> **Model ID `claude-opus-5` is authoritative as written here.** When the user asks to migrate to Claude Opus 5, write `model="claude-opus-5"` exactly. Do **not** WebFetch to verify - this guide is the source of truth for migration target IDs. The corresponding entry exists in `shared/models.md`.

Claude Opus 5 is the successor to Claude Opus 4.8 in the Opus line, and is strongest on long-horizon agentic work and coding. It is layered on top of the Opus 4.8 migration above; if the caller is coming from Opus 4.7 or older, apply those sections first. Like Claude Fable 5.1, it ships with **elevated cybersecurity safeguards, and its safety classifiers can decline a request**: you get a normal HTTP 200 with `stop_reason: "refusal"` and a `stop_details` category, not an error. Benign security and life-sciences work occasionally trips them, so **check `stop_reason` before reading `response.content`** - code that indexes `content[0]` unconditionally breaks on a refusal. Cyber-category refusals route to Opus 4.8 as the recommended fallback, so a fallback strategy genuinely recovers the request rather than just relabelling the failure. The full refusal semantics (pre-output vs mid-stream billing, retry strategies, fallback credit) are in the Claude Fable 5.1 section below and apply here unchanged.

Existing prompts and evals should carry over with strong out-of-the-box performance. **It is a drop-in upgrade at Opus 4.8's pricing** - $5 per million input tokens, $25 per million output - with the same feature set: 1M context (default, no beta header), 128K max output, adaptive thinking, prompt caching, batch processing, the Files API, PDF support, vision, and the full server-side and client-side tool set. `claude-opus-5` is a fixed ID with no date suffix, same scheme as `claude-opus-4-8`.

The migration is **the model-ID swap plus prompt re-tuning**, with two breaking changes covered below.

**Availability at launch:** Claude API (`claude-opus-5`), Amazon Bedrock (`anthropic.claude-opus-5`), Google Cloud (`claude-opus-5`), and Microsoft Foundry. Opus 4.8 stays available on all four.

**Rate limits are a separate bucket.** Opus 4.8/4.7/4.6/4.5 share one combined Opus limit; Claude Opus 5 does **not** draw from it. Shifting traffic over neither frees headroom on the old bucket nor inherits it - check your tier's Claude Opus 5 limits before moving volume.

**TL;DR for someone already on Claude Opus 4.8:** swap the model ID. Then re-tune: Claude Opus 5 writes longer user-facing responses and longer files on disk (add explicit conciseness and deliverable-length instructions - `effort` does not reliably shorten visible output), verifies its own work without being told (**delete** your verification instructions and harness verification steps), and can expand task scope (add a scope-discipline instruction). Run a fresh effort sweep - `low` and `medium` are unusually strong here and are the primary cost/latency lever.

### Breaking change 1: thinking is on by default

A request that omits the `thinking` parameter **thinks** on Claude Opus 5, unlike Claude Opus 4.8 and Opus 4.7 where omitting it meant no thinking. `thinking: {type: "adaptive"}` remains valid and is equivalent to the default - the wire value didn't change, the default did.

This is a silent cost and truncation change, not just a behavior one: **`max_tokens` is a hard cap on thinking *plus* response text.** A workload that ran without thinking on Opus 4.8 and sized `max_tokens` tightly around its answer can now truncate mid-response. Revisit `max_tokens` on every route that never set `thinking`. To keep the old behavior, pass `thinking: {type: "disabled"}` - subject to the effort cap below.

Raw thinking tokens are **never returned** on Claude Opus 5; `display` defaults to `"omitted"`, and `display: "summarized"` gets you a summary. This also means a fallback model cannot read Claude Opus 5's thinking.

### Breaking change 2: disabling thinking is capped at `high` effort

Disabling thinking is available only at effort **`high` or lower**; `thinking: {type: "disabled"}` combined with `xhigh` or `max` returns a 400. Opus 4.8 accepts that combination, so audit any route that disables thinking before migrating.

**The check is per request.** Effort and thinking are validated independently on every call, so a later request that raises effort to `xhigh` while thinking is still disabled is rejected even though earlier requests in the same conversation succeeded.

```python
# 400 on Claude Opus 5 - disabled thinking above `high`
client.messages.create(
    model="claude-opus-5",
    max_tokens=4096,
    thinking={"type": "disabled"},
    output_config={"effort": "xhigh"},
    messages=[...],
)
```

**Migrating:** either enable thinking at `xhigh`/`max`, or lower effort to `high` or below. Given how well Claude Opus 5 performs at `low` and `medium`, a latency-sensitive route that previously ran `xhigh` + disabled thinking is usually better served by `medium` with thinking on than by keeping the disabled path.

Everything else from the Opus 4.7/4.8 request surface is unchanged: `budget_tokens` still 400s (use `output_config.effort`), sampling parameters (`temperature`, `top_p`, `top_k`) are still rejected, last-assistant-turn prefills still 400, and `thinking.display` still defaults to `"omitted"`.

### Two failure modes when thinking is disabled

**Are you affected?** Only if you explicitly set `thinking: {type: "disabled"}`. Thinking is on by default on Claude Opus 5 (see Breaking change 1 above), so an unmodified request never hits either of these - but code carrying a disabled-thinking setting forward from Opus 4.8, where it was the default behaviour, does.

Both are specific to `thinking: {type: "disabled"}` on Claude Opus 5, and for both the **primary recommendation is the same: turn thinking back on and use a lower `effort` to control cost and verbosity instead.** Disabling thinking is the more expensive lever in every sense - it is what triggers these, and `low`/`medium` effort already gets you most of the token and latency saving (see § Effort below).

**1. Tool calls can arrive as plain text.** The model occasionally writes a tool call into its user-facing text rather than emitting a structured `tool_use` block. **The turn completes normally and the call never runs** - there is no error and no `tool_use` block to catch, so a harness sees a successful turn that silently did nothing. Worse in an agentic loop: the bogus text stays in conversation history and skews later turns. Most common on tool-heavy workloads such as search.

**2. `<thinking>` tags can leak into the visible response.** The model may emit `<thinking>` or other internal XML in its user-facing output.

If you cannot enable thinking, one instruction covers both failure modes - give the model explicit permission to talk before a tool call (the tool-as-text failure appears to come from suppressing the preamble it wants to write), and forbid internal tags generically:

> *"When you use a tool, you may say a brief sentence first. If no tool can express what the user asked for, say so instead of guessing. Do not include internal or system XML tags in your response."*

Two counterintuitive rules for that instruction:

- **Delete any instruction telling the model not to think or not to reason.** That kind of rule *increases* tag leakage rather than suppressing it.
- **Do not name thinking tags in the prompt.** Calling out `<thinking>` by name is measurably less effective than the generic "internal or system XML tags" wording above.

### New API features

Two additions, each behind its own beta header. Both are optional - a migrated request works without them.

**1. `fallbacks: "default"` - recommended for every caller.** Claude Opus 5's safety classifiers can decline a request; the `fallbacks` parameter re-runs a declined request on another model server-side instead of returning the refusal to you. Previously you named the substitute yourself (`"fallbacks": [{"model": "claude-opus-4-8"}]`). The new `"default"` mode picks Anthropic's recommended fallback automatically, routed **by refusal category** - cyber-category refusals go to Claude Opus 4.8.

```http
POST /v1/messages
anthropic-beta: server-side-fallback-2026-07-01

{"model": "claude-opus-5", "fallbacks": "default", "max_tokens": 1024,
 "messages": [{"role": "user", "content": "Say OK."}]}
```

**Prefer `"default"` over pinning a model.** Different fallback models carry different classifiers, so the right substitute depends on *why* the request was declined - and `"default"` removes the migration you would otherwise owe when a pinned fallback model is deprecated. Note the header is `server-side-fallback-2026-07-01`, distinct from the `-2026-06-01` header that gates the array form; the array form's semantics (content blocks, `usage.iterations`, sticky routing) are unchanged and documented in the Claude Fable 5.1 refusal section below.

**2. Mid-conversation tool changes (beta `mid-conversation-tool-changes-2026-07-01`).** Change a conversation's tool set between turns without invalidating the prompt cache. Previously `tools` was fixed for the conversation's lifetime and any edit re-billed the whole prefix. Append a `{"role": "system", "content": [...]}` message carrying a `tool_addition` or `tool_removal` block:

```python
messages = [
    {"role": "user", "content": "What tools do you have for weather in Paris?"},
    {"role": "system", "content": [
        {"type": "tool_addition", "tool": {"type": "tool_reference", "name": "get_forecast"}},
    ]},
]
```

The added tool must already be declared in `tools[]` with `"defer_loading": True` - declared up front, but not loaded into context until a `tool_addition` surfaces it. A `tool_removal` block must sit either immediately before an assistant message or at the end of `messages`. To *change* a tool's definition, remove the old one on one request, then send the updated entry in `tools[]` on the next. See `shared/tool-use-concepts.md` § Mid-conversation tool changes.

> Warning: Earlier previews of this feature used a different beta header and different block shapes. Both are deprecated - if the code you're migrating carries anything other than `mid-conversation-tool-changes-2026-07-01` with `tool_addition` / `tool_removal` / `tool_reference`, update the header and the shapes together.


> **SDK typings lag these blocks.** Pass them as plain dicts in Python (the SDK forwards unknown keys unchanged) or add a `@ts-expect-error` in TypeScript until the types catch up. `extra_body` / `extra_headers` work on `.stream()` exactly as on `.create()`.

### Capability improvements

**Agentic coding.** Claude Opus 5 is a workhorse for agentic coding and is strongest on *difficult* tasks - multi-file features, larger refactors, end-to-end feature work. It completes tasks rather than leaving stubs or placeholders. The gap over prior models is smaller on easy single-turn edits, so evaluate it on the hard end of your workload. To get the most out of it, give the complete task specification up front and let it run; longer autonomous sessions with more parallel agents show the strongest results, short interactive edits the least.

**Code review and bug-finding.** High precision *and* high recall - a high rate of real bugs per pass, with the extra findings mostly real rather than false positives. It stays accurate at lower effort, which makes a cheap fast pass at review time plus a thorough pass later a practical pattern.

**Effort: the full ladder, and where to start.** Claude Opus 5 supports all five levels - `low`, `medium`, `high`, `xhigh`, `max` - with no beta header. The API default is `high`.

- **Start at `high` (the API default), then sweep down.** `low` and `medium` are unusually effective on this model - strong quality at a fraction of the tokens and latency on many workloads - so treat them as the primary cost/latency lever and reserve `high` and above for tasks where your evals show a quality difference. Effort defaults carried over from a prior model are usually not the right setting here; run a fresh sweep.
- **`xhigh` and `max` are for measured wins, not a starting point.** `max` is the top tier for the deepest reasoning and worth testing where capability matters more than spend, but it can show diminishing returns and overthink simpler tasks.

At `xhigh` or `max`, **set a large `max_tokens`** so the model has room to think and act across tool calls and subagents. Start at 64K and tune.

**Lower prompt-cache minimum.** The minimum cacheable prompt is **512 tokens** on Claude Opus 5, down from 1024 on Opus 4.8. Prompts previously too short to cache now create entries with no code change - worth re-checking any prompt you'd written off as uncacheable. See `shared/prompt-caching.md`.

**Fast mode.** `speed: "fast"` (beta header `fast-mode-2026-02-01`) is supported on Claude Opus 5, priced at $10 / $50 per MTok. It is a research preview on the **Claude API only** - including Managed Agents - and is **not** available on Amazon Bedrock, Google Cloud, or Microsoft Foundry. Fast mode draws on dedicated rate limits separate from the standard Opus pools.

**Vision - give it tools, not more thinking.** Stronger on chart, document, and diagram understanding, and on UI and frontend visual replication. The highest-leverage change is **giving it tools to iteratively analyze, crop, and visually verify its own work**: on this model tool use is a markedly more cost-effective lever than raising thinking alone. Claude Opus 5 sits in the high-resolution tier alongside Opus 4.8 - 2576 px on the long edge, up to 4784 visual tokens per image - so coordinates map 1:1 to pixels and no scale-factor math is needed. Any prompt-side workaround you added for a prior model's vision limitations should be re-validated; several are now counterproductive.

**Long context.** 1M-token context window as both the default *and* the maximum. Instruction following, tool calling, and reasoning stay strong across the full window.

**Office and document tasks.** Generates and edits complex multi-sheet Excel files with non-trivial formulas, and visually strong PowerPoint decks that follow slide-design best practices. It can be prompted to adhere to a specific style or template when one is required.

**Multi-agent coordination.** Coordinates teams of subagents well - few cases of agents overwriting each other's work, and effective use of writer-verifier patterns. Workloads that benefit from multi-agent patterns are good fits. **Cost-sensitive workloads should cap multi-agent usage** - see the delegation section below, because this model reaches for subagents more readily than its predecessors.

### Behavioral shifts (prompt-tunable)

**Longer user-facing responses.** Default response text is longer than on prior models. **`effort` is not the lever here** - changing it may move thinking volume without reliably changing visible output length. Prompting is: in testing, a short conciseness instruction cut user-facing response length by ~20%.

> *"Keep responses focused, brief, and concise to avoid overwhelming the person. Disclaimers and caveats are brief, with most of the response on the main answer; when asked to explain something, give a high-level summary unless an in-depth one is specifically requested."*

For a long system prompt, pair that with a one-line reminder near the end:

> ```
> <tone_preference>
> Keep outputs reasonably concise.
> </tone_preference>
> ```

**More narration in agentic sessions** (the lever runs both ways - the same explicit-description technique tunes narration *up* or restyles it, if your product wants more). Claude Opus 5 narrates what it is about to do, and its per-message output in agentic sessions is longer than prior models'. It responds well to explicit guidance on *how* to communicate during a task rather than just *how much*. For coding agents, this block calibrates it:

> ```
> # Communicating with the user
> Your text output is what the user reads between tool calls; they usually can't see your thinking or the raw tool results. Write it for a teammate who stepped away and is catching up, not for a log file: they don't know the codenames or shorthand you created along the way, and they didn't watch your process unfold. Before your first tool call, say in a sentence what you're about to do; while working, give brief updates when you find something load-bearing or change direction.
>
> Lead with the outcome. Your first sentence after finishing should answer "what happened" or "what did you find" - the thing the user would ask for if they said "just give me the TLDR." Supporting detail and reasoning should come after, for readers who want them.
>
> Being readable and being concise are different things, and readable matters more. If the user has to reread your summary or ask you to explain, any time saved by brevity is gone. The way to keep output short is to be selective about what you include (drop details that don't change what the reader would do next), not to compress the writing into fragments, abbreviations, arrow chains like `A -> B -> fails`, or jargon. What you do include, write in complete sentences with the technical terms spelled out. Don't make the reader cross-reference labels or numbering you invented earlier; say what you mean in place.
>
> Match the response to the question: a simple question should be answered with a direct answer in prose, not headers and sections. Use tables only for short enumerable facts, with explanations in the surrounding prose rather than the cells. Calibrate to the user - a bit tighter for an expert, more explanatory for someone newer.
>
> Write code that reads like the surrounding code: match its comment density, naming, and idiom.
>
> Only write a code comment to state a constraint the code itself can't show - never to say where it came from, what the next line does, or why your change is correct; that's you talking to the reviewer, not the next reader, and it's noise the moment the PR merges.
> ```

**Longer written deliverables.** Separate from conversational verbosity: files Claude Opus 5 writes to disk - reports, Markdown documents, summaries - are often longer than on prior models. If your product ships Claude-authored documents, calibrate length explicitly:

> *"Match the length of written deliverables (especially Markdown files) to what the task needs: cover the substance, but do not pad documents with filler sections, redundant summaries, or boilerplate."*

**Self-check instructions are the same trap.** Beyond harness scaffolding, per-prompt re-check phrasing - *"double-check your answer"*, *"re-verify before responding"* - triggers the same extra work. Note this **inverts a standard prompting best practice**: "ask Claude to self-check" is generally sound advice and is wrong here, so a prompt library that applies it uniformly needs a carve-out for this model rather than a global rule.

**Over-verification - delete your verification scaffolding.** Claude Opus 5 verifies its own work without being asked. Instructions that *tell* it to verify ("include a final verification step for virtually any non-trivial task", "use a subagent to verify") now cause over-verification. **Removing them reduces over-verification with no capability regression** - this is a delete, not a rewrite. The same applies to harness-level scaffolding: separate verification steps carried over from prior models are likely redundant now.

**Task scope expansion.** It can add steps the user didn't request, or apply its own judgment about what the task should be without making that clear. In testing, this instruction reduced scope changes to nearly zero without producing excessive clarifying questions:

> *"Deliver what the user asked for, at the scope they intended. Interpret ambiguity the way a careful colleague would: make routine judgment calls yourself, and check in only when different readings would lead to materially different work. If you conclude the ask is mistaken or a better approach exists, say so in a sentence and keep going with the task as asked - don't quietly narrow, widen, or transform it. Finish the whole task, not just the easy part of it - only report completion when it's fully done. If you genuinely can't complete something, do the rest and state plainly what's missing and why. Stop short of actions or changes that are clearly beyond what the user's ask implies."*

The revised wording adds a **finish-the-whole-task** clause - report completion only when the work is actually done, and if something genuinely can't be finished, do the rest and say plainly what is missing. That covers premature "done" claims, which scope-discipline wording alone did not.

**Delegates to subagents more readily - the opposite of Opus 4.8.** This is a direction change worth flagging: Opus 4.8 *under*-reached for subagents and needed prompting to delegate. Claude Opus 5 reaches for them freely, which multiplies cost and latency - each subagent re-establishes context, re-explores, reports back, and then the coordinator re-reads the report. If your harness supports subagents, **any "delegate more" guidance you added for Opus 4.8 should come out**, and you likely want an explicit cap. A deterministic ceiling on spawn count is the reliable lever; this block reduces delegation and token spend:

> ```
> ## Delegating to subagents
> Subagents multiply cost and time: each one re-establishes context, re-explores, and reports back, and you then re-read its report. Delegate rarely and only when the payoff clearly exceeds that overhead.
>
> Do use subagents for:
> - Large tasks that are genuinely independent and parallelizable. For example, wide multi-file investigations.
>
> Do NOT use subagents for:
> - Work you could finish yourself in a handful of tool calls. For example: a few file reads, a handful of edits, a simple search task, relatively simple verification.
> - Review, verification, or to double check your work. Verification belongs in your main agent loop.
>
> Use of parallel or multiple subagents:
> - Do not use multiple subagents on a single small task. Parallel subagents are for genuinely independent, sizeable tracks (unrelated modules, a wide multi-file investigation), not for splitting one modest job into pieces.
> - If the task can be completed with one subagent, choose one subagent over multiple subagents. Keep spawn counts low.
> - Never use more than 20 parallel agents unless the user explicitly requests it.
>
> When delegating to subagents:
> - Brief the subagent precisely the first time. Avoid launching, waiting, and re-briefing.
> - If you delegate, commit to the delegation. Never redo the subagent's work and do not re-derive its findings once it reports back.
> - If you launch multiple agents for independent work, send them in a single message with multiple tool uses so they run concurrently.
> ```

Note the interaction with over-verification below: "do not use subagents to verify" and "delete your verification scaffolding" are the same underlying fix seen from two angles.

**Narrates self-corrections more than prior models.** It flags and explains its own earlier mistakes at length, which reads as thrash in a user-facing product. Scope corrections to the ones that actually change the user's outcome:

> ```
> # Corrections
> Avoid unnecessary or excessive self-correction. Only correct an earlier statement in your user-facing text when the error would change the user's code, conclusions, or decisions. State corrections plainly and concisely, and continue the task; combine multiple corrections rather than enumerating them all. For slips that change nothing for the user, simply make the correction and move on - no need to note it explicitly. Don't add apologies or preambles, don't be overly self-critical, and don't ruminate or give a detailed account of the mistake or tally past errors. Sometimes, other agents will report incorrect or misleading results - don't always take them at face value immediately. If other agents correct your statements and they are right, then simply update your approach without narrating too much about the correction to the user. This instruction does not apply to thinking blocks.
>
> A follow-up question about your earlier work is not, by itself, a signal that you got something wrong - answer what was asked. A statement that was accurate needs no correction: don't re-audit how you phrased it, how you verified it, or limits you already stated. When the user does point to a real error, correct it plainly as above.
> ```

The second paragraph matters as much as the first: a plain follow-up question can otherwise trigger a re-audit of work that was correct.

**Time to first token (TTFT).** Claude Opus 5 sometimes thinks before its first visible block, which raises TTFT - a problem for user-facing chat and voice, where the pause reads as latency. This one-line instruction reduces pre-first-block thinking significantly:

> *"Latency-sensitive; begin your visible answer immediately."*

Apply it only where first-token latency is user-visible; on background and agentic routes the pre-answer thinking is usually worth keeping.

**Severity filters still depress measured recall.** Unchanged from 4.7/4.8: if a review harness says "only report high-severity issues" or "be conservative", Claude Opus 5 follows it literally. Ask it to report everything with confidence and severity, and filter in a separate pass - see the **Code review** guidance in the Opus 4.7 section for the recommended prompt.

### Claude Opus 5 Migration Checklist

**`[BLOCKS]`** items cause a 400 error if missed; **`[TUNE]`** items are quality/cost adjustments - surface them to the user as recommendations.

- [ ] **[BLOCKS]** Update the `model=` string to `claude-opus-5`
- [ ] **[BLOCKS]** Any route combining `thinking: {type: "disabled"}` with `effort` of `xhigh` or `max`: enable thinking, or lower effort to `high` or below. Validated per request, so audit every call site, not just the first
- [ ] **[BLOCKS]** Every route that never set `thinking`: it now thinks, and `max_tokens` caps thinking + response text together. Raise `max_tokens` or pass `thinking: {type: "disabled"}` at effort `high` or below - otherwise responses truncate mid-answer
- [ ] **[BLOCKS]** *(only if coming from Opus 4.7 or earlier)* Apply the **Migrating to Opus 4.7** breaking changes first - `budget_tokens` -> adaptive thinking, strip `temperature`/`top_p`/`top_k`, remove last-assistant-turn prefills
- [ ] **[TUNE]** Effort: start at `high` (the API default) and sweep down - `low`/`medium` are unusually strong on this model and are the primary cost/latency lever; reserve `xhigh`/`max` for tasks where you've measured a quality difference. Prior-model defaults rarely transfer. At `xhigh`/`max`, set `max_tokens` to at least 64K
- [ ] **[TUNE]** Re-check prompts you'd written off as uncacheable - the minimum drops to 512 tokens (from 1024 on Opus 4.8)
- [ ] **[TUNE]** Rate limits: Claude Opus 5 is a separate bucket from the combined Opus 4.x pool - confirm your tier's limits before shifting volume
- [ ] **[TUNE]** Fast mode (`speed: "fast"`, `fast-mode-2026-02-01`, $10/$50) is Claude-API-only - drop it on Bedrock, Google Cloud, and Foundry routes
- [ ] **[TUNE]** Verbosity: add a conciseness instruction (and a `<tone_preference>` tag for long system prompts). Do **not** try to shorten output by lowering `effort` - it doesn't reliably work
- [ ] **[TUNE]** Agentic sessions: add a "Communicating with the user" block to calibrate inter-tool-call narration
- [ ] **[TUNE]** Claude-authored files: add a deliverable-length instruction
- [ ] **[TUNE]** **Delete** verification instructions from prompts and verification steps from the harness - including per-prompt *"double-check your answer"* phrasing, which inverts the usual self-check best practice on this model
- [ ] **[TUNE]** Add the scope-discipline instruction if the model expands task scope
- [ ] **[TUNE]** Vision pipelines: re-validate prompt-side workarounds written for a prior model's vision limitations
- [ ] **[TUNE]** Consider mid-conversation tool changes (`mid-conversation-tool-changes-2026-07-01`) - changes the tool set between turns without invalidating the prompt cache. Note per-turn `effort` / `task_budget` were **not** in this launch (per-message `effort` shipped later, beta `mid-conversation-output-config-2026-07-01`, and works on Claude Opus 5 too - see Migrating to Claude Fable 5.1 from Claude Fable 5 § New API features; `task_budget` stays request-level)
- [ ] **[TUNE]** Subagent-capable harnesses: this model delegates *more* readily than Opus 4.8 - remove any "delegate more" guidance you added for 4.8 and add an explicit cap
- [ ] **[TUNE]** User-facing products: add the corrections instruction if self-correction narration reads as thrash
- [ ] **[TUNE]** TTFT-sensitive routes (chat, voice): add *"Latency-sensitive; begin your visible answer immediately"* to reduce pre-first-block thinking; skip on background/agentic routes
- [ ] **[TUNE]** Any route running `thinking: {type: "disabled"}`: prefer turning thinking on at `low`/`medium` effort. Disabled thinking can emit tool calls as plain text (the call silently never runs) and leak `<thinking>` tags into output. If you must stay thinking-off, delete any don't-think/don't-reason rule and add the combined *"When you use a tool, you may say a brief sentence first. If no tool can express what the user asked for, say so instead of guessing. Do not include internal or system XML tags in your response"* - do not name `<thinking>` tags in the prompt
- [ ] **[TUNE]** Vision pipelines: give it crop/analyze/verify tools - cheaper and more effective than raising thinking
- [ ] **[TUNE]** Handle `stop_reason: "refusal"` before reading `content`, and opt into `fallbacks: "default"` (`server-side-fallback-2026-07-01`) rather than pinning a model - cyber-category refusals route to Claude Opus 4.8
- [ ] **[TUNE]** Long-horizon / agentic work: give the complete task spec up front in one turn rather than building it up across interactive turns

---

