"""Parameter substitution for LLM-Runner docker command templates.

A run configuration stores its docker command as a *template* whose
variable parts are written as ``{parameter_name}`` -- for example
``-v {models}/weights.hgn:/models/w.hgn:ro`` or
``-p 0.0.0.0:{port}:{port}``. The configuration also stores the list of
parameters that supply those values, and the backend renders the final
shell command at run time (see routers/runner.py).

Values are inserted **verbatim**, with no shell quoting: the command
template is already raw shell and may legitimately contain ``$(...)``
substitutions, so quoting the values would get in the way. A parameter
that the template actually references must therefore carry a non-empty
value, which ``render_command`` enforces.

Only identifier-shaped braces are treated as placeholders, so a stray
``{`` elsewhere in the command (a JSON literal, a format string) is left
untouched.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Optional

# A parameter name is a plain identifier: letters, digits and underscore,
# not starting with a digit. This is also what a placeholder may contain.
PARAM_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
PLACEHOLDER_RE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")

MAX_PARAM_NAME_CHARS = 60
MAX_PARAM_LABEL_CHARS = 120
MAX_PARAM_VALUE_CHARS = 4_000
MAX_PARAMS = 40


class ParamError(ValueError):
    """Raised for parameter validation / rendering errors."""


def _text(value: Any, field: str) -> str:
    """Coerce a JSON value to a string, rejecting non-scalars."""
    if value is None:
        return ""
    if isinstance(value, bool):
        raise ParamError(f"{field} must be a string, not a boolean")
    if isinstance(value, (str, int, float)):
        return str(value)
    raise ParamError(f"{field} must be a string")


def normalise_parameters(raw: Any) -> list[dict[str, str]]:
    """Validate and normalise a parameter list coming from the API.

    Each entry is reduced to ``{name, label, value}``. An empty ``label``
    falls back to the name so the UI always has something to show. Names
    are unique case-insensitively (placeholder matching itself is exact),
    which keeps near-duplicates like ``port`` / ``PORT`` out of a config.
    """
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ParamError("parameters must be a list")
    if len(raw) > MAX_PARAMS:
        raise ParamError(f"at most {MAX_PARAMS} parameters are allowed")

    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for idx, item in enumerate(raw, start=1):
        if not isinstance(item, dict):
            raise ParamError(f"parameter #{idx} must be an object")

        name = _text(item.get("name"), f"parameter #{idx} name").strip()
        label = _text(item.get("label"), f"parameter #{idx} label").strip()
        value = _text(item.get("value"), f"parameter #{idx} value")

        if not name:
            raise ParamError(f"parameter #{idx}: name is required")
        if len(name) > MAX_PARAM_NAME_CHARS:
            raise ParamError(
                f"parameter #{idx}: name longer than {MAX_PARAM_NAME_CHARS} characters"
            )
        if not PARAM_NAME_RE.match(name):
            raise ParamError(
                f"parameter {name!r}: name must be an identifier "
                "(letters, digits and underscore, not starting with a digit)"
            )
        if name.lower() in seen:
            raise ParamError(f"duplicate parameter {name!r}")
        seen.add(name.lower())
        if len(value) > MAX_PARAM_VALUE_CHARS:
            raise ParamError(
                f"parameter {name!r}: value longer than {MAX_PARAM_VALUE_CHARS} characters"
            )

        out.append({"name": name, "label": label or name, "value": value})
    return out


def find_placeholders(command: str) -> list[str]:
    """Placeholder names in a template, in order of first appearance."""
    found: list[str] = []
    for match in PLACEHOLDER_RE.finditer(command):
        if match.group(1) not in found:
            found.append(match.group(1))
    return found


def missing_parameters(command: str, params: Iterable[dict[str, str]]) -> list[str]:
    """Placeholders used by the template with no matching parameter."""
    names = {p["name"] for p in params}
    return [p for p in find_placeholders(command) if p not in names]


def unused_parameters(command: str, params: Iterable[dict[str, str]]) -> list[str]:
    """Defined parameters the template never references."""
    used = set(find_placeholders(command))
    return [p["name"] for p in params if p["name"] not in used]


def merge_values(
    params: list[dict[str, str]], overrides: Optional[dict[str, Any]] = None
) -> list[dict[str, str]]:
    """Apply run-time ``{name: value}`` overrides onto stored parameters.

    Only names that exist in ``params`` may be overridden; an unknown key
    is rejected rather than silently ignored, so a typo surfaces instead
    of quietly leaving the stored value in place.
    """
    if not overrides:
        return [dict(p) for p in params]

    known = {p["name"] for p in params}
    unknown = [k for k in overrides if k not in known]
    if unknown:
        raise ParamError(
            "unknown parameter(s): " + ", ".join(sorted(unknown))
            + f" -- defined: {', '.join(sorted(known)) or 'none'}"
        )

    merged = []
    for p in params:
        q = dict(p)
        if q["name"] in overrides:
            q["value"] = _text(overrides[q["name"]], f"parameter {q['name']!r}")
        merged.append(q)
    return merged


def render_command(command: str, params: list[dict[str, str]]) -> str:
    """Substitute every ``{name}`` in the template with its value.

    Raises ``ParamError`` if a referenced parameter is missing entirely or
    carries a blank value -- either would produce a broken command line.
    """
    values = {p["name"]: p.get("value", "") for p in params}

    blanks = [
        name
        for name in find_placeholders(command)
        if not str(values.get(name, "")).strip()
    ]
    if blanks:
        raise ParamError(
            "no value supplied for "
            + ", ".join(f"{{{n}}}" for n in blanks)
            + ". Fill it in the configuration, or pass it when starting the run."
        )

    return PLACEHOLDER_RE.sub(lambda m: values[m.group(1)], command)


def validate_template(command: str, params: list[dict[str, str]]) -> None:
    """Author-time check: every placeholder must have a defined parameter.

    Unused parameters are allowed (harmless, and useful while a template
    is being edited) -- the UI surfaces them as a hint instead.
    """
    missing = missing_parameters(command, params)
    if missing:
        raise ParamError(
            "command references undefined parameter(s): "
            + ", ".join(f"{{{n}}}" for n in missing)
        )
