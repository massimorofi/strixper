"""POST /api/v1/agent/chat -- agentic chat built on the OpenAI Agents SDK.

The non-agentic ``POST /chat`` sends the conversation upstream once and
streams back whatever comes out. This endpoint hands the same conversation
to an agent that can call tools -- inspecting live status, running the
tuning audit, counting tokens, starting or stopping engines -- loop
through as many turns as the task needs, and then answer.

The wire format is deliberately the same SSE vocabulary the plain chat
already uses, so the frontend renders an agent reply exactly like any
other reply:

  event: delta      data: {"text": "..."}     answer text chunk
  event: reasoning  data: {"text": "..."}     model thinking chunk
  event: tool       data: {...}              a tool call and its result
  event: done       data: {"finish_reason": "completed", "usage": {...},
                            "timings": {...}}
  event: error      data: {"message": "..."}

``tool`` is the only addition. Clients that ignore it -- an older frontend,
a curl one-liner -- still get a correct answer; they just do not see the
steps. The current frontend folds those steps into the same collapsed
reasoning block, so the look and feel is unchanged.

Two switches control behaviour, both per request:

  mode="agent"    the agent runs tools and iterates (the default)
  mode="plain"    a single upstream call, identical to /chat
  allow_actions   expose engine start/stop (off by default)
"""

from __future__ import annotations

import json
import time
from typing import Any, AsyncIterator, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from openai import AsyncOpenAI
from openai.types.shared import Reasoning
from agents import (
    Agent,
    FunctionTool,
    ModelSettings,
    OpenAIChatCompletionsModel,
    Runner,
    set_tracing_disabled,
)
from agents.exceptions import MaxTurnsExceeded
from agents.stream_events import RawResponsesStreamEvent, RunItemStreamEvent

from ..config import settings
from ..services.agent_exec_tools import build_exec_tools
from ..services.agent_tools import build_tools

router = APIRouter(tags=["agent"])

MAX_MESSAGE_CHARS = 100_000
MAX_MESSAGES = 200

# The tracing exporter wants a real OpenAI key and complains without one.
# Nothing here traces to OpenAI; everything stays on the local engine.
set_tracing_disabled(True)

BASE_INSTRUCTIONS = """\
You are the assistant inside strixper, an operations dashboard for an AMD
Strix Halo machine running local LLM inference through the Halogen engine.

You have tools that read the same live data the dashboard's tabs show: the
live snapshot, the engine's health and Prometheus metrics, the KV cache
counters, the host tuning audit, the hardware baseline, saved run
configurations, the active run, and a token counter.

How to work:
- Prefer checking over guessing. When asked about the machine, call the
  relevant tool rather than answering from memory or inventing numbers.
- Chain tools when a question needs several facts. If a tuning check
  reports a FAIL and the user asks why, pull the system info too before
  explaining.
- Report actual values. Quote the numbers the tools returned; do not
  round them into vagueness or soften a failure.
- If a tool errors or returns nothing useful, say so plainly instead of
  papering over it, and try a different tool if one fits.
- Be concise. This is an operations console: lead with the answer, then
  the supporting numbers. Use short lists over prose paragraphs.
- If the machine looks misconfigured, say what is wrong and what the
  recommended value is, drawn from the tuning check's own impact text.
"""

# Appended (not replaced) when the full-access tier is on, so the base
# persona survives and the extra guidance travels with the extra tools.
FULL_ACCESS_ADDENDUM = """
You also have execution tools: fetch_url, search_web, run_shell, run_python,
write_file, read_file, and list_directory. Use them to actually do the work
instead of asking the user to do it.

How to work with them:
- Plan first, then act. Decide the steps, then run them. Do not ask
  permission for each step you were already asked to do.
- Browse with fetch_url. It returns readable text, not raw HTML. If a page
  is empty or blocked, try search_web to find another source rather than
  giving up.
- search_web is for finding sources; fetch_url is for reading them. A normal
  research task is: search, pick results, fetch each, then summarise.
- Prefer run_python over run_shell for anything that computes, parses, or
  transforms. Use run_shell for system commands, file operations, and
  package tools.
- Write scripts with write_file, then run them with run_python or run_shell.
  Working files live in a single workspace directory; relative paths resolve
  there.
- Check what you actually got. After a fetch, read the content before
  summarising it. After a script, read its output before reporting success.
- A non-zero exit code is a result, not a failure of your attempt. Read the
  stderr, work out the cause, and fix it or report it.
- Do not delete, overwrite, or modify anything outside the workspace unless
  explicitly asked.
"""


class AgentChatMessage(BaseModel):
    role: str = Field(..., pattern="^(user|assistant|system)$")
    content: str = Field(..., min_length=1, max_length=MAX_MESSAGE_CHARS)


