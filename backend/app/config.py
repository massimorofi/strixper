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


class Settings:
    """Startup settings (see .env.example for the documented keys)."""

    def __init__(self) -> None:
        self.halogen_host: str = os.getenv("HALOGEN_HOST", "http://127.0.0.1:8731").rstrip("/")
        self.poll_interval_seconds: float = _get_float("POLL_INTERVAL_SECONDS", DEFAULT_POLL_INTERVAL)
        self.bind_host: str = os.getenv("BIND_HOST", "0.0.0.0")
        self.bind_port: int = int(os.getenv("BIND_PORT", "8000"))


settings = Settings()
