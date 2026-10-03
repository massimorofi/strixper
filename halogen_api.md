Halogen server has OpenAI API.
Beside that you can connect to:
http://host:<port>/health to get up to date health status information in JSON format.
EXAMPLE
http://192.168.1.172:8731/health

Response:
{"status":"ok","model":"halogen-qwen3.8-flash-next","endpoints":["/v1/chat/completions","/v1/completions","/v1/messages","/v1/messages/count_tokens","/v1/models","/v1/responses"],"cache_counters":"/cache","metrics":"/metrics","context":262144,"capability_probe":"ok","rope_scaling":null,"indexer_budget":2048,"vision":{"enabled":true,"endpoints":["/v1/chat/completions","/v1/messages","/v1/responses"],"input":"data: URL or bare base64 in an image content part; http(s) URLs are refused","max_pixels":3686400,"size_multiple":32},"version":{"api":"0.16.2","engine":"0.16.2","match":true},"modes":["all","engine","api","bench","sweep","convert","inspect","verify","ppl","niah"],"checkpoint_format":"hgn","chat_template":{"path":"/models/tokenizer/chat_template.jinja","sha256":"c3cf9e34abf4f9e36c2d72165aa9c132d3e2a725b6c2586aaa3a8af9d7a81041","probe":"passed","thinking_control":true},"engine":{"responds":true,"probe_s":1.625},"busy":true,"slots":4,"slot_ctx":262144,"kv_pool_positions":524288,"in_flight":4,"busy_for_s":258.9,"queued":13,"decode":"greedy at temperature 0 (the default); sampled at temperature > 0","drafters_available":["serial","mtp"],"drafter_default":"mtp","prompt_lookup":{"ngram":3,"chain":3,"applies_to":"greedy requests with the mtp drafter"},"shortlist_draft_head":false,"drafter_weights_loaded":true,"prompt_cache":{"enabled":true,"cap_mb":110,"mode":2,"snapshot_align":64,"bitwise_identical_to_cold":false},"composable_context":{"enabled":false},"tool_calls":{"wire_format":"qwen-xml (<function=>/<parameter=>)","streaming":true,"tool_choice":["auto","none","required","{type: function, function: {name}}"],"forced_call_is_a_prefill":true,"required_is_exact":"named tool_choice, or one tool","parallel_tool_calls":true,"constrained_decoding":false,"forced_call_disables_thinking":true,"argument_types_need_schema":true},"supported":["reasoning_effort","enable_thinking","preserve_thinking","max_thinking_tokens","thinking_budget_tokens","thinking_budget","thinking_token_budget","reasoning","thinking","tools","tool_choice","parallel_tool_calls","stop","max_tokens","max_completion_tokens","max_output_tokens","stream","stream_options"],"token_budget_aliases":["max_tokens","max_completion_tokens","max_output_tokens"],"max_tokens_default":8192,"reasoning_effort_default":"xhigh","reasoning_effort_values":["high","low","max","medium","minimal","none","xhigh"],"token_budget_covers_reasoning":true,"max_thinking_tokens_default":null,"thinking_answer_room":"max(1024, 15% of max_tokens)","end_of_turn_guard":true,"thinking_control_aliases":["thinking_budget_tokens","thinking_budget","thinking_token_budget","reasoning.{enabled,effort,max_tokens}","thinking.{type,budget_tokens}"],"server_defaults":{},"server_defaults_rule":"a field the request sends always wins; a default fills only a field the request omits. A request that sends temperature 0 decodes greedy and takes no sampling default","chat_template_kwargs":["reasoning_effort","enable_thinking","preserve_thinking"],"error_format":"openai","accepted_but_ignored":["n"],"not_implemented":[],"structured_output":{"enabled":true,"reason":null,"response_format":["json_schema","json_object"],"text.format":["json_schema","json_object"],"routes":["/v1/chat/completions","/v1/completions","/v1/responses","/v1/messages"],"sampling":"greedy only (temperature 0); sampled + schema is a 400","images":"a schema with images is a 400","thinking":"the reasoning block is unconstrained; the final text is constrained after </think>","tools":"a request with tools may open a <tool_call> instead of the JSON (the schema binds the final text, not a call)","keys":"object keys are emitted in schema order","enforced":["type","properties","required","additionalProperties","items","minItems","maxItems","minLength","maxLength","enum","const","anyOf","oneOf (as anyOf)","type: [..., \"null\"]","$ref","$defs"],"accepted_not_enforced":["minimum","maximum","exclusiveMinimum","exclusiveMaximum","multipleOf","title","description","default","examples"],"refused":["pattern","format","allOf","not","if","then","else","patternProperties","dependentRequired","dependentSchemas","uniqueItems","contains","propertyNames","unevaluatedProperties","unevaluatedItems","minProperties","maxProperties","prefixItems"]},"sampling":{"implemented":["temperature","top_p","top_k","min_p","seed","presence_penalty","frequency_penalty","repetition_penalty","logit_bias","logprobs","top_logprobs"],"decode":"temperature 0 (the default) is greedy: a forward pass and an argmax, byte-identical to serial greedy decode. temperature > 0 samples from the filtered target on the same drafter the request would otherwise get; with the MTP drafter the speculative accept/reject rule emits exactly the target distribution","filters":"top_k, top_p and min_p compose as an intersection (the HF/vLLM convention); top_p >= 1 and top_k = 0 disable a filter. A sampled request that omits top_k or top_p takes the operator's default, else the model's (filter_defaults)","filter_defaults":{"top_k":20,"top_p":0.95,"source":"generation_config.json"},"seed":"reproduces a request on the same drafter; a sampled MTP run and a sampled serial run agree in distribution, not token for token","penalties":"presence and frequency count GENERATED tokens only; repetition_penalty scales every id in the prompt or the output (vLLM's rule); applied on both drafters","logprobs":"a sampled request carries the chosen token's logprob on every token; at temperature 0, and with top_logprobs (up to 20) at any temperature, the first generated token only, so those need max_tokens 1","not_implemented":["logprobs with stream=true","logprobs past the first token at temperature 0","n > 1"]},"max_tokens_cap":65536,"max_tokens_over_cap":"400"}

