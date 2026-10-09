# Halogen Server — Validated REST API

**Validation date:** 2026-10-09
**Server:** Halogen `0.17.3` (API and engine versions match)
**Model:** `halogen-qwen3.8-flash-next`
**Base URL:** `http://127.0.0.1:8731` (LAN: `http://192.168.1.172:8731`)
**Reference spec:** [llama.cpp HTTP Server README](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md)

Every endpoint below was probed live against the running server. Results are marked
**VALIDATED** (HTTP 200 with expected payload) or **NOT IMPLEMENTED** (HTTP 404).

---

## 1. Endpoint Summary

| Method | Path | Purpose | Status |
|--------|------|---------|--------|
| GET | `/health` | Liveness + full capability report | ✅ VALIDATED |
| GET | `/metrics` | Prometheus metrics exposition | ✅ VALIDATED |
| GET | `/cache` | Prompt-cache counters | ✅ VALIDATED (Halogen-specific) |
| GET | `/v1/models` | OpenAI-compatible model info | ✅ VALIDATED |
| POST | `/v1/chat/completions` | OpenAI Chat Completions (+ SSE streaming) | ✅ VALIDATED |
| POST | `/v1/completions` | OpenAI Text Completions | ✅ VALIDATED |
| POST | `/v1/responses` | OpenAI Responses API | ✅ VALIDATED |
| POST | `/v1/messages` | Anthropic Messages API | ✅ VALIDATED |
| POST | `/v1/messages/count_tokens` | Anthropic token counting | ✅ VALIDATED |
| POST | `/completion` | llama.cpp native completion | ❌ 404 |
| POST | `/tokenize` | llama.cpp tokenize | ❌ 404 |
| POST | `/detokenize` | llama.cpp detokenize | ❌ 404 |
| POST | `/apply-template` | llama.cpp chat-template apply | ❌ 404 |
| GET/POST | `/props` | llama.cpp global properties | ❌ 404 |
| GET | `/slots` | llama.cpp slot state | ❌ 404 |
| POST | `/slots/{id}?action=save\|restore\|erase` | Slot cache management | ❌ 404 |
| GET/POST | `/lora-adapters` | LoRA adapter management | ❌ 404 |
| POST | `/embedding` | llama.cpp native embeddings | ❌ 404 |
| POST | `/embeddings` | non-OAI embeddings | ❌ 404 |
| POST | `/reranking` / `/v1/rerank` | Reranking | ❌ 404 |
| POST | `/infill` | FIM code infill | ❌ 404 |
| POST | `/v1/embeddings` | OpenAI embeddings | ❌ 404 |
| POST | `/v1/responses/input_tokens` | Responses token counting | ❌ 404 |
| POST | `/v1/chat/completions/input_tokens` | Chat input token counting | ❌ 404 |
| POST | `/v1/chat/completions/control` | Real-time reasoning control | ❌ 404 |
| POST | `/v1/systemone` | TypeSafe decision API | ❌ 404 |

**Bottom line:** Halogen implements the **OpenAI-compatible** and
**Anthropic-compatible** surfaces (the same routes llama.cpp exposes under `/v1`),
plus three Halogen-native observability endpoints (`/health`, `/metrics`,
`/cache`). It does **not** implement the legacy llama.cpp native endpoints
(`/completion`, `/tokenize`, `/props`, `/slots`, `/infill`, embeddings,
reranking) nor the newer auxiliary routes (`/v1/embeddings`, `input_tokens`,
`control`, `systemone`).

**What this dashboard consumes:** `GET /health`, `GET /metrics`,
`GET /v1/models` (polled every cycle) and `GET /cache` (prompt-cache telemetry,
surfaced in the Prompt Cache card), plus `POST /v1/messages/count_tokens`
(proxyed as `POST /api/v1/count-tokens` for the Token Counter widget).

---

## 2. Validated Endpoints in Detail

### 2.1 GET /health

Returns a rich JSON capability report (far beyond llama.cpp's simple
`{"status":"ok"}`). **HTTP 200**, `application/json`.

