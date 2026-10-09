"""LLM-Runner: create, run and store docker-based inference configurations.

A "run configuration" captures the docker command used to launch a
containerized LLM inference server. Configurations are stored locally as a
JSON list (see services/runner_store.py) and can be created, edited, deleted
and selected from the dashboard.

Starting a run launches the configured docker command as a subprocess and
streams its merged stdout/stderr back to the browser as Server-Sent Events:

    event: stdout  data: {"text": "..."}    output chunk
    event: exit    data: {"exit_code": 0}   process terminated
    event: error   data: {"message": "..."} failed to start / run

A run can be stopped explicitly via POST /runner/runs/{run_id}/stop, and is
also terminated automatically when the client disconnects.
"""

from __future__ import annotations

import asyncio
import json
import os
import pty
import uuid
from typing import Any, AsyncIterator, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator

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

# Active runs: run_id -> asyncio subprocess handle.
_active_runs: dict[str, asyncio.subprocess.Process] = {}


def get_store() -> RunnerStore:
    global _store
    if _store is None:
        _store = RunnerStore()
    return _store


class RunnerConfigCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=MAX_NAME_CHARS)
    docker_command: str = Field(..., min_length=1, max_length=MAX_COMMAND_CHARS)
    description: str = Field(default="", max_length=MAX_DESCRIPTION_CHARS)

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
        )
    except RunnerStoreError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/runner/configs/{config_id}")
async def delete_config(config_id: str) -> dict[str, Any]:
    try:
        await get_store().delete(config_id)
    except RunnerStoreError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"deleted": config_id}


# -- Running configurations ------------------------------------------------------


@router.post("/runner/configs/{config_id}/run")
async def run_config(config_id: str) -> StreamingResponse:
    cfg = get_store().get(config_id)
    if cfg is None:
        raise HTTPException(status_code=404, detail="configuration not found")

    run_id = uuid.uuid4().hex[:12]
    command = cfg["docker_command"]

    # Allocate a pseudo-terminal so the command sees a real TTY. This lets
    # `docker run -it ...` (TTY-allocating flags) work from the dashboard;
    # without it Docker aborts with "stdin is not a terminal".
    try:
        master_fd, slave_fd = pty.openpty()
    except OSError as exc:
        raise HTTPException(
            status_code=500, detail=f"failed to allocate pty: {exc}"
        ) from exc

    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
        )
    except Exception as exc:  # noqa: BLE001 -- surface as a stream error
        os.close(master_fd)
        os.close(slave_fd)
        raise HTTPException(
            status_code=502, detail=f"failed to start command: {exc}"
        ) from exc
    # The child holds the slave side; the parent only reads/writes the master.
    os.close(slave_fd)

    _active_runs[run_id] = proc

    # Bridge the PTY master onto an asyncio StreamReader.
    reader = asyncio.StreamReader()
    read_protocol = asyncio.StreamReaderProtocol(reader)
    loop = asyncio.get_running_loop()
    master_file = os.fdopen(master_fd, "rb", buffering=0)
    try:
        await loop.connect_read_pipe(lambda: read_protocol, master_file)
    except Exception as exc:  # noqa: BLE001
        _active_runs.pop(run_id, None)
        await _terminate(proc)
        master_file.close()
        raise HTTPException(
            status_code=502, detail=f"failed to attach pty: {exc}"
        ) from exc

    async def stream() -> AsyncIterator[str]:
        try:
            yield _sse("started", {"run_id": run_id, "command": command})
            while True:
                try:
                    chunk = await reader.read(65536)
                except (OSError, ConnectionResetError):
                    # PTY master raises EIO once the child's terminal closes.
                    break
                if not chunk:
                    break
                text = chunk.decode(errors="replace")
                # A PTY emits CRLF; normalise so the console renders cleanly.
                text = text.replace("\r\n", "\n").replace("\r", "\n")
                yield _sse("stdout", {"text": text})
            exit_code = await proc.wait()
            yield _sse("exit", {"exit_code": exit_code})
        finally:
            _active_runs.pop(run_id, None)
            await _terminate(proc)
            try:
                master_file.close()
            except OSError:
                pass

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/runner/runs/{run_id}/stop")
async def stop_run(run_id: str) -> dict[str, Any]:
    proc = _active_runs.get(run_id)
    if proc is None:
        raise HTTPException(status_code=404, detail="run is not active")
    await _terminate(proc)
    return {"stopped": run_id}
