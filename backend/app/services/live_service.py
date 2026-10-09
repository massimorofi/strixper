"""Background poller that keeps a fresh combined Halogen + hardware snapshot."""

from __future__ import annotations

import asyncio
import contextlib
from datetime import datetime, timezone
from typing import Any, Optional

from ..config import settings
from .engine_target import (
    normalise_base_url,
    save_target_async,
    load_target,
)
from .halogen_client import HalogenClient
from .rocm_monitor import RocmMonitor
from .system_monitor import SystemMonitor


class MetricStats:
    """Running min / max / mean for one numeric series.

    When ``ignore_zero`` is set, zero readings are skipped entirely (not counted,
    not summed, not considered for min/max). This is used for throughput metrics,
    where a 0 means "the engine hasn't reported activity yet" rather than a real
    measurement of zero.
    """

    __slots__ = ("count", "total", "min", "max", "ignore_zero")

    def __init__(self, ignore_zero: bool = False) -> None:
        self.count = 0
        self.total = 0.0
        self.min: Optional[float] = None
        self.max: Optional[float] = None
        self.ignore_zero = ignore_zero

    def add(self, value: float) -> None:
        if self.ignore_zero and value == 0:
            return
        self.count += 1
        self.total += value
        if self.min is None or value < self.min:
            self.min = value
        if self.max is None or value > self.max:
            self.max = value

    def as_dict(self) -> dict[str, float]:
        if self.count == 0:
            return {"min": 0.0, "max": 0.0, "avg": 0.0, "count": 0}
        return {
            "min": round(self.min, 2),
            "max": round(self.max, 2),
            "avg": round(self.total / self.count, 2),
            "count": self.count,
        }


class StatsTracker:
    """Accumulates session-wide statistics since startup (or last reset)."""

    KEYS = (
        "prompt_tps",
        "decode_tps",
        "gtt_used_gb",
        "gtt_pct",
        "vram_pct",
        "gpu_util_pct",
        "cpu_pct",
        "draft_acceptance",
        "cache_hit_rate",
        "cache_token_hit_rate",
    )

    # Throughput, utilization and acceptance-rate metrics: a 0 reading means
    # "idle / not reporting activity yet", not a real measurement of zero, so it
    # is excluded from the average and the minimum.
    IGNORE_ZERO_KEYS = (
        "prompt_tps",
        "decode_tps",
        "gpu_util_pct",
        "cpu_pct",
        "draft_acceptance",
    )

    def _new_metrics(self) -> dict[str, MetricStats]:
        return {
            k: MetricStats(ignore_zero=(k in self.IGNORE_ZERO_KEYS)) for k in self.KEYS
        }

    def __init__(self) -> None:
        self._metrics: dict[str, MetricStats] = self._new_metrics()
        self.started_at: str = utc_now_iso()
        self.total_samples: int = 0

    def record(self, sample: dict[str, Optional[float]]) -> None:
        self.total_samples += 1
        for key in self.KEYS:
            value = sample.get(key)
            if value is None:
                continue
            self._metrics[key].add(float(value))

    def snapshot(self) -> dict[str, Any]:
        data: dict[str, Any] = {k: self._metrics[k].as_dict() for k in self.KEYS}
        data["started_at"] = self.started_at
        data["samples"] = self.total_samples
        return data

    def reset(self) -> None:
        self._metrics = self._new_metrics()
        self.started_at = utc_now_iso()
        self.total_samples = 0


class RuntimeState:
    """Mutable runtime state shared between the poller and the API routers."""

    def __init__(self) -> None:
        # The engine address is whatever was last selected (persisted), not
        # necessarily the startup default: the user may have switched to a
        # different engine before this process started.
        saved = load_target()
        initial_url = (saved or {}).get("base_url") or settings.halogen_host

        self.poll_interval: float = settings.poll_interval_seconds
        self.latest: Optional[dict[str, Any]] = None
        self.wake: asyncio.Event = asyncio.Event()
        self.halogen: HalogenClient = HalogenClient(initial_url)
        self.monitor: SystemMonitor = SystemMonitor()
        self.rocm: RocmMonitor = RocmMonitor()
        self.stats: StatsTracker = StatsTracker()
        # Which LLM-Runner configuration the current engine came from, for
        # display. None when the target is the startup default.
        self.engine_config_id: Optional[str] = (saved or {}).get("config_id")
        self.engine_config_name: Optional[str] = (saved or {}).get("config_name")
        # Served model id, refreshed from /health on every poll. Used by
        # /api/v1/count-tokens (the model field is required by Halogen).
        # Seeded from the persisted target so a restart keeps the model of
        # the engine we are pointed at, not the startup default.
        saved_model = (saved or {}).get("model_name")
        self.model_name: str = (
            saved_model.strip() if isinstance(saved_model, str) and saved_model.strip()
            else settings.default_model
        )


