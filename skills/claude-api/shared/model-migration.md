# Model Migration Guide

> **If you arrived via `/claude-api migrate`:** this is the right file. Execute the steps below in order - do not summarize them back to the user. Start with Step 0 (confirm scope) before touching any file.

How to move existing code to newer Claude models. Covers breaking changes, deprecated parameters, and drop-in replacements for retired models.

For the latest, authoritative version (with code samples in every supported language), WebFetch the **Migration Guide** URL from `shared/live-sources.md`. Use this file for the consolidated, skill-resident reference; fall back to the live docs whenever a model launch or breaking change may have shifted the picture.

**This file is large.** Use the section names below to jump (or `Grep` this file for the heading text). Read Step 0 and Step 1 first - they apply to every migration. Then read only the per-target section for the model you are migrating to.

| Section | When you need it |
|---|---|
| Step 0: Confirm the migration scope | Always - before any edits |
| Step 1: Classify each file | Always - decides whether to swap, add-alongside, or skip |
| Per-SDK Syntax Reference | Translate the Python examples in this guide to TypeScript / Go / Ruby / Java / C# / PHP |
| Destination Models / Retired Model Replacements | Picking a target model |
| Breaking Changes by Source Model | Migrating to Opus 4.6 / Sonnet 4.6 |
| Beta Headers to Remove on 4.6 | Removing betas that are GA on 4.6 |
| Model-ID Rename Quick Reference | Every old -> new model ID string in one table |
| Per-Target Migration Guides | **Everything model-specific lives in separate files** - see the table in that section and read only the file(s) you need |
| Verify the Migration | After edits - runtime spot-check |
| Ground the migration with an eval | User reports a behavioral regression on the new model |

**TL;DR:** Change the model ID string. If you were using `budget_tokens`, switch to `thinking: {type: "adaptive"}`. If you were using assistant prefills, they 400 on both Opus 4.6 and Sonnet 4.6 - switch to one of the prefill replacements (most often `output_config.format`; see the table in Breaking Changes by Source Model). If you're moving from Sonnet 4.5 to Sonnet 4.6, set `effort` explicitly - 4.6 defaults to `high`. Remove the `effort-2025-11-24` and `fine-grained-tool-streaming-2025-05-14` beta headers (GA on 4.6); remove `interleaved-thinking-2025-05-14` once you're on adaptive thinking (keep it only while using the transitional `budget_tokens` escape hatch). Then drop back from `client.beta.messages.create` to `client.messages.create`. Dial back any aggressive "CRITICAL: YOU MUST" tool instructions; 4.6 follows the system prompt much more closely.

---

## Step 0: Confirm the migration scope

**Before any Write, Edit, or MultiEdit call, confirm the scope.** If the user's request does not explicitly name a single file, a specific directory, or an explicit file list, **ask first - do not start editing**. This is non-negotiable: even imperative-sounding requests like "migrate my codebase", "move my project to X", "upgrade to Sonnet 4.6", or bare "migrate to Opus 4.7" leave the scope ambiguous and require a clarifying question. Phrases like "my project", "my code", "my codebase", "the whole thing", "everywhere", or "across the repo" are **ambiguous, not directive** - they tell you *what* to do but not *where*. Ask before doing.

Offer the common scopes explicitly and wait for the answer before touching any file:

1. The entire working directory
2. A specific subdirectory (e.g. `src/`, `app/`, `services/billing/`)
3. A specific file or a list of files

Surface this as a single clarifying question so the user can answer in one turn. **Proceed without asking only when the scope is already unambiguous** - the user named an exact file ("migrate `extract.py` to Sonnet 4.6"), pointed at a specific directory ("migrate everything under `services/billing/` to Opus 4.6"), listed specific files ("update `a.py` and `b.py`"), or already answered the scope question in an earlier turn. If you can answer the question "which files is this change going to touch?" with a precise list from the prompt alone, proceed. If not, ask.

**Worked example.** If the user says *"Move my project to Opus 4.6. I want adaptive thinking everywhere it makes sense."* you do not know whether "my project" means the whole working directory, just `src/`, just the production code, or something else - the `everywhere` makes the intent clear (update every call site *within scope*) but the scope itself is still not defined. Do not start editing. Respond with:

> Before I start editing, can you confirm the scope? I can migrate:
> 1. Every `.py` file in the working directory
> 2. Just the files under `src/` (production code)
> 3. A specific subdirectory or list of files you name
>
> Which one?

Then wait for the answer. The same applies to *"Migrate to Opus 4.7"* and bare *"Help me upgrade to Sonnet 4.6"* - ask before editing.

**Sizing the scope question (large repos).** Before asking, get a per-directory count so the user can pick concretely:

```sh
rg -l "<old-model-id>" --type-not md | cut -d/ -f1 | sort | uniq -c | sort -rn
```

Present the breakdown in your scope question (e.g. *"Found 217 references across 3 directories: api/ (130), api-go/ (62), routing/ (25). Which to migrate?"*). Also confirm `git status` is clean before surveying - unexpected modifications mean a concurrent process; stop and investigate before proceeding.

---

## Step 1: Classify each file

Not every file that contains the old model ID is a **caller** of the API. Before editing, classify each file into one of these buckets - the right action differs:

| # | Bucket | What it looks like | Action |
|---|---|---|---|
| 1 | **Calls the API/SDK** | `client.messages.create(model=...)`, `anthropic.Anthropic()`, request payloads | Swap the model ID **and** apply the breaking-change checklist for the target version (below). |
| 2 | **Defines or serves the model** | Model registries, OpenAPI specs, routing/queue configs, model-policy enums, generated catalogs | The old entry **stays** (the model is still served). Ask whether to (a) add the new model alongside, (b) leave alone, or (c) retire the old model - never blind-replace. **If you can't ask, default to (a): add the new model alongside and flag it** - replacing would de-register a model that's still in production. |
| 3 | **References the ID as an opaque string** | UI fallback constants, capability-gate substring checks, generic test fixtures, label parsers, env defaults | Usually swap the string and verify any parser/regex/substring match handles the new ID - but check the sub-cases below first. |
| 4 | **Suffixed variant ID** | `claude-<model>-<suffix>` like `-fast`, `-1024k`, `-200k`, `[1m]`, dated snapshots | These are deployment/routing identifiers, not the public model ID. **Do not assume a new-model equivalent exists.** Verify in the registry first; if absent, leave the string alone and flag it. **Exception: `-fast` strings (e.g. `claude-opus-4-6-fast`) are handled by the Fast Mode section below**, which rewrites them to Claude Opus 5.5 plus `speed="fast"` and the `fast-mode-2026-02-01` beta rather than leaving them in place. |

