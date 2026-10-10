"""The live LLM-Runner run: a server-side resource, not a browser connection.

A run used to be a property of one HTTP stream -- its reader lived in the
SSE generator, and the generator's teardown stopped the container. That
made two basic things impossible: a second browser could not see a run that
another browser had started, and closing a browser killed an engine the
user still wanted up.

So the run is owned here instead. A background pump reads the container's
output from the moment the run starts, keeps a rolling backlog, and fans
the stream out to any number of viewers. Viewers come and go freely; the
run does not care. It is stopped only by an explicit stop.

Two ways a run comes into existence:

* **started** -- we launch ``docker run`` ourselves, under a PTY, and read
  the PTY master. This is preferred because image-pull progress and
  daemon-side flag errors reach the client's stderr and *never* appear in
  ``docker logs`` -- exactly the output an operator needs when a start
  goes wrong.
* **adopted** -- a container is already running (started before this
  backend process existed, or by another tool). We have no terminal on it,
  so we follow ``docker logs -f`` instead. Same interface to the caller,
  same stop path.

Concurrency note: console-buffer mutations are synchronous on the event
loop, so they need no locks. Start/adopt setup is serialized by the registry
lock; stopping uses a separate lock per run, allowing unrelated runs to stop
independently.
"""

from __future__ import annotations

import asyncio
import collections
import json
import logging
import os
import pty
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Awaitable, Callable, Optional

from .docker_control import (
    DEFAULT_STOP_TIMEOUT,
    container_is_running,
    extract_container_name,
    inspect_started_at,
    kill_container,
    list_running_names,
    stop_container,
)
from .engine_target import engine_url, port_from_params
from .params import ParamError, merge_values, render_command

logger = logging.getLogger(__name__)

# How much console history to keep for late-joining viewers, capped by
# bytes rather than line count so a chatty engine cannot inflate it.
# 2 MiB is roughly eight hours of a busy engine's access logging.
# Override with RUNNER_CONSOLE_BYTES.
CONSOLE_MAX_BYTES = int(os.getenv("RUNNER_CONSOLE_BYTES", str(2 * 1024 * 1024)))

# Per-viewer queue. A viewer that falls behind drops its own oldest output
# rather than being evicted: a throttled background tab (Chrome throttles
# them to about one timer tick a second) should still end up with the
# console, just missing some middle. Losing a viewer outright would mean a
# browser that lagged once never sees the rest of the run.
SUBSCRIBER_QUEUE = int(os.getenv("RUNNER_SUBSCRIBER_QUEUE", "2000"))

# How long to wait for a stopped container's port/GPU to actually be
# released before starting the next one anyway.
RELEASE_WAIT_SECONDS = float(os.getenv("RUNNER_RELEASE_WAIT", "20"))

# How long a stop waits for the pump to record the exit itself.
STOP_SETTLE_SECONDS = float(os.getenv("RUNNER_STOP_SETTLE", "15"))

# Lines of history an adopted run pulls from `docker logs`.
ADOPT_LOG_TAIL = int(os.getenv("RUNNER_ADOPT_TAIL", "2000"))

# Seconds between keepalive frames on an idle console stream, so a proxy
# with an idle timeout does not close a perfectly healthy connection.
SSE_HEARTBEAT_SECONDS = float(os.getenv("RUNNER_SSE_HEARTBEAT", "15"))

# How often to look for an engine container we are not tracking.
RECONCILE_SECONDS = float(os.getenv("RUNNER_RECONCILE", "20"))


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _normalise(raw: bytes) -> str:
    """Decode a chunk and undo the PTY's CRLF line endings."""
    text = raw.decode(errors="replace")
    return text.replace("\r\n", "\n").replace("\r", "\n")


