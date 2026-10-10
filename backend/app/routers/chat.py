"""POST /api/v1/chat -- AI chat inference for the AI-Chat tab.

Proxies Halogen's four inference endpoints behind one normalized interface so
the frontend can switch API style without changing its rendering logic:

  api="chat"        -> POST /v1/chat/completions   (OpenAI Chat Completions)
  api="messages"    -> POST /v1/messages          (Anthropic Messages)
  api="responses"   -> POST /v1/responses         (OpenAI Responses)
  api="completions" -> POST /v1/completions       (OpenAI Text Completions)

Every mode streams Server-Sent Events (``text/event-stream``) with normalized
events so the UI is identical regardless of the upstream API:

  event: delta      data: {"text": "..."}     answer text chunk
  event: reasoning  data: {"text": "..."}     thinking / chain-of-thought chunk
  event: done       data: {"finish_reason": "...", "usage": {...},
                            "timings": {...}}  terminal event
  event: error      data: {"message": "..."}  failure (Halogen down / bad request)

``stream: false`` returns a single JSON object instead of an SSE stream.
"""

from __future__ import annotations

import json
from typing import Any, AsyncIterator, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator

import httpx
from ..services.halogen_client import HalogenClient
from ..services.run_registry import registry

router = APIRouter(tags=["chat"])

MAX_MESSAGE_CHARS = 100_000
MAX_MESSAGES = 200

# Anthropic Messages requires this header on every request.
ANTHROPIC_VERSION = "2023-06-01"

API_STYLES = ("chat", "messages", "responses", "completions")


class ChatMessage(BaseModel):
    role: str = Field(..., pattern="^(user|assistant|system)$")
    content: str = Field(..., min_length=1, max_length=MAX_MESSAGE_CHARS)


class ChatRequest(BaseModel):
    messages: list[ChatMessage] = Field(..., min_length=1, max_length=MAX_MESSAGES)
    api: str = Field(default="chat")
    stream: bool = True
    max_tokens: Optional[int] = Field(default=None, ge=1, le=32768)
    temperature: Optional[float] = Field(default=None, ge=0.0, le=2.0)
    # Toggle the model's chain-of-thought. Halogen always thinks by default;
    # thinking=False suppresses it (verified per upstream API style).
    thinking: bool = True
    # An optional managed engine run selected in the AI-Chat UI.
    run_id: Optional[str] = None

    @field_validator("api")
    @classmethod
    def _valid_api(cls, value: str) -> str:
        if value not in API_STYLES:
            raise ValueError(f"api must be one of {API_STYLES}")
        return value

    @field_validator("messages")
    @classmethod
    def _not_blank(cls, value: list[ChatMessage]) -> list[ChatMessage]:
        for m in value:
            if not m.content.strip():
                raise ValueError("message content must not be blank")
        return value


def _sse(event: str, data: dict[str, Any]) -> str:
    """Format one SSE frame."""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _last_user_text(messages: list[dict[str, Any]]) -> str:
    """Most recent user turn (used by the single-prompt API styles)."""
    for m in reversed(messages):
        if m["role"] == "user":
            return m["content"]
    return messages[-1]["content"]


def _extract_chat(chunk: dict[str, Any]) -> tuple[Optional[str], Optional[str], Optional[dict]]:
    """OpenAI Chat Completions: (reasoning, content, done_payload)."""
    choices = chunk.get("choices") or []
    if not choices:
        return None, None, None
    delta = choices[0].get("delta") or {}
    reasoning = delta.get("reasoning_content")
    content = delta.get("content")
    done = None
    finish = choices[0].get("finish_reason")
    if finish:
        done = {
            "finish_reason": finish,
            "usage": chunk.get("usage"),
            "timings": chunk.get("timings"),
        }
    return reasoning, content, done