**Bucket 3 sub-cases - before swapping a string reference, check:**

- **Capability gate** (e.g. `if 'opus-4-6' in model_id:` enables a feature) -> **add the new ID alongside**, don't replace. The old model is still served and still has the capability, so replacing would silently disable the feature for any old-model traffic that still flows through. If you know no old-model traffic will hit this gate (single-caller codebase fully migrating), replacing is fine; if unsure, add alongside.
- **Registry-assert test** (e.g. `assert "claude-X" in supported_models`, `test_X_has_N_clusters`) -> **add an assertion for the new model alongside; keep the old one.** The old model is still served, so its assertion stays valid - but the registry should also include the new model, so assert that too. Heuristic: if the test references multiple model versions in a list, it's a registry test; if one model in a struct compared only to itself, it's a generic fixture.
- **Frozen / generated snapshot** -> **regenerate**, don't hand-edit.
- **Coupled to a definer** (e.g. an integration test that passes model authorization via a shared `conftest` seed list, or asserts on a billing-tier / rate-limit-group enum or a generated SKU/pricing catalog) -> **verify the definer has a new-model entry first.** If not, add a seed entry (reusing the nearest existing tier as a placeholder); if you can't confidently do that, ask the user how to populate the definer. **Do not skip the test.** Swapping without populating the definer will make the test fail at runtime.

When migrating tests specifically: breaking parameters (`temperature`, `top_p`, `budget_tokens`) are usually absent - test fixtures rarely set sampling params on placeholder models. The breaking-change scan is still required, but expect mostly clean results.

**Find intentionally-flagged sync points first.** Many codebases tag spots that must change at every model launch with comment markers like `MODEL LAUNCH`, `KEEP IN SYNC`, `@model-update`, or similar. Grep for whatever convention the repo uses *before* the broad model-ID grep - those markers point at the load-bearing changes.

---

## Per-SDK Syntax Reference

Code examples in this guide are Python. **The same fields exist in every official Anthropic SDK** - Stainless generates all 7 from the same OpenAPI spec, so JSON field names map 1:1 with only case-convention differences. Use the rows below to translate the Python examples to the SDK you are migrating.

