"""GET/POST /api/v1/config -- runtime settings (polling interval)."""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

router = APIRouter(tags=["config"])


class ConfigUpdate(BaseModel):
    poll_interval_seconds: Optional[float] = Field(default=None, ge=0.5, le=300)


def _current(state) -> dict[str, Any]:
    return {
        "poll_interval_seconds": state.poll_interval,
        "halogen_host": state.halogen.base_url,
    }


@router.get("/config")
async def get_config(request: Request) -> dict[str, Any]:
    return _current(request.app.state.rt)


@router.post("/config")
async def update_config(body: ConfigUpdate, request: Request) -> dict[str, Any]:
    state = request.app.state.rt
    if body.poll_interval_seconds is not None:
        state.poll_interval = body.poll_interval_seconds
        state.wake.set()  # re-poll immediately with the new cadence
    return _current(state)
