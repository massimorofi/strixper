import asyncio
import json
import os
import tempfile
import unittest
from contextlib import AsyncExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers.agent import _require_mcp_admin, router as agent_router
from app.services.mcp_manager import (
    _ConnectedServer,
    MCPManager,
    MCPServerConfig,
    _sanitize_schema,
)
from app.services.skill_loader import (
    _resolve_resource,
    build_skill_tools,
    scan_skills,
)


class SkillLoaderTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)

    def tearDown(self):
        self.tempdir.cleanup()

    def write_skill(self, folder: str, name: str | None = None) -> Path:
        skill_dir = self.root / folder
        skill_dir.mkdir()
        skill_dir.joinpath("SKILL.md").write_text(
            f"---\nname: {name or folder}\ndescription: Test skill.\n---\n\nDo the task safely.\n",
            encoding="utf-8",
        )
        return skill_dir

    def test_discovers_valid_skill_and_reports_invalid_manifest(self):
        self.write_skill("valid-skill")
        (self.root / "bad-skill").mkdir()
        (self.root / "bad-skill" / "SKILL.md").write_text("not a manifest")

        scan = scan_skills(self.root)

        self.assertEqual(set(scan.skills), {"valid-skill"})
        self.assertEqual(len(scan.entries), 2)
        self.assertFalse(next(item for item in scan.entries if item["directory"] == "bad-skill")["valid"])

    def test_rejects_skill_resource_path_escape(self):
        skill_dir = self.write_skill("contained-skill")
        outside = self.root / "outside.txt"
        outside.write_text("secret")
        (skill_dir / "link.txt").symlink_to(outside)
        skill = scan_skills(self.root).skills["contained-skill"]

        with self.assertRaises(ValueError):
            _resolve_resource(skill, "../outside.txt")
        with self.assertRaises(ValueError):
            _resolve_resource(skill, "link.txt")

    def test_skill_tools_load_manifest_instructions(self):
        self.write_skill("loadable-skill")
        tools = {tool.name: tool for tool in build_skill_tools(scan_skills(self.root))}
        result = asyncio.run(tools["load_skill"].on_invoke_tool(None, '{"name":"loadable-skill"}'))

        self.assertIn("Do the task safely.", result)


