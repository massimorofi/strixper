"""POST /api/v1/stats/reset -- reset the session statistics to zero."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

router = APIRouter(tags=["stats"])


@router.post("/stats/reset")
async def reset_stats(request: Request) -> dict[str, Any]:
    """Zero out the running min/avg/max accumulators and restart the clock."""
    state = request.app.state.rt
    state.stats.reset()
    return state.stats.snapshot()
