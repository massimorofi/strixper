## Migrating to Claude Fable 5.1

> **Model IDs `claude-fable-5-1` and `claude-mythos-5-1` are authoritative as written here.** When the user asks to migrate to Claude Fable 5.1, write `model="claude-fable-5-1"` exactly; a Mythos Preview migrator in Project Glasswing writes `model="claude-mythos-5-1"` (everyone else: `claude-fable-5-1`). Do **not** WebFetch to verify - this guide is the source of truth for migration target IDs. The corresponding entries exist in `shared/models.md`.

Claude Fable 5.1 is Anthropic's most capable widely released model - for the most demanding reasoning and long-horizon agentic work. **Claude Mythos 5.1** (`claude-mythos-5-1`) offers the same capabilities and pricing through Project Glasswing (participation is the only way to access it), and succeeds the invitation-only **Claude Mythos Preview** (`claude-mythos-preview`). Everything in this section applies to both models except where § Claude Mythos 5.1 below says otherwise (the history-editing check, platform availability, and safeguards that depend on the access program). Mythos Preview migrators in Project Glasswing target `claude-mythos-5-1`; everyone else targets `claude-fable-5-1`. 1M token context window by default (the maximum is also the default), up to 128K output tokens per request.

**Migrate to Claude Fable 5.1 only when the user explicitly chose it.** It is not the default Opus upgrade path - pricing is above Opus-tier. For "upgrade to the latest model" requests, the target is `claude-opus-5-5`.

### Breaking changes (vs Opus-tier and Mythos Preview)

> Claude Fable 5.1 carries three further breaking changes introduced after Claude Fable 5: forced `tool_choice` (`any` / `tool`) returns a 400, thinking blocks are bound to the producing model, and editing earlier turns invalidates thinking blocks. They are covered in § Migrating to Claude Fable 5.1 from Claude Fable 5 below - apply that section on top of this one when coming from Opus-tier or older.

1. **Thinking is always on - remove all `thinking` configuration.** Adaptive thinking applies automatically whenever the `thinking` parameter is unset (an explicit `{type: "adaptive"}` is also accepted). Any other configuration is rejected: `thinking: {type: "disabled"}` and `{type: "enabled", budget_tokens: N}` both return a 400. `budget_tokens` has no replacement - the `output_config.effort` parameter is a separate output-level control, not a thinking budget.

   ```python
   # Before (Mythos Preview / older models)
   client.messages.create(
       model="claude-mythos-preview",
       max_tokens=16000,
       thinking={"type": "enabled", "budget_tokens": 10000},
       messages=[...],
   )

   # After (Claude Fable 5.1) - no thinking field at all
   client.messages.create(
       model="claude-fable-5-1",
       max_tokens=16000,
       output_config={"effort": "high"},
       messages=[...],
   )
   ```

2. **Assistant prefill is not supported.** Replace last-assistant-turn prefills with structured outputs (`output_config.format`) or system prompt instructions - same replacement patterns as the 4.6-family prefill removal above. (One exception: the fallback-credit prefill claim - the server accepts the echoed assistant message when redeeming a credit; see the refusal section below.)

3. **Interleaved scratchpad is not supported** (Mythos Preview migrators only). Inter-tool reasoning is returned in thinking blocks instead, which adaptive thinking produces automatically between tool calls.

### Thinking output on Claude Fable 5.1 and Claude Mythos 5.1

On Claude Fable 5.1 and Claude Mythos 5.1, the raw chain of thought is never returned. What you receive are **regular `thinking` blocks**, not encrypted blobs or `redacted_thinking`: `display: "summarized"` returns a readable summary of the reasoning, and with `"omitted"` - the default, same as Opus 4.8/4.7 - responses still include `thinking` blocks but the `thinking` field is an empty string. `display` controls visibility only; thinking happens and is billed the same under every setting. When continuing a conversation on the same model, pass thinking blocks back to the API **unchanged** (the standard multi-turn pattern; dropping or editing them breaks the turn).

When continuing on the same model, pass each thinking block back **exactly as received - including blocks whose `thinking` text is empty**. The API rejects blocks whose content has been *modified*, not blocks you have read; displaying the summary is fine, editing or reconstructing blocks is not.