```json
{
  "status": "ok",
  "model": "halogen-qwen3.8-flash-next",
  "endpoints": ["/v1/chat/completions", "/v1/completions", "/v1/messages",
                 "/v1/messages/count_tokens", "/v1/models", "/v1/responses"],
  "cache_counters": "/cache",
  "metrics": "/metrics",
  "context": 262144,
  "capability_probe": "ok",
  "rope_scaling": null,
  "indexer_budget": 2048,
  "vision": {
    "enabled": true,
    "endpoints": ["/v1/chat/completions", "/v1/messages", "/v1/responses"],
    "input": "data: URL or bare base64 in an image content part; http(s) URLs are refused",
    "max_pixels": 3686400,
    "size_multiple": 32
  },
  "version": { "api": "0.17.3", "engine": "0.17.3", "match": true },
  "modes": ["all", "engine", "api", "bench", "sweep", "convert",
             "inspect", "verify", "ppl", "niah"],
  "checkpoint_format": "hgn",
  "chat_template": {
    "path": "/models/tokenizer/chat_template.jinja",
    "sha256": "c3cf9e34abf4f9e36c2d72165aa9c132d3e2a725b6c2586aaa3a8af9d7a81041",
    "probe": "passed",
    "thinking_control": true
  },
  "engine": { "responds": true, "probe_s": 0.011 },
  "busy": false,
  "slots": 4,
  "slot_ctx": 262144,
  "kv_pool_positions": 524288,
  "in_flight": 0,
  "busy_for_s": 0,
  "queued": 0,
  "decode": "greedy at temperature 0 (the default); sampled at temperature > 0",
  "drafters_available": ["serial", "mtp"],
  "drafter_default": "mtp",
  "prompt_lookup": {
    "ngram": 3,
    "chain": 3,
    "applies_to": "greedy requests with the mtp drafter"
  },
  "shortlist_draft_head": false,
  "drafter_weights_loaded": true,
  "prompt_cache": {
    "enabled": true,
    "cap_mb": 110,
    "mode": 2,
    "snapshot_align": 64,
    "bitwise_identical_to_cold": false
  },
  "composable_context": { "enabled": false },
  "tool_calls": {
    "wire_format": "qwen-xml (function / parameter tags)",
    "streaming": true,
    "tool_choice": ["auto", "none", "required",
                     "{type: function, function: {name}}"],
    "forced_call_is_a_prefill": true,
    "required_is_exact": "named tool_choice, or one tool",
    "parallel_tool_calls": true,
    "constrained_decoding": false,
    "forced_call_disables_thinking": true,
    "argument_types_need_schema": true
  },
  "supported": ["reasoning_effort", "enable_thinking", "preserve_thinking",
                 "max_thinking_tokens", "thinking_budget_tokens",
                 "thinking_budget", "thinking_token_budget", "reasoning",
                 "thinking", "tools", "tool_choice", "parallel_tool_calls",
                 "stop", "max_tokens", "max_completion_tokens",
                 "max_output_tokens", "stream", "stream_options"],
  "token_budget_aliases": ["max_tokens", "max_completion_tokens",
                           "max_output_tokens"],
  "max_tokens_default": 8192,
  "reasoning_effort_default": "xhigh",
  "reasoning_effort_values": ["high", "low", "max", "medium", "minimal",
                               "none", "xhigh"],
  "token_budget_covers_reasoning": true,
  "max_thinking_tokens_default": null,
  "thinking_answer_room": "max(1024, 15% of max_tokens)",
  "end_of_turn_guard": true,
  "thinking_control_aliases": ["thinking_budget_tokens", "thinking_budget",
    "thinking_token_budget", "reasoning.{enabled,effort,max_tokens}",
    "thinking.{type,budget_tokens}"],
  "server_defaults": {},
  "server_defaults_rule": "a field the request sends always wins; a default fills only a field the request omits. A request that sends temperature 0 decodes greedy and takes no sampling default",
  "chat_template_kwargs": ["reasoning_effort", "enable_thinking",
                           "preserve_thinking"],
  "error_format": "openai",
  "accepted_but_ignored": ["n"],
  "not_implemented": [],
  "structured_output": {
    "enabled": true,
    "reason": null,
    "response_format": ["json_schema", "json_object"],
    "text.format": ["json_schema", "json_object"],
    "routes": ["/v1/chat/completions", "/v1/completions", "/v1/responses",
                "/v1/messages"],
    "sampling": "greedy only (temperature 0); sampled + schema is a 400",
    "images": "a schema with images is a 400",
    "thinking": "the reasoning block is unconstrained; the final text is constrained after the thinking close tag",
    "tools": "a request with tools may open a tool call instead of the JSON (the schema binds the final text, not a call)",
    "keys": "object keys are emitted in schema order",
    "enforced": ["type", "properties", "required", "additionalProperties",
      "items", "minItems", "maxItems", "minLength", "maxLength", "enum",
      "const", "anyOf", "oneOf (as anyOf)", "type: [..., \"null\"]", "$ref",
      "$defs"],
    "accepted_not_enforced": ["minimum", "maximum", "exclusiveMinimum",
      "exclusiveMaximum", "multipleOf", "title", "description", "default",
      "examples"],
    "refused": ["pattern", "format", "allOf", "not", "if", "then", "else",
      "patternProperties", "dependentRequired", "dependentSchemas",
      "uniqueItems", "contains", "propertyNames", "unevaluatedProperties",
      "unevaluatedItems", "minProperties", "maxProperties", "prefixItems"]
  },
  "sampling": {
    "implemented": ["temperature", "top_p", "top_k", "min_p", "seed",
      "presence_penalty", "frequency_penalty", "repetition_penalty",
      "logit_bias", "logprobs", "top_logprobs"],
    "decode": "temperature 0 (the default) is greedy: a forward pass and an argmax, byte-identical to serial greedy decode. temperature > 0 samples from the filtered target on the same drafter the request would otherwise get; with the MTP drafter the speculative accept/reject rule emits exactly the target distribution",
    "filters": "top_k, top_p and min_p compose as an intersection (the HF/vLLM convention); top_p >= 1 and top_k = 0 disable a filter. A sampled request that omits top_k or top_p takes the operator's default, else the model's (filter_defaults)",
    "filter_defaults": {
      "top_k": 20,
      "top_p": 0.95,
      "source": "generation_config.json"
    },
    "seed": "reproduces a request on the same drafter; a sampled MTP run and a sampled serial run agree in distribution, not token for token",
    "penalties": "presence and frequency count GENERATED tokens only; repetition_penalty scales every id in the prompt or the output (vLLM's rule); applied on both drafters",
    "logprobs": "a sampled request carries the chosen token's logprob on every token; at temperature 0, and with top_logprobs (up to 20) at any temperature, the first generated token only, so those need max_tokens 1",
    "not_implemented": ["logprobs with stream=true",
      "logprobs past the first token at temperature 0", "n > 1"]
  },
  "max_tokens_cap": 65536,
  "max_tokens_over_cap": "400"
}
```