class ConsoleBuffer:
    """Rolling console text, fanned out to any number of live viewers.

    Holds the last ``max_bytes`` of output. Late joiners read the snapshot;
    existing viewers get each chunk as it lands.
    """

    __slots__ = (
        "max_bytes",
        "chunks",
        "dropped_chunks",
        "dropped_bytes",
        "closed",
        "_bytes",
        "_subscribers",
    )

    def __init__(self, max_bytes: int = CONSOLE_MAX_BYTES) -> None:
        self.max_bytes = max_bytes
        self.chunks: collections.deque = collections.deque()
        self._bytes = 0
        self.dropped_chunks = 0
        self.dropped_bytes = 0
        self.closed = False
        self._subscribers: set[asyncio.Queue] = set()

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=SUBSCRIBER_QUEUE)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subscribers.discard(q)

    def append(self, text: str) -> None:
        if not text:
            return
        size = len(text)
        self.chunks.append(text)
        self._bytes += size
        # Keep at least one chunk so the console is never empty just
        # because a single read exceeded the budget.
        while self._bytes > self.max_bytes and len(self.chunks) > 1:
            old = self.chunks.popleft()
            self._bytes -= len(old)
            self.dropped_chunks += 1
            self.dropped_bytes += len(old)
        self._broadcast(text)

    def snapshot(self) -> str:
        return "".join(self.chunks)

    def _broadcast(self, text: str) -> None:
        """Push to every viewer. A full queue sheds its own oldest chunk."""
        for q in list(self._subscribers):
            try:
                q.put_nowait(text)
            except asyncio.QueueFull:
                try:
                    stale = q.get_nowait()
                    self.dropped_chunks += 1
                    self.dropped_bytes += len(stale)
                except asyncio.QueueEmpty:
                    pass
                try:
                    q.put_nowait(text)
                except asyncio.QueueFull:
                    pass

    def close(self) -> None:
        """Wake every viewer with a sentinel so their streams end cleanly."""
        if self.closed:
            return
        self.closed = True
        for q in list(self._subscribers):
            try:
                q.put_nowait(None)
            except asyncio.QueueFull:
                # Viewer is wedged; a None won't fit either. Its own read
                # timeout and the run's exit state cover it.
                pass


@dataclass
class ActiveRun:
    """One live engine container, plus everything needed to watch and stop it."""

    run_id: str
    config_id: str
    config_name: str
    container_name: Optional[str]
    command: str
    started_at: str
    # "running" until the pump sees EOF, then "exited".
    status: str = "running"
    exit_code: Optional[int] = None
    # True when this backend attached to a pre-existing container rather
    # than launching it.
    adopted: bool = False
    engine_url: Optional[str] = None
    model_name: Optional[str] = None
    engine_switch_error: str = ""
    console: ConsoleBuffer = field(default_factory=ConsoleBuffer)

    proc: Optional[asyncio.subprocess.Process] = None
    reader: Any = None
    pump_task: Optional[asyncio.Task] = None
    master_file: Any = None
    stop_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    _halted: bool = False
    _halt_method: str = ""
    # True once we switched from our own PTY to `docker logs -f`, which
    # happens when a detached (`-d`) run's client exits but its container
    # carries on.
    _following_logs: bool = False

    def summary(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "config_id": self.config_id,
            "config_name": self.config_name,
            "container_name": self.container_name,
            "engine_url": self.engine_url,
            "model_name": self.model_name,
            "started_at": self.started_at,
            "status": self.status,
            "exit_code": self.exit_code,
            "adopted": self.adopted,
            "engine_switch_error": self.engine_switch_error or None,
        }

    async def stop(self) -> str:
        """Stop the container, then let the pump record that it ended.

        Stopping goes through docker rather than signal-sending the client:
        with `-it` on a PTY the container does not follow its client out.
        The pump -- not this method -- decides the run is over, so the
        exit is recorded the same way whether it came from a stop, a
        crash, or the engine quitting on its own.
        """
        async with self.stop_lock:
            if self._halted:
                return self._halt_method

            method = "already_exited"
            if self.proc is not None and self.proc.returncode is None:
                if self.container_name:
                    if await stop_container(
                        self.container_name, timeout=DEFAULT_STOP_TIMEOUT
                    ):
                        method = "docker_stop"
                    elif await kill_container(self.container_name):
                        method = "docker_kill"
                    else:
                        method = "process"
                else:
                    method = "process"
                # Whether or not docker cooperated, make sure the local
                # client is gone too -- a lingering `docker run` would
                # keep the PTY and its slot alive.
                await _terminate(self.proc)

            self._halted = True
            self._halt_method = method

        # Bounded wait for the pump to see EOF. Shielded so a cancelled
        # HTTP request cannot kill the pump and leave the run stuck
        # "running"; if the pump is wedged, settle it here.
        if self.pump_task is not None:
            try:
                await asyncio.wait_for(
                    asyncio.shield(self.pump_task), timeout=STOP_SETTLE_SECONDS
                )
            except (asyncio.TimeoutError, asyncio.CancelledError):
                await _mark_exited(self)

        return method


