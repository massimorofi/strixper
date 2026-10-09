"""Halogen Strix Halo Operations Dashboard -- FastAPI backend.

Serves the aggregated live-status API, the on-demand tuning audit, and the
built frontend (when frontend/dist exists).
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .config import settings
from .routers import chat, config_routes, live, runner, stats_routes, tokens, tuning
from .services.live_service import RuntimeState, managed_poller


@asynccontextmanager
async def lifespan(app: FastAPI):
    state = RuntimeState()
    app.state.rt = state
    async with managed_poller(state):
        yield


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
