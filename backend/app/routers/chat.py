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

import asyncio
import json
import logging
import time
from typing import Any, AsyncIterator, Literal, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator, model_validator

import httpx
from ..config import settings
from ..services.halogen_client import HalogenClient
from ..services.run_registry import registry
from ..services import vision_helper
from ..services.chat_retry import (
    call_with_retry,
    describe_failure,
    is_retryable,
    retry_budget,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["chat"])

MAX_MESSAGE_CHARS = 100_000
MAX_MESSAGES = 200
# Largest text file inlined into a plain (non-agentic) chat turn.
MAX_INLINE_FILE_CHARS = 100_000

# Anthropic Messages requires this header on every request.
ANTHROPIC_VERSION = "2023-06-01"

API_STYLES = ("chat", "messages", "responses", "completions")

# API styles that have no content-part concept and therefore cannot carry images.
TEXT_ONLY_API = "completions"

# Cached per engine URL: (fetched_at, enabled, max_pixels, size_multiple).
_VISION_CACHE: dict[str, tuple[float, bool, Optional[int], Optional[int]]] = {}
VISION_CACHE_TTL = 30.0


class Attachment(BaseModel):
    type: Literal["image", "file"] = "image"
    file_id: str
    name: str = ""
    size: int = 0
    mime_type: str = "application/octet-stream"
    workspace_path: Optional[str] = None


class ChatMessage(BaseModel):
    role: str = Field(..., pattern="^(user|assistant|system)$")
    # ``min_length=1`` used to live here. It made an attachment-only turn --
    # drag a picture in with no text, the common gesture -- fail validation
    # before reaching any handler. The model-level check below replaces it
    # with the rule that actually matters: the turn must contain something.
    content: str = Field(default="", max_length=MAX_MESSAGE_CHARS)
    attachments: list[Attachment] = Field(default_factory=list)

    @model_validator(mode="after")
    def _something_present(self) -> "ChatMessage":
        if not self.content.strip() and not self.attachments:
            raise ValueError("message must have text content or at least one attachment")
        return self


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
            # The per-message model validator already rejects a turn that is
            # empty in both text and attachments; this one only guards
            # against whitespace-only *text* on a text-only turn.
            if not m.content.strip() and not m.attachments:
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


def _last_user_content(messages: list[dict[str, Any]]) -> Any:
    """Most recent user turn, preserving the content-part array if present."""
    for m in reversed(messages):
        if m["role"] == "user":
            return m["content"]
    return messages[-1]["content"]


async def _vision_caps(client: HalogenClient, engine_key: str) -> tuple[bool, Optional[int], Optional[int]]:
    """``(enabled, max_pixels, size_multiple)`` for *client*, briefly cached.

    The engine advertises its real vision limits in ``/health``. Reading
    them rather than hard-coding means a build with a different tower gets
    its own constraints. Cached because ``/health`` is polled anyway and a
    per-turn HTTP round trip in the request path is not worth it.
    """
    cached = _VISION_CACHE.get(engine_key)
    if cached is not None and (time.time() - cached[0]) < VISION_CACHE_TTL:
        return cached[1], cached[2], cached[3]
    try:
        health = await client.get_health()
    except Exception:
        # An unreachable /health must not be read as "no vision": that
        # would break image chat on a transient blip. Assume permissive
        # and let the upstream report any real problem.
        return True, None, None
    enabled = vision_helper.vision_enabled(health)
    max_pixels, size_multiple = vision_helper.vision_constraints(health)
    _VISION_CACHE[engine_key] = (time.time(), enabled, max_pixels, size_multiple)
    return enabled, max_pixels, size_multiple


def _attachment_text(attachment: Attachment) -> str:
    """Inline a non-image attachment as text for a plain chat turn."""
    from ..services.file_store import FileStore, FileStoreError

    store: FileStore = _get_store()
    rel = attachment.workspace_path
    if not rel:
        # The client may only know the file id; the record holds the real path.
        try:
            rel = store.require(attachment.file_id).path
        except FileStoreError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        text = store.read_text(rel)
    except FileStoreError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    header = f"[Attached file: {attachment.name}]"
    body = text["content"]
    if len(body) > MAX_INLINE_FILE_CHARS:
        body = body[:MAX_INLINE_FILE_CHARS]
    return f"{header}\n{body}"