class MCPManagerTests(unittest.IsolatedAsyncioTestCase):
    async def test_config_is_persisted_without_secret_values(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "mcp_servers.json"
            manager = MCPManager(path)
            config = MCPServerConfig(
                id="example",
                command="python3",
                args=["-m", "example_server"],
                env={"API_TOKEN": {"secretRef": "EXAMPLE_SECRET"}},
            )
            await manager.create(config)

            saved = path.read_text(encoding="utf-8")
            self.assertIn("EXAMPLE_SECRET", saved)
            self.assertNotIn("secret-value", saved)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertTrue(MCPManager(path).has_server("example"))

    async def test_missing_secret_reference_is_reported_without_value(self):
        config = MCPServerConfig(
            id="example",
            command="python3",
            env={"API_TOKEN": {"secretRef": "STRIXPER_TEST_MISSING_SECRET"}},
        )
        manager = MCPManager(Path(tempfile.gettempdir()) / "not-created-mcp-config.json")
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("STRIXPER_TEST_MISSING_SECRET", None)
            with self.assertRaisesRegex(ValueError, "STRIXPER_TEST_MISSING_SECRET"):
                manager._resolve_environment(config)
            with self.assertRaisesRegex(ValueError, "secret reference"):
                manager._process_environment(config)

    async def test_stdio_server_does_not_inherit_admin_token(self):
        manager = MCPManager(Path(tempfile.gettempdir()) / "not-created-mcp-config.json")
        config = MCPServerConfig(id="isolated", command="python3")
        with patch.dict(
            os.environ,
            {"MCP_ADMIN_TOKEN": "do-not-forward", "PATH": "/usr/bin"},
            clear=True,
        ):
            environment = manager._process_environment(config)
        self.assertTrue(environment["PATH"].endswith(os.pathsep + "/usr/bin"))
        self.assertNotIn("MCP_ADMIN_TOKEN", environment)

    async def test_mcp_process_path_uses_server_install_root(self):
        manager = MCPManager(Path(tempfile.gettempdir()) / "not-created-mcp-config.json")
        config = MCPServerConfig(id="example", command="example-server")
        with patch("app.services.mcp_manager.settings.mcp_servers_dir", "/opt/mcp-servers"):
            with patch.dict(os.environ, {"PATH": "/usr/bin"}, clear=True):
                environment = manager._process_environment(config)

        self.assertEqual(
            environment["PATH"],
            os.pathsep.join(
                [
                    "/opt/mcp-servers/example/bin",
                    "/opt/mcp-servers/example/.venv/bin",
                    "/opt/mcp-servers/example/node_modules/.bin",
                    "/opt/mcp-servers/bin",
                    "/usr/bin",
                ]
            ),
        )

    async def test_legacy_tieline_npx_config_migrates_to_install_root_command(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "mcp_servers.json"
            path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "servers": [
                            {
                                "id": "tieline",
                                "transport": "stdio",
                                "command": "npx",
                                "args": ["-y", "tieline@0.1.29", "serve"],
                                "env": {
                                    "TIELINE_WORKSPACE": {
                                        "secretRef": "TIELINE_WORKSPACE"
                                    }
                                },
                                "enabled": True,
                                "minimum_access": "actions",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            manager = MCPManager(path)
            migrated = json.loads(path.read_text(encoding="utf-8"))["servers"][0]

        self.assertEqual(manager.list_configs()[0]["command"], "tieline")
        self.assertEqual(migrated["command"], "tieline")
        self.assertEqual(migrated["args"], ["serve"])

    async def test_disable_and_delete_remove_server_config(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = MCPManager(Path(directory) / "mcp_servers.json")
            await manager.create(MCPServerConfig(id="sample", command="python3"))
            await manager.upsert(
                MCPServerConfig(id="sample", command="python3", enabled=False)
            )
            self.assertEqual(manager.list_configs()[0]["status"], "disabled")
            await manager.delete("sample")
            self.assertEqual(manager.list_configs(), [])

    async def test_no_mcp_tools_are_exposed_without_admin_token(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = MCPManager(Path(directory) / "mcp_servers.json")
            await manager.create(MCPServerConfig(id="sample", command="python3"))
            manager._configs["sample"] = manager._configs["sample"].model_copy(
                update={"enabled": True}
            )
            with patch("app.services.mcp_manager.settings.mcp_admin_token", ""):
                self.assertEqual(await manager.get_tools_for_access("full_access"), [])

    async def test_mcp_access_level_is_enforced_for_connected_server(self):
        class FakeSession:
            async def call_tool(self, name, arguments):
                return SimpleNamespace(content=[], isError=False)

        with tempfile.TemporaryDirectory() as directory:
            manager = MCPManager(Path(directory) / "mcp_servers.json")
            config = MCPServerConfig(
                id="restricted",
                command="unused",
                enabled=True,
                minimum_access="full_access",
            )
            manager._configs[config.id] = config
            manager._connected[config.id] = _ConnectedServer(
                config,
                AsyncExitStack(),
                FakeSession(),
                [
                    SimpleNamespace(
                        name="dangerous",
                        description="Test",
                        inputSchema={"type": "object", "properties": {}},
                    )
                ],
            )
            with patch("app.services.mcp_manager.settings.mcp_admin_token", "set"):
                read_only = await manager.get_tools_for_access("read-only")
                full_access = await manager.get_tools_for_access("full_access")
                status = manager.manager_status()

        self.assertEqual(read_only, [])
        self.assertEqual([tool.name for tool in full_access], ["mcp_restricted_dangerous"])
        self.assertEqual(status["servers"][0]["tools"], ["mcp_restricted_dangerous"])

    async def test_rejects_duplicate_or_invalid_server_ids(self):
        with self.assertRaises(ValueError):
            MCPServerConfig(id="../bad", command="python3")

    async def test_schema_default_annotations_are_removed_recursively(self):
        schema = {
            "default": "outer",
            "properties": {
                "query": {"type": "string", "default": "inner"},
                "items": {"type": "array", "items": {"default": []}},
            },
        }
        _sanitize_schema(schema)
        self.assertEqual(
            schema,
            {
                "properties": {
                    "query": {"type": "string"},
                    "items": {"type": "array", "items": {}},
                }
            },
        )

    async def test_mcp_schema_is_wrapped_as_callable_agents_tool(self):
        class FakeSession:
            async def call_tool(self, name, arguments):
                self.called = (name, arguments)
                return SimpleNamespace(
                    content=[SimpleNamespace(text="tool result")],
                    isError=False,
                )

        with tempfile.TemporaryDirectory() as directory:
            manager = MCPManager(Path(directory) / "mcp_servers.json")
            config = MCPServerConfig(id="fixture", command="unused")
            session = FakeSession()
            connected = _ConnectedServer(
                config,
                AsyncExitStack(),
                session,
                [],
            )
            manager._connected[config.id] = connected
            definition = SimpleNamespace(
                name="lookup",
                description="Fixture lookup",
                inputSchema={
                    "type": "object",
                    "properties": {"query": {"type": "string", "default": "hello"}},
                    "required": ["query"],
                },
            )

            tool = manager._make_tool(connected, definition)
            output = await tool.on_invoke_tool(None, '{"query":"world"}')

            self.assertEqual(output, "tool result")
            self.assertEqual(session.called, ("lookup", {"query": "world"}))
            self.assertNotIn("default", json.dumps(tool.params_json_schema))

    async def test_snake_case_mcp_sdk_fields_are_supported(self):
        class FakeSession:
            async def call_tool(self, name, arguments):
                self.called = (name, arguments)
                return SimpleNamespace(
                    content=[],
                    structured_content={"answer": "found"},
                    is_error=False,
                )

        with tempfile.TemporaryDirectory() as directory:
            manager = MCPManager(Path(directory) / "mcp_servers.json")
            config = MCPServerConfig(id="snake-case", command="unused")
            session = FakeSession()
            connected = _ConnectedServer(config, AsyncExitStack(), session, [])
            manager._connected[config.id] = connected
            definition = SimpleNamespace(
                name="lookup",
                description="Fixture lookup",
                input_schema={"type": "object", "properties": {}},
            )

            tool = manager._make_tool(connected, definition)
            output = await tool.on_invoke_tool(None, "{}")

        self.assertEqual(json.loads(output), {"answer": "found"})
        self.assertEqual(session.called, ("lookup", {}))


class MCPAdminAuthTests(unittest.TestCase):
    def test_management_is_disabled_without_server_token(self):
        with patch("app.routers.agent.settings.mcp_admin_token", ""):
            with self.assertRaises(HTTPException) as raised:
                _require_mcp_admin(None)
        self.assertEqual(raised.exception.status_code, 503)

    def test_requires_matching_token(self):
        with patch("app.routers.agent.settings.mcp_admin_token", "expected"):
            with self.assertRaises(HTTPException) as raised:
                _require_mcp_admin("wrong")
            _require_mcp_admin("expected")
        self.assertEqual(raised.exception.status_code, 401)

    def test_mcp_management_route_requires_token_header(self):
        with tempfile.TemporaryDirectory() as directory:
            app = FastAPI()
            app.include_router(agent_router, prefix="/api/v1")
            app.state.mcp = MCPManager(Path(directory) / "mcp_servers.json")
            with patch("app.routers.agent.settings.mcp_admin_token", "expected"):
                with TestClient(app) as client:
                    denied = client.get("/api/v1/agent/mcp-servers")
                    allowed = client.get(
                        "/api/v1/agent/mcp-servers",
                        headers={"X-MCP-Admin-Token": "expected"},
                    )
                    created = client.post(
                        "/api/v1/agent/mcp-servers",
                        headers={"X-MCP-Admin-Token": "expected"},
                        json={
                            "id": "safe-config",
                            "command": "python3",
                            "env": {"API_TOKEN": {"secretRef": "BACKEND_API_TOKEN"}},
                        },
                    )
        self.assertEqual(denied.status_code, 401)
        self.assertEqual(allowed.status_code, 200)
        self.assertTrue(allowed.json()["configured"])
        self.assertEqual(created.status_code, 200)
        self.assertEqual(
            created.json()["env"]["API_TOKEN"]["secretRef"],
            "BACKEND_API_TOKEN",
        )


if __name__ == "__main__":
    unittest.main()
