"""Stopping the docker containers the LLM-Runner starts.

`docker run -it` attaches the CLI to a pseudo-terminal, and killing that
client process does **not** stop the container -- the container keeps
running with nothing attached to it, holding the GPU and the published port.
A SIGTERM to the CLI is no better in practice: it tears down the client's
own stream handling, and whether the container follows depends on signal
forwarding through the TTY, which `-it` does not guarantee.

So stopping a run has to go through docker explicitly. ``docker stop`` is
the graceful path (SIGTERM to the container's PID 1, escalating to
SIGKILL after ``--time`` seconds), with ``docker kill`` as a fallback for a
container that ignores the graceful signal entirely.
"""

from __future__ import annotations

import asyncio
import os
import re
from typing import Optional

# `--name foo` and `--name=foo`.
CONTAINER_NAME_RE = re.compile(r"--name(?:\s+|=)(\S+)")

# Seconds docker waits for the container to stop before it force-kills it.
# A server that handles SIGTERM returns well inside this; one that ignores
# it makes the user wait the whole way, so keep it short enough that the
# Stop button still feels responsive. Override with RUNNER_STOP_TIMEOUT.
DEFAULT_STOP_TIMEOUT = int(os.getenv("RUNNER_STOP_TIMEOUT", "5"))

# Extra headroom on our own wait on the docker CLI, so a wedged docker
# daemon surfaces as a failed stop rather than a hung request.
DOCKER_CLI_GUARD = 25


def extract_container_name(command: str) -> Optional[str]:
    """Pull the `--name` value out of a docker run command.

    Reads the *rendered* command, so parameter substitution has already
    happened. An unrendered ``{placeholder}`` is rejected -- it would name
    a container that cannot exist. Returns None when the command has no
    ``--name`` at all, in which case there is no reliable handle on the
    container and the caller falls back to killing the client process.
    """
    for match in CONTAINER_NAME_RE.finditer(command):
        name = match.group(1).strip()
        if not name or name.startswith("{"):
            continue
        return name
    return None


async def _run_docker(*args: str, guard: int) -> bool:
    """Run a docker CLI command, True when it exited zero."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "docker",
            *args,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.wait_for(proc.wait(), timeout=guard)
        return proc.returncode == 0
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        return False
    except (OSError, FileNotFoundError):
        return False


async def stop_container(name: str, timeout: int = DEFAULT_STOP_TIMEOUT) -> bool:
    """Graceful `docker stop`, escalating to SIGKILL inside docker after `timeout`."""
    return await _run_docker(
        "stop", "--time", str(timeout), name, guard=timeout + DOCKER_CLI_GUARD
    )


async def kill_container(name: str) -> bool:
    """Immediate `docker kill` (SIGKILL to the container)."""
    return await _run_docker("kill", name, guard=DOCKER_CLI_GUARD)


async def container_is_running(name: str) -> bool:
    """Whether docker reports a container by this name currently running."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "docker",
            "inspect",
            "-f",
            "{{.State.Running}}",
            name,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=DOCKER_CLI_GUARD)
        return proc.returncode == 0 and out.decode().strip() == "true"
    except (asyncio.TimeoutError, OSError, FileNotFoundError):
        return False


async def list_running_names() -> list[str]:
    """Names of every running container, [] when docker cannot answer.

    One call instead of one ``inspect`` per configuration: discovery runs
    on a timer, so the cheap form matters.
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            "docker",
            "ps",
            "--format",
            "{{.Names}}",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=DOCKER_CLI_GUARD)
    except (asyncio.TimeoutError, OSError, FileNotFoundError):
        return []
    if proc.returncode != 0:
        return []
    return [line.strip() for line in out.decode(errors="replace").splitlines() if line.strip()]


async def inspect_started_at(name: str) -> Optional[str]:
    """When the container started, as recorded by docker. None if unreadable.

    Used for adopted runs: the container was already up, so its own clock
    is the honest start time -- not the moment we noticed it.
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            "docker",
            "inspect",
            "-f",
            "{{.State.StartedAt}}",
            name,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=DOCKER_CLI_GUARD)
    except (asyncio.TimeoutError, OSError, FileNotFoundError):
        return None
    if proc.returncode != 0:
        return None
    value = out.decode(errors="replace").strip()
    return value or None
