"""GET/POST /api/v1/config -- runtime settings (polling interval, engine address)."""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from ..services.engine_target import EngineTargetError
from ..services.live_service import set_engine_target

router = APIRouter(tags=["config"])


class ConfigUpdate(BaseModel):
    poll_interval_seconds: Optional[float] = Field(default=None, ge=0.5, le=300)
    # Point the dashboard at a different engine. This normally happens by
    # itself when a run starts; set it directly to follow an engine that
    # was launched outside the LLM-Runner.
    halogen_host: Optional[str] = Field(default=None, max_length=300)


def _current(state) -> dict[str, Any]:
    return {
        "poll_interval_seconds": state.poll_interval,
        "halogen_host": state.halogen.base_url,
        "engine_config_id": state.engine_config_id,
        "engine_config_name": state.engine_config_name,
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
    if body.halogen_host is not None:
        try:
            # Clears the "came from this config" association: a hand-set
            # address has no originating configuration.
            await set_engine_target(state, body.halogen_host)
        except EngineTargetError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _current(state)