async def set_engine_target(
    state: RuntimeState,
    base_url: str,
    config_id: Optional[str] = None,
    config_name: Optional[str] = None,
    model_name: Optional[str] = None,
) -> dict[str, Any]:
    """Point the dashboard at a different engine, and remember it.

    ``HalogenClient`` builds each request URL from ``base_url`` at call
    time rather than at construction, so reassigning it here takes effect
    on the very next request -- nothing needs to be rebuilt. The write is
    persisted so a backend restart comes back on the same engine, and the
    poll loop is woken so the new state shows up immediately instead of
    after a full interval.

    ``model_name`` is the id the engine serves, taken from the run
    configuration. It seeds ``state.model_name`` right away so the chat
    asks for the right model even before the first successful ``/health``
    -- an engine that does not report its model in ``/health`` would
    otherwise leave the chat on whatever the previous engine used.
    """
    url = normalise_base_url(base_url)
    model = (model_name or "").strip()
    state.halogen.base_url = url
    state.engine_config_id = config_id
    state.engine_config_name = config_name
    if model:
        state.model_name = model
    # Persist whatever the model now actually is. A switch that supplies
    # no model -- a hand-typed address in the engine chip, say -- keeps the
    # current one rather than silently blanking it on disk while the
    # running process still holds it.
    await save_target_async(url, config_id, config_name, state.model_name)
    state.wake.set()
    return engine_target_snapshot(state)


def engine_target_snapshot(state: RuntimeState) -> dict[str, Any]:
    return {
        "base_url": state.halogen.base_url,
        "config_id": state.engine_config_id,
        "config_name": state.engine_config_name,
        "model_name": state.model_name,
    }


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


async def build_live_snapshot(state: RuntimeState) -> dict[str, Any]:
    """One aggregated sample: Halogen engine state + local hardware telemetry."""
    # Halogen and rocm-smi are independent, so poll them concurrently.
    halogen_result, rocm_result = await asyncio.gather(
        state.halogen.snapshot(),
        state.rocm.snapshot(),
        return_exceptions=True,
    )

    if isinstance(halogen_result, BaseException):
        halogen_data = {"status": "unreachable", "error": str(halogen_result)}
        connected = False
    else:
        halogen_data = halogen_result
        connected = True
        # Keep the served model id current for the count-tokens proxy.
        served_model = halogen_data.get("model")
        if served_model:
            state.model_name = served_model

    if isinstance(rocm_result, BaseException):
        rocm_data = {"available": False, "error": str(rocm_result)}
    else:
        rocm_data = rocm_result

    hardware = state.monitor.snapshot()
    hardware["rocm"] = rocm_data

    # Accumulate session-wide statistics (since startup / last reset).
    draft_rate = halogen_data.get("draft_acceptance_rate")
    cache = halogen_data.get("cache") or {}
    state.stats.record(
        {
            "prompt_tps": halogen_data.get("prompt_tokens_per_sec"),
            "decode_tps": halogen_data.get("predicted_tokens_per_sec"),
            "gtt_used_gb": hardware.get("gpu", {}).get("gtt_used_gb"),
            "gtt_pct": hardware.get("gpu", {}).get("gtt_usage_pct"),
            "vram_pct": rocm_data.get("vram_usage_pct"),
            "gpu_util_pct": rocm_data.get("gpu_util_pct"),
            "cpu_pct": hardware.get("cpu", {}).get("usage_pct"),
            # Store as a percentage (0-100) to match the other % series.
            "draft_acceptance": (draft_rate * 100) if draft_rate is not None else None,
            "cache_hit_rate": (
                cache.get("hit_rate") * 100
                if cache.get("hit_rate") is not None
                else None
            ),
            "cache_token_hit_rate": (
                cache.get("token_hit_rate") * 100
                if cache.get("token_hit_rate") is not None
                else None
            ),
        }
    )

    return {
        "timestamp": utc_now_iso(),
        "connected": connected,
        "halogen": halogen_data,
        "hardware": hardware,
        "stats": state.stats.snapshot(),
    }


async def poll_loop(state: RuntimeState) -> None:
    """Poll forever; wakes early when the interval is changed via POST /config."""
    while True:
        state.latest = await build_live_snapshot(state)
        try:
            await asyncio.wait_for(state.wake.wait(), timeout=max(state.poll_interval, 0.5))
        except asyncio.TimeoutError:
            pass
        state.wake.clear()


@contextlib.asynccontextmanager
async def managed_poller(state: RuntimeState):
    task = asyncio.create_task(poll_loop(state))
    try:
        yield task
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        await state.halogen.close()
