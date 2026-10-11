"""Application settings loaded from environment variables / a .env file."""

from __future__ import annotations

import os
from pathlib import Path

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


def _get_list(name: str, default: list[str]) -> list[str]:
    """Comma-separated list setting; blank or unset falls back to *default*."""
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return list(default)
    items = [item.strip() for item in raw.split(",")]
    items = [item for item in items if item]
    return items if items else list(default)


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
        # Research tasks need headroom: a single "find and summarise X" job
        # can easily spend 15+ steps (search, fetch, write script, run it,
        # read output, refine). 8 cuts the agent off mid-task.
        self.agent_max_turns: int = _get_int("AGENT_MAX_TURNS", 150)
        # Output cap per model response. A long ``write_file`` call puts the
        # whole file body inside one tool-call JSON object, so a small cap
        # truncates the arguments and the call fails to parse. 2048 was too
        # small in practice, and 8192 still cut large HTML/CSS bodies off
        # mid-string; 16384 leaves room for a substantial file in one call.
        self.agent_max_tokens: int = _get_int("AGENT_MAX_TOKENS", 16384)
        # Largest single file body the agent may write in one ``write_file``
        # call. Beyond this the tool refuses with instructions to continue in
        # ``append=True`` chunks, so an over-long generation cannot leave a
        # half-written file behind and call it a success.
        self.agent_max_write_chars: int = _get_int("AGENT_MAX_WRITE_CHARS", 12_000)
        # Optional replacement for the built-in system prompt.
        self.agent_instructions: str = os.getenv("AGENT_INSTRUCTIONS", "").strip()
        # Working directory for the full-access tier: downloads, generated
        # scripts, and any files the agent writes. Defaults to a directory
        # next to the backend package so it resolves the same way inside the
        # container (/app/backend/...) and in a local checkout.
        _backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.agent_workspace: str = os.getenv(
            "AGENT_WORKSPACE", os.path.join(_backend_dir, "agent_workspace")
        ).strip()
        # Root for user uploads and the workspace browser. Defaults to the
        # agent workspace so the agent sees exactly what the user uploads;
        # a separate value is only useful when the two must be isolated.
        self.workspace_root: str = os.getenv(
            "STRIXPER_WORKSPACE", self.agent_workspace
        ).strip()
        # ---- Upload / vision limits --------------------------------------
        self.max_image_size_mb: int = _get_int("MAX_IMAGE_SIZE_MB", 10)
        self.max_file_size_mb: int = _get_int("MAX_FILE_SIZE_MB", 50)
        self.max_images_per_message: int = _get_int("MAX_IMAGES_PER_MESSAGE", 5)
        # Pixel budget enforced at upload time. The chat path uses the
        # engine's own advertised limits; these are the fallback for when
        # no engine has reported yet, and the hard ceiling for uploads.
        self.image_max_pixels: int = _get_int("IMAGE_MAX_PIXELS", 3_686_400)
        self.image_size_multiple: int = _get_int("IMAGE_SIZE_MULTIPLE", 32)
        # Uploaded/generated files older than this are removed at startup.
        self.workspace_ttl_days: int = _get_int("WORKSPACE_TTL_DAYS", 7)
        self.allowed_image_types: list[str] = _get_list(
            "ALLOWED_IMAGE_TYPES",
            ["image/png", "image/jpeg", "image/webp", "image/gif"],
        )
        # ``["*"]`` means any file type is accepted.
        self.allowed_file_types: list[str] = _get_list("ALLOWED_FILE_TYPES", ["*"])
        # Largest number of text characters ``workspace_read`` returns.
        self.max_workspace_read_bytes: int = _get_int(
            "MAX_WORKSPACE_READ_BYTES", 1_000_000
        )
        self.skills_dir: str = os.getenv(
            "SKILLS_DIR",
            str(Path(__file__).resolve().parents[2] / "skills"),
        ).strip()
        self.data_dir: str = os.getenv(
            "STRIXPER_DATA_DIR",
            str(Path(_backend_dir) / "data"),
        ).strip()
        self.mcp_servers_dir: str = os.getenv(
            "MCP_SERVERS_DIR",
            str(Path(__file__).resolve().parents[2] / "mcp-servers"),
        ).strip()
        # MCP server commands execute as this process and inherit the
        # container's mounts/privileges. Management APIs fail closed unless
        # an administrator configures a high-entropy bearer token.
        self.mcp_admin_token: str = os.getenv("MCP_ADMIN_TOKEN", "").strip()
        self.mcp_call_timeout_seconds: float = _get_float(
            "MCP_CALL_TIMEOUT_SECONDS", 30.0
        )


settings = Settings()
