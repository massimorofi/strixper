"""Shared retry policy for chat requests.

A dropped connection used to end a chat turn immediately: the user saw a
"network error" bubble and had to resend by hand. Long sessions hit this
constantly -- engine restarts, proxy idle timeouts, DNS blips, transient
5xx. Every chat call (plain and agentic) now retries before the failure is
surfaced.

The budget lives in config:

  CHAT_RETRY_ATTEMPTS        total tries, initial attempt included (default 6,
                             i.e. the first try plus 5 retries)
  CHAT_RETRY_PAUSE_SECONDS   pause between tries (default 10)
  CHAT_RETRY_TIMEOUT_SECONDS optional upper bound on one attempt
                             (default 0 = no extra cap beyond httpx's own)

Only *transient* failures are retried. A 400/401/404/422 will fail exactly
the same way on the sixth try, so retrying it just burns 10s each time; the
user cancellation signal must never be retried either.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, Optional

import httpx

try:  # openai is only needed by the agentic path; stay importable without it
    import openai
except ImportError:  # pragma: no cover
    openai = None  # type: ignore[assignment]

from ..config import settings

logger = logging.getLogger(__name__)

# Statuses that mean "try again later" rather than "your request is wrong".
RETRYABLE_STATUS = frozenset({408, 409, 425, 429, 500, 502, 503, 504})

DEFAULT_RETRY_ATTEMPTS = 6
DEFAULT_RETRY_PAUSE_SECONDS = 10.0
DEFAULT_RETRY_TIMEOUT_SECONDS = 0.0


def retry_budget() -> tuple[int, float]:
    """Return (total_attempts, pause_seconds), sanitised."""
    attempts = _positive_int(getattr(settings, "chat_retry_attempts", None),
                            DEFAULT_RETRY_ATTEMPTS)
    pause = _non_negative_float(getattr(settings, "chat_retry_pause_seconds", None),
                               DEFAULT_RETRY_PAUSE_SECONDS)
    return attempts, pause


def retry_timeout() -> float:
    """Per-attempt ceiling in seconds; 0 means "no extra cap".

    Left disabled by default on purpose. A long reasoning answer can
    legitimately stream for several minutes, and cutting that off would
    turn a working request into a retry. httpx already bounds each read.
    """
    value = _non_negative_float(
        getattr(settings, "chat_retry_timeout_seconds", None),
        DEFAULT_RETRY_TIMEOUT_SECONDS,
    )
    return value if value > 0 else 0.0


def _positive_int(value: Any, fallback: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return fallback
    return parsed if parsed > 0 else fallback


def _positive_float(value: Any, fallback: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return fallback
    return parsed if parsed > 0 else fallback


def _non_negative_float(value: Any, fallback: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return fallback
    return parsed if parsed >= 0 else fallback


def _status_code(exc: BaseException) -> int:
    """HTTP status carried by an exception, or 0 when there is none."""
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code if exc.response is not None else 0
    if openai is not None and isinstance(exc, openai.APIStatusError):
        try:
            return int(exc.status_code or 0)
        except (TypeError, ValueError):
            return 0
    return 0


def is_retryable(exc: BaseException) -> bool:
    """Whether a failed chat call is worth retrying.

    Retried: connection/DNS/protocol failures, timeouts, 5xx, 429 and the
    other "server hiccup" statuses.

    Not retried: user cancellation and definitive client errors (400, 401,
    403, 404, 422, ...) -- those are deterministic, so retrying them only
    delays the error message.
    """
    # Stop button / client disconnect. CancelledError derives from
    # BaseException in modern Python so most ``except Exception`` clauses
    # already miss it, but openai/httpx can surface it as a plain exception.
    if isinstance(exc, asyncio.CancelledError):
        return False
    if isinstance(exc, (KeyboardInterrupt, SystemExit)):
        return False

    status = _status_code(exc)
    if status:
        return status in RETRYABLE_STATUS

    # httpx: ConnectError, ReadError, WriteError, RemoteProtocolError,
    # TimeoutException (and its connect/read/pool/partial subtypes).
    if isinstance(exc, httpx.TransportError):
        return True
    # Socket-level failures raised underneath httpx.
    if isinstance(exc, (ConnectionError, TimeoutError, OSError)):
        return True

    if openai is not None:
        # APITimeoutError subclasses APIConnectionError, so this covers both.
        if isinstance(exc, openai.APIConnectionError):
            return True
        if isinstance(exc, openai.RateLimitError):
            return True

    return False


def describe_failure(exc: BaseException, attempts: int) -> str:
    """Human-readable message for an exhausted retry loop."""
    if isinstance(exc, httpx.HTTPStatusError) and exc.response is not None:
        detail = exc.response.text[:500]
        return (
            f"Engine chat failed ({exc.response.status_code}): {detail} "
            f"[{attempts} attempts]"
        )
    return f"Engine unreachable: {exc} [{attempts} attempts]"


async def call_with_retry(
    factory: Callable[[], Any],
    *,
    attempts: Optional[int] = None,
    pause: Optional[float] = None,
    timeout: Optional[float] = None,
) -> Any:
    """Run ``await factory()`` until it succeeds or the budget runs out.

    ``factory`` must build a fresh awaitable on each call -- a coroutine
    object cannot be awaited twice. Raises the last exception once the
    retries are exhausted.
    """
    total, configured_pause = retry_budget()
    if attempts is not None:
        total = _positive_int(attempts, total)
    pause = configured_pause if pause is None else _non_negative_float(
        pause, configured_pause
    )
    if timeout is None:
        timeout = retry_timeout()
    else:
        timeout = _non_negative_float(timeout, retry_timeout())
    # wait_for(timeout=0) would cancel the task immediately, so "no cap"
    # has to be spelled None.
    effective_timeout = timeout if timeout and timeout > 0 else None

    last_exc: BaseException = RuntimeError("no attempt was made")
    for attempt in range(1, total + 1):
        try:
            return await asyncio.wait_for(factory(), timeout=effective_timeout)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - classified by is_retryable
            last_exc = exc
            if attempt >= total or not is_retryable(exc):
                raise
            logger.warning(
                "Chat request failed on attempt %d/%d (%s); retrying in %.0fs",
                attempt, total, exc, pause,
            )
        if pause:
            await asyncio.sleep(pause)
    raise last_exc  # pragma: no cover - the loop returns or raises first