def _get_store():
    """The process-wide file store (imported lazily to avoid a cycle at import)."""
    from ..services.file_store import get_store

    return get_store()


def _image_bytes(attachment: Attachment) -> bytes:
    store = _get_store()
    try:
        if attachment.file_id:
            return store.read_bytes(attachment.file_id)
        raise FileStoreNotFound(attachment.name)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail=f"could not read the attached image {attachment.name!r}: {exc}",
        ) from exc


class FileStoreNotFound(Exception):
    """Internal marker: the attachment id is unknown."""


def _validate_request_images(
    body: ChatRequest,
    enabled: bool,
    max_pixels: Optional[int],
    size_multiple: Optional[int],
) -> None:
    """Check every image attachment against engine and policy limits.

    Runs before anything is sent upstream so the user gets one clear error
    naming the offending file rather than an opaque 400 from the engine.
    """
    from ..services.vision_helper import ImageValidationError

    if body.api == TEXT_ONLY_API:
        total = sum(
            1 for m in body.messages for a in m.attachments if a.type == "image"
        )
        if total:
            raise HTTPException(
                status_code=422,
                detail=(
                    "images cannot be sent with api=\"completions\" -- that style "
                    "is raw text completion and has no image support. Use "
                    "api=\"chat\", \"messages\" or \"responses\"."
                ),
            )
        return

    if not enabled:
        raise HTTPException(
            status_code=400,
            detail=(
                "the loaded engine has no vision tower, so images cannot be "
                "analysed. Select a vision-capable run (a vision tower must be "
                "mounted) or send text only."
            ),
        )

    max_bytes = settings.max_image_size_mb * 1024 * 1024
    for index, message in enumerate(body.messages):
        images = [a for a in message.attachments if a.type == "image"]
        if not images:
            continue
        if len(images) > settings.max_images_per_message:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"message {index + 1} has {len(images)} images; at most "
                    f"{settings.max_images_per_message} per message"
                ),
            )
        for attachment in images:
            data = _image_bytes(attachment)
            label = attachment.name or attachment.file_id
            try:
                vision_helper.validate_image(
                    data,
                    allowed_types=settings.allowed_image_types,
                    max_pixels=max_pixels,
                    size_multiple=size_multiple,
                    max_bytes=max_bytes,
                )
            except ImageValidationError as exc:
                raise HTTPException(
                    status_code=422, detail=f"{label}: {exc}"
                ) from exc


