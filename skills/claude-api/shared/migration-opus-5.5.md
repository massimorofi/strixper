## Migrating to Claude Opus 5.5

> **Model ID `claude-opus-5-5` is authoritative as written here.** When the user asks to migrate to Claude Opus 5.5, write `model="claude-opus-5-5"` exactly. Do **not** WebFetch to verify - this guide is the source of truth for migration target IDs. The corresponding entry exists in `shared/models.md`.

Claude Opus 5.5 succeeds Claude Opus 5 in the Opus line for long-running agentic coding and knowledge work, **at a lower price** - $4 / $20 per MTok input / output against Claude Opus 5's $5 / $25. Same 1M token context window (default and maximum), same 128K max output, same tokenizer as Claude Opus 5 (token counts unchanged; coming from a pre-Opus-4.7 model, follow the tokenizer guidance in the Claude Opus 5 section). Knowledge cutoff June 2026. Available at launch on the Claude API (`claude-opus-5-5`), Amazon Bedrock (`anthropic.claude-opus-5-5`), Claude Platform on AWS, Google Cloud, and Microsoft Foundry (all as `claude-opus-5-5`; on Foundry under both hosting options, "Hosted on Anthropic" and "Hosted on Azure"); Claude Opus 5 stays available on all of them. Existing Claude Opus 5 prompts should perform well out of the box; the Claude Opus 5 prompting patterns below remain a reasonable starting point.

**Claude Opus 5.5 is the default Opus migration target.** This section is layered on top of the Claude Opus 5 migration above - a caller coming from Opus 4.8 or older applies § Migrating to Claude Opus 5 first (Opus 4.7 or older: the sections before that), with two exceptions to what that section (and the earlier ones) say: Claude Opus 5's "thinking can be disabled at `high` or below" does not carry over, and neither does the acceptance of the earlier `computer_20251124` tool outside Amazon Bedrock (breaking change 4 below). Coming from Claude Sonnet 5: the request surface already matches (adaptive thinking, no sampling parameters, no prefill) - apply this section on top of the Claude Sonnet 5 code, re-baselining for Opus-tier pricing and rate limits.

