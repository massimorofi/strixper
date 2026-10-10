"""Tool definitions for the agentic chat.

Every tool is a thin wrapper around a capability the dashboard already has
(``live_service``, ``tuning_checker``, ``halogen_client``, the LLM-Runner
store). The agent therefore sees exactly what the other tabs see -- no
hidden side channel, and no capability the user cannot reach by hand.

Tools come in two groups:

  * **read-only** -- status, metrics, tuning, token counting, config
    listing. Safe to expose unconditionally.
  * **actions** -- start and stop an engine container. These reach
    ``docker.sock``, which is root-equivalent on the host, so they are
    registered only when the caller explicitly allows them.

Tools return a JSON string rather than a typed object. The payloads are
large nested dicts the model reads better as pretty-printed JSON than as a
repr, and truncating a string is safe while truncating a structured value
is not.

``build_tools`` builds the list per request rather than at import time:
the tools close over the runtime state, and the actions toggle has to take
effect per request, so there is nothing worth caching across requests.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from agents import FunctionTool, function_tool

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .live_service import RuntimeState


def _clip(value: Any, limit: int) -> str:
    """Serialise to JSON and truncate so one payload cannot flood the context."""
    try:
        text = json.dumps(value, ensure_ascii=False, indent=2)
    except (TypeError, ValueError):
        text = str(value)
    if len(text) > limit:
        return text[:limit] + f"\n... [truncated, {len(text)} chars total]"
    return text


def build_tools(
    state: "RuntimeState", allow_actions: bool, run_id: str | None = None
) -> list[FunctionTool]:
    """Build the tool set bound to the current runtime state.

    Args:
        state: Live runtime state (engine target, stats, latest snapshot).
        allow_actions: Include the engine start/stop tools. These control
            containers through docker and are root-equivalent on the host.
    """

    from .run_registry import registry

    run = registry.get_run(run_id) if run_id else None

    async def engine_call(method: str, *args: Any, **kwargs: Any) -> Any:
        if run is None or not run.engine_url:
            return await getattr(state.halogen, method)(*args, **kwargs)
        from .halogen_client import HalogenClient

        client = HalogenClient(run.engine_url)
        try:
            return await getattr(client, method)(*args, **kwargs)
        finally:
            await client.close()

    # -- read-only tools ------------------------------------------------------

    @function_tool
    async def get_live_status() -> str:
        """Read the current live snapshot of the machine and the engine.

        Returns engine connectivity and model, busy/slot state, in-flight
        and queued requests, GPU utilisation, VRAM and GTT usage, CPU
        load, and session throughput averages. Use this for any question
        about what the system is doing right now.
        """
        from .live_service import build_live_snapshot, build_run_snapshot

        snapshot = (
            await build_run_snapshot(state, run.run_id, run.engine_url)
            if run is not None and run.engine_url
            else await build_live_snapshot(state)
        )
        return _clip(snapshot, 6000)

    @function_tool
    async def get_engine_health() -> str:
        """Query the inference engine's own /health endpoint directly.

        Returns the served model id, context length, supported endpoints,
        vision capability, and the API/engine version match. Use this to
        check whether the engine is reachable and what it actually serves,
        as opposed to what the dashboard was configured to expect.
        """
        return _clip(await engine_call("get_health"), 4000)

    @function_tool
    async def get_engine_metrics() -> str:
        """Read the raw Prometheus metrics exposed by the engine.

        Returns unparsed metric name/value pairs: decode and prefill rates,
        cache hit rate, queue depth, and token counters. Use this when the
        aggregated snapshot is missing a number, or when asked about one
        specific metric.
        """
        return _clip(await engine_call("get_metrics"), 6000)

    @function_tool
    async def get_cache_stats() -> str:
        """Read the engine's KV / prompt cache counters.

        Returns cache hit and miss counts and the derived hit ratio.
        Useful when reasoning about why prefill is fast or slow.
        """
        return _clip(await engine_call("get_cache"), 3000)

    @function_tool
    async def run_tuning_check() -> str:
        """Run the full Strix Halo host compliance audit.

        Runs eight checks -- kernel version, IOMMU, boot parameters, GTT
        size, ROCm SMI, firmware, TuneD profile, udev rules -- and returns
        each one's PASS/WARN/FAIL status with the expected value, the
        actual value, and the performance impact. Use this for any
        question about whether the machine is configured correctly.
        """
        from .tuning_checker import run_all_checks

        return _clip(await run_all_checks(), 8000)

    @function_tool
    async def get_system_info() -> str:
        """Read the hardware baseline: CPU model, GPU, memory bandwidth."""
        from .tuning_checker import get_system_info as _system_info

        return _clip(await _system_info(), 2000)

    @function_tool
    async def count_prompt_tokens(text: str) -> int:
        """Count how many tokens a piece of text costs before sending it.

        Args:
            text: The text to tokenise.

        Returns the exact input token count reported by the engine,
        including chat-template overhead. Use this before drafting a long
        prompt so it fits the context window.
        """
        result = await engine_call(
            "count_tokens",
            messages=[{"role": "user", "content": text}],
            model=(run.model_name if run else None) or state.model_name,
        )
        count = result.get("input_tokens")
        return int(count) if isinstance(count, int) else 0

    @function_tool
    async def list_runner_configs() -> str:
        """List the saved LLM-Runner configurations.

        Returns each configuration's id, name, description, and the
        parameter names it declares. Use this to find the config id that
        start_engine needs.
        """
        from ..routers.runner import get_store

        configs = get_store().list()
        summary = [
            {
                "id": cfg.get("id"),
                "name": cfg.get("name"),
                "description": (cfg.get("description") or "")[:200],
                "parameters": [p.get("name") for p in (cfg.get("parameters") or [])],
            }
            for cfg in configs
        ]
        return _clip({"count": len(summary), "configs": summary}, 6000)

    @function_tool
    async def get_active_run() -> str:
        """List all currently running engine runs.

        Each entry includes its run id, config name, container name,
        engine URL, start time, status, and any engine-switch error.
        """
        from .run_registry import registry

        return _clip({"running": [item.summary() for item in registry.get_running()]}, 3000)

    tools: list[FunctionTool] = [
        get_live_status,
        get_engine_health,
        get_engine_metrics,
        get_cache_stats,
        run_tuning_check,
        get_system_info,
        count_prompt_tokens,
        list_runner_configs,
        get_active_run,
    ]

    if not allow_actions:
        return tools

    # -- action tools (docker.sock reachable; opt-in) -------------------------

    @function_tool
    async def start_engine(config_id: str) -> str:
        """Start the engine for a saved configuration.

        Starts this engine alongside any existing runs. A sufficiently
        provisioned host is required to run multiple engines concurrently.

        Args:
            config_id: The configuration id from list_runner_configs.

        Returns the new run summary including run_id and engine_url.
        """
        from ..routers.runner import ConfigNotFound, launch_config

        try:
            return _clip(await launch_config(state, config_id), 2000)
        except ConfigNotFound:
            return json.dumps({"error": f"configuration {config_id!r} not found"})

    @function_tool
    async def stop_engine(run_id: str) -> str:
        """Stop one running engine container.

        Args:
            run_id: The run id from get_active_run.

        Returns the method that stopped it (docker stop, docker kill, or
        already exited).
        """
        from .run_registry import registry

        run = registry.get_run(run_id)
        if run is None:
            return json.dumps({"error": f"run {run_id!r} was not found"})
        method = await registry.stop(run_id)
        return _clip({"stopped": run_id, "method": method}, 1000)

    tools.extend([start_engine, stop_engine])
    return tools