def _extract_completions(chunk: dict[str, Any]) -> tuple[Optional[str], Optional[str], Optional[dict]]:
    """OpenAI Text Completions: (reasoning, content, done_payload)."""
    choices = chunk.get("choices") or []
    if not choices:
        return None, None, None
    content = choices[0].get("text")
    done = None
    finish = choices[0].get("finish_reason")
    if finish:
        done = {
            "finish_reason": finish,
            "usage": chunk.get("usage"),
            "timings": chunk.get("timings"),
        }
    return None, content, done


def _extract_responses(chunk: dict[str, Any]) -> tuple[Optional[str], Optional[str], Optional[dict]]:
    """OpenAI Responses API: (reasoning, content, done_payload)."""
    etype = chunk.get("type")
    if etype == "response.output_text.delta":
        return None, chunk.get("delta"), None
    if etype == "response.reasoning_text.delta":
        return chunk.get("delta"), None, None
    if etype == "response.completed":
        resp = chunk.get("response") or {}
        status = resp.get("status")
        finish = "stop" if status == "completed" else status
        return None, None, {
            "finish_reason": finish,
            "usage": resp.get("usage"),
            "timings": resp.get("timings"),
        }
    return None, None, None


def _extract_messages(chunk: dict[str, Any]) -> tuple[Optional[str], Optional[str], Optional[dict]]:
    """Anthropic Messages: (reasoning, content, done_payload)."""
    etype = chunk.get("type")
    if etype == "content_block_delta":
        delta = chunk.get("delta") or {}
        dtype = delta.get("type")
        if dtype == "text_delta":
            return None, delta.get("text"), None
        if dtype == "thinking_delta":
            return delta.get("thinking"), None, None
        return None, None, None
    if etype == "message_delta":
        return None, None, {
            "finish_reason": chunk.get("delta", {}).get("stop_reason"),
            "usage": chunk.get("usage"),
            "timings": chunk.get("timings"),
        }
    return None, None, None


_EXTRACTORS = {
    "chat": _extract_chat,
    "completions": _extract_completions,
    "responses": _extract_responses,
    "messages": _extract_messages,
}


def _upstream_path(api: str) -> str:
    return {
        "chat": "/v1/chat/completions",
        "messages": "/v1/messages",
        "responses": "/v1/responses",
        "completions": "/v1/completions",
    }[api]


def _build_payload(
    api: str, body: ChatRequest, model: str
) -> tuple[dict[str, Any], dict[str, str]]:
    """Return (json payload, extra headers) for the chosen upstream API."""
    messages = [m.model_dump() for m in body.messages]
    stream = body.stream

    if api == "chat":
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": stream,
        }
        if body.max_tokens is not None:
            payload["max_tokens"] = body.max_tokens
        if body.temperature is not None:
            payload["temperature"] = body.temperature
        if not body.thinking:
            payload["reasoning_effort"] = "none"
        return payload, {}

    if api == "messages":
        # Anthropic: the system prompt is a separate top-level field.
        system_parts = [m["content"] for m in messages if m["role"] == "system"]
        conv = [m for m in messages if m["role"] != "system"]
        payload = {"model": model, "messages": conv, "stream": stream}
        if body.max_tokens is not None:
            payload["max_tokens"] = body.max_tokens
        if body.temperature is not None:
            payload["temperature"] = body.temperature
        if system_parts:
            payload["system"] = "\n\n".join(system_parts)
        if not body.thinking:
            payload["thinking"] = {"type": "disabled"}
        return payload, {"anthropic-version": ANTHROPIC_VERSION}

    if api == "responses":
        # Responses API takes a single `input` (the latest user turn).
        payload = {
            "model": model,
            "input": _last_user_text(messages),
            "stream": stream,
        }
        if body.max_tokens is not None:
            payload["max_output_tokens"] = body.max_tokens
        if body.temperature is not None:
            payload["temperature"] = body.temperature
        if not body.thinking:
            payload["reasoning"] = {"effort": "none"}
        return payload, {}

    # api == "completions": raw text completion from the latest user turn.
    payload = {
        "model": model,
        "prompt": _last_user_text(messages),
        "stream": stream,
    }
    if body.max_tokens is not None:
        payload["max_tokens"] = body.max_tokens
    if body.temperature is not None:
        payload["temperature"] = body.temperature
    if not body.thinking:
        payload["reasoning_effort"] = "none"
    return payload, {}