**Key semantics:**
- `context` 262144, `slots` 4, `kv_pool_positions` 524288 (2× context).
- `max_tokens_default` 8192, `max_tokens_cap` 65536 (over cap → 400).
- `reasoning_effort_default` `"xhigh"`; accepted values: high/low/max/medium/
  minimal/none/xhigh.
- Decode is **greedy at temperature 0** (the default); sampled at temp > 0.
- Structured JSON output requires **temperature 0** (sampled + schema → 400).
- Vision enabled but only **base64 / data-URL** images; `http(s)` URLs refused.
- Tool-call wire format is **qwen-xml** (function / parameter tags).
- `n` is accepted but ignored (n > 1 not supported).

### 2.2 GET /metrics

Prometheus text exposition. **HTTP 200**, `text/plain`.

```
llamacpp:prompt_tokens_total
llamacpp:prompt_seconds_total
llamacpp:prompt_tokens_seconds
llamacpp:tokens_predicted_total
llamacpp:tokens_predicted_seconds_total
llamacpp:predicted_tokens_seconds
llamacpp:requests_processing
llamacpp:requests_deferred
llamacpp:kv_cache_tokens
llamacpp:kv_cache_usage_ratio
halogen:requests_total
halogen:prompt_tokens_cached_total
halogen:draft_tokens_total
halogen:draft_tokens_accepted_total
halogen:structured_requests_total
halogen:kv_pool_positions
halogen:kv_pool_reserved_tokens
halogen:draft_list_misses_total
halogen:draft_list_tokens_total
```

**Important:** `prompt_tokens_seconds` / `predicted_tokens_seconds` are
"since last scrape" gauges that **decay to 0** when the engine is idle — do not
read them as live throughput (see §7 of `strixper_specs.md`).

### 2.3 GET /cache

Halogen-specific prompt-cache counters. **HTTP 200**, `application/json`.
`/health` advertises this endpoint via `"cache_counters": "/cache"`.