def _prepare_messages(
    body: ChatRequest,
    api: str,
    vision_enabled_flag: bool,
    max_pixels: Optional[int] = None,
    size_multiple: Optional[int] = None,
) -> list[dict[str, Any]]:
    """Turn the request messages into upstream content.

    Text-only turns keep the plain string form so nothing changes on the
    wire for existing traffic. A turn carrying attachments becomes a
    content-part array in the vocabulary of the chosen API style.
    """
    prepared: list[dict[str, Any]] = []
    for m in body.messages:
        text = m.content
        images = [a for a in m.attachments if a.type == "image"]
        files = [a for a in m.attachments if a.type != "image"]

        if not m.attachments:
            prepared.append({"role": m.role, "content": text})
            continue

        # Non-image attachments are inlined as text for every style; the
        # engine has no file concept either.
        extra_text = "\n\n".join(_attachment_text(a) for a in files)
        combined = "\n\n".join(p for p in (text, extra_text) if p.strip())

        if not images:
            prepared.append({"role": m.role, "content": combined})
            continue

        if not vision_enabled_flag:
            raise HTTPException(
                status_code=400,
                detail=(
                    "the loaded engine has no vision tower, so images cannot be "
                    "analysed. Select a vision-capable run (a vision tower must "
                    "be mounted) or send text only."
                ),
            )

        if api == TEXT_ONLY_API:
            raise HTTPException(
                status_code=422,
                detail=(
                    "images cannot be sent with api=\"completions\" -- that style "
                    "is raw text completion and has no image support. Use "
                    "api=\"chat\", \"messages\" or \"responses\"."
                ),
            )

        parts: list[dict[str, Any]] = []
        if combined.strip():
            parts.append({"type": "text", "text": combined})
        for img in images:
            data = _image_bytes(img)
            # Magic bytes are authoritative: the declared type is what a
            # client guessed, and a wrong one breaks the engine mid-turn.
            mime = (
                vision_helper.sniff_mime(data)
                or (img.mime_type if img.mime_type != "application/octet-stream" else None)
                or "image/png"
            )
            if api == "messages":
                parts.append(vision_helper.anthropic_image_part(data, mime))
            elif api == "responses":
                parts.append(vision_helper.responses_image_part(data, mime))
            else:
                parts.append(vision_helper.openai_image_part(data, mime))
        prepared.append({"role": m.role, "content": parts})
    return prepared


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
    api: str, body: ChatRequest, model: str, messages: Optional[list[dict[str, Any]]] = None
) -> tuple[dict[str, Any], dict[str, str]]:
    """Return (json payload, extra headers) for the chosen upstream API.

    *messages* is the prepared (attachment-expanded) form. When omitted the
    plain request messages are used, which keeps this callable usable for
    text-only traffic.
    """
    if messages is None:
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
        # Responses API takes a single `input` (the latest user turn). With
        # attachments that turn is a content-part array rather than a
        # string; both are valid `input` shapes.
        payload = {
            "model": model,
            "input": _last_user_content(messages),
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

    has_images = any(a.type == "image" for m in body.messages for a in m.attachments)
    enabled, max_pixels, size_multiple = await _vision_caps(client, client.base_url)
    if has_images:
        _validate_request_images(body, enabled, max_pixels, size_multiple)
    prepared = _prepare_messages(body, body.api, enabled)
    payload, headers = _build_payload(body.api, body, model, prepared)

    if not body.stream:
        try:
            result = await call_with_retry(
                lambda: client.post_json(_upstream_path(body.api), payload, headers)
            )
        except Exception as exc:  # noqa: BLE001 - re-raised as HTTP 502
            if owns_client:
                await client.close()
            if isinstance(exc, HTTPException):
                raise
            attempts = retry_budget()[0]
            raise HTTPException(
                status_code=502, detail=describe_failure(exc, attempts)
            ) from exc
        if owns_client:
            await client.close()
        return _sync_result(body.api, result, model)

    total_attempts, pause = retry_budget()

    async def event_stream() -> AsyncIterator[str]:
        # A streaming reply cannot be restarted once text has reached the
        # browser: the retry would duplicate the answer. So the retry loop
        # only covers failures that happen before the first chunk is shown
        # (engine down, connection reset, DNS blip), which is where the
        # "network error" bubble actually comes from. Once text is on
        # screen, the error is surfaced instead of replayed.
        emitted = False
        for attempt in range(1, total_attempts + 1):
            try:
                async for chunk in client.stream_sse(
                    _upstream_path(body.api), payload, headers
                ):
                    reasoning, content, done = extract(chunk)
                    if reasoning:
                        emitted = True
                        yield _sse("reasoning", {"text": reasoning})
                    if content:
                        emitted = True
                        yield _sse("delta", {"text": content})
                    if done:
                        yield _sse("done", done)
                break
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - classified below
                last_error = describe_failure(exc, total_attempts)
                if attempt >= total_attempts or emitted or not is_retryable(exc):
                    logger.warning(
                        "Chat stream failed (attempt %d/%d, emitted=%s): %s",
                        attempt, total_attempts, emitted, exc,
                    )
                    yield _sse("error", {"message": last_error})
                    break
                logger.warning(
                    "Chat stream failed on attempt %d/%d (%s); retrying in %.0fs",
                    attempt, total_attempts, exc, pause,
                )
                if pause:
                    await asyncio.sleep(pause)
                continue
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
