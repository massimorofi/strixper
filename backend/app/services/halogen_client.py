"""Async client for the Halogen LLM server.

Talks to four endpoints:
  GET  /health                   -- liveness, model, slots, queue, capabilities
  GET  /metrics                  -- Prometheus-style counters and gauges
  GET  /cache                    -- prompt-cache counters (Halogen-specific)
  POST /v1/messages/count_tokens -- input token counting (Anthropic-compatible)
"""

from __future__ import annotations

import asyncio
from typing import Any, Optional

import httpx


def parse_prometheus(text: str) -> dict[str, float]:
    """Parse a Prometheus text exposition into {metric_name: value}.

    Comment lines (# HELP / # TYPE) and unparsable lines are skipped.
    """
    metrics: dict[str, float] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        try:
            metrics[parts[0]] = float(parts[1])
        except ValueError:
            continue
    return metrics


def build_halogen_state(
    health: dict[str, Any],
    metrics: dict[str, float],
    models: dict[str, Any],
    cache: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Merge the Halogen payloads into the `halogen` section of live-status."""
    draft_total = metrics.get("halogen:draft_tokens_total")
    draft_accepted = metrics.get("halogen:draft_tokens_accepted_total")
    acceptance: Optional[float] = None
    if draft_total and draft_total > 0 and draft_accepted is not None:
        acceptance = round(draft_accepted / draft_total, 4)

    context = health.get("context")
    if context is None:
        data = models.get("data") or []
        if data:
            context = data[0].get("max_model_len") or data[0].get("context_length")

    return {
        "status": health.get("status", "ok"),
        "model": health.get("model"),
        "busy": health.get("busy"),
        "slots": health.get("slots"),
        "in_flight": health.get("in_flight"),
        "queued": health.get("queued"),
        "context_size": context,
        "kv_pool_positions": metrics.get("halogen:kv_pool_positions", health.get("kv_pool_positions")),
        "kv_cache_usage_ratio": metrics.get("llamacpp:kv_cache_usage_ratio"),
        "prompt_tokens_per_sec": metrics.get("llamacpp:prompt_tokens_seconds"),
        "predicted_tokens_per_sec": metrics.get("llamacpp:predicted_tokens_seconds"),
        "prompt_tokens_total": metrics.get("llamacpp:prompt_tokens_total"),
        "tokens_predicted_total": metrics.get("llamacpp:tokens_predicted_total"),
        "prompt_tokens_cached_total": metrics.get("halogen:prompt_tokens_cached_total"),
        "draft_acceptance_rate": acceptance,
        "cache": cache or {},
    }


class HalogenClient:
    """Non-blocking HTTP client for the Halogen engine/API server."""

    def __init__(self, base_url: str, timeout: float = 5.0) -> None:
        self.base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(timeout=timeout)

    async def close(self) -> None:
        await self._client.aclose()

    async def get_health(self) -> dict[str, Any]:
        resp = await self._client.get(f"{self.base_url}/health")
        resp.raise_for_status()
        return resp.json()

    async def get_metrics(self) -> dict[str, float]:
        resp = await self._client.get(f"{self.base_url}/metrics")
        resp.raise_for_status()
        return parse_prometheus(resp.text)

    async def get_models(self) -> dict[str, Any]:
        resp = await self._client.get(f"{self.base_url}/v1/models")
        resp.raise_for_status()
        return resp.json()

    async def get_cache(self) -> dict[str, Any]:
        """Prompt-cache counters (Halogen-specific observability endpoint)."""
        resp = await self._client.get(f"{self.base_url}/cache")
        resp.raise_for_status()
        return resp.json()

    async def count_tokens(
        self,
        messages: list[dict[str, Any]],
        model: str,
        system: Optional[str] = None,
        tools: Optional[list[dict[str, Any]]] = None,
    ) -> dict[str, Any]:
        """Count input tokens for a message list without generating.

        Uses the Anthropic-compatible /v1/messages/count_tokens endpoint.
        Returns {"input_tokens": <int>}.
        """
        payload: dict[str, Any] = {"model": model, "messages": messages}
        if system:
            payload["system"] = system
        if tools:
            payload["tools"] = tools
        resp = await self._client.post(
            f"{self.base_url}/v1/messages/count_tokens", json=payload
        )
        resp.raise_for_status()
        return resp.json()

    async def snapshot(self) -> dict[str, Any]:
        """Fetch all endpoints concurrently and merge them.

        /health is required (raises on failure); /metrics, /v1/models and /cache
        are best-effort so a partial outage still yields usable numbers.
        """
        health, metrics, models, cache = await asyncio.gather(
            self.get_health(),
            self.get_metrics(),
            self.get_models(),
            self.get_cache(),
            return_exceptions=True,
        )
        if isinstance(health, BaseException):
            raise health
        if isinstance(metrics, BaseException):
            metrics = {}
        if isinstance(models, BaseException):
            models = {}
        if isinstance(cache, BaseException):
            cache = {}
        return build_halogen_state(health, metrics, models, cache)
