"""LLM-Runner: create, run and store docker-based inference configurations.

A "run configuration" captures the docker command used to launch a
containerized LLM inference server. Configurations are stored locally as a
JSON list (see services/runner_store.py) and can be created, edited, deleted
and selected from the dashboard.

The stored command is a **template**: wherever a value should be
variable -- a model directory, a port, a context size -- the configuration
declares a named parameter and the command references it as
``{parameter_name}``::

    -v {models}/weights.hgn:/models/w.hgn:ro -p 0.0.0.0:{port}:{port}

Parameters are edited in the same form as the command, added and removed
freely, and validated on save so no placeholder is left unfilled. At run
time the backend renders the template against the parameter values and
launches the resulting command; a run may also override individual values
without saving them first.

Starting and watching are **separate calls**, because a run is a
server-side resource rather than a property of one browser tab:

    POST /runner/configs/{id}/run        start it, returns JSON
    GET  /runner/active                what is running right now
    GET  /runner/runs/{id}/stream      watch it (SSE)
    POST /runner/runs/{id}/stop        stop it

The stream delivers a ``backlog`` event with everything already produced,
then live chunks::

    event: backlog  data: {"text": "...", "last_seq": 12, ...}  history
    event: stdout   data: {"text": "..."}                       new output
    event: exit     data: {"exit_code": 0}                      process gone

Closing a stream detaches that viewer and nothing else -- the engine keeps
running until something stops it on purpose.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator

from ..services.engine_target import (
    EngineTargetError,
    engine_url,
    port_from_params,
)
from ..services.docker_control import extract_container_name
from ..services.live_service import (
    RuntimeState,
    build_run_snapshot,
    set_engine_target,
)
from ..services.params import (
    MAX_PARAMS,
    ParamError,
    merge_values,
    render_command,
)
from ..services.run_registry import registry, stream_console
from ..services.runner_store import (
    MAX_COMMAND_CHARS,
    MAX_DESCRIPTION_CHARS,
    MAX_NAME_CHARS,
    RunnerStore,
    RunnerStoreError,
)

router = APIRouter(tags=["runner"])

# One store instance per process, created lazily on first use.
_store: Optional[RunnerStore] = None


def get_store() -> RunnerStore:
    global _store
    if _store is None:
        _store = RunnerStore()
    return _store


def _param_value(params: Optional[list[dict[str, Any]]], name: str) -> Optional[str]:
    """Value of one rendered parameter, or None when absent or blank."""
    for param in params or []:
        if param.get("name") != name:
            continue
        value = (param.get("value") or "").strip()
        return value or None
    return None


class RunnerParameter(BaseModel):
    """One named value substituted into the docker command template."""

    name: str = Field(..., min_length=1, max_length=60)
    label: str = Field(default="", max_length=120)
    value: str = Field(default="")


class RunnerConfigCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=MAX_NAME_CHARS)
    docker_command: str = Field(..., min_length=1, max_length=MAX_COMMAND_CHARS)
    description: str = Field(default="", max_length=MAX_DESCRIPTION_CHARS)
    parameters: list[RunnerParameter] = Field(default_factory=list, max_length=MAX_PARAMS)

    @field_validator("name", "docker_command")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must contain at least one non-whitespace character")
        return value


class RunnerConfigUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=MAX_NAME_CHARS)
    docker_command: Optional[str] = Field(
        default=None, min_length=1, max_length=MAX_COMMAND_CHARS
    )
    description: Optional[str] = Field(default=None, max_length=MAX_DESCRIPTION_CHARS)
    # Omitted leaves the parameter list untouched; [] clears it.
    parameters: Optional[list[RunnerParameter]] = Field(
        default=None, max_length=MAX_PARAMS
    )


def _sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def _terminate(proc: asyncio.subprocess.Process) -> None:
    """Graceful SIGTERM, then SIGKILL after a short grace period."""
    if proc.returncode is not None:
        return
    try:
        proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
    except ProcessLookupError:
        pass


async def halt_run(run: ActiveRun) -> str:
    """Stop a run, taking the container down with it.

    Killing the `docker run` client is not enough -- with `-it` attached to
    a PTY the container outlives its client -- so the container is stopped
    through docker first, then the client process is reaped.

    Idempotent: whichever path reaches it first (an explicit stop request,
    or the stream tearing down after a client disconnect) does the work,
    and later calls return the same result. Returns the method that did it.
    """
    async with run.stop_lock:
        if run.halted:
            return run.halt_method

        method = "already_exited"
        if run.proc.returncode is None:
            if run.container_name:
                if await stop_container(
                    run.container_name, timeout=DEFAULT_STOP_TIMEOUT
                ):
                    method = "docker_stop"
                elif await kill_container(run.container_name):
                    method = "docker_kill"
                else:
                    method = "process"
            else:
                method = "process"

            # Whether or not docker cooperated, make sure the local client
            # is gone too -- a lingering `docker run` would keep the PTY
            # and its slot in the table alive.
            await _terminate(run.proc)

        run.halted = True
        run.halt_method = method
        return method


# -- Configuration CRUD --------------------------------------------------------


@router.get("/runner/configs")
async def list_configs() -> dict[str, Any]:
    return {"configs": get_store().list()}


@router.get("/runner/configs/{config_id}")
async def get_config(config_id: str) -> dict[str, Any]:
    cfg = get_store().get(config_id)
    if cfg is None:
        raise HTTPException(status_code=404, detail="configuration not found")
    return cfg


@router.post("/runner/configs")
async def create_config(body: RunnerConfigCreate) -> dict[str, Any]:
    try:
        return await get_store().create(
            name=body.name,
            docker_command=body.docker_command,
            description=body.description,
            parameters=[p.model_dump() for p in body.parameters],
        )
    except RunnerStoreError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.put("/runner/configs/{config_id}")
async def update_config(config_id: str, body: RunnerConfigUpdate) -> dict[str, Any]:
    try:
        return await get_store().update(
            config_id,
            name=body.name,
            docker_command=body.docker_command,
            description=body.description,
            parameters=(
                [p.model_dump() for p in body.parameters]
                if body.parameters is not None
                else None
            ),
        )
    except RunnerStoreError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/runner/configs/{config_id}")
async def delete_config(config_id: str) -> dict[str, Any]:
    # A running engine was launched from this configuration and is still
    # holding its port and GPU. Deleting the config would leave a live
    # container with nothing describing it -- stop the run first.
    active = next(
        (
            run
            for run in registry.get_running()
            if run.config_id == config_id
        ),
        None,
    )
    if active is not None:
        raise HTTPException(
            status_code=409,
            detail=(
                f"{active.config_name or 'this configuration'} is currently running. "
                "Stop it before deleting."
            ),
        )
    try:
        await get_store().delete(config_id)
    except RunnerStoreError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"deleted": config_id}


# -- Running configurations ------------------------------------------------------


class RunRequest(BaseModel):
    """Optional run-time overrides for the configuration's parameters.

    Keys must be parameters the configuration already declares; the values
    here win over the stored ones for this run only and are not persisted.
    """

    values: Optional[dict[str, str]] = Field(default=None)


class PreviewRequest(BaseModel):
    """Command template + parameters to render without saving or running."""

    docker_command: str = Field(..., min_length=1, max_length=MAX_COMMAND_CHARS)
    parameters: list[RunnerParameter] = Field(default_factory=list, max_length=MAX_PARAMS)


@router.post("/runner/preview")
async def preview_command(body: PreviewRequest) -> dict[str, Any]:
    """Render a docker command template against its parameters.

    Lets the UI show the exact command a run would launch, using the same
    code path the run itself uses.
    """
    try:
        params = [p.model_dump() for p in body.parameters]
        command = render_command(body.docker_command, params)
    except ParamError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"command": command}


class ConfigNotFound(Exception):
    """No stored configuration with that id."""


async def launch_config(
    state: RuntimeState,
    config_id: str,
    values: Optional[dict[str, str]] = None,
) -> dict[str, Any]:
    """Start a configuration's engine and repoint the dashboard at it.

    Shared by ``POST /runner/configs/{id}/run`` and the agent's
    ``start_engine`` tool so both paths render the template, launch the
    container, and follow the new engine identically. Raises
    ``ConfigNotFound`` for an unknown id, ``ValueError`` for a bad
    template, and ``RuntimeError`` for a launch failure; the HTTP wrapper
    maps those onto status codes.
    """
    cfg = get_store().get(config_id)
    if cfg is None:
        raise ConfigNotFound(config_id)

    # Reconstruct the concrete command from the stored template and the
    # parameter values (request overrides take precedence over stored).
    try:
        params = merge_values(cfg.get("parameters") or [], values)
        command = render_command(cfg["docker_command"], params)
    except ParamError as exc:
        raise ValueError(str(exc)) from exc
    if not extract_container_name(command):
        raise ValueError(
            "Docker command must include a unique --name so Strixper can track "
            "and stop the engine container."
        )

    try:
        run = await registry.start(
            command=command,
            config_id=config_id,
            config_name=cfg.get("name", ""),
        )
    except Exception as exc:  # noqa: BLE001 -- surface a clean start failure
        raise RuntimeError(f"failed to start command: {exc}") from exc

    # A configuration that declares a `port` is a network server, so the
    # dashboard follows it: repoint the live-status client at the engine we
    # just launched. Done *after* the start succeeds, so a run that failed
    # to launch cannot leave the dashboard pointed at nothing.
    engine_port = port_from_params(params)
    if engine_port is not None:
        run.engine_url = engine_url(engine_port)
        run.model_name = _param_value(params, "served_model_name") or _param_value(
            params, "model_file"
        )
        try:
            snapshot = await set_engine_target(
                state,
                run.engine_url,
                config_id,
                cfg.get("name"),
                # The chat needs the model id the engine actually serves.
                # Take it from the config; /health overrides it when the
                # engine reports one.
                model_name=_param_value(params, "served_model_name"),
            )
            run.engine_url = snapshot["base_url"]
        except EngineTargetError as exc:
            # The run itself is fine; only the switch failed. Say so rather
            # than leaving the user guessing why the live view is stale.
            run.engine_switch_error = str(exc)
    else:
        run.model_name = _param_value(params, "served_model_name") or _param_value(
            params, "model_file"
        )

    return run.summary()


@router.post("/runner/configs/{config_id}/run")
async def run_config(
    config_id: str,
    request: Request,
    body: Optional[RunRequest] = None,
) -> dict[str, Any]:
    """Start a configuration's engine and return immediately.

    Starting and watching are separate calls: this launches the run, and
    ``GET /runner/runs/{run_id}/stream`` delivers its console. Separating
    them is what lets a browser that arrives later attach to a run it did
    not start -- the run belongs to the backend, not to the request that
    launched it.
    """
    try:
        return await launch_config(
            request.app.state.rt, config_id, body.values if body else None
        )
    except ConfigNotFound:
        raise HTTPException(status_code=404, detail="configuration not found")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/runner/active")
async def get_active_run() -> dict[str, Any]:
    """Compatibility endpoint: the most recently started running engine."""
    run = registry.get_active()
    return {"active": run.summary() if run else None}


@router.get("/runner/runs")
async def get_runs() -> dict[str, Any]:
    """List each currently running engine and its independent console identity."""
    return {"runs": [run.summary() for run in registry.get_running()]}


@router.get("/runner/runs/{run_id}/live-status")
async def get_run_live_status(run_id: str, request: Request) -> dict[str, Any]:
    run = registry.get_run(run_id)
    if run is None or run.status != "running" or not run.engine_url:
        raise HTTPException(status_code=404, detail="running engine not found")
    snapshot = await build_run_snapshot(request.app.state.rt, run_id, run.engine_url)
    model = snapshot.get("halogen", {}).get("model")
    if model:
        run.model_name = model
    return {
        **snapshot,
        "run": run.summary(),
    }


@router.get("/runner/runs/{run_id}/stream")
async def watch_run(run_id: str) -> StreamingResponse:
    """Attach a viewer to a live run: backlog first, then the live tail.

    Disconnecting detaches the viewer and nothing else. The run keeps
    going -- stopping it is the Stop button's job, not the connection's.
    """
    run = registry.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")

    return StreamingResponse(
        stream_console(run),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/runner/runs/{run_id}/stop")
async def stop_run(run_id: str) -> dict[str, Any]:
    run = registry.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    method = await registry.stop(run_id)
    return {"stopped": run_id, "method": method}


@router.post("/runner/stop-all")
async def stop_all_runs() -> dict[str, Any]:
    results = await registry.stop_all()
    errors = {run_id: value for run_id, value in results.items() if value.startswith("error:")}
    if errors:
        raise HTTPException(status_code=502, detail={"stopped": results, "errors": errors})
    return {"stopped": results}