def _sync_result(api: str, result: dict[str, Any], model: str) -> dict[str, Any]:
    """Normalize a non-streaming response into the common shape."""
    if api == "chat":
        choices = result.get("choices") or []
        message = (choices[0].get("message") or {}) if choices else {}
        content = message.get("content", "")
        reasoning = message.get("reasoning_content")
        finish = choices[0].get("finish_reason") if choices else None
    elif api == "completions":
        choices = result.get("choices") or []
        content = choices[0].get("text", "") if choices else ""
        reasoning = None
        finish = choices[0].get("finish_reason") if choices else None
    elif api == "messages":
        parts = result.get("content") or []
        content = "".join(p.get("text", "") for p in parts if p.get("type") == "text")
        reasoning = "".join(
            p.get("thinking", "") for p in parts if p.get("type") == "thinking"
        )
        finish = result.get("stop_reason")
    else:  # responses
        out = result.get("output") or []
        content = "".join(
            c.get("text", "")
            for item in out
            if item.get("type") == "message"
            for c in (item.get("content") or [])
            if c.get("type") == "output_text"
        )
        reasoning = "".join(
            c.get("text", "")
            for item in out
            if item.get("type") == "reasoning"
            for c in (item.get("content") or [])
        )
        finish = result.get("status")

    return {
        "content": content,
        "reasoning": reasoning or None,
        "finish_reason": finish,
        "model": result.get("model", model),
        "usage": result.get("usage"),
        "timings": result.get("timings"),
    }


@router.post("/chat")
async def chat(body: ChatRequest, request: Request) -> Any:
    """Send the conversation to Halogen and return the assistant reply."""
    state = request.app.state.rt
    run = registry.get_run(body.run_id) if body.run_id else None
    if body.run_id and (run is None or run.status != "running" or not run.engine_url):
        raise HTTPException(status_code=404, detail="selected engine is no longer running")
    client = (
        HalogenClient(run.engine_url)
        if run is not None and run.engine_url
        else state.halogen
    )
    owns_client = client is not state.halogen
    model = (run.model_name if run else None) or state.model_name
    extract = _EXTRACTORS[body.api]
    payload, headers = _build_payload(body.api, body, model)

    if not body.stream:
        try:
            result = await client.post_json(_upstream_path(body.api), payload, headers)
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text[:500] if exc.response is not None else str(exc)
            raise HTTPException(
                status_code=502,
                detail=f"Engine chat failed ({exc.response.status_code}): {detail}",
            ) from exc
        except httpx.HTTPError as exc:
            raise HTTPException(
                status_code=502, detail=f"Engine unreachable: {exc}"
            ) from exc
        finally:
            if owns_client:
                await client.close()
        return _sync_result(body.api, result, model)

    async def event_stream() -> AsyncIterator[str]:
        try:
            async for chunk in client.stream_sse(_upstream_path(body.api), payload, headers):
                reasoning, content, done = extract(chunk)
                if reasoning:
                    yield _sse("reasoning", {"text": reasoning})
                if content:
                    yield _sse("delta", {"text": content})
                if done:
                    yield _sse("done", done)
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text[:500] if exc.response is not None else str(exc)
            yield _sse(
                "error",
                {"message": f"Engine chat failed ({exc.response.status_code}): {detail}"},
            )
        except httpx.HTTPError as exc:
            yield _sse("error", {"message": f"Engine unreachable: {exc}"})
        finally:
            if owns_client:
                await client.close()

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
