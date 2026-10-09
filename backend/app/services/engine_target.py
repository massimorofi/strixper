"""Which inference engine the dashboard talks to.

The engine is a docker container started through the LLM-Runner, publishing
its API on a host port. Rather than being pinned to one port at process
launch, the dashboard follows the engine that was last started: launching a
configuration that declares a ``port`` parameter repoints the client at
that port, and the choice is persisted so it survives a backend restart.

Two separate concerns, deliberately kept apart:

* ``bind`` (a configuration parameter) decides **who may reach** the
  container from outside -- ``127.0.0.1`` for local-only, ``0.0.0.0`` for
  the LAN.
* the engine target decides **where this backend connects** to reach the
  engine. It is always loopback: the backend runs on the same host as the
  containers it supervises, and routing through an outward-facing address
  to reach a local service just adds a failure mode.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# Default location: backend/data/engine_target.json (override with
# ENGINE_TARGET_PATH for tests or alternative layouts).
DEFAULT_TARGET_PATH = (
    Path(__file__).resolve().parent.parent.parent / "data" / "engine_target.json"
)
TARGET_PATH = Path(os.getenv("ENGINE_TARGET_PATH", str(DEFAULT_TARGET_PATH)))

# The configuration parameter that carries the engine's published port.
ENGINE_PORT_PARAM = "port"

# Loopback is where a locally-supervised engine actually answers.
LOCAL_ENGINE_HOST = "127.0.0.1"

ALLOWED_SCHEMES = ("http", "https")


class EngineTargetError(ValueError):
    """Raised when a supplied engine address is not usable."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def normalise_base_url(raw: Any) -> str:
    """Validate and canonicalise an engine base URL.

    Requires an http/https scheme and a real host, and drops any trailing
    slash so callers can safely append ``/health`` and friends. Raises
    ``EngineTargetError`` rather than guessing, so a typo surfaces instead
    of silently producing an unreachable client.
    """
    if not isinstance(raw, str) or not raw.strip():
        raise EngineTargetError("engine address must be a non-empty string")

    url = raw.strip()
    parsed = urlparse(url)

    if parsed.scheme not in ALLOWED_SCHEMES:
        raise EngineTargetError(
            f"engine address must start with http:// or https:// (got {parsed.scheme or 'nothing'})"
        )
    if not parsed.netloc:
        raise EngineTargetError(f"engine address has no host: {url!r}")

    return url.rstrip("/")


def engine_url(port: Any) -> str:
    """Build the loopback base URL for an engine published on ``port``."""
    return f"http://{LOCAL_ENGINE_HOST}:{validate_port(port)}"


def validate_port(value: Any) -> int:
    """Coerce to an int and check it is a usable TCP port."""
    try:
        port = int(str(value).strip())
    except (TypeError, ValueError):
        raise EngineTargetError(f"port is not a number: {value!r}") from None
    if not 1 <= port <= 65535:
        raise EngineTargetError(f"port out of range 1-65535: {port}")
    return port


def port_from_params(params: Optional[list[dict[str, Any]]]) -> Optional[int]:
    """Read the engine port out of a rendered parameter list.

    Returns ``None`` when the configuration declares no ``port`` parameter
    or its value is not a valid port -- a configuration that isn't a
    network server simply doesn't repoint the dashboard.
    """
    for param in params or []:
        if param.get("name") != ENGINE_PORT_PARAM:
            continue
        value = (param.get("value") or "").strip()
        if not value:
            return None
        try:
            return validate_port(value)
        except EngineTargetError:
            return None
    return None


# -- persistence --------------------------------------------------------------

_lock = asyncio.Lock()


def load_target() -> Optional[dict[str, Any]]:
    """Read the persisted target, or None when there is none.

    A missing or unreadable file is not an error: the caller falls back to
    its own default. A file that parses but holds a bad address is treated
    the same way, so a corrupt file can never wedge the dashboard onto a
    dead engine at startup.
    """
    try:
        raw = TARGET_PATH.read_text(encoding="utf-8")
    except OSError:
        return None

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None

    if not isinstance(data, dict):
        return None

    base_url = data.get("base_url")
    if not isinstance(base_url, str):
        return None
    try:
        data["base_url"] = normalise_base_url(base_url)
    except EngineTargetError:
        return None
    return data


def save_target(
    base_url: str,
    config_id: Optional[str] = None,
    config_name: Optional[str] = None,
    model_name: Optional[str] = None,
) -> dict[str, Any]:
    """Persist the target atomically (temp file + os.replace).

    The write goes through a lock so concurrent switches cannot interleave
    and leave a half-written file behind.

    ``model_name`` travels with the target because the model id belongs to
    the engine, not to the dashboard: switching engines changes which
    model the chat must ask for.
    """
    payload = {
        "base_url": normalise_base_url(base_url),
        "config_id": config_id,
        "config_name": config_name,
        "model_name": (model_name or "").strip() or None,
        "updated_at": _utc_now(),
    }
    # Persistence is best-effort: the switch itself has already taken
    # effect in memory, and an unwritable directory must not turn a
    # perfectly good run into a failure.
    try:
        TARGET_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = TARGET_PATH.with_suffix(TARGET_PATH.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        os.replace(tmp, TARGET_PATH)
    except OSError as exc:
        logger.warning("could not persist engine target to %s: %s", TARGET_PATH, exc)
    return payload


async def save_target_async(
    base_url: str,
    config_id: Optional[str] = None,
    config_name: Optional[str] = None,
    model_name: Optional[str] = None,
) -> dict[str, Any]:
    """Locked variant of :func:`save_target` for use from request handlers."""
    async with _lock:
        return save_target(base_url, config_id, config_name, model_name)
