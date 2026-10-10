"""Application settings loaded from environment variables / a .env file."""

from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()

DEFAULT_POLL_INTERVAL = 5.0


def _get_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def _get_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


class Settings:
    """Startup settings (see .env.example for the documented keys)."""

    def __init__(self) -> None:
        self.halogen_host: str = os.getenv("HALOGEN_HOST", "http://127.0.0.1:8731").rstrip("/")
        self.poll_interval_seconds: float = _get_float("POLL_INTERVAL_SECONDS", DEFAULT_POLL_INTERVAL)
        self.bind_host: str = os.getenv("BIND_HOST", "0.0.0.0")
        self.bind_port: int = int(os.getenv("BIND_PORT", "8000"))
        # Model id assumed before any engine has told us what it serves.
        # A run configuration's `served_model_name` and the engine's own
        # /health both override it.
        self.default_model: str = os.getenv(
            "DEFAULT_MODEL", "halogen-qwen3.8-flash-next"
        ).strip()

        # ---- Agentic chat -------------------------------------------------
        # The agent talks to the same engine through the OpenAI-compatible
        # API, so it needs no separate endpoint. These settings bound the
        # loop; the per-request body can override the turn limit.
        self.agent_max_turns: int = _get_int("AGENT_MAX_TURNS", 8)
        # Optional replacement for the built-in system prompt.
        self.agent_instructions: str = os.getenv("AGENT_INSTRUCTIONS", "").strip()


settings = Settings()