Another end point is:
http://host:<port>/metrics that reply with a list of metrics of the current running processes.
Each line of the metrics is preceeded by two commented lines starting with "#" that explain what the metric is for
EXAMPLE:
http://192.168.1.172:8731/metrics
Response:
# HELP llamacpp:prompt_tokens_total Number of prompt tokens processed.
# TYPE llamacpp:prompt_tokens_total counter
llamacpp:prompt_tokens_total 806600
# HELP llamacpp:prompt_seconds_total Prompt process time.
# TYPE llamacpp:prompt_seconds_total counter
llamacpp:prompt_seconds_total 591.261
# HELP llamacpp:tokens_predicted_total Number of generation tokens processed.
# TYPE llamacpp:tokens_predicted_total counter
llamacpp:tokens_predicted_total 24920
# HELP llamacpp:tokens_predicted_seconds_total Predict process time.
# TYPE llamacpp:tokens_predicted_seconds_total counter
llamacpp:tokens_predicted_seconds_total 1582.82
# HELP llamacpp:prompt_tokens_seconds Average prompt throughput in tokens/s. (since the last scrape)
# TYPE llamacpp:prompt_tokens_seconds gauge
llamacpp:prompt_tokens_seconds 1361.02
# HELP llamacpp:predicted_tokens_seconds Average generation throughput in tokens/s. (since the last scrape)
# TYPE llamacpp:predicted_tokens_seconds gauge
llamacpp:predicted_tokens_seconds 16.7812
# HELP llamacpp:requests_processing Number of requests processing.
# TYPE llamacpp:requests_processing gauge
llamacpp:requests_processing 1
# HELP llamacpp:requests_deferred Number of requests deferred.
# TYPE llamacpp:requests_deferred gauge
llamacpp:requests_deferred 0
# HELP llamacpp:kv_cache_tokens KV-cache tokens (positions the engine's pool holds, busy and held regions).
# TYPE llamacpp:kv_cache_tokens gauge
llamacpp:kv_cache_tokens 453376
# HELP llamacpp:kv_cache_usage_ratio KV-cache usage. 1 means 100 percent usage. (positions held over the pool)
# TYPE llamacpp:kv_cache_usage_ratio gauge
llamacpp:kv_cache_usage_ratio 0.864746
# HELP halogen:requests_total Requests completed.
# TYPE halogen:requests_total counter
halogen:requests_total 25
# HELP halogen:prompt_tokens_cached_total Prompt tokens the prompt cache covered (not processed).
# TYPE halogen:prompt_tokens_cached_total counter
halogen:prompt_tokens_cached_total 476938
# HELP halogen:draft_tokens_total Tokens proposed by the draft head and prompt lookup.
# TYPE halogen:draft_tokens_total counter
halogen:draft_tokens_total 334
# HELP halogen:draft_tokens_accepted_total Of those, accepted.
# TYPE halogen:draft_tokens_accepted_total counter
halogen:draft_tokens_accepted_total 282
# HELP halogen:structured_requests_total Requests decoded under a JSON schema.
# TYPE halogen:structured_requests_total counter
halogen:structured_requests_total 0
# HELP halogen:kv_pool_positions The KV pool, in positions.
# TYPE halogen:kv_pool_positions gauge
halogen:kv_pool_positions 524288
# HELP halogen:kv_pool_reserved_tokens Positions the requests holding a front-end slot asked for (prompt + max_tokens each), admitted or not.
# TYPE halogen:kv_pool_reserved_tokens gauge
halogen:kv_pool_reserved_tokens 128712

Finally you have another endpoint:
http://host:<port>/v1/models that list teh available models with their own parameters values in JSON format.
EXAMPLE
http://192.168.1.172:8731/v1/models
Response:
{"object":"list","has_more":false,"first_id":"halogen-qwen3.8-flash-next","last_id":"halogen-qwen3.8-flash-next","data":[{"id":"halogen-qwen3.8-flash-next","object":"model","type":"model","display_name":"halogen-qwen3.8-flash-next","created_at":"1970-01-01T00:00:00Z","owned_by":"halogen","created":0,"max_model_len":262144,"context_length":262144,"meta":{"n_ctx_train":262144,"n_ctx":262144},"max_tokens_cap":65536,"max_tokens_default":8192}]}