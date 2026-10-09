"""Local JSON store for LLM run configurations (LLM-Runner tab).

Each configuration captures the docker command used to launch a containerized
LLM inference server, together with a human-friendly name and description. The
command is a *template*: its variable parts are written as ``{parameter_name}``
and the matching values live in the configuration's ``parameters`` list (see
services/params.py). The store validates the two against each other on every
write, so a saved configuration always renders into a complete command.
Configurations are persisted to a JSON file on disk so they survive backend
restarts. All mutations go through an asyncio lock and are written atomically
(temp file + os.replace) so a crash mid-write cannot corrupt the store.
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .params import ParamError, normalise_parameters, validate_template

# Default location: backend/data/llm_runner_configs.json (override with
# RUNNER_STORE_PATH for tests or alternative layouts).
DEFAULT_STORE_PATH = (
    Path(__file__).resolve().parent.parent.parent / "data" / "llm_runner_configs.json"
)
STORE_PATH = Path(os.getenv("RUNNER_STORE_PATH", str(DEFAULT_STORE_PATH)))

MAX_NAME_CHARS = 120
MAX_COMMAND_CHARS = 10_000
MAX_DESCRIPTION_CHARS = 1_000


def _utc_now() -> str:
    return (
        datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    )


class RunnerStoreError(Exception):
    """Raised for store-level validation / not-found errors."""


class RunnerStore:
    """CRUD store for named docker run configurations."""

    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = Path(path) if path else STORE_PATH
        self._lock = asyncio.Lock()
        self._configs: list[dict[str, Any]] = []
        self._load()

    # -- persistence ----------------------------------------------------------

    def _load(self) -> None:
        try:
            raw = self.path.read_text(encoding="utf-8")
            data = json.loads(raw)
            configs = data if isinstance(data, list) else []
        except (OSError, json.JSONDecodeError):
            # Missing or corrupt file: start from an empty list. The next
            # successful mutation will rewrite the file.
            configs = []

        # Configurations written before parameters existed have no
        # `parameters` key; normalise it to an empty list so every config
        # read out of the store has the same shape. A stored list that no
        # longer validates (hand-edited file, say) is dropped rather than
        # poisoning the whole store.
        for cfg in configs:
            if not isinstance(cfg, dict):
                continue
            try:
                cfg["parameters"] = normalise_parameters(cfg.get("parameters"))
            except ParamError:
                cfg["parameters"] = []
        self._configs = configs

    def _persist(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._configs, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)

    # -- queries ---------------------------------------------------------------

    def list(self) -> list[dict[str, Any]]:
        return list(self._configs)

    def get(self, config_id: str) -> Optional[dict[str, Any]]:
        for cfg in self._configs:
            if cfg.get("id") == config_id:
                return dict(cfg)
        return None

    # -- mutations --------------------------------------------------------------

    def _check_name_unique(self, name: str, exclude_id: Optional[str] = None) -> None:
        lowered = name.strip().lower()
        for cfg in self._configs:
            if cfg.get("id") != exclude_id and cfg.get("name", "").strip().lower() == lowered:
                raise RunnerStoreError(f"A configuration named {name!r} already exists")

    @staticmethod
    def _check_template(docker_command: str, parameters: list[dict[str, str]]) -> None:
        """Reject a command whose placeholders the parameter list can't fill."""
        try:
            validate_template(docker_command, parameters)
        except ParamError as exc:
            raise RunnerStoreError(str(exc)) from exc

    async def create(
        self,
        name: str,
        docker_command: str,
        description: str = "",
        parameters: Optional[list[Any]] = None,
    ) -> dict[str, Any]:
        name = name.strip()
        docker_command = docker_command.strip()
        if not name:
            raise RunnerStoreError("name must not be blank")
        if not docker_command:
            raise RunnerStoreError("docker_command must not be blank")
        try:
            params = normalise_parameters(parameters)
        except ParamError as exc:
            raise RunnerStoreError(str(exc)) from exc
        self._check_template(docker_command, params)
        async with self._lock:
            self._check_name_unique(name)
            now = _utc_now()
            cfg = {
                "id": uuid.uuid4().hex[:12],
                "name": name,
                "docker_command": docker_command,
                "description": description.strip(),
                "parameters": params,
                "created_at": now,
                "updated_at": now,
            }
            self._configs.append(cfg)
            self._persist()
            return dict(cfg)

    async def update(
        self,
        config_id: str,
        name: Optional[str] = None,
        docker_command: Optional[str] = None,
        description: Optional[str] = None,
        parameters: Optional[list[Any]] = None,
    ) -> dict[str, Any]:
        # Normalise what was supplied before touching the store, so a bad
        # payload leaves nothing behind.
        new_params = None
        if parameters is not None:
            try:
                new_params = normalise_parameters(parameters)
            except ParamError as exc:
                raise RunnerStoreError(str(exc)) from exc

        async with self._lock:
            idx = next(
                (i for i, c in enumerate(self._configs) if c.get("id") == config_id),
                None,
            )
            if idx is None:
                raise RunnerStoreError(f"configuration {config_id!r} not found")
            cfg = self._configs[idx]

            # Resolve what the stored configuration *would* become, validate
            # that whole picture, and only then write it. Validating after
            # assigning would leave the in-memory copy corrupted on a
            # rejected payload -- the file would stay correct while the
            # running process diverged from it.
            next_name = cfg.get("name", "")
            if name is not None:
                name = name.strip()
                if not name:
                    raise RunnerStoreError("name must not be blank")
                self._check_name_unique(name, exclude_id=config_id)
                next_name = name

            next_command = cfg.get("docker_command", "")
            if docker_command is not None:
                docker_command = docker_command.strip()
                if not docker_command:
                    raise RunnerStoreError("docker_command must not be blank")
                next_command = docker_command

            next_params = new_params if new_params is not None else (
                cfg.get("parameters") or []
            )

            self._check_template(next_command, next_params)

            cfg["name"] = next_name
            cfg["docker_command"] = next_command
            cfg["parameters"] = next_params
            if description is not None:
                cfg["description"] = description.strip()
            cfg["updated_at"] = _utc_now()
            self._persist()
            return dict(cfg)

    async def delete(self, config_id: str) -> None:
        async with self._lock:
            before = len(self._configs)
            self._configs = [c for c in self._configs if c.get("id") != config_id]
            if len(self._configs) == before:
                raise RunnerStoreError(f"configuration {config_id!r} not found")
            self._persist()