```json
{
  "entries": 24,
  "bytes": 2793351648,
  "last_entry_bytes": 116389652,
  "max_entries": 24,
  "hits": 62,
  "misses": 9,
  "stores": 135,
  "evicted": 2,
  "prompt_tokens_saved": 4019426,
  "store_ms_total": 3666.5,
  "restore_ms_total": 344.9,
  "last_store_ms": 0.0,
  "last_restore_ms": 0.0,
  "refused": 0,
  "rows_copied": 2,
  "rows_copied_ms": 0.3,
  "superseded": 52,
  "composable_context": {
    "chunks": 0, "bytes": 0, "stored": 0,
    "composed": 0, "evicted": 0, "composed_tokens": 0
  },
  "disk": {
    "on": false, "records": 0, "lineages": 0, "bytes": 0,
    "budget_bytes": 0, "hits": 0, "misses": 0, "persisted": 0,
    "branches": 0, "evicted": 0, "skipped": 0,
    "restore_ms_total": 0.0, "restored_bytes": 0
  },
  "tapped": 29,
  "full_hits": 3,
  "pool": {
    "waiting_for_room": 0, "waiting_s": 0.0, "relocated": 0,
    "cold_resorts": 0, "positions": 524288, "used": 288512,
    "busy_regions": 0, "held_regions": 7, "room_clamped": 0,
    "moved": 2, "packed": 0, "taken_over": 0, "usage_ratio": 0.5503
  },
  "dropped": 19,
  "hit_rate": 0.8784,
  "token_hit_rate": 0.9652,
  "snapshot_places": ["system_end", "last_user_start", "history_end"]
}
```

**Field meanings:**
- `entries` / `max_entries` — live cache snapshots vs. configured capacity.
- `hits` / `misses` — request-level cache lookups.
- `hit_rate` — server-computed request hit ratio (0–1).
- `token_hit_rate` — fraction of prompt tokens covered by cache (0–1).
- `prompt_tokens_saved` — total prompt tokens not re-processed thanks to hits.
- `stores` / `evicted` / `superseded` / `dropped` — cache write/eviction churn.
- `store_ms_total` / `restore_ms_total` — cumulative write / read latency.
- `refused` — cache writes declined (e.g. no room).
- `pool.usage_ratio` — KV-pool occupancy (same ratio as
  `llamacpp:kv_cache_usage_ratio`).
- `disk.on` — whether the disk cache tier is enabled (false here).
- `composable_context` — chunked-context composition stats (disabled here).

### 2.4 GET /v1/models

OpenAI-compatible model listing. **HTTP 200**.

```json
{
  "object": "list",
  "has_more": false,
  "first_id": "halogen-qwen3.8-flash-next",
  "last_id": "halogen-qwen3.8-flash-next",
  "data": [
    {
      "id": "halogen-qwen3.8-flash-next",
      "object": "model",
      "type": "model",
      "display_name": "halogen-qwen3.8-flash-next",
      "created_at": "1970-01-01T00:00:00Z",
      "owned_by": "halogen",
      "created": 0,
      "max_model_len": 262144,
      "context_length": 262144,
      "meta": { "n_ctx_train": 262144, "n_ctx": 262144 },
      "max_tokens_cap": 65536,
      "max_tokens_default": 8192
    }
  ]
}
```

### 2.5 POST /v1/chat/completions

OpenAI Chat Completions. **HTTP 200** (sync) and **SSE streaming** validated.

**Request:**
```json
{
  "model": "halogen-qwen3.8-flash-next",
  "messages": [{ "role": "user", "content": "Reply with exactly: hi" }],
  "max_tokens": 8,
  "reasoning_effort": "none"
}
```

**Response (sync):**
```json
{
  "id": "chatcmpl-162ca346f86d46ed895a7d31",
  "created": 1791558339,
  "model": "halogen-qwen3.8-flash-next",
  "object": "chat.completion",
  "choices": [
    {
      "index": 0,
      "finish_reason": "stop",
      "message": { "role": "assistant", "content": "hi" }
    }
  ],
  "usage": {
    "prompt_tokens": 17,
    "completion_tokens": 2,
    "total_tokens": 19,
    "completion_tokens_details": { "reasoning_tokens": 0 }
  },
  "timings": {
    "prompt_n": 17,
    "predicted_n": 2,
    "prompt_ms": 323.1,
    "predicted_ms": 43.8,
    "prompt_per_second": 52.615,
    "predicted_per_second": 45.662,
    "cache_n": 0,
    "disk_restore_n": 0,
    "disk_restore_ms": 0.0,
    "prefix_n": 1,
    "draft_n": 2,
    "draft_n_accepted": 2
  }
}
```

