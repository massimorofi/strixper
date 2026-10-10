"""Persistent MCP server configuration and stdio session management."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import tempfile
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncGenerator, Literal

from agents import FunctionTool
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from ..config import settings

MCP_START_TIMEOUT_SECONDS = 20
MCP_MAX_SERVERS = 32
MCP_MAX_TOOLS_PER_SERVER = 64
MCP_MAX_TOOLS_TOTAL = 256
MCP_MAX_TOOL_INPUT_BYTES = 100_000
MCP_MAX_TOOL_OUTPUT_BYTES = 32_000
_ID = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")
_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_ACCESS_RANK = {"read-only": 0, "actions": 1, "full_access": 2}


class SecretReference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    secretRef: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")


class MCPServerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=64)
    transport: Literal["stdio"] = "stdio"
    command: str = Field(min_length=1, max_length=256)
    args: list[str] = Field(default_factory=list, max_length=64)
    env: dict[str, SecretReference] = Field(default_factory=dict, max_length=32)
    enabled: bool = False
    minimum_access: Literal["read-only", "actions", "full_access"] = "read-only"

    @field_validator("id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        if not _ID.fullmatch(value):
            raise ValueError("id must contain lowercase letters, numbers, and hyphens")
        return value

    @field_validator("command")
    @classmethod
    def validate_command(cls, value: str) -> str:
        if "\x00" in value or value.strip() != value:
            raise ValueError("command contains invalid whitespace or NUL")
        return value

    @field_validator("args")
    @classmethod
    def validate_args(cls, values: list[str]) -> list[str]:
        if any("\x00" in value or len(value) > 4096 for value in values):
            raise ValueError("arguments may not contain NUL or exceed 4096 characters")
        return values

    @field_validator("env")
    @classmethod
    def validate_env(cls, values: dict[str, SecretReference]) -> dict[str, SecretReference]:
        if any(not _ENV_NAME.fullmatch(key) for key in values):
            raise ValueError("environment variable names must follow POSIX syntax")
        return values


class MCPStore(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    servers: list[MCPServerConfig] = Field(default_factory=list, max_length=MCP_MAX_SERVERS)

    @field_validator("servers")
    @classmethod
    def unique_ids(cls, servers: list[MCPServerConfig]) -> list[MCPServerConfig]:
        ids = [server.id for server in servers]
        if len(ids) != len(set(ids)):
            raise ValueError("server IDs must be unique")
        return servers


@dataclass
class _ConnectedServer:
    config: MCPServerConfig
    stack: AsyncExitStack
    session: ClientSession
    tools: list[Any]
    call_lock: asyncio.Lock = field(default_factory=asyncio.Lock)


def _sanitize_schema(value: Any) -> None:
    if isinstance(value, dict):
        value.pop("default", None)
        for nested in value.values():
            _sanitize_schema(nested)
    elif isinstance(value, list):
        for nested in value:
            _sanitize_schema(nested)


def _safe_tool_name(server_id: str, name: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9_-]", "_", f"mcp_{server_id}_{name}").strip("_")
    if len(normalized) <= 64:
        return normalized
    digest = hashlib.sha256(normalized.encode()).hexdigest()[:8]
    return f"{normalized[:55]}_{digest}"


class MCPManager:
    def __init__(self, config_path: str | Path):
        self.config_path = Path(config_path)
        self._lock = asyncio.Lock()
        self._server_locks: dict[str, asyncio.Lock] = {}
        self._configs: dict[str, MCPServerConfig] = {}
        self._connected: dict[str, _ConnectedServer] = {}
        self._errors: dict[str, str] = {}
        self._load_error: str | None = None
        self._load()

    def _load(self) -> None:
        if not self.config_path.exists():
            self._configs = {}
            return
        try:
            raw = json.loads(self.config_path.read_text(encoding="utf-8"))
            store = MCPStore.model_validate(raw)
            configs = {config.id: config for config in store.servers}
            legacy_tieline = configs.get("tieline")
            if (
                legacy_tieline is not None
                and legacy_tieline.command == "npx"
                and any(argument.startswith("tieline@") for argument in legacy_tieline.args)
                and "serve" in legacy_tieline.args
            ):
                environment = dict(legacy_tieline.env)
                environment.setdefault(
                    "TIELINE_WORKSPACE",
                    SecretReference(secretRef="TIELINE_WORKSPACE"),
                )
                configs["tieline"] = legacy_tieline.model_copy(
                    update={
                        "command": "tieline",
                        "args": ["serve"],
                        "env": environment,
                    }
                )
                self._persist(configs)
            self._configs = configs
        except (OSError, UnicodeError, json.JSONDecodeError, ValidationError, ValueError) as exc:
            self._load_error = f"Configuration file is invalid ({type(exc).__name__})."
            self._configs = {}

    def has_server(self, server_id: str) -> bool:
        return server_id in self._configs

    def _server_lock(self, server_id: str) -> asyncio.Lock:
        if server_id not in self._server_locks:
            self._server_locks[server_id] = asyncio.Lock()
        return self._server_locks[server_id]

    @asynccontextmanager
    async def _server_update_lock(self, server_id: str) -> AsyncGenerator[None, None]:
        async with self._lock:
            async with self._server_lock(server_id):
                yield

    def _persist(self, configs: dict[str, MCPServerConfig]) -> None:
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        store = MCPStore(servers=list(configs.values()))
        fd, temporary_path = tempfile.mkstemp(
            prefix=f".{self.config_path.name}.",
            suffix=".tmp",
            dir=self.config_path.parent,
        )
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(store.model_dump(mode="json"), stream, indent=2, ensure_ascii=False)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_path, self.config_path)
        except Exception:
            try:
                os.unlink(temporary_path)
            except OSError:
                pass
            raise

    def _resolve_environment(
        self, config: MCPServerConfig, *, redact_errors: bool = False
    ) -> dict[str, str]:
        resolved: dict[str, str] = {}
        missing: list[str] = []
        for key, reference in config.env.items():
            value = os.getenv(reference.secretRef)
            if value is None:
                missing.append(reference.secretRef)
            else:
                resolved[key] = value
        if missing:
            detail = "required environment secret reference(s) are missing"
            if not redact_errors:
                detail += f": {', '.join(missing)}"
            raise ValueError(detail)
        return resolved

    def _process_environment(self, config: MCPServerConfig) -> dict[str, str]:
        # Do not inherit MCP_ADMIN_TOKEN or unrelated backend credentials.
        # Server-specific values are forwarded only through explicit refs.
        inherited_names = (
            "PATH",
            "HOME",
            "TMPDIR",
            "TMP",
            "TEMP",
            "LANG",
            "LC_ALL",
            "TZ",
            "SSL_CERT_FILE",
            "SSL_CERT_DIR",
        )
        environment = {
            name: os.environ[name]
            for name in inherited_names
            if name in os.environ
        }
        inherited_path = environment.get("PATH", "")
        server_root = Path(settings.mcp_servers_dir) / config.id
        server_bins = (
            server_root / "bin",
            server_root / ".venv" / "bin",
            server_root / "node_modules" / ".bin",
            Path(settings.mcp_servers_dir) / "bin",
        )
        path_entries = [str(path) for path in server_bins]
        if inherited_path:
            path_entries.append(inherited_path)
        environment["PATH"] = os.pathsep.join(path_entries)
        environment.update(self._resolve_environment(config, redact_errors=True))
        return environment

    async def _connect(self, config: MCPServerConfig) -> _ConnectedServer:
        stack = AsyncExitStack()
        try:
            params = StdioServerParameters(
                command=config.command,
                args=config.args,
                env=self._process_environment(config),
            )
            read_stream, write_stream = await asyncio.wait_for(
                stack.enter_async_context(stdio_client(params)),
                timeout=MCP_START_TIMEOUT_SECONDS,
            )
            session = await asyncio.wait_for(
                stack.enter_async_context(ClientSession(read_stream, write_stream)),
                timeout=MCP_START_TIMEOUT_SECONDS,
            )
            await asyncio.wait_for(
                session.initialize(), timeout=MCP_START_TIMEOUT_SECONDS
            )
            response = await asyncio.wait_for(
                session.list_tools(), timeout=MCP_START_TIMEOUT_SECONDS
            )
            if len(response.tools) > MCP_MAX_TOOLS_PER_SERVER:
                raise ValueError(
                    f"server exposes more than {MCP_MAX_TOOLS_PER_SERVER} tools"
                )
            return _ConnectedServer(config, stack, session, list(response.tools))
        except BaseException:
            await stack.aclose()
            raise

    async def _ensure_connected(self, config: MCPServerConfig) -> _ConnectedServer:
        async with self._server_lock(config.id):
            if self._configs.get(config.id) != config:
                raise RuntimeError("MCP server configuration changed during connection.")
            current = self._connected.get(config.id)
            if current and current.config == config:
                return current
            try:
                replacement = await self._connect(config)
            except Exception as exc:
                self._errors[config.id] = f"Connection failed ({type(exc).__name__})."
                raise RuntimeError(self._errors[config.id]) from None
            old = self._connected.get(config.id)
            self._connected[config.id] = replacement
            self._errors.pop(config.id, None)
            if old:
                async with old.call_lock:
                    await old.stack.aclose()
            return replacement

    def _make_tool(self, connected: _ConnectedServer, tool_definition: Any) -> FunctionTool:
        schema = getattr(tool_definition, "input_schema", None)
        if schema is None:
            schema = getattr(tool_definition, "inputSchema", None)
        if not isinstance(schema, dict) or schema.get("type", "object") != "object":
            raise ValueError(f"MCP tool {tool_definition.name!r} has an invalid input schema")
        schema = json.loads(json.dumps(schema))
        _sanitize_schema(schema)
        if len(json.dumps(schema)) > 32_000:
            raise ValueError(f"MCP tool {tool_definition.name!r} schema is too large")
        exposed_name = _safe_tool_name(connected.config.id, tool_definition.name)

        async def invoke(_context: Any, raw_input: str) -> str:
            _ = _context
            if len(raw_input.encode("utf-8")) > MCP_MAX_TOOL_INPUT_BYTES:
                raise ValueError("MCP tool arguments exceed the input size limit")
            arguments = json.loads(raw_input)
            if not isinstance(arguments, dict):
                raise ValueError("MCP tool arguments must be a JSON object")
            async with connected.call_lock:
                if self._connected.get(connected.config.id) is not connected:
                    raise RuntimeError("MCP server configuration changed; retry this tool call")
                result = await asyncio.wait_for(
                    connected.session.call_tool(tool_definition.name, arguments),
                    timeout=settings.mcp_call_timeout_seconds,
                )
            chunks: list[str] = []
            structured_content = getattr(result, "structured_content", None)
            if structured_content is None:
                structured_content = getattr(result, "structuredContent", None)
            if structured_content is not None:
                chunks.append(
                    json.dumps(structured_content, ensure_ascii=False, default=str)
                )
            else:
                for item in result.content:
                    text = getattr(item, "text", None)
                    if isinstance(text, str):
                        chunks.append(text)
                    else:
                        mime_type = getattr(item, "mime_type", None)
                        if mime_type is None:
                            mime_type = getattr(item, "mimeType", None)
                        kind = type(item).__name__
                        chunks.append(
                            f"[Non-text MCP content omitted: {mime_type or kind}]"
                        )
            output = "\n".join(chunks)
            if len(output.encode("utf-8")) > MCP_MAX_TOOL_OUTPUT_BYTES:
                output = (
                    output.encode("utf-8")[:MCP_MAX_TOOL_OUTPUT_BYTES]
                    .decode("utf-8", errors="ignore")
                    + "\n[truncated]"
                )
            is_error = getattr(result, "is_error", None)
            if is_error is None:
                is_error = getattr(result, "isError", None)
            if is_error is None:
                raise RuntimeError("MCP response omitted its error status.")
            if is_error:
                raise RuntimeError(f"MCP server reported a tool error: {output}")
            return output

        return FunctionTool(
            name=exposed_name,
            description=(tool_definition.description or f"MCP tool {tool_definition.name}")[:1024],
            params_json_schema=schema,
            on_invoke_tool=invoke,
            strict_json_schema=False,
            timeout_seconds=settings.mcp_call_timeout_seconds + 2,
        )

    async def get_tools_for_access(self, access: str) -> list[FunctionTool]:
        if access not in _ACCESS_RANK:
            raise ValueError(f"unknown agent access level: {access}")
        if not settings.mcp_admin_token:
            return []
        eligible = [
            config
            for config in list(self._configs.values())
            if config.enabled
            and _ACCESS_RANK[access] >= _ACCESS_RANK[config.minimum_access]
        ]
        outcomes = await asyncio.gather(
            *(self._ensure_connected(config) for config in eligible),
            return_exceptions=True,
        )
        # Individual startup failures are retained in status and do not block
        # healthy MCP servers or built-in agent tools.
        connected_servers = [
            outcome for outcome in outcomes if isinstance(outcome, _ConnectedServer)
        ]
        tools: list[FunctionTool] = []
        names: set[str] = set()
        for connected in connected_servers:
            for definition in connected.tools:
                try:
                    tool = self._make_tool(connected, definition)
                except Exception as exc:
                    self._errors[connected.config.id] = (
                        f"Tool schema rejected ({type(exc).__name__})."
                    )
                    continue
                if tool.name in names:
                    self._errors[connected.config.id] = (
                        f"Duplicate normalized tool name rejected: {tool.name}."
                    )
                    continue
                if len(tools) >= MCP_MAX_TOOLS_TOTAL:
                    self._errors[connected.config.id] = (
                        f"Tool limit of {MCP_MAX_TOOLS_TOTAL} reached."
                    )
                    break
                names.add(tool.name)
                tools.append(tool)
        return tools

    def list_configs(self, *, include_tools: bool = True) -> list[dict[str, Any]]:
        result = []
        for config in sorted(self._configs.values(), key=lambda item: item.id):
            connection = self._connected.get(config.id)
            result.append(
                {
                    **config.model_dump(mode="json"),
                    "status": (
                        "connected" if connection else
                        "error" if config.id in self._errors else
                        "disabled" if not config.enabled else "disconnected"
                    ),
                    "error": self._errors.get(config.id),
                    "tools": (
                        [
                            _safe_tool_name(config.id, tool.name)
                            for tool in (connection.tools if connection else [])
                        ]
                        if include_tools
                        else []
                    ),
                }
            )
        return result

    def manager_status(self) -> dict[str, Any]:
        return {
            "configured": bool(settings.mcp_admin_token),
            "config_error": self._load_error,
            "servers": self.list_configs(),
        }

    async def test_config(self, config: MCPServerConfig) -> dict[str, Any]:
        temporary: _ConnectedServer | None = None
        try:
            temporary = await self._connect(config.model_copy(update={"enabled": True}))
            return {
                "ok": True,
                "tool_count": len(temporary.tools),
                "tools": [_safe_tool_name(config.id, tool.name) for tool in temporary.tools],
            }
        except Exception as exc:
            return {"ok": False, "error": f"Connection test failed ({type(exc).__name__})."}
        finally:
            if temporary:
                await temporary.stack.aclose()

    async def upsert(self, config: MCPServerConfig) -> dict[str, Any]:
        async with self._server_update_lock(config.id):
            if self._load_error:
                raise ValueError("stored MCP configuration is invalid; repair the file before editing")
            configs = dict(self._configs)
            replacement: _ConnectedServer | None = None
            if config.enabled:
                try:
                    replacement = await self._connect(config)
                except Exception as exc:
                    raise ValueError(
                        f"server could not be enabled ({type(exc).__name__})"
                    ) from None
            configs[config.id] = config
            try:
                self._persist(configs)
            except Exception:
                if replacement:
                    await replacement.stack.aclose()
                raise
            old = self._connected.get(config.id)
            if replacement:
                self._connected[config.id] = replacement
                self._errors.pop(config.id, None)
            else:
                self._connected.pop(config.id, None)
            self._configs = configs
            self._load_error = None
            if old:
                async with old.call_lock:
                    await old.stack.aclose()
            return next(item for item in self.list_configs() if item["id"] == config.id)

    async def create(self, config: MCPServerConfig) -> dict[str, Any]:
        async with self._server_update_lock(config.id):
            if self._load_error:
                raise ValueError("stored MCP configuration is invalid; repair the file before editing")
            if config.id in self._configs:
                raise KeyError(config.id)
            replacement: _ConnectedServer | None = None
            if config.enabled:
                try:
                    replacement = await self._connect(config)
                except Exception as exc:
                    raise ValueError(
                        f"server could not be enabled ({type(exc).__name__})"
                    ) from None
            configs = dict(self._configs)
            configs[config.id] = config
            try:
                self._persist(configs)
            except Exception:
                if replacement:
                    await replacement.stack.aclose()
                raise
            self._configs = configs
            self._load_error = None
            if replacement:
                self._connected[config.id] = replacement
            return next(item for item in self.list_configs() if item["id"] == config.id)

    async def delete(self, server_id: str) -> None:
        async with self._server_update_lock(server_id):
            if self._load_error:
                raise ValueError("stored MCP configuration is invalid; repair the file before editing")
            if server_id not in self._configs:
                raise KeyError(server_id)
            configs = dict(self._configs)
            del configs[server_id]
            self._persist(configs)
            old = self._connected.pop(server_id, None)
            self._errors.pop(server_id, None)
            self._configs = configs
            if old:
                async with old.call_lock:
                    await old.stack.aclose()

    async def shutdown(self) -> None:
        async with self._lock:
            failures: list[str] = []
            for server_id in list(self._connected):
                async with self._server_lock(server_id):
                    connection = self._connected.pop(server_id, None)
                    if connection:
                        try:
                            async with connection.call_lock:
                                await connection.stack.aclose()
                        except Exception as exc:
                            failures.append(f"{server_id}: {type(exc).__name__}")
            if failures:
                raise RuntimeError(
                    "Failed to close MCP server sessions (" + ", ".join(failures) + ")."
                )
