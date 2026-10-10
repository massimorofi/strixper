"""Halogen Strix Halo Operations Dashboard -- FastAPI backend.

Serves the aggregated live-status API, the on-demand tuning audit, and the
built frontend (when frontend/dist exists).
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .config import settings
from .routers import agent, chat, config_routes, live, runner, stats_routes, tokens, tuning
from .services.live_service import RuntimeState, managed_poller, set_engine_target
from .services.mcp_manager import MCPManager
from .services.run_registry import registry, reconcile_loop

logger = logging.getLogger(__name__)


def _model_for_config(config_id: str) -> Optional[str]:
    """The best model identifier declared by a stored engine configuration."""
    cfg = runner.get_store().get(config_id) or {}
    preferred = ("served_model_name", "model", "model_file")
    for param in cfg.get("parameters") or []:
        if param.get("name") in preferred:
            value = (param.get("value") or "").strip()
            if value:
                return value
    return None


def _make_adopted_hook(state: RuntimeState):
    """Build the callback that points the dashboard at a newly adopted engine.

    Adoption is not a one-shot: an engine can appear long after startup (it
    was still pulling its image when this backend came up, say), so the
    same "we now have a run, follow it" step has to run from the reconcile
    loop as well as from the initial scan.
    """

    async def on_adopted(run) -> None:
        if not run.engine_url:
            return
        await set_engine_target(
            state,
            run.engine_url,
            run.config_id,
            run.config_name,
            model_name=_model_for_config(run.config_id),
        )
        run.model_name = state.model_name

    return on_adopted


@asynccontextmanager
async def lifespan(app: FastAPI):
    state = RuntimeState()
    app.state.rt = state
    mcp_manager = MCPManager(Path(settings.data_dir) / "mcp_servers.json")
    app.state.mcp = mcp_manager
    if mcp_manager.manager_status()["config_error"]:
        logger.error("MCP configuration could not be loaded; management requires repair.")
    on_adopted = _make_adopted_hook(state)

    # A docker container outlives the process that started it, so an engine
    # can be up before this backend is. Adopt it rather than showing an
    # empty console: the run becomes visible, watchable and stoppable, and
    # the dashboard and chat are pointed at it.
    try:
        await registry.discover(runner.get_store().list(), on_adopted=on_adopted)
    except Exception as exc:  # noqa: BLE001 -- never block startup on this
        logger.warning("engine adoption failed: %s", exc)

    async with managed_poller(state):
        reconcile = asyncio.create_task(
            reconcile_loop(registry, lambda: runner.get_store().list(), on_adopted)
        )
        try:
            yield
        finally:
            reconcile.cancel()
            try:
                await reconcile
            except asyncio.CancelledError:
                pass
            try:
                await mcp_manager.shutdown()
            finally:
                await registry.shutdown()


app = FastAPI(
    title="Halogen Strix Halo Operations Dashboard",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(live.router, prefix="/api/v1")
app.include_router(tuning.router, prefix="/api/v1")
app.include_router(config_routes.router, prefix="/api/v1")
app.include_router(stats_routes.router, prefix="/api/v1")
app.include_router(tokens.router, prefix="/api/v1")
app.include_router(chat.router, prefix="/api/v1")
app.include_router(runner.router, prefix="/api/v1")
app.include_router(agent.router, prefix="/api/v1")


@app.get("/api/v1/healthz")
async def healthz() -> dict[str, str]:
    """Backend's own liveness (independent of Halogen)."""
    return {"status": "ok"}


# Serve the built frontend in production (after `npm run build`).
_DIST = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"
if _DIST.is_dir():
    app.mount("/", StaticFiles(directory=str(_DIST), html=True), name="static")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=settings.bind_host,
        port=settings.bind_port,
        reload=False,
    )
