"""POST /api/v1/count-tokens -- count input tokens for a prompt, no generation.

Proxies Halogen's Anthropic-compatible /v1/messages/count_tokens endpoint so the
dashboard can report the exact token cost of a prompt (including the chat
template overhead) before it is ever sent for generation.
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

import httpx

router = APIRouter(tags=["tokens"])

MAX_TEXT_CHARS = 2_000_000


class CountTokensRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=MAX_TEXT_CHARS)
    system: Optional[str] = Field(default=None, max_length=MAX_TEXT_CHARS)

    @field_validator("text")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("text must contain at least one non-whitespace character")
        return value


@router.post("/count-tokens")
async def count_tokens(body: CountTokensRequest, request: Request) -> dict[str, Any]:
    """Return the token count for ``text`` (+ optional ``system`` prompt)."""
    state = request.app.state.rt
    model = state.model_name

    messages = [{"role": "user", "content": body.text}]
    try:
        result = await state.halogen.count_tokens(
            messages=messages, model=model, system=body.system
        )
    except httpx.HTTPStatusError as exc:
        detail = exc.response.text[:500] if exc.response is not None else str(exc)
        raise HTTPException(
            status_code=502,
            detail=f"Halogen count_tokens failed ({exc.response.status_code}): {detail}",
        ) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Halogen unreachable: {exc}") from exc

    input_tokens = result.get("input_tokens")
    if not isinstance(input_tokens, int):
        raise HTTPException(
            status_code=502,
            detail=f"Halogen returned an unexpected payload: {result!r}",
        )

    return {
        "input_tokens": input_tokens,
        "model": model,
        "chars": len(body.text),
        "system_chars": len(body.system) if body.system else 0,
    }