async def _terminate(proc: Optional[asyncio.subprocess.Process]) -> None:
    """Graceful SIGTERM, then SIGKILL after a short grace period."""
    if proc is None or proc.returncode is not None:
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


def _close_quietly(f) -> None:
    if f is None:
        return
    try:
        f.close()
    except OSError:
        pass


async def _open_log_follow(name: str) -> Optional[asyncio.subprocess.Process]:
    """`docker logs -f` on a container we did not start. None if it won't run."""
    try:
        return await asyncio.create_subprocess_exec(
            "docker",
            "logs",
            "--tail",
            str(ADOPT_LOG_TAIL),
            "-f",
            name,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
    except (OSError, FileNotFoundError) as exc:
        logger.warning("could not follow logs of %s: %s", name, exc)
        return None


async def _mark_exited(run: ActiveRun) -> None:
    """Record the end of a run. Idempotent -- the first caller wins.

    ``status`` is set before anything is awaited so a second caller (the
    stop path, say) sees it and bails rather than racing us to a
    different exit code.
    """
    if run.status == "exited":
        return
    run.status = "exited"
    exit_code: Optional[int] = None
    if run.proc is not None:
        try:
            exit_code = await asyncio.wait_for(run.proc.wait(), timeout=10)
        except asyncio.TimeoutError:
            exit_code = None
    run.exit_code = exit_code
    run.console.close()


async def _pump(run: ActiveRun) -> None:
    """Read the run's output source until it ends, buffering and fanning out.

    Sole writer of the run's terminal state. Everything that ends a run --
    a crash, an exit, a stop, a container dying on its own -- arrives here
    as EOF, so there is exactly one place that decides a run is over.
    """
    try:
        while True:
            try:
                chunk = await run.reader.read(65536)
            except (OSError, ConnectionResetError):
                # A PTY master raises EIO when the child's terminal closes.
                chunk = b""

            if chunk:
                run.console.append(_normalise(chunk))
                continue

            # EOF on our own PTY does not necessarily mean the run ended:
            # a detached (`-d`) run's client exits immediately while the
            # container keeps serving. If the container is still up, keep
            # watching it through docker instead of declaring it dead.
            if (
                not run._following_logs
                and run.container_name
                and await container_is_running(run.container_name)
            ):
                follow = await _open_log_follow(run.container_name)
                if follow is None:
                    break
                run._following_logs = True
                run.proc = follow
                run.reader = follow.stdout
                _close_quietly(run.master_file)
                run.master_file = None
                continue

            break
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 -- the pump must never die loudly
        logger.warning("run %s pump failed: %s", run.run_id, exc)
    finally:
        await _mark_exited(run)


class RunRegistry:
    """Owns concurrent engine runs: start, adopt, stop, and watch each one."""

    def __init__(self) -> None:
        self.runs: dict[str, ActiveRun] = {}
        self._lock = asyncio.Lock()

    def get_active(self) -> Optional[ActiveRun]:
        running = self.get_running()
        return running[-1] if running else None

    def get_run(self, run_id: str) -> Optional[ActiveRun]:
        return self.runs.get(run_id)

    def get_running(self) -> list[ActiveRun]:
        return [run for run in self.runs.values() if run.status == "running"]

    def list_runs(self) -> list[ActiveRun]:
        return sorted(self.runs.values(), key=lambda run: run.started_at, reverse=True)

    async def start(
        self,
        command: str,
        config_id: str,
        config_name: str,
    ) -> ActiveRun:
        """Launch a new run without disturbing any other engine."""
        async with self._lock:
            container_name = extract_container_name(command)
            if container_name and any(
                run.status == "running" and run.container_name == container_name
                for run in self.runs.values()
            ):
                raise RuntimeError(
                    f"container {container_name!r} is already tracked as running"
                )
            run = ActiveRun(
                run_id=uuid.uuid4().hex[:12],
                config_id=config_id,
                config_name=config_name,
                container_name=container_name,
                started_at=_utc_now(),
                command=command,
            )
            await _launch(run, command)
            self.runs[run.run_id] = run
            self._arm_pump(run)
            return run

    async def adopt(
        self,
        container_name: str,
        config_id: str,
        config_name: str,
        command: str = "",
    ) -> Optional[ActiveRun]:
        """Take over a container this process did not start."""
        async with self._lock:
            existing = next(
                (
                    run
                    for run in self.runs.values()
                    if run.status == "running" and run.container_name == container_name
                ),
                None,
            )
            if existing is not None:
                return existing
            proc = await _open_log_follow(container_name)
            if proc is None:
                return None

            started = await inspect_started_at(container_name) or _utc_now()
            run = ActiveRun(
                run_id=uuid.uuid4().hex[:12],
                config_id=config_id,
                config_name=config_name,
                container_name=container_name,
                started_at=started,
                command=command,
                adopted=True,
            )
            run.proc = proc
            run.reader = proc.stdout
            run._following_logs = True
            self.runs[run.run_id] = run
            self._arm_pump(run)
            return run

    def _arm_pump(self, run: ActiveRun) -> None:
        async def wrapped() -> None:
            await _pump(run)

        # Keep the strong reference on the run itself: unreferenced tasks
        # can be garbage-collected mid-flight.
        run.pump_task = asyncio.create_task(wrapped())

    async def stop(self, run_id: str) -> Optional[str]:
        """Stop one run, leaving every other engine untouched."""
        run = self.runs.get(run_id)
        if run is None or run.status != "running":
            return None
        method = await run.stop()
        if run.container_name:
            await _wait_until_gone(run.container_name)
        return method

    async def stop_active(self) -> Optional[str]:
        """Compatibility helper: stop the most recently started running run."""
        run = self.get_active()
        return await self.stop(run.run_id) if run is not None else None

    async def stop_all(self) -> dict[str, str]:
        """Stop every running engine and report each stop result."""
        runs = list(reversed(self.get_running()))

        async def stop_one(run: ActiveRun) -> tuple[str, str]:
            try:
                return run.run_id, await self.stop(run.run_id) or "already_exited"
            except Exception as exc:
                logger.exception("could not stop run %s", run.run_id)
                return run.run_id, f"error: {exc}"

        return dict(await asyncio.gather(*(stop_one(run) for run in runs)))

    async def shutdown(self) -> None:
        """Stop managed engines and release console-pump resources."""
        results = await self.stop_all()
        for run_id, result in results.items():
            if result.startswith("error:"):
                logger.error("engine run %s was not stopped during shutdown: %s", run_id, result)
        for run in self.runs.values():
            if run.pump_task is not None and not run.pump_task.done():
                run.pump_task.cancel()
                try:
                    await run.pump_task
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass
            _close_quietly(run.master_file)
            run.master_file = None

    async def discover(
        self,
        configs: list[dict[str, Any]],
        on_adopted: Optional[Callable[[ActiveRun], Awaitable[None]]] = None,
    ) -> list[ActiveRun]:
        """Find all configured engine containers already running, and adopt them.

        A container outlives the process that launched it, so after a
        backend restart -- or when something outside this dashboard
        started the engine -- there can be a live server this process
        knows nothing about. Without this the UI shows an empty console
        and no way to stop it.

        Each configuration's own ``--name`` is the handle: the command is
        rendered exactly as a real start would render it, and the name out
        of that result is what we ask docker about. Matching against what
        would actually run, rather than a stored string that may have
        drifted, is the point.
        """
        running = set(await list_running_names())
        if not running:
            return []

        adopted: list[ActiveRun] = []
        for cfg in configs or []:
            name = rendered_container_name(cfg)
            if not name or name not in running:
                continue

            if any(
                run.status == "running" and run.container_name == name
                for run in self.runs.values()
            ):
                continue
            run = await self.adopt(
                container_name=name,
                config_id=cfg.get("id", ""),
                config_name=cfg.get("name", ""),
                command=_render(cfg),
            )
            if run is None:
                continue

            run.engine_url = engine_url_for(cfg)
            if on_adopted is not None:
                try:
                    await on_adopted(run)
                except Exception as exc:  # noqa: BLE001 -- adoption stands
                    logger.warning("post-adoption hook failed for %s: %s", name, exc)
                    run.engine_switch_error = str(exc)

            logger.info("adopted already-running engine %s (config %r)", name, cfg.get("name"))
            adopted.append(run)
        return adopted


async def _wait_until_gone(name: str, timeout: float = RELEASE_WAIT_SECONDS) -> bool:
    """Poll until docker no longer reports `name` running, or we give up."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if not await container_is_running(name):
            return True
        await asyncio.sleep(0.4)
    return not await container_is_running(name)


def _sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _render(cfg: dict[str, Any]) -> str:
    """Render a stored configuration's command template. '' if it won't render."""
    try:
        params = merge_values(cfg.get("parameters") or [], None)
        return render_command(cfg.get("docker_command") or "", params)
    except ParamError:
        return ""


def rendered_container_name(cfg: dict[str, Any]) -> Optional[str]:
    """The container name a configuration would use, from its rendered command.

    Falls back to the raw template when it will not render -- a broken
    config still names its container literally, and ``extract_container_name``
    rejects an unrendered ``{placeholder}``, so the fallback cannot
    produce a phantom match.
    """
    command = _render(cfg)
    if not command:
        command = cfg.get("docker_command") or ""
    return extract_container_name(command)


def engine_url_for(cfg: dict[str, Any]) -> Optional[str]:
    """Loopback URL of the engine a configuration publishes, if any."""
    try:
        params = merge_values(cfg.get("parameters") or [], None)
        port = port_from_params(params)
    except ParamError:
        return None
    if port is None:
        return None
    try:
        return engine_url(port)
    except Exception:  # noqa: BLE001 -- a bad port simply means no target
        return None


async def _launch(run: ActiveRun, command: str) -> None:
    """Start the docker client under a PTY and wire up the read side."""
    master_fd, slave_fd = pty.openpty()
    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
        )
    except Exception:
        os.close(master_fd)
        os.close(slave_fd)
        raise
    # The child holds the slave side; we only ever read the master.
    os.close(slave_fd)

    reader = asyncio.StreamReader()
    protocol = asyncio.StreamReaderProtocol(reader)
    master_file = os.fdopen(master_fd, "rb", buffering=0)
    try:
        await asyncio.get_running_loop().connect_read_pipe(lambda: protocol, master_file)
    except Exception:
        _close_quietly(master_file)
        await _terminate(proc)
        raise

    run.proc = proc
    run.reader = reader
    run.master_file = master_file


async def stream_console(run: ActiveRun) -> AsyncIterator[str]:
    """SSE body for one viewer: backlog first, then the live tail.

    The viewer subscribes *before* the backlog is read, with no await
    between, so nothing produced in between is lost or repeated -- the
    backlog is reported as a single blob and the live stream carries only
    chunks that landed after it.
    """
    q = run.console.subscribe()
    try:
        yield _sse(
            "state",
            {
                "run_id": run.run_id,
                "status": run.status,
                "exit_code": run.exit_code,
                "backlog": run.console.snapshot(),
            },
        )

        if run.status == "exited":
            yield _sse("exit", {"exit_code": run.exit_code})
            return

        while True:
            try:
                item = await asyncio.wait_for(q.get(), timeout=SSE_HEARTBEAT_SECONDS)
            except asyncio.TimeoutError:
                # Keepalive through a silent stretch of engine output.
                yield _sse("ping", {})
                continue

            if item is None:
                yield _sse("exit", {"exit_code": run.exit_code})
                return
            yield _sse("stdout", {"text": item})
    finally:
        run.console.unsubscribe(q)


async def reconcile_loop(
    reg: RunRegistry,
    get_configs: Callable[[], list[dict[str, Any]]],
    on_adopted: Optional[Callable[[ActiveRun], Awaitable[None]]] = None,
    interval: float = RECONCILE_SECONDS,
) -> None:
    """Periodically look for an engine container we are not tracking.

    A startup-only scan has a hole in it: if the backend restarts while
    ``docker run`` is still pulling an image, the container does not exist
    yet, nothing adopts it, and the dashboard stays blind to it forever.
    Re-checking turns adoption from a snapshot taken once into a property
    the system keeps.
    """
    while True:
        await asyncio.sleep(interval)
        try:
            await reg.discover(get_configs(), on_adopted=on_adopted)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 -- a bad cycle is not fatal
            logger.warning("run reconcile failed: %s", exc)


# One registry per process.
registry = RunRegistry()
