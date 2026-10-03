"""GET /api/v1/tuning-check -- on-demand Strix Halo compliance audit."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from ..services.tuning_checker import get_system_info, run_all_checks

router = APIRouter(tags=["tuning"])


@router.get("/tuning-check")
async def tuning_check() -> dict[str, Any]:
    return await run_all_checks()


@router.get("/system-info")
async def system_info() -> dict[str, Any]:
    return await get_system_info()