**Streaming:** standard SSE (`data: {...}\n\n` chunks). Deltas carry
`delta.reasoning_content` for thinking output; the final chunk carries
`finish_reason` + `timings`; stream ends with `data: [DONE]`.

### 2.6 POST /v1/completions

OpenAI Text Completions. **HTTP 200**.

**Request:**
```json
{
  "model": "halogen-qwen3.8-flash-next",
  "prompt": "Once upon a",
  "max_tokens": 8,
  "temperature": 0
}
```

**Response:**
```json
{
  "id": "cmpl-695d4781b264499a94aa3d98",
  "created": 1791558372,
  "model": "halogen-qwen3.8-flash-next",
  "object": "text_completion",
  "choices": [
    {
      "index": 0,
      "finish_reason": "length",
      "text": " time, in a magical land called School"
    }
  ],
  "usage": {
    "prompt_tokens": 3,
    "completion_tokens": 8,
    "total_tokens": 11,
    "completion_tokens_details": { "reasoning_tokens": 0 }
  },
  "timings": {
    "prompt_n": 3, "predicted_n": 8,
    "prompt_ms": 99.4, "predicted_ms": 197.7,
    "prompt_per_second": 30.181, "predicted_per_second": 40.465,
    "cache_n": 0, "disk_restore_n": 0, "disk_restore_ms": 0.0,
    "prefix_n": 0, "draft_n": 7, "draft_n_accepted": 2
  }
}
```

### 2.7 POST /v1/responses

OpenAI Responses API. **HTTP 200**.

**Request:**
```json
{
  "model": "halogen-qwen3.8-flash-next",
  "input": "Say hi",
  "max_output_tokens": 8,
  "reasoning": { "effort": "none" }
}
```

**Response:**
```json
{
  "id": "resp_dd66a274251247ea8fd1c7fd",
  "object": "response",
  "created_at": 1791558373,
  "status": "incomplete",
  "model": "halogen-qwen3.8-flash-next",
  "output": [
    {
      "id": "msg_dd66a274251247ea8fd1c7fd",
      "type": "message",
      "role": "assistant",
      "status": "completed",
      "content": [
        { "type": "output_text", "text": "Hi! How can I help you today",
          "annotations": [] }
      ]
    }
  ],
  "parallel_tool_calls": true,
  "max_output_tokens": 8,
  "usage": {
    "input_tokens": 14,
    "output_tokens": 8,
    "total_tokens": 22,
    "output_tokens_details": { "reasoning_tokens": 0 }
  },
  "timings": {
    "prompt_n": 14, "predicted_n": 8,
    "prompt_ms": 290.3, "predicted_ms": 121.5,
    "prompt_per_second": 48.226, "predicted_per_second": 65.844,
    "cache_n": 0, "disk_restore_n": 0, "disk_restore_ms": 0.0,
    "prefix_n": 3, "draft_n": 5, "draft_n_accepted": 4
  },
  "incomplete_details": { "reason": "max_output_tokens" }
}
```

### 2.8 POST /v1/messages

Anthropic Messages API. **HTTP 200**. Send `anthropic-version: 2023-06-01`.

**Request:**
```json
{
  "model": "halogen-qwen3.8-flash-next",
  "max_tokens": 8,
  "messages": [{ "role": "user", "content": "Say hi" }]
}
```

**Response:**
```json
{
  "id": "msg_ab1c63bb1d374081b7e25592",
  "type": "message",
  "role": "assistant",
  "model": "halogen-qwen3.8-flash-next",
  "content": [
    {
      "type": "thinking",
      "thinking": "We need\n\nConsidering the limited time by",
      "signature": "hgn1.eJwLT1XIS01N4eJyzs8rzkxJLcrMS1coyUhVyMnMzSxJTVEoycxNVUiqBAAbAg41"
    }
  ],
  "stop_reason": "max_tokens",
  "stop_sequence": null,
  "usage": {
    "input_tokens": 54,
    "output_tokens": 8,
    "cache_read_input_tokens": 0,
    "cache_creation_input_tokens": 0
  },
  "timings": {
    "prompt_n": 54, "predicted_n": 8,
    "prompt_ms": 710.7, "predicted_ms": 191.9,
    "prompt_per_second": 75.981, "predicted_per_second": 41.688,
    "cache_n": 0, "disk_restore_n": 0, "disk_restore_ms": 0.0,
    "prefix_n": 40, "draft_n": 0, "draft_n_accepted": 0
  }
}
```