> **Verify type and method names against the SDK source before writing them into customer code.** WebFetch the relevant repository from the SDK source-code table in `shared/live-sources.md` (one row per SDK) and confirm the exact symbol - particularly for typed SDKs (Go, Java, C#) where union/builder names can differ from the JSON shape. Do not guess type names that aren't in the table below or in `<lang>/claude-api/README.md`.


### `thinking` - `budget_tokens` -> adaptive

| SDK | Before | After |
|---|---|---|
| Python | `thinking={"type": "enabled", "budget_tokens": N}` | `thinking={"type": "adaptive"}` |
| TypeScript | `thinking: { type: 'enabled', budget_tokens: N }` | `thinking: { type: 'adaptive' }` |
| Go | `Thinking: anthropic.ThinkingConfigParamOfEnabled(N)` | `Thinking: anthropic.ThinkingConfigParamUnion{OfAdaptive: &anthropic.ThinkingConfigAdaptiveParam{}}` |
| Ruby | `thinking: { type: "enabled", budget_tokens: N }` | `thinking: { type: "adaptive" }` |
| Java | `.thinking(ThinkingConfigEnabled.builder().budgetTokens(N).build())` | `.thinking(ThinkingConfigAdaptive.builder().build())` |
| C# | `Thinking = new ThinkingConfigEnabled { BudgetTokens = N }` | `Thinking = new ThinkingConfigAdaptive()` |
| PHP | `thinking: ['type' => 'enabled', 'budget_tokens' => N]` | `thinking: ['type' => 'adaptive']` |

### Sampling parameters - `temperature` / `top_p` / `top_k`

(Remove the field entirely on Opus 4.7; on Claude 4.x keep at most one of `temperature` or `top_p`.)

| SDK | Field(s) to remove |
|---|---|
| Python | `temperature=...`, `top_p=...`, `top_k=...` |
| TypeScript | `temperature: ...`, `top_p: ...`, `top_k: ...` |
| Go | `Temperature: anthropic.Float(...)`, `TopP: anthropic.Float(...)`, `TopK: anthropic.Int(...)` |
| Ruby | `temperature: ...`, `top_p: ...`, `top_k: ...` |
| Java | `.temperature(...)`, `.topP(...)`, `.topK(...)` |
| C# | `Temperature = ...`, `TopP = ...`, `TopK = ...` |
| PHP | `temperature: ...`, `topP: ...`, `topK: ...` |

### Prefill replacement - structured outputs via `output_config.format`

| SDK | Remove (last assistant turn) | Add |
|---|---|---|
| Python | `{"role": "assistant", "content": "..."}` | `output_config={"format": {"type": "json_schema", "schema": SCHEMA}}` |
| TypeScript | `{ role: 'assistant', content: '...' }` | `output_config: { format: { type: 'json_schema', schema: SCHEMA } }` |
| Go | trailing `anthropic.MessageParam{Role: "assistant", ...}` | `OutputConfig: anthropic.OutputConfigParam{Format: anthropic.JSONOutputFormatParam{...}}` |
| Ruby | `{ role: "assistant", content: "..." }` | `output_config: { format: { type: "json_schema", schema: SCHEMA } }` |
| Java | trailing `Message.builder().role(ASSISTANT)...` | `.outputConfig(OutputConfig.builder().format(JsonOutputFormat.builder()...build()).build())` |
| C# | trailing `new Message { Role = "assistant", ... }` | `OutputConfig = new OutputConfig { Format = new JsonOutputFormat { ... } }` |
| PHP | trailing `['role' => 'assistant', 'content' => '...']` | `outputConfig: ['format' => ['type' => 'json_schema', 'schema' => $SCHEMA]]` |

### `thinking.display` - opt back into summarized reasoning (Opus 4.7)

| SDK | Add |
|---|---|
| Python | `thinking={"type": "adaptive", "display": "summarized"}` |
| TypeScript | `thinking: { type: 'adaptive', display: 'summarized' }` |
| Go | `Thinking: anthropic.ThinkingConfigParamUnion{OfAdaptive: &anthropic.ThinkingConfigAdaptiveParam{Display: anthropic.ThinkingConfigAdaptiveDisplaySummarized}}` |
| Ruby | `thinking: { type: "adaptive", display: "summarized" }` (or `display_:` when constructing the model class directly) |
| Java | `.thinking(ThinkingConfigAdaptive.builder().display(ThinkingConfigAdaptive.Display.SUMMARIZED).build())` |
| C# | `Thinking = new ThinkingConfigAdaptive { Display = Display.Summarized }` |
| PHP | `thinking: ['type' => 'adaptive', 'display' => 'summarized']` |

For any field not in these tables, the JSON key in the Python example translates directly: `snake_case` for Python/TypeScript/Ruby, `camelCase` named args for PHP, `PascalCase` struct fields for Go/C#, `camelCase` builder methods for Java.

---

## Explain every change you make

Migration edits often look arbitrary to a user who hasn't read the release notes - a removed `temperature`, a deleted prefill, a rewritten system-prompt sentence. **For each edit, tell the user what you changed and why**, tied to the specific API or behavioral change that motivates it. Do this in your summary as you work, not just at the end.

Be especially explicit about **system-prompt edits**. Users are rightly protective of their prompts, and prompt-tuning changes are judgment calls (not hard API requirements). For any prompt edit:

- Quote the before and after text.
- State the behavioral shift that motivates it (e.g. *"Opus 4.7 calibrates response length to task complexity, so I added an explicit length instruction"*, or *"4.6 follows instructions more literally, so 'CRITICAL: YOU MUST use the search tool' will now overtrigger - softened to 'Use the search tool when...'"*).
- Make clear which prompt edits are **optional tuning** (tone, length, subagent guidance) versus which code edits are **required to avoid a 400** (sampling params, `budget_tokens`, prefills). Never present an optional prompt change as mandatory.

If you're applying several prompt-tuning edits at once, offer them as a short list the user can accept or decline item-by-item rather than silently rewriting their system prompt.

---

## Before You Migrate

1. **Confirm the target model ID.** Use only the exact strings from `shared/models.md` - do not append date suffixes to aliases (`claude-opus-4-6`, not `claude-opus-4-6-20251101`). Guessing an ID will 404.
2. **Check which features your code uses** with this checklist:
 - `thinking: {type: "enabled", budget_tokens: N}` -> migrate to adaptive thinking on Opus 4.6 / Sonnet 4.6 (still functional but deprecated)
 - Assistant-turn prefills (`messages` ending with `role: "assistant"`) -> must change on Opus 4.6 / Sonnet 4.6 (returns 400)
 - `output_format` parameter on `messages.create()` -> must change on all models (deprecated API-wide)
 - `max_tokens > ~16000` -> must stream on any model (above ~16K risks SDK HTTP timeouts). When streaming, every current model reaches 128K except Haiku 4.5, which caps at 64K
 - Beta headers `effort-2025-11-24`, `fine-grained-tool-streaming-2025-05-14`, `interleaved-thinking-2025-05-14` -> GA on 4.6, remove them and switch from `client.beta.messages.create` to `client.messages.create`
 - Moving Sonnet 4.5 -> Sonnet 4.6 with no `effort` set -> 4.6 defaults to `high`, which may change your latency/cost profile
 - System prompts with `CRITICAL`, `MUST`, `If in doubt, use X` language -> likely to overtrigger on 4.6 (see Prompt-Behavior Changes)
 - Coming from 3.x / 4.0 / 4.1: also check sampling params (`temperature` + `top_p`), tool versions (`text_editor_20250728`), `refusal` + `model_context_window_exceeded` stop reasons, trailing-newline tool-param handling
3. **Test on a single request first.** Run one call against the new model, inspect the response, then roll out.

---

## Destination Models (recommended targets)

| If you're on...                         | Migrate to         | Why                                               |
| ------------------------------------- | ------------------ | ------------------------------------------------- |
| Claude Mythos Preview (`claude-mythos-preview`) | `claude-mythos-5-1` (Project Glasswing successor) or `claude-fable-5-1` (GA) | Same tokenizer family - mostly a model-ID swap; remove `thinking` config and prefill; see Migrating to Claude Fable 5.1 |
| Claude Fable 5 (`claude-fable-5`) | `claude-fable-5-1` | Same tier, same per-token price, same tokenizer; three breaking changes (forced `tool_choice` 400s, "preserved thinking") - see Migrating to Claude Fable 5.1 from Claude Fable 5 |
| Claude Mythos 5 (`claude-mythos-5`) | `claude-mythos-5-1` | Same path as claude-fable-5 -> claude-fable-5-1; see § Claude Mythos 5.1 under Migrating to Claude Fable 5.1 from Claude Fable 5 |
| Claude Opus 5 (`claude-opus-5`)         | `claude-opus-5-5` | The current Opus. Lower price ($4 / $20 vs $5 / $25), same context window and tokenizer; four breaking changes (thinking can't be disabled, forced `tool_choice` 400s, preserved thinking, computer use via the toolset on the Claude API and Google Cloud) - see Migrating to Claude Opus 5.5 |
| Opus 4.8                              | `claude-opus-5-5` | Apply the Claude Opus 5 section (thinking on by default, prompt re-tuning), then the Claude Opus 5.5 section, which replaces Claude Opus 5's thinking-disabled route and `computer_20251124` |
| Opus 4.7                              | `claude-opus-5-5` | Apply the Opus 4.8 section (prompt re-tuning, no new breaking changes), then the Claude Opus 5 and Claude Opus 5.5 sections |
| Opus 4.6                              | `claude-opus-5-5` | Apply the Opus 4.7 breaking changes, then 4.8 re-tuning, then the Claude Opus 5 and Claude Opus 5.5 sections |
| Opus 4.0 / 4.1 / 4.5 / Opus 3         | `claude-opus-5-5` | Apply 4.6 -> 4.7 -> 4.8 -> Claude Opus 5 -> Claude Opus 5.5 in order (adaptive thinking, drop sampling params, then re-tune) |
| Claude Sonnet 5 (`claude-sonnet-5`)     | `claude-sonnet-5-5` | The current Sonnet. Same prices and tokenizer; five breaking changes (`disabled` thinking 400s - use `between_tools`, forced `tool_choice` 400s, preserved thinking, computer use via the toolset on the Claude API and Google Cloud, fewer advisor pairings) - see Migrating to Claude Sonnet 5.5 |
| Sonnet 4.6                            | `claude-sonnet-5-5` | Apply the Claude Sonnet 5 section (adaptive thinking on by default, new tokenizer), then the Claude Sonnet 5.5 section, which replaces its thinking-disabled route, forced `tool_choice` on Bedrock, `computer_20251124`, and effort advice |
| Sonnet 4.0 / 4.5 / 3.7 / 3.5          | `claude-sonnet-5-5` | Apply the Sonnet 4.6 changes first, then the Claude Sonnet 5 and Claude Sonnet 5.5 sections |
| Claude Haiku 4.5 (`claude-haiku-4-5`) | `claude-haiku-5-5` | The current Haiku. 1M context and 128K output, adaptive thinking with `effort`; five breaking changes (`budget_tokens` 400s, non-default sampling parameters 400, prefill 400, computer use via the toolset on the Claude API and Google Cloud, preserved thinking's history-editing check) and a newer tokenizer - see Migrating to Claude Haiku 5.5 |
| Haiku 3 / 3.5                         | `claude-haiku-5-5` | Apply the 3.x changes (Additional Changes When Coming from 3.x / 4.0 / 4.1 -> 4.6), then the Claude Haiku 5.5 section |

Default to the latest Opus (Claude Opus 5.5) for the caller's tier unless they explicitly chose otherwise. The Sonnet target is Claude Sonnet 5.5 (`claude-sonnet-5-5`), and the Haiku target is Claude Haiku 5.5 (`claude-haiku-5-5`). The Opus migrations layer: if you're on an older Opus, apply each version's section in order up to your target (e.g. 4.5 -> 4.8 means the 4.6, 4.7, and 4.8 sections in sequence). A 4.7 -> 4.8 move has no new breaking changes - see Migrating to Opus 4.8 below.

---

## Retired Model Replacements

These models return 404 - update immediately:

| Retired model                 | Retired       | Drop-in replacement  |
| ----------------------------- | ------------- | -------------------- |
| `claude-3-7-sonnet-20250219`  | Feb 19, 2026  | `claude-sonnet-5-5` |
| `claude-3-5-haiku-20241022`   | Feb 19, 2026  | `claude-haiku-5-5`   |
| `claude-3-opus-20240229`      | Jan 5, 2026   | `claude-opus-4-8`    |
| `claude-3-5-sonnet-20241022`  | Oct 28, 2025  | `claude-sonnet-5-5` |
| `claude-3-5-sonnet-20240620`  | Oct 28, 2025  | `claude-sonnet-5-5` |
| `claude-3-sonnet-20240229`    | Jul 21, 2025  | `claude-sonnet-5-5` |
| `claude-2.1`, `claude-2.0`    | Jul 21, 2025  | `claude-sonnet-5-5` |

## Deprecated Models (retiring soon)

| Model                         | Retires       | Replacement          |
| ----------------------------- | ------------- | -------------------- |
| `claude-3-haiku-20240307`     | Apr 19, 2026  | `claude-haiku-5-5`   |
| `claude-opus-4-20250514`      | June 15, 2026 | `claude-opus-4-8`    |
| `claude-sonnet-4-20250514`    | June 15, 2026 | `claude-sonnet-5-5` |

---

## Breaking Changes by Source Model

### Migrating from Sonnet 4.5 to Sonnet 4.6 (effort default change)

Sonnet 4.5 had no `effort` parameter; Sonnet 4.6 defaults to `high`. If you just switch the model string and do nothing else, you may see noticeably higher latency and token usage. Set `effort` explicitly.

**Recommended starting points:**

| Workload                                          | Start at       | Notes                                                                                                    |
| ------------------------------------------------- | -------------- | -------------------------------------------------------------------------------------------------------- |
| Chat, classification, content generation          | `low`          | With `thinking: {"type": "disabled"}` you'll see similar or better performance vs. Sonnet 4.5 no-thinking |
| Most applications (balanced)                      | `medium`       | The default sweet spot for quality vs. cost                                                              |
| Agentic coding, tool-heavy workflows              | `medium`       | Pair with adaptive thinking and a generous `max_tokens` (up to 128K with streaming - Sonnet 4.6's ceiling) |
| Autonomous multi-step agents, long-horizon loops  | `high`         | Scale down to `medium` if latency/tokens become a concern                                                 |
| Computer-use agents                               | `high` + adaptive | Sonnet 4.6's best computer-use accuracy is on adaptive + high                                          |

For non-thinking chat workloads specifically:

```python
client.messages.create(
    model="claude-sonnet-4-6",
    max_tokens=8192,
    thinking={"type": "disabled"},
    output_config={"effort": "low"},
    messages=[{"role": "user", "content": "..."}],
)
```

**When to use Opus 4.6 instead:** hardest and longest-horizon problems - large code migrations, deep research, extended autonomous work. Sonnet 4.6 wins on fast turnaround and cost efficiency.

### Migrating to Opus 4.6 / Sonnet 4.6 (from any older model)

**1. Manual extended thinking is deprecated - use adaptive thinking.**

`thinking: {type: "enabled", budget_tokens: N}` (manual extended thinking with a fixed token budget) is deprecated on Opus 4.6 and Sonnet 4.6. Replace it with `thinking: {type: "adaptive"}`, which lets Claude decide when and how much to think. Adaptive thinking also enables interleaved thinking automatically (no beta header needed).

```python
# Old (still works on older models, deprecated on 4.6)
response = client.messages.create(
    model="claude-sonnet-4-5",
    max_tokens=16000,
    thinking={"type": "enabled", "budget_tokens": 8000},
    messages=[...]
)

# New (Opus 4.6 / Sonnet 4.6)
response = client.messages.create(
    model="claude-opus-4-6",  # or "claude-sonnet-4-6"
    max_tokens=16000,
    thinking={"type": "adaptive"},
    output_config={"effort": "high"},  # optional: low | medium | high | max
    messages=[...]
)
```

Adaptive thinking is the long-term target, and on internal evaluations it outperforms manual extended thinking. Move when you can.

**Transitional escape hatch:** manual extended thinking is still *functional* on Opus 4.6 and Sonnet 4.6 (deprecated, will be removed in a future release). If you need a hard ceiling while migrating - for example, to bound token spend on a runaway workload before you've tuned `effort` - you can keep `budget_tokens` around alongside an explicit `effort` value, then remove it in a follow-up. `budget_tokens` must be strictly less than `max_tokens`:

```python
# Transitional only - deprecated, plan to remove
client.messages.create(
    model="claude-sonnet-4-6",
    max_tokens=16384,
    thinking={"type": "enabled", "budget_tokens": 8192},  # must be < max_tokens
    output_config={"effort": "medium"},
    messages=[...],
)
```

If the user asks for a "thinking budget" on 4.6, the preferred answer is `effort` - use `low`, `medium`, `high`, or `max` rather than a token count.

**2. Effort parameter (Opus 4.5, Opus 4.6, Sonnet 4.6 only).**

Controls thinking depth and overall token spend. Goes inside `output_config`, not top-level. Default is `high`. `max` is supported on Fable 5, Opus 4.6 and later, Sonnet 5.5, Sonnet 5, Sonnet 4.6, and Haiku 5.5 - it errors on Sonnet 4.5 and Haiku 4.5.

```python
output_config={"effort": "medium"}  # often the best cost / quality balance
```

### Migrating to the 4.6 family (Opus 4.6 and Sonnet 4.6)

**3. Assistant-turn prefills return 400 (Opus 4.6 and Sonnet 4.6).**

Prefilled responses on the final assistant turn are no longer supported on either Opus 4.6 or Sonnet 4.6 - both return a 400. Adding assistant messages *elsewhere* in the conversation (e.g., for few-shot examples) still works. Pick the replacement that matches what the prefill was doing:

| Prefill was used for                               | Replacement                                                                                                                               |
| -------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------- |
| Forcing JSON / YAML / schema output                | `output_config.format` with a `json_schema` - see example below                                                                           |
| Forcing a classification label                     | Tool with an enum field containing valid labels, or structured outputs                                                                    |
| Skipping preambles (`Here is the summary:\n`)      | System prompt instruction: *"Respond directly without preamble. Do not start with phrases like 'Here is...' or 'Based on...'."*           |
| Steering around bad refusals                       | Usually no longer needed - 4.6 refuses far more appropriately. Plain user-turn prompting is sufficient.                                   |
| Continuing an interrupted response                 | Move continuation into the user turn: *"Your previous response was interrupted and ended with `[last text]`. Continue from there."*     |
| Injecting reminders / context hydration            | Inject into the user turn instead. For complex agent harnesses, expose context via a tool call or during compaction.                      |

```python
# Old (fails on Opus 4.6 / Sonnet 4.6) - prefill forcing JSON shape
messages=[
    {"role": "user", "content": "Extract the name."},
    {"role": "assistant", "content": "{\"name\": \""},
]

# New - structured outputs replace the prefill
response = client.messages.create(
    model="claude-opus-4-6",
    max_tokens=1024,
    output_config={"format": {"type": "json_schema", "schema": {...}}},
    messages=[{"role": "user", "content": "Extract the name."}],
)
```

**4. Stream for `max_tokens > ~16K` (all models); only Haiku 4.5 caps lower, at 64K.**

Non-streaming requests hit SDK HTTP timeouts at high `max_tokens`, regardless of model - stream for anything above ~16K output. The streamable ceiling is 128K for every current model except Haiku 4.5, which caps at 64K.

```python
with client.messages.stream(model="claude-opus-4-6", max_tokens=64000, ...) as stream:
    message = stream.get_final_message()
```

**5. Tool-call JSON escaping may differ (Opus 4.6 and Sonnet 4.6).**

Both 4.6 models can produce tool call `input` fields with Unicode or forward-slash escaping. Always parse with `json.loads()` / `JSON.parse()` - never raw-string-match the serialized input.

### All models

**6. `output_format` -> `output_config.format` (API-wide).**

The old top-level `output_format` parameter on `messages.create()` is deprecated. Use `output_config.format` instead. This is not 4.6-specific - applies to every model.

---

## Beta Headers to Remove on 4.6

Several beta headers that were required on 4.5 are now GA on 4.6 and should be removed. Leaving them in is harmless but misleading; removing them also lets you move from `client.beta.messages.create(...)` back to `client.messages.create(...)`.

| Header                                    | Status on 4.6                                              | Action                                                  |
| ----------------------------------------- | ---------------------------------------------------------- | ------------------------------------------------------- |
| `effort-2025-11-24`                       | Effort parameter is GA                                     | Remove                                                  |
| `fine-grained-tool-streaming-2025-05-14`  | GA                                                         | Remove                                                  |
| `interleaved-thinking-2025-05-14`         | Adaptive thinking enables interleaved thinking automatically | Remove when using adaptive thinking; still functional on Sonnet 4.6 *with* manual extended thinking, but that path is deprecated |
| `token-efficient-tools-2025-02-19`        | Built in to all Claude 4+ models                           | Remove (no effect)                                      |
| `output-128k-2025-02-19`                  | Built in to Claude 4+ models                               | Remove (no effect)                                      |

Once you remove all of these and finish moving to adaptive thinking, you can switch the SDK call site from the beta namespace back to the regular one:

```python
# Before
response = client.beta.messages.create(
    model="claude-opus-4-5",
    betas=["interleaved-thinking-2025-05-14", "effort-2025-11-24"],
    ...
)

# After
response = client.messages.create(
    model="claude-opus-4-6",
    thinking={"type": "adaptive"},
    output_config={"effort": "high"},
    ...
)
```

---

## Additional Changes When Coming from 3.x / 4.0 / 4.1 -> 4.6

If you're jumping from Opus 4.1, Sonnet 4, Sonnet 3.7, or an older Claude 3.x model directly to 4.6, apply everything above *plus* the items in this section. Users already on Opus 4.5 / Sonnet 4.5 can skip this.

**1. Sampling parameters: `temperature` OR `top_p`, not both.**

Passing both will error on every Claude 4+ model:

```python
# Old (3.x only - errors on 4+)
client.messages.create(temperature=0.7, top_p=0.9, ...)

# New
client.messages.create(temperature=0.7, ...)  # or top_p, not both
```

**2. Update tool versions.**

Legacy tool versions are not supported on 4+. **Both the `type` and the `name` field change** - `text_editor_20250728` and `str_replace_based_edit_tool` are a pair; updating one without the other 400s. Also remove the `undo_edit` command from your text-editor integration:

| Old                                               | New                                                     |
| ------------------------------------------------- | ------------------------------------------------------- |
| `text_editor_20250124` + `str_replace_editor`     | `text_editor_20250728` + `str_replace_based_edit_tool`  |
| `code_execution_*` (earlier versions)             | `code_execution_20260521`                               |
| `undo_edit` command                               | *(no longer supported - delete call sites)*             |

```python
# Before
tools = [{"type": "text_editor_20250124", "name": "str_replace_editor"}]

# After - BOTH fields change
tools = [{"type": "text_editor_20250728", "name": "str_replace_based_edit_tool"}]
```

**3. Handle the `refusal` stop reason.**

Claude 4+ can return `stop_reason: "refusal"` on the response. If your code only handles `end_turn` / `tool_use` / `max_tokens`, add a branch:

```python
if response.stop_reason == "refusal":
    # Surface the refusal to the user; do not retry with the same prompt
    ...
```

**4. Handle the `model_context_window_exceeded` stop reason (4.5+).**

Distinct from `max_tokens`: it means the model hit the *context window* limit, not the requested output cap. Handle both:

```python
if response.stop_reason == "model_context_window_exceeded":
    # Context window exhausted - compact or split the conversation
    ...
elif response.stop_reason == "max_tokens":
    # Requested output cap hit - retry with higher max_tokens or stream
    ...
```

**5. Trailing newlines preserved in tool call string parameters (4.5+).**

4.5 and 4.6 preserve trailing newlines that older models stripped. If your tool implementations do exact string matching against tool-call `input` values (e.g., `if name == "foo"`), verify they still match when the model sends `"foo\n"`. Normalizing with `.rstrip()` on the receiving side is usually the simplest fix.

**6. Haiku: rate limits reset between generations.**

Each Haiku generation from 4.5 on has its own rate-limit pool, separate from Haiku 3 / 3.5. If you're ramping traffic as you migrate, check your tier's limits for the target Haiku at [API rate limits](https://platform.claude.com/docs/en/api/rate-limits) - a quota that comfortably served Haiku 3.5 traffic may need a tier bump for the same volume on the new model.

---

## Prompt-Behavior Changes (Opus 4.5 / 4.6, Sonnet 4.6)

These don't break your code, but prompts that worked on 4.5-and-earlier may over- or under-trigger on 4.6. Tune as needed. For a standing, model-general audit of dated prompt text beyond this migration - skills and tool descriptions included - read `shared/prompt-audit.md` (or invoke `/claude-api prompt-audit`).

**1. Aggressive instructions cause overtriggering.** Opus 4.5 and 4.6 follow the system prompt much more closely than earlier models. Prompts written to *overcome* the old reluctance are now too aggressive:

| Before (worked on 4.0 / 4.5)                | After (use on 4.6)                        |
| ------------------------------------------- | ----------------------------------------- |
| `CRITICAL: You MUST use this tool when...`  | `Use this tool when...`                   |
| `Default to using [tool]`                   | `Use [tool] when it would improve X`      |
| `If in doubt, use [tool]`                   | *(delete - no longer needed)*             |

If the model is now overtriggering a tool or skill, the fix is almost always to dial back the language, not to add more guardrails.

**2. Overthinking and excessive exploration (Opus 4.6).** At higher `effort` settings, Opus 4.6 explores more before answering. If that burns too many thinking tokens, lower `effort` first (`medium` is often the sweet spot) before adding prose instructions to constrain reasoning.

**3. Overeager subagent spawning (Opus 4.6).** Opus 4.6 has a strong preference for delegating to subagents. If you see it spawning a subagent for something a direct `grep` or `read` would solve, add guidance: *"Use subagents only for parallel or independent workstreams. For single-file reads or sequential operations, work directly."*

**4. Overengineering (Opus 4.5 / 4.6).** Both models may add extra files, abstractions, or defensive error handling beyond what was asked. If you want minimal changes, prompt for it explicitly: *"Only make changes directly requested. Don't add helpers, abstractions, or error handling for scenarios that can't happen."*

**5. LaTeX math output (Opus 4.6).** Opus 4.6 defaults to LaTeX (`\frac{}{}`, `$...$`) for math and technical content. If you need plain text, instruct it explicitly: *"Format all math as plain text - no LaTeX, no `$`, no `\frac{}{}`. Use `/` for division and `^` for exponents."*

**6. Skipped verbal summaries (4.6 family).** The 4.6 models are more concise and may skip the summary paragraph after a tool call, jumping straight to the next action. If you rely on those summaries for visibility, add: *"After completing a task that involves tool use, provide a brief summary of what you did."*

**7. "Think" as a trigger word (Opus 4.5 with thinking disabled).** When `thinking` is off, Opus 4.5 is particularly sensitive to the word *think* and may reason more than you want. Use `consider`, `evaluate`, or `reason through` instead.

---

## Model-ID Rename Quick Reference

| Old string (migration source)  | New string         |
| ------------------------------ | ------------------ |
| `claude-opus-5`             | `claude-opus-5-5`      |
| `claude-opus-4-8`              | `claude-opus-5-5`      |
| `claude-opus-4-7`              | `claude-opus-5-5`      |
| `claude-opus-4-6`              | `claude-opus-5-5`      |
| `claude-opus-4-5`              | `claude-opus-5-5`      |
| `claude-opus-4-1`              | `claude-opus-5-5`      |
| `claude-opus-4-0`              | `claude-opus-5-5`      |
| `claude-mythos-preview`        | `claude-mythos-5-1` (Project Glasswing) or `claude-fable-5-1` |
| `claude-fable-5`            | `claude-fable-5-1`     |
| `claude-mythos-5`           | `claude-mythos-5-1`    |
| `claude-sonnet-4-6`            | `claude-sonnet-5-5`     |
| `claude-sonnet-4-5`            | `claude-sonnet-5-5`     |
| `claude-sonnet-4-0`            | `claude-sonnet-5-5`     |
| `claude-sonnet-5`                | `claude-sonnet-5-5`     |
| `claude-haiku-4-5` (or `claude-haiku-4-5-20251001`) | `claude-haiku-5-5`      |

Older aliases (`claude-opus-4-7`, `claude-opus-4-6`, `claude-opus-4-5`, `claude-sonnet-4-6`, `claude-sonnet-4-5`, `claude-haiku-4-5`, etc.) are still active and can be pinned if you need time before upgrading - see `shared/models.md` for the full legacy list.

### Amazon Bedrock model IDs

If the code uses the `AnthropicBedrockMantle` client (Python `anthropic[bedrock]`, TypeScript `@anthropic-ai/bedrock-sdk`, Java `BedrockMantleBackend`, Go `bedrock.NewMantleClient`, etc.) or targets `https://bedrock-mantle.{region}.api.aws/anthropic`, it is running on **Claude in Amazon Bedrock**. All breaking changes in this guide apply unchanged there - it serves the same Messages API shape - but model IDs carry an `anthropic.` provider prefix:

| First-party ID | Bedrock ID |
|---|---|
| `claude-opus-4-8` | `anthropic.claude-opus-4-8` |
| `claude-opus-5` | `anthropic.claude-opus-5` |
| `claude-opus-5-5` | `anthropic.claude-opus-5-5` |
| `claude-fable-5-1` | `anthropic.claude-fable-5-1` |
| `claude-fable-5` | `anthropic.claude-fable-5` |
| `claude-mythos-5-1` | `anthropic.claude-mythos-5-1` (us-east-1 only, not publicly listed) |
| `claude-opus-4-7` | `anthropic.claude-opus-4-7` |
| `claude-sonnet-5` | `anthropic.claude-sonnet-5` |
| `claude-sonnet-5-5` | `anthropic.claude-sonnet-5-5` |
| `claude-haiku-5-5` | `anthropic.claude-haiku-5-5` |
| `claude-haiku-4-5` | `anthropic.claude-haiku-4-5` |

When migrating a Bedrock file, apply the same rename-table row as first-party, then keep/add the `anthropic.` prefix. Do **not** generate a first-party `claude-*` ID for a Bedrock client - it will 400.

**Skip for Bedrock:** the `code_execution_*` tool-version checklist item and the **Task Budgets** section - neither is available on Bedrock (see `shared/platform-availability.md` for the per-feature table). Everything else in this guide - `effort`, adaptive/extended thinking, `output_config.format`, `thinking.display`, token counting - is available on Bedrock; fine-grained tool streaming (`eager_input_streaming`) is available on Bedrock's newer serving stack only (see the per-model note in `shared/platform-availability.md`).

> **Out of scope:** the legacy Amazon Bedrock integration (`InvokeModel` / `Converse` APIs with ARN-versioned IDs like `anthropic.claude-3-5-sonnet-20241022-v2:0`) uses a different request shape and model-ID format. This guide does not cover it; WebFetch the Bedrock page in `shared/live-sources.md` if the user is migrating between the two Bedrock integrations.

### Claude Platform on AWS

If the code uses `AnthropicAWS` / `AnthropicAws` / `anthropicaws.NewClient` / `AnthropicAwsClient` (or targets `https://aws-external-anthropic.{region}.api.aws`), it is running on **Claude Platform on AWS** - Anthropic-operated, same-day API parity. Model IDs are **bare first-party** strings; apply the rename table above **verbatim** and every breaking-change section in this guide unchanged. There is nothing to skip. Do **not** add an `anthropic.` prefix (that's Amazon Bedrock, a separate offering). See `shared/claude-platform-on-aws.md` for client/auth details.

---

## Migration Checklist

Every item is tagged: **`[BLOCKS]`** items cause a 400 error, infinite loop, silent timeout, or wrong tool selection if missed - apply these as code edits, not as suggestions. **`[TUNE]`** items are quality/cost adjustments.

For each file that calls `messages.create()` / equivalent SDK method:

- [ ] **[BLOCKS]** Update the `model=` string to the new alias
- [ ] **[BLOCKS]** Replace `budget_tokens` with `thinking={"type": "adaptive"}` (deprecated on Opus 4.6 / Sonnet 4.6)
- [ ] **[BLOCKS]** Move `format` from top-level `output_format` into `output_config.format`
- [ ] **[BLOCKS]** Remove any assistant-turn prefills if targeting Opus 4.6 or Sonnet 4.6 (see the prefill replacement table)
- [ ] **[BLOCKS]** Switch to streaming if `max_tokens > ~16000` (otherwise SDK HTTP timeout)
- [ ] **[TUNE]** Verify tool-input handling parses JSON rather than raw-string-matching the serialized input (4.6 may escape Unicode / forward slashes differently; most SDKs already expose `block.input` as a parsed object)
- [ ] **[TUNE]** Set `output_config={"effort": "..."}` explicitly - especially when moving Sonnet 4.5 -> Sonnet 4.6 (4.6 defaults to `high`)
- [ ] **[TUNE]** Remove GA beta headers: `effort-2025-11-24`, `fine-grained-tool-streaming-2025-05-14`, `token-efficient-tools-2025-02-19`, `output-128k-2025-02-19`; remove `interleaved-thinking-2025-05-14` once on adaptive thinking
- [ ] **[TUNE]** Switch `client.beta.messages.create(...)` -> `client.messages.create(...)` once all betas are removed
- [ ] **[TUNE]** Review system prompt for aggressive tool language (`CRITICAL:`, `MUST`, `If in doubt`) and dial it back

**Extra items when coming from 3.x / 4.0 / 4.1:**
- [ ] **[BLOCKS]** Remove either `temperature` or `top_p` (passing both 400s on Claude 4+)
- [ ] **[BLOCKS]** Update text-editor tool `type` to `text_editor_20250728`
- [ ] **[BLOCKS]** Update text-editor tool `name` to `str_replace_based_edit_tool` - **changing only the `type` and keeping `name: "str_replace_editor"` returns a 400**
- [ ] **[BLOCKS]** Update code-execution tool to `code_execution_20260521`
- [ ] **[BLOCKS]** Delete any `undo_edit` command call sites
- [ ] **[TUNE]** Add handling for `stop_reason == "refusal"`
- [ ] **[TUNE]** Add handling for `stop_reason == "model_context_window_exceeded"` (4.5+)
- [ ] **[TUNE]** Verify tool-param string matching tolerates trailing newlines (preserved on 4.5+)
- [ ] **[TUNE]** If moving to a newer Haiku: review rate-limit tier (separate pool from Haiku 3.x)

**Verification:**
- [ ] Run one test request and inspect `response.stop_reason`, `response.usage`, and whether tool-use / thinking behavior matches expectations

For cached prompts: the render order and hash inputs did not change, so existing `cache_control` breakpoints keep working. However, **changing the model string invalidates the existing cache** - the first request on the new model will write the cache fresh.

---


---

## Per-Target Migration Guides

The detailed per-model guidance lives in separate files so you only load what the
migration actually needs. **Read exactly the file(s) for the target model** (plus
any intermediate hop the user is crossing) - do not read all of them.

| Target model | Read this file |
|---|---|
| Claude Opus 4.7 | `shared/migration-opus-4.7.md` |
| Claude Opus 4.8 | `shared/migration-opus-4.8.md` |
| Claude Opus 5 | `shared/migration-opus-5.md` |
| Claude Opus 5.5 | `shared/migration-opus-5.5.md` |
| Claude Sonnet 5 | `shared/migration-sonnet-5.md` |
| Claude Sonnet 5.5 | `shared/migration-sonnet-5.5.md` |
| Claude Haiku 5.5 | `shared/migration-haiku-5.5.md` |
| Claude Fable 5.1 (from Opus-tier / Mythos Preview) | `shared/migration-fable-5.1.md` |
| Claude Fable 5.1 (from Claude Fable 5) | `shared/migration-fable-5.1-from-fable-5.md` |

Each per-target file contains that model's breaking changes, silent default
changes, new API features, capability deltas, behavioral shifts, and its own
migration checklist. The sections above ("Breaking Changes by Source Model",
"Beta Headers to Remove on 4.6", "Model-ID Rename Quick Reference") still apply
to migrations targeting the 4.6 family and remain in this file.

> **Multi-hop migrations.** If the user is jumping over a version (e.g. Opus 4.6
> -> Opus 5.5), read the target file first; it notes what carries over. Add the
> intermediate target's file only if something is unclear or a breaking change
> from that hop bites.

## Verify the Migration

After updating, spot-check that the new model is actually being used. Replace `YOUR_TARGET_MODEL` with the model string you migrated to (e.g. `claude-fable-5-1`, `claude-opus-5-5`, `claude-opus-5`, `claude-opus-4-8`, `claude-opus-4-7`, `claude-sonnet-5-5`, `claude-sonnet-5`, `claude-sonnet-4-6`, `claude-haiku-5-5`, `claude-haiku-4-5`) and keep the assertion prefix in sync:

```python
YOUR_TARGET_MODEL = "claude-opus-5-5"  # or "claude-opus-5", "claude-opus-4-7", "claude-sonnet-5-5", "claude-sonnet-5", "claude-sonnet-4-6", "claude-haiku-5-5", "claude-haiku-4-5"
response = client.messages.create(model=YOUR_TARGET_MODEL, max_tokens=64, messages=[...])
assert response.model.startswith(YOUR_TARGET_MODEL), response.model
```

Prefix collision: `claude-opus-5-5` starts with `claude-opus-5`, so when your target is `claude-opus-5`, also assert `not response.model.startswith("claude-opus-5-5")`. The same holds for Sonnet: `claude-sonnet-5-5` starts with `claude-sonnet-5`, so a `claude-sonnet-5` check also asserts `not response.model.startswith("claude-sonnet-5-5")`.

For rate-limit headroom changes, pricing, or capability deltas (vision, structured outputs, effort support), query the Models API:

```python
m = client.models.retrieve(YOUR_TARGET_MODEL)
m.max_input_tokens, m.max_tokens
m.capabilities["effort"]["max"]["supported"]
```

See `shared/models.md` for the full capability lookup pattern.

---

## Ground the migration with an eval

A spot-check confirms the new model answers; it doesn't confirm the app still behaves the way the user wants. When the user reports a behavioral regression on the new model - e.g. *"it refuses things the old one handled fine"*, *"tool calls dropped off after the swap"*, *"responses got twice as long"* - don't tune the prompt by feel. Read `shared/evals/build-eval.md` and build a small eval that captures the regression, then read `shared/evals/eval-hillclimb.md` to iterate the prompt or harness against that eval until the score moves. Grounding the fix in an eval keeps the migration decision honest and leaves the user with a regression test for the next model swap.