Regular thinking blocks aren't origin-locked - they replay across models fine (the server renders them into the target model's prompt). Fable-tier thinking is the exception: a Claude Fable 5.1 / Claude Mythos 5.1 block is read only by that pair (apart from Claude Mythos 5.1, no other model can read a Claude Fable 5.1 block - see Migrating to Claude Fable 5.1 from Claude Fable 5), and a thinking block from Claude Fable 5/Claude Mythos 5 replayed to a different model is **dropped from the prompt** rather than rendered (except by Claude Fable 5.1 / Claude Mythos 5.1, which read these blocks) - typically silently (this behavior is still rolling out, so don't build logic that depends on the drop being silent). The drop happens before the prompt is priced, so a dropped block **lowers `usage.input_tokens`** - you aren't billed for it, and there's nothing to strip for cost. Don't strip *regular* thinking blocks either: removing them can trigger ordering/signature 400s. Two rules for replay bodies stand regardless: fallback-credit retries must echo the refused body **unchanged**, and `fallback` blocks from a mid-output fallback stay where they appeared.

Related: a request that tries to elicit the model's internal reasoning *in the response text* may be refused with `stop_details.category: "reasoning_extraction"` - applications needing reasoning visibility should read the summarized `thinking` blocks instead of prompting for reasoning. You can still ask for a short explanation of the answer or a summary of the actions taken; see [Keep reasoning in thinking blocks](https://platform.claude.com/docs/en/build-with-claude/refusals-and-fallback#keep-reasoning-in-thinking-blocks).

### Tokenizer - unchanged from Opus 4.8

Claude Fable 5.1 uses the **same tokenizer as Claude Opus 4.8** (the tokenizer introduced with Opus 4.7). Token counts are roughly unchanged when migrating from Opus 4.7/4.8 or from `claude-mythos-preview`; per-token pricing differs.

- Coming **from Opus 4.7/4.8 or `claude-mythos-preview`**: token counts are roughly unchanged. Re-baseline cost and latency on your own workloads for the per-token price difference.
- Coming **from Opus 4.6, Sonnet, Haiku, or older**: the Opus 4.7 tokenizer tokenizes the same content to roughly 1×-1.35× as many tokens (varies by content and workload shape). Do not reuse token counts, context-window budgets, or `max_tokens` settings measured on the old model; re-baseline with `count_tokens`.

To measure the difference on your own prompts, call `count_tokens` once with your current model and once with `model: "claude-fable-5-1"`, and compare the two `input_tokens` values.

### `refusal` stop reason - handle before reading content

Claude Fable 5.1 runs safety classifiers on incoming requests, targeting research biology and most cybersecurity content (Claude Fable 5.1 is not intended for those domains); benign adjacent work - security tooling, life-sciences tasks - can occasionally trigger false positives, which is why the fallback patterns below matter even for legitimate workloads. (Most Claude consumer surfaces ship with built-in Opus 4.8 fallbacks; API callers configure their own.) A declined request returns a **successful HTTP 200** with `stop_reason: "refusal"`, plus a `stop_details` object with the policy category (values such as `"cyber"`, `"bio"`, `"reasoning_extraction"`, `"frontier_llm"`, or `null` - treat `null` as a permanent valid state; see the refusal category table in the public docs for the full set). **Branch on `stop_reason`, never on `stop_details`** - `stop_details` is informational and can be `null` even on a refusal, and `explanation` is not guaranteed present. Note that classifier blocks and ordinary model refusals (the model itself declining) both surface as `stop_reason: "refusal"`; `stop_details.category` tells you which class you're handling, and therefore whether retrying on a fallback model is the right response. The classifier can fire **before any output** (empty `content` array; counts against rate limits - for billing, see [How refusals are billed](https://platform.claude.com/docs/en/build-with-claude/refusals-and-fallback#how-refusals-are-billed)) or **mid-stream** after partial output (input and already-streamed output are billed at normal rates - discard the partial output rather than treating it as complete). Code that reads `response.content[0]` unconditionally will break - check `stop_reason` first:

```python
response = client.messages.create(model="claude-fable-5-1", max_tokens=1024, messages=[...])
if response.stop_reason == "refusal":
    # classifiers declined; content is empty (pre-output) or partial (mid-stream)
    handle_refusal()
else:
    print(response.content[0].text)
```

**Default to opting in.** Fallbacks are not automatic on the API - a request without them simply stops on a refusal. Migrated and new Claude Fable 5.1 code should ship with pattern 1 below (pattern 2 on providers without server-side support) from day one, not as a later hardening step: emit the opt-in in the code, tell the user it's there, and remove it only if they decline.

Three ways to retry a refused request on another model, in order of preference:

**1. Server-side `fallbacks` parameter (beta; Claude API and Claude Platform on AWS) - preferred.** One round trip, a plain client, no client-side logic. Name substitute models (the supported fallback targets are `claude-opus-4-8` and `claude-opus-5`, expansion expected); on a policy decline the API runs the next model on the same request and returns its answer, with credit-style repricing applied automatically. A `stop_reason: "refusal"` on the final response means the whole chain refused.

```python
response = client.beta.messages.create(
    model="claude-fable-5-1",
    max_tokens=1024,
    betas=["server-side-fallback-2026-06-01"],
    fallbacks=[{"model": "claude-opus-4-8"}],
    messages=[{"role": "user", "content": "Hello, Claude"}],
)

# Switch points: one fallback block per model that ran and declined this turn
for block in response.content:
    if block.type == "fallback":
        print(f"{block.from_.model} declined; {block.to.model} continued")

# Served-by signal: a fallback_message in usage.iterations means a fallback model
# ran; pair it with stop_reason to confirm the fallback served the response
# (a fallback model can also refuse). Covers sticky turns too.
fallback_ran = any(
    entry.type == "fallback_message" for entry in response.usage.iterations or []
)
if fallback_ran and response.stop_reason != "refusal":
    print(f"Served by {response.model}")
```

Key semantics:

- **Header depends on the form you use.** The **array** form (`fallbacks: [{...}]`) requires exactly `server-side-fallback-2026-06-01` - other `server-side-fallback-*` values reject it with a 400, and that header carries the *earliest* date of the series (`-2026-06-09` and `-2026-06-02` were earlier previews), so do not "correct" it to a newer-looking date. The **`"default"` scalar** form uses `server-side-fallback-2026-07-01` instead - see § New API features under Migrating to Claude Opus 5. Pairing either header with the other form 400s. Rejected on the Batches API; available on the Claude API and Claude Platform on AWS; not on Amazon Bedrock, Vertex AI, or Microsoft Foundry (use pattern 2 there - the SDK middleware). Entries may override `max_tokens` per hop (bounding that attempt's own output independently of the top-level `max_tokens`); `thinking`, `output_config`, and `speed` overrides are rolling out (`speed` additionally requires its beta) - until your requests accept them, include only `model` and `max_tokens` in each entry. Entries must be distinct and must be in the requested model's `allowed_fallback_models` (published on `/v1/models` when the `server-side-fallback-2026-06-01` beta header is set - not yet visible under the `fallback-credit-*` header alone, and not exposed on Amazon Bedrock, Vertex AI, or Microsoft Foundry). The request *with an entry's overrides merged in* must be valid as a direct request to that entry's model.
- **Triggers on policy declines only** - rate limits, overloads, and server errors on the requested model are returned as-is, never falling back.
- **Reading the response:** a `fallback` content block (`{"type": "fallback", "from": {"model": ...}, "to": {"model": ...}}`) marks each switch point in `content`; the served-by signal is a `fallback_message` entry in `usage.iterations` (don't rely on the block - sticky-served turns have none). Top-level `model` names the model that produced the message.
- **Billing:** `usage.iterations` is the per-attempt source of truth; top-level `usage` covers only the attempt that produced the returned message. Declined-before-output attempts are reported (for whether they're billed, see [How refusals are billed](https://platform.claude.com/docs/en/build-with-claude/refusals-and-fallback#how-refusals-are-billed)); an attempt that declines mid-stream bills at normal rates, and fallback attempts bill at the fallback model's rates. Each attempt claims the rate limits of the model that ran it - if the fallback model is rate-limited or overloaded, the fallback attempt is not made and the preceding refusal is returned instead with `stop_details.recommended_model` naming a model to retry directly (the recommendation is a hint, not a guarantee, and is `null` when no recommendation is available) - size fallback-model limits for expected refusal volume.
- **Sticky routing:** once a conversation falls back, later requests with `fallbacks` (streaming and non-streaming - on a stream the decision is made before it opens, so `message_start` already names the fallback model) are served directly by the fallback model for ~1 hour (best-effort; org-scoped content-hash record, not message content; not recorded for ZDR orgs). Handle the requested model being tried again at any time.
- **Echoing fallback turns back:** after a mid-output fallback, omit `thinking`, `redacted_thinking`, and `tool_use` blocks - plus any `server_tool_use` block without its matching `server_tool_result`, and any other unrecognized model-internal block type - that appear *before* the final `fallback` block; text blocks, paired server-tool blocks, and everything after the boundary echo normally. The `fallback` block itself is an ignored audit marker (keep or drop). Streaming: the retry happens on the same stream and already-received content is never invalidated - a pre-output block is seamless (`message_start` names the fallback model; the `fallback` block arrives as an ordinary `content_block_start`, first in `content` - there is no special SSE event type; note `message_start` arrives only after the declined attempt, so time-to-first-byte includes it), and a mid-stream block keeps the partial, marks the boundary with the block, and continues - only the partial's `text` blocks are passed to the fallback model as continuation context (other block types stay in `content` but aren't part of it). Non-streaming mid-output declines omit the declined partial entirely.

**2. SDK client-side middleware - for providers without server-side fallbacks (Amazon Bedrock, Vertex AI, Microsoft Foundry).** Register it on the client and every `client.beta.messages` request (streaming included) retries refusals automatically, splicing the fallback model's events onto the open stream in the same wire shape as pattern 1 (a `fallback` content block at each boundary, per-hop `usage.iterations`). It is also a beta surface: the middleware sends the `fallback-credit-2026-07-01` header by default so retries are repriced via credit tokens (override with its `betas` option). `BetaFallbackState` pins follow-up turns to the model that accepted (the client-side analog of sticky routing) - reuse one state object per conversation:

```python
from anthropic import Anthropic, BetaFallbackState, BetaRefusalFallbackMiddleware

client = Anthropic(middleware=[BetaRefusalFallbackMiddleware([{"model": "claude-opus-4-8"}])])
state = BetaFallbackState()  # pins follow-ups to the model that accepted
with state:
    response = client.beta.messages.create(model="claude-fable-5-1", max_tokens=1024, messages=messages)
```

Create **one state per conversation** - it is the pinning scope; sharing one across conversations pins unrelated threads together, and a conversation without a state is never pinned. Per-language naming (from the GA SDK examples - don't improvise):

- **TypeScript**: `betaRefusalFallbackMiddleware([...])` in the client's `middleware` array; pass `{ fallbackState: state }` (a `BetaFallbackState`) as a request option.
- **Go**: `option.WithMiddleware(betafallback.BetaRefusalFallbackMiddleware([]anthropic.BetaFallbackParam{{Model: ...}}))` (package `lib/betafallback`); state via `betafallback.WithBetaFallbackState(&betafallback.BetaFallbackState{})` passed as a request option. Server-side equivalents: `Fallbacks: []anthropic.BetaFallbackParam{...}` + `anthropic.AnthropicBetaServerSideFallback2026_06_01`.
- **C#**: it's a *handler* - `new AnthropicClient { Handlers = [new BetaRefusalFallbackHandler { Fallbacks = [new(Model.ClaudeOpus4_8)] }] }` (namespace `Anthropic.Helpers`); state via `BetaFallbackState.Create()` scoped per call with `using (fallbackState.Use()) { ... }`. Server-side equivalents: `Fallbacks = [new(Model.ClaudeOpus4_8)]` + `AnthropicBeta.ServerSideFallback2026_06_01`.

For languages not listed (Java, Ruby, PHP) - or for a full runnable program in any language - each public SDK repo ships a fallbacks example under `examples/` (e.g. `examples/fallbacks.py`, `examples/refusal-fallback/`): WebFetch the repo from `shared/live-sources.md` § SDK Repositories rather than improvising the binding.

**3. Hand-rolled retry + fallback credit (raw HTTP, or SDKs without the middleware).** Detect the refusal via `stop_reason` and re-send the conversation as-is on a model with broader availability such as `claude-opus-4-8` (no stripping required either way: Claude Fable 5's thinking blocks are silently ignored by models other than Claude Fable 5.1 / Claude Mythos 5.1, which read them, and Claude Fable 5.1's own blocks are dropped by the API for any other model - breaking change 2 in § Migrating to Claude Fable 5.1 from Claude Fable 5); keep using the fallback model for subsequent turns. **Fallback credit** (beta: Claude API, Claude Platform on AWS, Amazon Bedrock, Vertex AI, and Microsoft Foundry) makes those retries cheaper. Prompt caches are per-model, so a plain retry pays cold cache-writes on the new model. With the `fallback-credit-2026-07-01` beta header (send it on both the original request and the retry), a refusal's `stop_details` carries `fallback_credit_token` (opaque; `null` when unavailable) and `fallback_has_prefill_claim`. The earlier `fallback-credit-2026-06-01` header is still accepted and grants the same fields. Echo the token as the top-level `fallback_credit_token` request parameter on the retry (typed in the GA SDKs; on a pre-GA SDK pass it via `extra_body`) and the previously-cached span bills at cache-read rates - the retry costs what it would have if the conversation had been on that model all along. Rules: the retry body must match the refused request **exactly** in every prompt-shaping field (`system`, `messages`, `tools`, `tool_choice`, `thinking` - do **not** strip thinking blocks when redeeming a credit - the server handles them); the retry model must be in the refused model's `allowed_fallback_models`; the token expires in 5 minutes; Batches results carry no tokens. If `fallback_has_prefill_claim` is `true`, append one assistant message echoing the refused response's `content` - the retry model continues from where the refused model stopped (and completed server-tool work isn't re-run). When echoing, strip trailing whitespace from a final `text` block (the prefill validator rejects it; the credit match tolerates that edit), after omitting any unpaired `tool_use` blocks. On a 400, fall back to the unchanged body with the token; on a 400 naming `fallback_credit_token`, retry without it (credit forfeited).

**Migrating code built on the v1 preview.** If the code you're editing carries any of these markers, it targets the discontinued preview surface - migrate it to the v2 shapes above, and ship the header and parameter changes together (the v1 parameter shape under the v2 header is a 400):

| v1 marker (replace) | v2 |
|---|---|
| `server-side-fallback-2026-06-09` / `-2026-06-02` header | `server-side-fallback-2026-06-01` (array form; the `"default"` scalar form uses `-2026-07-01`) |
| `fallback: {model, on_partial}` single object | `fallbacks: [{model, ...}]` array (1-3); `on_partial` no longer exists - partial-output behavior is fixed (streams keep the partial; non-streaming omits it). Unknown keys in an entry are a 400 |
| Top-level `response.fallback` object (`from_model`, `reason`) | Never emitted - read `fallback` content blocks (switch points, no `reason` field) and `usage.iterations` (served-by) |
| `event: fallback` SSE with discard indices | No dedicated event; streamed content is never invalidated - the switch arrives as an ordinary `content_block_start`/`stop` pair of type `fallback` |
| `fallback_primary` / `fallback_retry` iteration types | Blocked attempts are plain `message` entries; the serving attempt is `fallback_message` |
| `reason: "sticky"` | No reason field - sticky turns carry no block; detect via `fallback_message` in `usage.iterations` + `response.model` |
| `recommended_model` meaning "primary served the refusal" | Now populated only when the fallback attempt *couldn't run* (rate-limited/overloaded) - its presence means a direct retry on that model may succeed, not that it refused too |

### Data retention requirement

Claude Fable 5.1 requires **30-day data retention** and is not available under zero data retention. Requests from an organization whose data-retention configuration doesn't meet the requirement return `400 invalid_request_error` - if a migration suddenly 400s with no obvious request problem, check the org's retention configuration before debugging the payload. On Amazon Bedrock, Google Vertex AI, and Microsoft Foundry, data-retention requirements are set by each platform.

### What carries over unchanged

Same Messages API and tool-use patterns as Opus-tier and Mythos Preview. Supported at launch: `output_config.effort` (`low`/`medium`/`high`/`xhigh`/`max`), Task Budgets (beta, `task-budgets-2026-03-13` header - on Claude Fable 5.1 confirm at launch), compaction (beta, `compact-2026-01-12` header), the memory tool, tool-call clearing via context editing, and high-resolution vision (no downscaling cap, as on Opus 4.7+).

### Behavioral shifts (prompt-tunable)

None of these are API-breaking, but they're where migrated workloads feel different. Claude Fable 5.1's biggest gains are on work *above* what prior models could do (long-horizon autonomous runs, first-shot implementations of well-specified systems, end-to-end enterprise deliverables - financial analysis, spreadsheets, slides, docs - code review/debugging and repository-history search, vision on dense or degraded images - it's explicitly trained to use bash and crop tools on flipped/blurry/noisy inputs - navigating ambiguity, parallel sub-agent delegation and collaboration - it reliably sustains ongoing communications with long-running sub-agents and peer agents; note bug-finding gains exclude security-focused analysis, where the cyber classifiers apply) - don't evaluate it only on workloads older models already handled.

**Longer turns by default - the biggest structural shift.** Individual requests on hard tasks can run many minutes at higher effort (a 15-minute single request is normal when the task involves gathering context, building, and self-verifying). Before migrating, plan timeouts, streaming, and user-facing progress indicators; structure work so callers check in on runs asynchronously rather than blocking inside one request. On ambiguous tasks Claude Fable 5.1 may need a small nudge to avoid overplanning:

> When you have enough information to act, act. Do not re-derive facts already established in the conversation, re-litigate a decision the user has already made, or narrate options you will not pursue in user-facing messages. If you are weighing a choice, give a recommendation, not an exhaustive survey. This does not apply to thinking blocks.

**Consider all effort levels.** `output_config.effort` is the primary intelligence/latency/cost control. Recommended defaults: `high` for most tasks, `xhigh` for the most capability-sensitive workloads, `medium`/`low` for routine work. Lower effort settings - including `low` - still perform very well on Claude Fable 5.1, often exceeding the `xhigh` or even `max` performance of previous models. Reduce effort if a task completes correctly but takes longer than necessary, or for a quicker interactive working style. At higher effort on routine work, Claude Fable 5.1 can gather context and deliberate beyond what the task needs (the flip side: higher effort buys excellent verification behavior and the most rigorous outputs). To prevent unrequested tidying or refactoring at higher effort:

> Don't add features, refactor, or introduce abstractions beyond what the task requires. A bug fix doesn't need surrounding cleanup and a one-shot operation usually doesn't need a helper. Don't design for hypothetical future requirements - do the simplest thing that works well. Avoid premature abstraction. Avoid half-finished implementations either. Don't add error handling, fallbacks, or validation for scenarios that cannot happen. Trust internal code and framework guarantees. Only validate at system boundaries (user input, external APIs). Don't use feature flags or backwards-compatibility shims when you can just change the code.

**Instruction following is strong - use it.** Claude Fable 5.1 is very responsive to explicit communication-style sections in system prompts; invest in them rather than fighting output style downstream. Un-steered - especially at higher effort - it can elaborate beyond what the task needs: heavily-structured PR descriptions, sections on alternatives that weren't chosen, comments narrating what the next line does. You don't need to enumerate these behaviors by name; a brief instruction is just as effective:

> Lead with the outcome. Your first sentence after finishing should answer "what happened" or "what did you find" - the thing the user would ask for if they said "just give me the TLDR." Supporting detail and reasoning come after. Being readable and being concise are different things, and readability matters more. The way to keep output short is to be selective about what you include (drop details that don't change what the reader would do next), not to compress the writing into fragments, abbreviations, arrow chains like A -> B -> fails, or jargon.

**Ground progress claims on long runs.** Require progress claims to be audited against tool results - in testing this nearly eliminated fabricated status reports on tasks designed to elicit them:

> Before reporting progress, audit each claim against a tool result from this session. Only report work you can point to evidence for; if something is not yet verified, say so explicitly. Report outcomes faithfully: if tests fail, say so with the output; if a step was skipped, say that; when something is done and verified, state it plainly without hedging.

**State boundaries explicitly.** Claude Fable 5.1 sometimes takes unrequested-but-adjacent actions (e.g. composing an email straight to drafts, creating backup git branches). Define what it should *not* do:

> When the user is describing a problem, asking a question, or thinking out loud rather than requesting a change, the deliverable is your assessment. Report your findings and stop. Don't apply a fix until they ask for one. Before running a command that changes system state - restarts, deletes, config edits - check that the evidence actually supports that specific action. A signal that pattern-matches to a known failure may have a different cause.

**Let it delegate - asynchronously.** Parallel sub-agents are dependable on Claude Fable 5.1 - instead of suppressing delegation (a common prior-model guardrail), use sub-agents frequently and give explicit guidance on *when* delegation is desirable. Sub-agents that communicate **asynchronously** with the orchestrator outperform spawn-and-block: long-lived agents keep their context instead of re-establishing it per subtask (cache-read savings), the orchestrator isn't bottlenecked on the slowest sub-agent, and context persists across subtasks.

> Delegate independent subtasks to sub-agents and keep working while they run. Intervene if a sub-agent goes off track or is missing relevant context.

**Give it a memory surface.** Claude Fable 5.1 performs notably better when it can write learnings somewhere for future reference - even a plain `.md` file. Tell it where, tell it to consult that file in future sessions, and give it a format:

> Store one lesson per file with a one-line summary at the top. Record corrections and confirmed approaches alike, including why they mattered. Don't save what the repo or chat history already records; update an existing note rather than creating a duplicate; delete notes that turn out to be wrong.

**Rare: early stopping.** Deep into long sessions it can occasionally end a turn with a text-only statement of intent ("I'll now run X") without the tool call, or ask permission it doesn't need. A "continue" recovers it interactively; for autonomous pipelines add a system reminder:

> You are operating autonomously. The user is not watching in real time and cannot answer questions mid-task, so asking 'Want me to...?' or 'Shall I...?' will block the work. For reversible actions that follow from the original request, proceed without asking. Offering follow-ups after the task is done is fine; asking permission after already discussing with the user before doing the work is not. Before ending your turn, check your last paragraph. If it is a plan, an analysis, a question, a list of next steps, or a promise about work you have not done ('I'll...', 'let me know when...'), do that work now with tool calls. End your turn only when the task is complete or you are blocked on input only the user can provide.

**Rare: context anxiety.** In very long sessions it can worry about running out of context - suggesting a new session or trimming its own work - most often when the harness surfaces a remaining-token countdown. Avoid showing explicit context-budget counts; if you must:

> You have ample context remaining. Do not stop, summarize, or suggest a new session on account of context limits - continue the work.

**Give the reason, not just the request.** Claude Fable 5.1 performs better when it understands the intent behind a request - it connects the task to relevant information rather than inferring intent on its own. This matters most for long-running agents juggling context from disparate workstreams:

> I'm working on [the larger task] for [who it's for]. They need [what the output enables]. With that in mind: [request].

**Readability in long agentic sessions.** Deep into extended conversations (many tool calls, large working context) Claude Fable 5.1 can produce text users find hard to follow - dense arrow-chain shorthand, implementation-level detail, references to thinking the user never saw. A communication-style addendum strongly mitigates this; adapt:

> Terse shorthand is fine between tool calls (that's you thinking out loud, and brevity there is good). Your final summary is different: it's for a reader who didn't see any of that. If you've been working for a while without the user watching - overnight, across many tool calls, since they last spoke - your final message is their first look at any of it. Write it as a re-grounding, not a continuation of your working thread: the outcome first, then the one or two things you need from them, each explained as if new. The vocabulary you built up while working is yours, not theirs; leave it behind unless you re-introduce it. When you write the summary at the end, drop the working shorthand. Write complete sentences. Spell out terms instead of abbreviating them. Don't use arrow chains, hyphen-stacked compounds, or labels you made up earlier - the reader doesn't have the context to decode them. When you mention files, commits, flags, or other identifiers, give each one its own plain-language clause saying what it is or what changed - never pack several into one parenthesized run or slash-separated list. Open with the outcome: one sentence on what happened or what you found. Then the supporting detail. If you have to choose between short and clear, choose clear.

### Long-running agent recommendations

- **Make self-verification explicit.** For long-running builds, instruct it to establish and run its own checking harness on a cadence ("Establish a method for checking your own work as you build; run it every [interval], verifying against the specification with sub-agents"). Separate fresh-context verifier sub-agents tend to outperform self-critique.
- **De-prescribe migrated prompts and skills.** Prompts and skills written for prior models are often too prescriptive for Claude Fable 5.1 and *reduce* output quality. After migrating, A/B the workload with older step-by-step scaffolding removed - prefer stating the goal and constraints over enumerating the steps. Claude Fable 5.1 is also good at updating skills on the fly from what it learns mid-task - let it.
- **Start at the top of your difficulty range.** The teams with the best outcomes gave it their hardest unsolved problems first - have it scope the problem, ask questions, then execute.
- **Add a `send_to_user` tool for verbatim mid-task delivery.** When an asynchronous agent must deliver something the user sees *exactly as written* mid-run (a deliverable, a progress update with specific numbers, a direct answer), give it a client-side tool whose input you render directly in the UI - tool inputs are never summarized, so content arrives intact. Return a simple acknowledgement as the tool result:

```json
{
  "name": "send_to_user",
  "description": "Display a message directly to the user. Use this for progress updates, partial results, or content the user must see exactly as written before the task finishes.",
  "input_schema": {
    "type": "object",
    "properties": {
      "message": { "type": "string", "description": "The content to display to the user." }
    },
    "required": ["message"]
  }
}
```

For agents that only narrate routine progress, the model's default progress narration is typically adequate without this tool.

### Claude Fable 5.1 Migration Checklist

- [ ] **[BLOCKS]** Also apply the Claude Fable 5.1 from Claude Fable 5 Migration Checklist below - it carries the three breaking changes introduced after Claude Fable 5 (forced `tool_choice` 400s, model-bound thinking blocks, the history-editing check), which this checklist predates
- [ ] **[BLOCKS]** Update the `model=` string to `claude-fable-5-1` (`claude-mythos-5-1` for Mythos Preview migrators in Project Glasswing)
- [ ] **[BLOCKS]** Remove `thinking: {type: "disabled"}` (errors on Claude Fable 5.1)
- [ ] **[BLOCKS]** Replace assistant prefill with structured outputs or system prompt instructions
- [ ] **[BLOCKS]** Confirm the org meets the 30-day data-retention requirement (ZDR orgs get `400 invalid_request_error` on every request; ZDR only if expressly authorized by Anthropic, or enable 30-day retention for one workspace)
- [ ] **[BLOCKS]** Remove all other `thinking` configuration (`{type: "enabled", budget_tokens: N}` returns a 400, same as on Opus 4.7/4.8); control depth with `output_config.effort` instead
- [ ] **[BLOCKS]** If thinking content is surfaced to users or stored in logs: add `thinking: {type: "adaptive", display: "summarized"}` (the default is `"omitted"` - otherwise the rendered text is empty)
- [ ] **[TUNE]** Re-baseline cost and latency on your own workloads - token counts are roughly unchanged from Opus 4.7/4.8 and Mythos Preview (same tokenizer); per-token pricing differs. Coming from Opus 4.6, Sonnet, Haiku, or older, token counts differ - use `count_tokens` with each model to compare
- [ ] **[TUNE]** Add `stop_reason == "refusal"` handling before reading `response.content` (pre-output: empty, billing per [How refusals are billed](https://platform.claude.com/docs/en/build-with-claude/refusals-and-fallback#how-refusals-are-billed); mid-stream: billed at normal rates - discard the partial); opt into a fallback by default - server-side `fallbacks` (Claude API and Claude Platform on AWS: `fallbacks: "default"` with `server-side-fallback-2026-07-01`, or the array form with `server-side-fallback-2026-06-01`) where available, otherwise the SDK middleware or fallback credit (`fallback-credit-2026-07-01`, exact body); a bare client-side replay (history as-is; models other than Claude Fable 5.1 / Claude Mythos 5.1 drop Fable's thinking blocks) is the floor, not the recommendation
- [ ] **[TUNE]** If you surfaced thinking text to users, plan for the thinking output change - the raw chain of thought is never returned; render the `display: "summarized"` summary (per the [BLOCKS] item above); pass blocks back unchanged on the same model; other models drop them from the prompt (unbilled; Claude Mythos 5.1 instead reads them)
- [ ] **[TUNE]** Plan for minutes-long turns: timeouts, streaming, async check-ins, progress UX (see Behavior changes above)
- [ ] **[TUNE]** Run an effort sweep including low/medium for routine workloads; add the no-tidying instruction if higher effort produces unrequested refactors
- [ ] **[TUNE]** A/B with prior-model scaffolding removed - over-prescriptive prompts/skills reduce Claude Fable 5.1 output quality

---