### 2.9 POST /v1/messages/count_tokens

Anthropic-compatible token counting — **no generation**, pure tokenizer pass.
**HTTP 200**.

**Request:**
```json
{
  "model": "halogen-qwen3.8-flash-next",
  "messages": [{ "role": "user", "content": "Hello, how are you?" }],
  "system": "Optional system prompt (Anthropic-style, separate from messages)"
}
```

**Response:**
```json
{ "input_tokens": 58 }
```

**Notes (validated):**
- `model` is **required** — omitting it returns **HTTP 500** with a pydantic
  `ValidationError` (Halogen reuses the chat request model, which requires a
  string `model`).
- `system` is optional; when present its tokens are included in the count.
- `messages: []` → **HTTP 400**
  (`"messages is empty: /v1/messages needs at least one user turn"`).
- The count includes the full chat-template overhead, not just the raw text —
  a 25-char prompt counts as 59 tokens, so template framing dominates short
  inputs.
- This is the endpoint the dashboard's **Token Counter** widget uses (proxied as
  `POST /api/v1/count-tokens`).

---

## 3. Error Format

Halogen uses the **OpenAI error envelope** (`error_format: "openai"`):

```json
{
  "type": "error",
  "error": {
    "type": "invalid_request_error",
    "message": "1 validation error for MessagesReq ..."
  }
}
```

Observed error types: `invalid_request_error` (400), `api_error` (500).
Error responses carry the standard HTTP status code alongside the envelope.

---

## 4. Compatibility Notes vs llama.cpp Spec

| llama.cpp feature | Halogen status |
| --- | --- |
| `/health` returns `{"status":"ok"}` | ✅ Rich capability report instead |
| `/metrics` Prometheus | ✅ Same format, Halogen adds `halogen:*` metrics |
| `/cache` | ✅ Halogen-specific (not in llama.cpp) |
| `/v1/models` | ✅ OpenAI-compatible |
| `/v1/chat/completions` | ✅ Sync + SSE streaming |
| `/v1/completions` | ✅ |
| `/v1/responses` | ✅ |
| `/v1/messages` (Anthropic) | ✅ Not in llama.cpp — Halogen addition |
| `/v1/messages/count_tokens` | ✅ Not in llama.cpp — Halogen addition |
| `/completion` (native) | ❌ 404 |
| `/tokenize`, `/detokenize` | ❌ 404 |
| `/apply-template` | ❌ 404 |
| `/props` | ❌ 404 |
| `/slots`, `/slots/{id}` | ❌ 404 |
| `/lora-adapters` | ❌ 404 |
| `/embedding`, `/embeddings`, `/v1/embeddings` | ❌ 404 |
| `/reranking`, `/v1/rerank` | ❌ 404 |
| `/infill` | ❌ 404 |
| `/v1/responses/input_tokens` | ❌ 404 |
| `/v1/chat/completions/input_tokens` | ❌ 404 |
| `/v1/chat/completions/control` | ❌ 404 |
| `/v1/systemone` | ❌ 404 |

**Halogen is NOT a drop-in llama.cpp server.** It implements the OpenAI +
Anthropic surfaces plus its own observability trio (`/health`, `/metrics`,
`/cache`), and deliberately omits the legacy native llama.cpp routes. Use
`/v1/messages/count_tokens` for token counting (the llama.cpp `/tokenize`
route does not exist).

---

## 5. curl Quick Reference

```bash
BASE=http://127.0.0.1:8731

# Liveness + capabilities
curl -s $BASE/health | python3 -m json.tool

# Prometheus metrics
curl -s $BASE/metrics

# Prompt-cache counters
curl -s $BASE/cache | python3 -m json.tool

# Model info
curl -s $BASE/v1/models | python3 -m json.tool

# Chat completion (sync)
curl -s -X POST $BASE/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"halogen-qwen3.8-flash-next","messages":[{"role":"user","content":"Hello"}],"max_tokens":64}'

# Chat completion (streaming)
curl -N -X POST $BASE/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"halogen-qwen3.8-flash-next","messages":[{"role":"user","content":"Hello"}],"stream":true}'

# Token count (model is REQUIRED)
curl -s -X POST $BASE/v1/messages/count_tokens \
  -H "Content-Type: application/json" \
  -d '{"model":"halogen-qwen3.8-flash-next","messages":[{"role":"user","content":"Hello, how are you?"}]}'
# → {"input_tokens":58}
```
