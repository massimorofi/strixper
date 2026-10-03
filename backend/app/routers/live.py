"""GET /api/v1/live-status -- aggregated Halogen + hardware snapshot."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from ..services.live_service import build_live_snapshot

router = APIRouter(tags=["live"])


@router.get("/live-status")
async def live_status(request: Request) -> dict[str, Any]:
    state = request.app.state.rt
    if state.latest is None:
        state.latest = await build_live_snapshot(state)
    return state.latest
