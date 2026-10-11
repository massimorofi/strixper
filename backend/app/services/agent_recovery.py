"""Self-recovery plumbing for the agentic chat.

Three failure modes kept killing runs mid-task, and all three are recoverable
if the model is told what happened instead of the run being torn down:

1. **Truncated tool-call arguments.** A long ``write_file`` body lives inside
   one tool-call JSON object. When the output cap cuts it off mid-string the
   assistant message carries invalid JSON, and the Halogen engine rejects the
   *whole* next request with::

       400 tool_calls[].function.arguments for 'write_file' is not valid JSON:
       Unterminated string starting at: line 1 column 41 (char 40)

   One bad item poisons every subsequent call, so it has to be repaired
   before the request leaves the process. ``sanitize_model_input`` does that
   as a ``call_model_input_filter``.

2. **Hallucinated tool names.** The model occasionally calls ``write`` when
   the tool is ``write_file``. The SDK default aborts the run; returning the
   error to the model with the list of real names lets it correct itself.

3. **Turn-limit exhaustion.** Handled with a
   ``RunErrorHandlers(max_turns=...)`` so the run ends with a usable summary
   rather than a bare error event.

Everything here is deliberately conservative: it repairs what is provably
broken and leaves everything else untouched.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from agents.run_config import CallModelData, ModelInputData

from ..config import settings

logger = logging.getLogger(__name__)

# A tool call whose arguments are not valid JSON is replaced with this. It is
# valid JSON for any schema (an empty object satisfies "type": "object"), so
# the engine accepts the replayed history, and the paired tool output tells
# the model what actually happened.
_TRUNCATED_ARGS = "{}"

_TRUNCATED_NOTE = (
    "Your previous call to '{name}' was cut off before the arguments were "
    "complete, so it never ran. Do not retry it as-is. Write smaller pieces: "
    "for files, write the first part with write_file and continue with "
    "append=true in subsequent calls, no more than {max_write_chars} "
    "characters per call."
)


def _arguments_are_valid(raw: Any) -> bool:
    """True when a tool-call ``arguments`` string parses as a JSON object."""
    if isinstance(raw, dict):
        return True
    if not isinstance(raw, str) or not raw.strip():
        return False
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        return False
    return isinstance(parsed, dict)


def sanitize_model_input(data: CallModelData[Any]) -> ModelInputData:
    """Drop or repair history items that would make the engine reject the request.

    Runs immediately before every model call, so it also protects against a
    truncated call that arrived from the client's own conversation history
    rather than from this run.
    """
    items = list(data.model_data.input or [])
    if not items:
        return ModelInputData(input=items, instructions=data.model_data.instructions)

    answered = {
        item.get("call_id")
        for item in items
        if isinstance(item, dict) and item.get("type") == "function_call_output"
    }

    repaired: list[Any] = []
    fixed_calls = 0

    for item in items:
        if not isinstance(item, dict):
            repaired.append(item)
            continue

        if item.get("type") == "function_call" and not _arguments_are_valid(
            item.get("arguments")
        ):
            name = item.get("name") or "tool"
            call_id = item.get("call_id") or ""
            repaired.append({**item, "arguments": _TRUNCATED_ARGS})
            # Only add the explanation when nothing else answers the call;
            # a second output for the same call_id is itself invalid.
            if call_id and call_id not in answered:
                repaired.append(
                    {
                        "type": "function_call_output",
                        "call_id": call_id,
                        "output": _TRUNCATED_NOTE.format(
                            name=name,
                            max_write_chars=settings.agent_max_write_chars,
                        ),
                    }
                )
                answered.add(call_id)
            fixed_calls += 1
            continue

        repaired.append(item)

    if fixed_calls:
        logger.warning(
            "Repaired %d truncated tool call(s) before sending to the engine",
            fixed_calls,
        )

    repaired = _close_dangling_calls(repaired)
    return ModelInputData(input=repaired, instructions=data.model_data.instructions)


def _close_dangling_calls(items: list[Any]) -> list[Any]:
    """Give every unanswered function_call a tool output.

    Inserted immediately after the call so the pairing stays adjacent, which
    is what chat-completions templates expect.
    """
    answered = {
        item.get("call_id")
        for item in items
        if isinstance(item, dict) and item.get("type") == "function_call_output"
    }
    out: list[Any] = []
    for item in items:
        out.append(item)
        call_id = item.get("call_id") if isinstance(item, dict) else None
        if isinstance(item, dict) and item.get("type") == "function_call" and call_id:
            if call_id not in answered:
                out.append(
                    {
                        "type": "function_call_output",
                        "call_id": call_id,
                        "output": (
                            "No result was recorded for this call. Treat it as "
                            "not done and decide what to do next."
                        ),
                    }
                )
                answered.add(call_id)
    return out


def format_tool_error(args: Any) -> str:
    """Turn a tool-not-found error into guidance the model can act on.

    The SDK's default message says the tool does not exist. That is true and
    useless. This names the tools that do exist and suggests the closest
    match, which is usually all the model needs to recover on the next turn.
    """
    tool_name = getattr(args, "tool_name", "") or "unknown"
    kind = getattr(args, "kind", "")
    if kind != "tool_not_found":
        return ""

    available = _available_tool_names(getattr(args, "run_context", None))
    hint = _closest(tool_name, available)
    message = (
        f"Tool '{tool_name}' does not exist. Available tools: "
        f"{', '.join(available) if available else '(none)'}."
    )
    if hint:
        message += f" Did you mean '{hint}'? Call it with the correct name."
    else:
        message += " Do not call tools that are not in that list."
    return message


def _available_tool_names(run_context: Any) -> list[str]:
    try:
        agent = getattr(run_context, "agent", None)
        tools = getattr(agent, "tools", None) or []
        names = [getattr(t, "name", "") for t in tools]
        return [n for n in names if n]
    except Exception:  # noqa: BLE001 - advisory only
        return []


def _closest(name: str, candidates: list[str]) -> str | None:
    """Cheapest useful suggestion: substring first, then shared-prefix."""
    lowered = name.lower()
    for cand in candidates:
        c = cand.lower()
        if lowered in c or c in lowered:
            return cand
    best: tuple[int, str] | None = None
    for cand in candidates:
        score = _common_prefix_len(lowered, cand.lower())
        if score >= 3 and (best is None or score > best[0]):
            best = (score, cand)
    return best[1] if best else None


def _common_prefix_len(a: str, b: str) -> int:
    n = 0
    for x, y in zip(a, b):
        if x != y:
            break
        n += 1
    return n


def max_turns_handler(args: Any) -> Any:
    """End a turn-limited run with a summary instead of an error."""
    from agents.run_error_handlers import RunErrorHandlerResult

    run_data = getattr(args, "run_data", None)
    text = _last_assistant_text(run_data)
    if text:
        summary = text
    else:
        summary = (
            "The agent reached its turn limit before producing a final "
            "answer. The tool steps above show what was done."
        )
    return RunErrorHandlerResult(final_output=summary, include_in_history=True)


def _last_assistant_text(run_data: Any) -> str:
    """Most recent non-empty assistant message from the run's output items."""
    try:
        items = getattr(run_data, "new_items", None) or []
    except Exception:  # noqa: BLE001
        return ""
    for item in reversed(items):
        raw = getattr(item, "raw_item", None)
        content = raw.get("content") if isinstance(raw, dict) else getattr(raw, "content", None)
        if isinstance(content, str) and content.strip():
            return content.strip()
        if isinstance(content, list):
            parts = [
                p.get("text", "") if isinstance(p, dict) else getattr(p, "text", "")
                for p in content
            ]
            joined = "".join(part for part in parts if part).strip()
            if joined:
                return joined
    return ""