**What changes, in one line:** four breaking changes for code running on Claude Opus 5 (thinking can't be disabled; forced `tool_choice` 400s; thinking blocks are tied to the model and the conversation - "preserved thinking"; on the Claude API and Google Cloud the `computer_20251124` tool 400s - use the computer toolset), one response-shape change that fails no request (text between tool calls comes back in `thinking` blocks), a **default effort of `medium`** where Claude Opus 5's is `high`, and a broader safety-classifier set (`bio` joins `cyber` and `reasoning_extraction`). The first three breaking changes are the same mechanisms Claude Fable 5.1 introduced - the sections below give the Claude Opus 5.5 specifics and point at § Migrating to Claude Fable 5.1 from Claude Fable 5 for the shared mechanics rather than repeating them. Everything else in the Claude Opus 5 request surface carries over: mid-conversation system messages and per-message effort (which some of the tips below use), mid-conversation tool changes, task budgets, compaction, the 512-token minimum cacheable prompt, batch, the Files API, PDF support, vision, and the server-side and client-side tools.

### Breaking change 1: thinking can't be disabled

On Claude Opus 5, thinking is on by default and `thinking: {type: "disabled"}` is accepted at effort `high` or below. On Claude Opus 5.5 thinking is **always on**: `{"type": "disabled"}` and `{"type": "enabled", "budget_tokens": N}` both return a 400 `invalid_request_error` at every effort level, with no beta header involved:

```text
"thinking.type.disabled" is not supported for this model. Use "thinking.type.adaptive" and "output_config.effort" to control thinking behavior.
```

(`"thinking.type.enabled" is not supported for this model. ...` for the budget form.) Omit the `thinking` field or send `{type: "adaptive"}`, which is equivalent. **Effort is now the control for how much the model thinks, and therefore for latency and cost** (§ Choosing an effort level below). Migrate a route that disables thinking or sets a budget as follows:

1. **Remove the `thinking` field** (or set it to `{type: "adaptive"}`).
2. **If time to first token matters, set `output_config.effort` to `low`.** At `low` the model keeps its thinking short; how often it skips thinking altogether depends on the prompts. Measure, and move to `medium` if quality drops. A system-prompt line such as *"Answer directly without deliberating."* can reduce thinking further (and with it TTFT and cost) - measure quality on your own use case before keeping it, since less thinking can cost accuracy.
3. **Size `max_tokens` for the thinking as well as the reply.** Thinking counts toward `max_tokens` even though its text isn't returned under the default `display`, so a limit sized for a no-thinking route cuts replies off. For long agentic coding turns, 64K has worked well.
4. **Read the response by block `type`, not position.** A response can begin with one or more `thinking` blocks; under the default `display: "omitted"` they come back with an empty `thinking` string. Set `display: "summarized"` for a readable summary of the reasoning. Pass `thinking` blocks back unmodified in tool-use loops.

```python
# Before - accepted on Claude Opus 5, 400 on Claude Opus 5.5
client.messages.create(
    model="claude-opus-5",
    max_tokens=16000,
    thinking={"type": "disabled"},
    messages=[{"role": "user", "content": "..."}],
)

# After - thinking is always on; effort is the control
client.messages.create(
    model="claude-opus-5-5",
    max_tokens=16000,
    output_config={"effort": "low"},
    messages=[{"role": "user", "content": "..."}],
)
```

**Prompts written for thinking disabled.** Three follow-ups if the Claude Opus 5 integration ran with thinking off: (a) start at `low` and measure, as above; (b) **remove instructions that stood in for thinking** - a prompt that asked the model to write its reasoning into the response text as a substitute for thinking should go, and the reasoning read from `display: "summarized"` blocks instead; a prompt that pushes the model to reproduce its internal reasoning in the response may be **declined** with `stop_details.category: "reasoning_extraction"`; (c) re-test the two thinking-disabled mitigations from § Two failure modes when thinking is disabled under Claude Opus 5 - both address artifacts that appeared only with thinking off, so check whether the combined "brief sentence before a tool call / say so if no tool fits / no internal XML tags" instruction is still needed, and **delete any rule telling the model not to think either way** (it can't comply, and such rules increase tag leakage).

### Breaking change 2: forced tool use is rejected

As on Claude Fable 5.1: `tool_choice: {"type": "any"}` and `{"type": "tool", "name": "..."}` return a 400 `invalid_request_error` (`tool_choice: type "tool" and "any" are not supported for this model.`) on the Messages API, the Message Batches API, and the token-counting endpoint, where Claude Opus 5 accepts both. `{"type": "auto"}` (the default) and `{"type": "none"}` are unchanged; `disable_parallel_tool_use: true` still works with `auto` but now means *at most* one call. Migrate by intent - the full patterns (steering from the prompt, `strict: true` for schema-valid arguments, structured outputs for extraction, the advisor tool) are under § Breaking change 1: forced tool use is rejected in § Migrating to Claude Fable 5.1 from Claude Fable 5. The two most common:

- **Steering toward a tool:** `tool_choice: {"type": "auto"}` plus the expectation in the prompt ("Use the `get_weather` tool to answer"), with `strict: true` on the tool definition (schema sets `additionalProperties: false`) so the arguments match the schema. Because `auto` does not guarantee a call, **check that one was made and retry if it wasn't.**
- **Extracting structured data:** if the forced call existed only to get JSON back, replace it with structured outputs (`output_config.format`).

```python
# Before - 400 on Claude Opus 5.5
response = client.messages.create(
    model="claude-opus-5",
    max_tokens=1024,
    tools=tools,
    tool_choice={"type": "tool", "name": "get_weather"},
    messages=[{"role": "user", "content": "What's the weather in Paris?"}],
)

# After - auto + strict tool use, steering in the prompt, and a check that the call happened
response = client.messages.create(
    model="claude-opus-5-5",
    max_tokens=1024,
    tools=[{**tool, "strict": True} for tool in tools],
    tool_choice={"type": "auto"},
    messages=[{"role": "user", "content": "What's the weather in Paris? Use the get_weather tool."}],
)
if not any(block.type == "tool_use" for block in response.content):
    ...  # retry, or fall back to a text answer
```

### Breaking change 3: thinking blocks are tied to the model and the conversation

Both halves of "preserved thinking" from Claude Fable 5.1 apply to Claude Opus 5.5; the mechanics (what invalidates a block, the `drop_block` request shape, `input_transformations`, the three-step audit, the append-only replacements table, which client-side compaction shapes break) are under § Breaking change 2 and § Breaking change 3 in § Migrating to Claude Fable 5.1 from Claude Fable 5 and apply verbatim. What is specific to Claude Opus 5.5:

- **Model binding - who reads whose blocks.** Claude Opus 5.5 reads thinking blocks from Claude Opus 5, from earlier Opus, Sonnet, and Haiku models, and from Claude Haiku 5.5 - a conversation that *moves onto* `claude-opus-5-5` keeps its reasoning - but **not** from any Fable or Mythos model. In the other direction, on the Claude API only Claude Fable 5.1 and Claude Mythos 5.1 read a Claude Opus 5.5 block; **no other model does** - so a router switch, a client-side retry on another model, or a classifier-refusal fallback (server-side or SDK middleware) to Claude Opus 5 / Claude Opus 4.8 runs the turns after the switch without Claude Opus 5.5's reasoning. The API drops what the target can't read before the model sees it: the request succeeds, dropped blocks aren't billed, and with the `thinking-binding-controls-2026-08-01` header the drop is reported in `input_transformations` with `reason: "model_binding_mismatch"`. Whether Claude Fable 5.1 / Claude Mythos 5.1 also read Claude Opus 5.5's blocks on Amazon Bedrock and Google Cloud is not documented - the docs state it for the Claude API only. Keep passing blocks back unchanged when you switch models; don't strip them yourself.
- **Conversation binding - who is enforced.** Same posture as Claude Fable 5.1: on every platform the prefix check (the `system` prompt, the `tools` array, and every earlier message must be byte-identical to when the block was produced) is enforced by default for accounts **created on or after August 31, 2026, 00:00 UTC** - a replayed block after such an edit is a 400. Older accounts opt in by setting `thinking.block_binding.prefix_mismatch_behavior` (`"error"` or `"drop_block"`, beta `thinking-binding-controls-2026-08-01`). Claude Code, claude.ai, Managed Agents, and the Agent SDK keep the prefix intact; **if your code builds `messages` itself, run the three-step check before migrating** - and do it now even on an exempt account, because it also raises prompt-cache hit rates. The three edits that break the prefix and their append-only replacements: a per-turn reminder injected and later deleted, or a system prompt changed mid-session (append a mid-conversation `role: "system"` message instead; for a one-turn reminder the `clear_at: "next_user_message"` form under beta `mid-conversation-system-clear-at-2026-08-21` is a limited beta - without it, append the reminder as a text block after the `tool_result` blocks and leave earlier copies in place); tools added or removed mid-session (declare the full set at session start and send `tool_addition` / `tool_removal` blocks, beta `mid-conversation-tool-changes-2026-07-01`); and compaction that summarizes older turns while replaying newer ones with their thinking blocks (use server-side compaction or context editing - the on-demand `compaction` parameter under beta `compact-2026-09-04`, offered on the Claude API, Claude Platform on AWS, Google Cloud, and Microsoft Foundry but not yet Amazon Bedrock, is designed to keep the retained turns' blocks valid after the swap - or client-side *simple* compaction that replaces the whole history with a summary and replays no earlier thinking, or set `drop_block`). Two compaction details that follow Claude Fable 5.1: a threshold-compaction request with custom `instructions` summarizes from the visible conversation only - earlier thinking blocks are not part of the summarizer's input, so tell it what the summary must retain (on-demand compaction's summarizer reads earlier thinking with or without `instructions`); and any assistant turn you re-insert after a compaction block needs its `thinking` / `redacted_thinking` blocks removed, or `drop_block` set.

```http
POST /v1/messages
anthropic-beta: thinking-binding-controls-2026-08-01

{"model": "claude-opus-5-5", "max_tokens": 64000,
 "thinking": {"type": "adaptive", "block_binding": {"prefix_mismatch_behavior": "drop_block"}},
 "messages": [ ...full history with thinking blocks replayed verbatim... ]}
```

### Breaking change 4: computer use only through the computer toolset

> **Re-check before promising computer use on a partner platform.** Which platforms offer the toolset can change - WebFetch the Computer Use page from `shared/live-sources.md` and read its Compatibility section.

Claude Opus 5 accepts computer use both as the `computer_toolset_20260801` toolset and, with the `computer-use-2025-11-24` beta header, as the earlier `computer_20251124` tool. **On the Claude API and Google Cloud, Claude Opus 5.5 accepts only the toolset**: a `tools` entry of type `computer_20251124` returns a 400 `invalid_request_error` that names the rejected type and then lists the accepted ones after `Did you mean one of` (it begins `'claude-opus-5-5' does not support tool types: computer_20251124.`). The toolset is GA on the Claude API and Google Cloud with no beta header. On Amazon Bedrock, Claude Opus 5.5 still accepts `computer_20251124` (with its beta header), as Claude Opus 5 does - keep that version there. For other platforms, check the computer use tool's Compatibility section (`shared/tool-use-concepts.md` § Computer Use has the toolset summary). This is more than a `tools`-entry swap, so make and test the change on Claude Opus 5 first (it accepts both forms):

- **Request:** drop the beta header and the beta client namespace; the entry is `{"type": "computer_toolset_20260801"}` with **no `name`** and no `display_width_px` / `display_height_px`; an optional `configs` map turns individual member tools on or off (`{"zoom": {"enabled": false}}`). All 17 members, `zoom` included, are on by default. The entry can't share a request with a `computer_20251124` entry or another tool named `computer`.
- **Agent loop:** Claude's calls are `tool_use` blocks whose `name` is the member (`screenshot`, `left_click`, `type`, `zoom`, ...) - **the action is the block's `name`, not `input.action`** - carrying `"toolset_name": "computer"`, and there can be **several per turn** (a batch action), each its own block. Return one `tool_result` per `tool_use`, matched by `tool_use_id`, all in the next `user` message, **every one echoing `"toolset_name": "computer"`** (a result that omits it is rejected); only `screenshot` and `zoom` results need an image, a short `OK` is enough for the rest. Coordinates are in the pixel space of the full screenshots you return, also after a `zoom`. Screenshots must already fit the model's image limits (the toolset takes no display dimensions and the API doesn't downscale for you).

```python
# Before - 400 on Claude Opus 5.5
client.beta.messages.create(
    model="claude-opus-5",
    max_tokens=4096,
    betas=["computer-use-2025-11-24"],
    tools=[{"type": "computer_20251124", "name": "computer",
            "display_width_px": 1024, "display_height_px": 768}],
    messages=[{"role": "user", "content": "Open the display settings."}],
)

# After - no beta header; the toolset entry takes no name or display size
client.messages.create(
    model="claude-opus-5-5",
    max_tokens=4096,
    tools=[{"type": "computer_toolset_20260801"}],
    messages=[{"role": "user", "content": "Open the display settings."}],
)
```

Integrations already on the toolset, and the browser use toolset (`browser_toolset_20260801`), need no change.

### Text between tool calls comes back in thinking blocks

On Claude Opus 5, the short notes the model writes between tool calls (what it just found, what it's doing next) come back as `text` blocks. On Claude Opus 5.5, as on Claude Fable 5.1, notes longer than a sentence or two come back as **progress-update `thinking` blocks**, at most one before each tool call, and under the default `display: "omitted"` their text is empty - no request fails, but a client that renders only `text` blocks goes quiet for the length of a long agentic turn. Fix: set `thinking.display: "updates"` (beta `thinking-display-updates-2026-08-18`) to get a short summary of each note as text while reasoning stays hidden (`"summarized"` returns both, mixed), render each non-empty `thinking` block ahead of the `tool_use` it precedes, and pass the blocks back unchanged - the consumption rules (streaming `thinking_delta`, the interrupted-work sentinel, zero-or-more per response) are under addition 3 in § New API features of § Migrating to Claude Fable 5.1 from Claude Fable 5:

```http
POST /v1/messages
anthropic-beta: thinking-display-updates-2026-08-18

{"model": "claude-opus-5-5", "max_tokens": 64000,
 "thinking": {"type": "adaptive", "display": "updates"},
 "tools": [...],
 "messages": [{"role": "user", "content": "Review the PRs open against our billing service."}]}
```

Three levers on what users see:

1. **Receive them** - `display: "updates"` as above (a `"summarized"` display returns them too, mixed with the reasoning summaries).
2. **If the model may need to hand the user something *verbatim*** partway through a long turn - a code snippet, an exact value - give it a simple tool for sending the user a message and tell it to reserve the tool for that content. Declare the tool in `tools` from the **first** request of the session: adding it later edits the conversation's prefix and invalidates earlier thinking blocks (breaking change 3).
3. **For more frequent or predictable updates** - a one-line statement of intent before the first tool call and a short recap at the end - say so in the system prompt: when you want user-facing text and what it should contain. The model follows such instructions reasonably well; this helps most in pair programming and other human-in-the-loop work.

### Choosing an effort level - the default is `medium`, and the levels don't map 1:1 from Claude Opus 5

Effort is the main control for how much Claude Opus 5.5 thinks, and with adaptive-only thinking it is the first setting to adjust when trading off intelligence, latency, and cost. Two things change from Claude Opus 5:

- **The API default is `medium`** (Claude Opus 5 and earlier Opus models default to `high`), so a request that omits `effort` now runs one level lower than it did. **Set `effort` explicitly** and re-run the sweep rather than carrying the Claude Opus 5 setting over. Effort names don't mean the same amount of thinking across models: in Anthropic's testing, Claude Opus 5.5 at `medium` exceeds Claude Opus 5 at `high` on coding and knowledge-work evaluations, and on several coding evaluations `low` comes close to it at much lower cost. Start at `medium` and test the neighboring levels; reserve `xhigh` and `max` for work where you have measured a quality gain (all five levels are supported; `max` is uncapped).
- **At a given level, Claude Opus 5.5 tends to think more per turn than Claude Opus 5**, especially at `xhigh` and `max`. If you keep the `effort` value you set for Claude Opus 5, expect longer turns and more output tokens. To get less thinking, **lower the effort level before adding "think less" instructions** - lowering effort reduces thinking, cost, and latency more reliably than prompting does. Set `max_tokens` with room for the thinking as well as the reply (thinking counts toward it even though the text isn't returned - a limit sized for Claude Opus 5 with thinking off can cut replies off; 64K is a reasonable starting point for long agentic coding turns).

Change effort for individual turns without invalidating the prompt cache with a **per-message effort change** (beta `mid-conversation-output-config-2026-07-01`; the request shape is addition 1 under § New API features of § Migrating to Claude Fable 5.1 from Claude Fable 5, and Claude Opus 5 already supports it). Changing the **top-level** `effort` between requests does invalidate the prompt cache.

### Safeguards - a broader classifier set than Claude Opus 5

Claude Opus 5.5 runs cybersecurity **and biology** safety classifiers similar to Claude Fable 5.1's; coming from Claude Opus 5, the biology classifier is new. Everyday health and educational questions are unaffected, but requests the classifier treats as dual-use biology research (virology, toxicology, molecular design) are declined; on the cybersecurity side, finding vulnerabilities in source code is allowed. Separately - as on Claude Opus 5 - a request that tries to get the model to reproduce its internal reasoning in the response text may be declined with `stop_details.category: "reasoning_extraction"`; if a prompt does this (for example, to get visible reasoning with thinking off), remove the instruction, set `display: "summarized"`, and read the `thinking` blocks. You can still ask for a short explanation of the answer or a summary of the actions taken; see [Keep reasoning in thinking blocks](https://platform.claude.com/docs/en/build-with-claude/refusals-and-fallback#keep-reasoning-in-thinking-blocks). **`reasoning_extraction` declines are not retried on a fallback model.**

A classifier decline arrives as a normal HTTP 200 with `stop_reason: "refusal"` and a `stop_details` object naming the category (`"cyber"`, `"bio"`, `"reasoning_extraction"`, ...; branch on `stop_reason`, treat `stop_details` as informational - the full handling is § `refusal` stop reason under § Migrating to Claude Fable 5.1). A refusal before any output still counts against your rate limits; for whether it is billed, see [How refusals are billed](https://platform.claude.com/docs/en/build-with-claude/refusals-and-fallback#how-refusals-are-billed). Retry on another model with server-side fallbacks - `fallbacks: "default"` under beta `server-side-fallback-2026-07-01` retries on the model Anthropic recommends for that category, the array form under `server-side-fallback-2026-06-01` names your own targets (§ New API features under § Migrating to Claude Opus 5 has both shapes; read Claude Opus 5.5's permitted targets from `allowed_fallback_models` on its `/v1/models` entry, as § `refusal` stop reason describes - expect Claude Opus 5 / claude-opus-4-8), the SDK middleware on platforms without server-side fallback, or your own retry. A fallback model runs without Claude Opus 5.5's thinking blocks (breaking change 3). **Ship the opt-in from day one**, as the Claude Fable 5.1 section says.

The classifiers can still flag benign requests - the fallback opt-in is what keeps a false positive from becoming an outage. (The docs give no prompt change that avoids Claude Opus 5.5's false positives; the **Safeguard false positives** tips under § Migrating to Claude Fable 5.1 from Claude Fable 5 are documented for Claude Fable 5.1 only - do not offer them for Claude Opus 5.5.)

### What carries over unchanged from Claude Opus 5 - and what to re-check

- **Feature set:** per-message effort (beta), mid-conversation system messages (no header) and tool changes (beta), task budgets, compaction (including the on-demand `compaction` parameter, beta `compact-2026-09-04`, which has its own docs), prompt caching with the 512-token minimum, batch processing (up to 300K output tokens with the `output-300k-2026-03-24` beta), the Files API, PDF support, vision, structured outputs, strict tool use, and the same server-side and client-side tools - except computer use, which needs the toolset (breaking change 4). Programmatic tool calling lists the model.
- **Pricing:** $4 / $20 per MTok; 5-minute cache writes $5 and 1-hour cache writes $8; **cache reads $0.20 per MTok (0.05x base input)**; batch $2 / $10. The cache-read discount is deeper than Claude Opus 5's, so long agentic sessions that re-read a cached prefix save more, and a miss costs relatively more - keeping the cache warm (per-message effort, append-only histories, the keep-alive patterns in `shared/prompt-caching.md`) matters more.
- **Rate limits:** a separate pool from Claude Opus 5's, with its own per-tier numbers. Re-check your tier's Claude Opus 5.5 limits before moving volume.
- **Priority Tier:** not supported (as Claude Opus 5).
- **Fast mode:** research preview on the Claude API only (not Bedrock, Claude Platform on AWS, Google Cloud, or Foundry), `speed: "fast"` under beta `fast-mode-2026-02-01`, at **$8 / $40 per MTok** (2x the standard price, the same multiple as Claude Opus 5's $10 / $50).
- **Data retention / ZDR:** nothing new is documented - treat Claude Opus 5.5 as Claude Opus 5 here.
- **SDK constants:** `Model.ClaudeOpus5_5` (C#), `anthropic.ModelClaudeOpus5_5` (Go), `Model.CLAUDE_OPUS_5_5` (Java, PHP), `Anthropic::Model::CLAUDE_OPUS_5_5` (Ruby) - published with each SDK's launch release; the bare string `"claude-opus-5-5"` works everywhere before then.

### Capability improvements versus Claude Opus 5

**Cheaper per solved task, not just per token.** On many coding, analysis, and vision tasks, Claude Opus 5.5 at its default effort matched or beat Claude Opus 5 while using fewer tokens, and its price per token is 20% lower than Claude Opus 5's (60% lower for cache reads) - so expect the cost per solved task to be significantly lower for most tasks, and re-baseline `shared/cost-optimization.md`'s Claude Opus 5 figures rather than scaling them by the list price alone.

- **Agentic coding and code review:** the largest measured gains are on multistep work in a real codebase (carrying a change through a large repository until its tests pass) - in Anthropic's testing, at its default `medium` effort it matched or beat Claude Opus 5's `high`-effort results on such tasks, in fewer steps and with about half the tokens - and on code review, where it catches more bugs with fewer false alarms. It explains its changes in plain language, which makes its work easier to review and trust. It tends to finish the same task with fewer tokens.
- **Knowledge work:** a more reliable analyst - much less likely than Claude Opus 5 to state a figure or cite a source the inputs don't support (citations pointed at the right source much more often in one customer's measurement); at `medium` it produced better long analytical deliverables than Claude Opus 5 at `high` with roughly 40% fewer output tokens; better at building and auditing financial models; more detail-oriented on large inputs (a date in a long thread that falls on the wrong weekday, a chart in a deck that doesn't match the figures) **without more false-positive flags**.
- **Communication and writing:** clearer prose, most noticeably in how it reports on agentic work - its updates while it works and its summary when it finishes say plainly what it did, what it found, and what it needs from you, with less jargon and fewer stock phrases.
- **Charts, diagrams, screenshots, and computer use:** reads visual material more accurately at every effort level without extra tooling - in Anthropic's testing, even at `low` it read charts more accurately than Claude Opus 5 at its highest effort, at roughly a tenth of the output tokens per chart (Claude Opus 5 read charts well only by running code to crop, zoom, and measure) - values on dense charts, and meaning that depends on position rather than text (which boxes an arrow connects, what changed between two diagram versions, exactly when a meeting starts and ends in a calendar screenshot). Also more reliable at multistep computer use from screenshots: at its default effort it matched the success rate Claude Opus 5 reached only at a much higher effort setting, in fewer steps and with roughly 40% fewer tokens.

### Behavioral shifts (prompt-tunable)

- **Re-evaluate Claude Opus 5-specific instructions.** Instructions tuned for Claude Opus 5's behavior (the verbosity, over-verification, and scope prompts under § Behavioral shifts of § Migrating to Claude Opus 5) may no longer be needed - keep them as the starting point, then re-test each on your own evals rather than carrying them over untouched.
- **Progress updates:** covered above - render the `thinking` blocks, and ask in the system prompt for the cadence you want.
- **Frontend design defaults:** asked for frontend work without design direction, it falls back on a few default styles, and a general instruction such as "avoid a generic AI look" mostly swaps one default for another. **It responds well to instructions that name specific patterns to avoid.** Work iteratively - look at which styles the first result used instead, and extend the list:

  > *"Output a vanilla HTML/CSS personal website with placeholder data. Do not use a cream or off-white background, italic accent words in headlines, numbered "01/02/03" section labels, monospace labels, or pill-shaped buttons."*

- **Ingesting complex visual inputs:** it reads charts, diagrams, and screenshots considerably more precisely out of the box, so **harness scaffolding built for visual inputs on earlier models may no longer be needed - re-test it.** For the densest inputs, two things still add accuracy: higher-resolution images (most of all for technical drawings), and image-processing tools - run it as an agent with a container holding the raw images and libraries such as PIL and OpenCV so it can crop, zoom, measure, and verify; if a container is too much overhead, a cropping tool alone still helps. It uses these tools more effectively at higher effort levels; without tools, raising effort improves its reading of technical drawings but does little for charts.
- **Long turns:** at `xhigh` / `max`, turns run longer than on Claude Opus 5 - plan timeouts, streaming, and progress UX accordingly, and lower effort before prompting for brevity.

### Claude Opus 5.5 Migration Checklist

- [ ] **[BLOCKS]** Model ID -> `claude-opus-5-5` (Bedrock: `anthropic.claude-opus-5-5`). Coming from Opus 4.8 or older, the Claude Opus 5 checklist first - except that disabling thinking is not an option.
- [ ] **[BLOCKS]** Remove `thinking: {type: "disabled"}` and `{type: "enabled", budget_tokens}` on every route - both 400 at every effort level. Choose an effort level instead; size `max_tokens` for thinking plus the reply; read content blocks by `type`; pass `thinking` blocks back unmodified.
- [ ] **[BLOCKS]** Replace `tool_choice` `any` / `tool` with `auto` plus `strict: true` (steering in the prompt, and a check that the call happened) or structured outputs - on `count_tokens` and Batches too.
- [ ] **[BLOCKS]** Computer use on the Claude API and Google Cloud: declare `{"type": "computer_toolset_20260801"}` (no beta header, no `name` / display size) instead of `computer_20251124` (Amazon Bedrock still accepts `computer_20251124`), and update the agent loop for member `tool_use` blocks (action = block `name`), batch actions, and `toolset_name` on every result; confirm the toolset is offered on your platform. Test on Claude Opus 5 first.
- [ ] **[BLOCKS]** If the harness builds `messages` itself: run the preserved-thinking three-step check (§ Migrating to Claude Fable 5.1 from Claude Fable 5) - accounts created on or after 2026-08-31 are enforced by default on every platform; set `prefix_mismatch_behavior` explicitly under `thinking-binding-controls-2026-08-01` and replace every history edit with its append-only form. Declare from the first request any tool the session may need later.
- [ ] **[BLOCKS]** Handle `stop_reason: "refusal"` before reading `content` (`bio` is a new category) and ship a fallback opt-in; `reasoning_extraction` is not retried on a fallback.
- [ ] **[TUNE]** Set `effort` explicitly - the default is `medium`, one level below Claude Opus 5's `high` - and re-run the sweep including `low` / `medium`; lower effort before adding "think less" prompts; reserve `xhigh` / `max` for measured gains; use per-message effort (beta) to vary it without a cache reset.
- [ ] **[TUNE]** If the UI showed text between tool calls: `display: "updates"` (beta) or `"summarized"`, render non-empty `thinking` blocks; give the model a send-message tool (declared at session start) for verbatim mid-turn content; ask in the system prompt for the update cadence you want.
- [ ] **[TUNE]** If a router or fallback can move the conversation to another model, expect it to run without Claude Opus 5.5's thinking blocks (only Claude Fable 5.1 / Claude Mythos 5.1 on the Claude API keep them).
- [ ] **[TUNE]** If thinking was disabled on Claude Opus 5: start at `low`, remove reasoning-in-the-response instructions, re-test the thinking-disabled mitigation instruction, delete any "don't think" rule.
- [ ] **[TUNE]** Re-test visual-input scaffolding (may be unnecessary now); for frontend work, name the specific default patterns to avoid rather than asking for "no generic look"; re-evaluate Claude Opus 5-specific verbosity / verification / scope instructions.
- [ ] **[TUNE]** Re-baseline cost and latency at the chosen effort level: $4 / $20, cache reads $0.20 per MTok; separate rate-limit pool; no Priority Tier; fast mode is Claude API only at $8 / $40.

---