class AgentChatRequest(BaseModel):
    messages: list[AgentChatMessage] = Field(..., min_length=1, max_length=MAX_MESSAGES)
    # "agent" runs the tool loop; "plain" is a single upstream call.
    mode: str = Field(default="agent")
    # Exposes start_engine/stop_engine. Off unless explicitly turned on.
    allow_actions: bool = False
    # Exposes web fetch/search, shell, python execution, and file I/O.
    # Strictly more powerful than allow_actions, so it implies it.
    full_access: bool = False
    max_turns: int = Field(default=settings.agent_max_turns, ge=1, le=30)
    max_tokens: Optional[int] = Field(default=None, ge=1, le=32768)
    temperature: Optional[float] = Field(default=None, ge=0.0, le=2.0)
    thinking: bool = True
    instructions: Optional[str] = None


def _sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _raw_get(raw: Any, key: str, default: Any = None) -> Any:
    """Read a key from a raw item that may be a dict or an object.

    ``ResponseFunctionToolCall`` is TypedDict-like: the fields are dict
    keys, not attributes. Other raw items are real objects. Handle both so
    one helper covers every case.
    """
    if isinstance(raw, dict):
        return raw.get(key, default)
    return getattr(raw, key, default)


def _tool_detail(raw: Any) -> str:
    """A short human-readable summary of a tool call's arguments."""
    args = _raw_get(raw, "arguments", "")
    try:
        parsed = json.loads(args) if args else {}
    except (TypeError, ValueError):
        parsed = {}
    if isinstance(parsed, dict) and parsed:
        return ", ".join(f"{k}={v}" for k, v in list(parsed.items())[:3])
    return ""


def _truncate(text: str, limit: int) -> str:
    if text is None:
        return ""
    return text if len(text) <= limit else text[:limit] + "… [truncated]"


def _resolve_tools(state: Any, body: AgentChatRequest) -> list[FunctionTool]:
    """Pick the tool tier for this request.

    Three tiers, each a superset of the previous:

      read-only        status, metrics, tuning, token counting, configs
      allow_actions   + start_engine / stop_engine
      full_access     + web, shell, python, file I/O

    ``full_access`` implies ``allow_actions``: an agent that can run shell
    commands can already do anything the engine controls can do, so
    withholding them would only be confusing.
    """
    tools = build_tools(state, body.allow_actions or body.full_access)
    if body.full_access:
        tools.extend(build_exec_tools(state, settings.agent_workspace))
    return tools


def _build_agent(state: Any, body: AgentChatRequest) -> Agent:
    """Assemble the agent bound to the current engine target."""
    base_url = state.halogen.base_url
    model = OpenAIChatCompletionsModel(
        model=state.model_name or settings.default_model,
        openai_client=AsyncOpenAI(base_url=f"{base_url}/v1", api_key="none"),
    )
    # ``reasoning.effort="none"`` is how Halogen disables thinking, and the
    # SDK forwards it to chat-completions from that nested shape only. A flat
    # ``reasoning_effort=`` kwarg is silently dropped by the pydantic
    # dataclass, so thinking would stay on. Verified to coexist with tool
    # calling on this engine.
    model_settings = ModelSettings(
        temperature=body.temperature if body.temperature is not None else 0.2,
        max_tokens=body.max_tokens if body.max_tokens is not None else settings.agent_max_tokens,
        reasoning=None if body.thinking else Reasoning(effort="none"),
        # The SDK only asks for usage on streaming when it recognises a real
        # OpenAI endpoint. Halogen is not one, so ask explicitly -- without
        # this the final usage block never arrives and the token stats are
        # blank.
        include_usage=True,
    )
    # Per-request override wins over the AGENT_INSTRUCTIONS env var, which in
    # turn wins over the built-in prompt. The full-access guidance is appended
    # to whichever of those is used -- it describes tools the model would
    # otherwise not know it has.
    instructions = (
        (body.instructions or "").strip()
        or settings.agent_instructions
        or BASE_INSTRUCTIONS
    )
    if body.full_access and FULL_ACCESS_ADDENDUM.strip() not in instructions:
        instructions = instructions.rstrip() + "\n" + FULL_ACCESS_ADDENDUM
    return Agent(
        name="strixper-assistant",
        instructions=instructions,
        model=model,
        model_settings=model_settings,
        tools=_resolve_tools(state, body),
    )


def _sum_usage(responses: list[Any]) -> Optional[dict[str, int]]:
    """Add up token usage across every model call in the run.

    An agentic answer is several upstream calls, not one, so the totals
    have to be summed or they understate the cost. Halogen does not
    report usage on the Responses path, so everything can come back zero;
    in that case nothing is returned rather than a misleading row of zeros.
    """
    input_tokens = 0
    output_tokens = 0
    requests = 0
    for resp in responses or []:
        usage = (
            resp.get("usage") if isinstance(resp, dict) else getattr(resp, "usage", None)
        )
        if not usage:
            continue
        inp = (
            usage.get("input_tokens")
            if isinstance(usage, dict)
            else getattr(usage, "input_tokens", None)
        )
        out = (
            usage.get("output_tokens")
            if isinstance(usage, dict)
            else getattr(usage, "output_tokens", None)
        )
        if isinstance(inp, int):
            input_tokens += inp
        if isinstance(out, int):
            output_tokens += out
        requests += 1
    if not (input_tokens or output_tokens):
        return None
    return {"input_tokens": input_tokens, "output_tokens": output_tokens}


@router.post("/agent/chat")
async def agent_chat(body: AgentChatRequest, request: Request) -> Any:
    """Run an agentic turn over the conversation and stream the result."""
    state = request.app.state.rt

    if body.mode == "plain":
        # Hand back to the existing single-shot path rather than
        # reimplementing it, so "plain" really is the same thing.
        from .chat import ChatRequest, chat as plain_chat

        return await plain_chat(
            ChatRequest(
                messages=[m.model_dump() for m in body.messages],
                api="chat",
                stream=True,
                max_tokens=body.max_tokens,
                temperature=body.temperature,
                thinking=body.thinking,
            ),
            request,
        )

    agent = _build_agent(state, body)

    async def event_stream() -> AsyncIterator[str]:
        started = time.monotonic()
        tool_count = 0
        # call_id -> tool name, so the output event can name the tool that
        # produced it: function_call_output carries only the call id.
        names: dict[str, str] = {}
        try:
            result = Runner.run_streamed(
                agent,
                input=[m.model_dump() for m in body.messages],
                max_turns=body.max_turns,
            )
            async for event in result.stream_events():
                if isinstance(event, RawResponsesStreamEvent):
                    data = event.data
                    etype = data.type
                    if etype == "response.output_text.delta" and data.delta:
                        yield _sse("delta", {"text": data.delta})
                    elif etype == "response.reasoning_summary_text.delta" and data.delta:
                        yield _sse("reasoning", {"text": data.delta})
                    continue

                if not isinstance(event, RunItemStreamEvent):
                    continue

                if event.name == "tool_called":
                    item = event.item
                    raw = item.raw_item
                    name = _raw_get(raw, "name", "tool")
                    call_id = _raw_get(raw, "call_id", "")
                    if call_id:
                        names[call_id] = name
                    tool_count += 1
                    yield _sse(
                        "tool",
                        {
                            "phase": "call",
                            "name": name,
                            "detail": _tool_detail(raw),
                        },
                    )

                elif event.name == "tool_output":
                    item = event.item
                    raw = item.raw_item
                    call_id = _raw_get(raw, "call_id", "")
                    out = item.output
                    if not isinstance(out, str):
                        out = json.dumps(out, ensure_ascii=False)
                    yield _sse(
                        "tool",
                        {
                            "phase": "result",
                            "name": names.get(call_id, "tool"),
                            "output": _truncate(out, 1200),
                        },
                    )

            elapsed = time.monotonic() - started
            yield _sse(
                "done",
                {
                    "finish_reason": "completed",
                    "usage": _sum_usage(getattr(result, "raw_responses", [])),
                    "timings": {"total_ms": round(elapsed * 1000)},
                    "tool_calls": tool_count,
                },
            )

        except MaxTurnsExceeded as exc:
            yield _sse(
                "error",
                {
                    "message": (
                        f"The agent reached its {body.max_turns}-turn limit "
                        f"without finishing: {exc}"
                    )
                },
            )
        except Exception as exc:  # noqa: BLE001 - stream must end cleanly
            yield _sse("error", {"message": f"Agent run failed: {exc}"})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# Tools that can change state outside the model's own reasoning. Surfaced so
# the UI can warn rather than let the user discover it later.
_DESTRUCTIVE = {"start_engine", "stop_engine", "run_shell", "run_python", "write_file"}


@router.get("/agent/tools")
async def agent_tools_list(
    request: Request,
    allow_actions: bool = Query(False),
    full_access: bool = Query(False),
) -> dict[str, Any]:
    """The tools available to the agent right now.

    Reflects the same state the agent sees, so the UI can show which
    capabilities are live -- and whether actions are enabled -- without
    running a turn. Both flags must match what a chat request will send,
    otherwise this reports a capability set the agent does not have.
    """
    state = request.app.state.rt
    tools = build_tools(state, allow_actions=allow_actions or full_access)
    if full_access:
        tools = tools + build_exec_tools(state, settings.agent_workspace)
    return {
        "allow_actions": allow_actions or full_access,
        "full_access": full_access,
        "tier": "full" if full_access else ("actions" if allow_actions else "read-only"),
        "tools": [
            {
                "name": getattr(t, "name", "?"),
                "description": (getattr(t, "description", "") or "").strip(),
                "destructive": getattr(t, "name", "") in _DESTRUCTIVE,
            }
            for t in tools
        ]
    }
