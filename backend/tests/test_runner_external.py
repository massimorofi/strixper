"""Tests for external (monitor-only) LLM-Runner configurations.

External configs launch nothing: they store the root address of an
already-running LLM server so the dashboard can monitor it and the chat
can use it. These tests cover the store's kind-aware validation, the
launch/stop lifecycle of an external run, and the guards that keep the
two kinds from tripping over each other.
"""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

# Point the persistent stores at throwaway locations before any app
# module reads them at import time. setUp also patches the module
# constants directly, so isolation holds even when other test modules
# imported these modules first.
_TMP_ROOT = tempfile.mkdtemp()
os.environ["RUNNER_STORE_PATH"] = os.path.join(_TMP_ROOT, "runner_configs.json")
os.environ["ENGINE_TARGET_PATH"] = os.path.join(_TMP_ROOT, "engine_target.json")

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.routers import runner as runner_mod  # noqa: E402
from app.services import engine_target as engine_target_mod  # noqa: E402
from app.services import run_registry as run_registry_mod  # noqa: E402
from app.services import runner_store as runner_store_mod  # noqa: E402
from app.services.halogen_client import HalogenClient  # noqa: E402
from app.services.run_registry import registry  # noqa: E402
from app.services.runner_store import RunnerStore, RunnerStoreError  # noqa: E402

API = "/api/v1"
EXTERNAL_URL = "http://127.0.0.1:59999"


def fresh_tmp() -> str:
    return tempfile.mkdtemp()


class StoreKindTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.store = RunnerStore(Path(fresh_tmp()) / "configs.json")

    async def test_external_create_normalises_url(self):
        cfg = await self.store.create(
            name="ext", kind="external", base_url="http://Host:8080/"
        )
        self.assertEqual(cfg["kind"], "external")
        self.assertEqual(cfg["base_url"], "http://Host:8080")
        self.assertEqual(cfg["docker_command"], "")
        self.assertEqual(cfg["parameters"], [])

    async def test_external_blank_base_url_rejected(self):
        with self.assertRaises(RunnerStoreError):
            await self.store.create(name="ext", kind="external", base_url="   ")

    async def test_external_bad_scheme_rejected(self):
        with self.assertRaises(RunnerStoreError):
            await self.store.create(name="ext", kind="external", base_url="ftp://host:21")

    async def test_external_path_url_rejected(self):
        # HalogenClient appends /v1/... itself; a base carrying a path
        # would double it up, so it must be refused at save time.
        with self.assertRaises(RunnerStoreError):
            await self.store.create(
                name="ext", kind="external", base_url="http://host:8080/v1"
            )

    async def test_unknown_kind_rejected(self):
        with self.assertRaises(RunnerStoreError):
            await self.store.create(
                name="x", kind="vm", docker_command="docker run --name a b"
            )

    async def test_docker_create_still_requires_command(self):
        with self.assertRaises(RunnerStoreError):
            await self.store.create(name="x", kind="docker", docker_command="  ")

    async def test_load_kindless_json_defaults_to_docker(self):
        path = Path(fresh_tmp()) / "configs.json"
        path.write_text(
            json.dumps(
                [
                    {
                        "id": "abc123",
                        "name": "legacy",
                        "docker_command": "docker run --name legacy img",
                        "description": "",
                        "created_at": "2020-01-01T00:00:00Z",
                        "updated_at": "2020-01-01T00:00:00Z",
                    }
                ]
            ),
            encoding="utf-8",
        )
        store = RunnerStore(path)
        cfg = store.list()[0]
        self.assertEqual(cfg["kind"], "docker")
        self.assertIsNone(cfg["base_url"])


class UpdateTransitionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.store = RunnerStore(Path(fresh_tmp()) / "configs.json")

    async def make_docker(self):
        return await self.store.create(
            name="dk",
            kind="docker",
            docker_command="docker run --rm --name dk -p 8080:8080 img",
        )

    async def make_external(self):
        return await self.store.create(
            name="ex", kind="external", base_url="http://10.0.0.5:8080"
        )

    async def test_docker_to_external_requires_base_url(self):
        cfg = await self.make_docker()
        with self.assertRaises(RunnerStoreError):
            await self.store.update(cfg["id"], kind="external")

    async def test_docker_to_external_clears_command(self):
        cfg = await self.make_docker()
        updated = await self.store.update(
            cfg["id"], kind="external", base_url="http://10.0.0.5:8080"
        )
        self.assertEqual(updated["kind"], "external")
        self.assertEqual(updated["docker_command"], "")
        self.assertEqual(updated["parameters"], [])

    async def test_external_to_docker_requires_command(self):
        cfg = await self.make_external()
        with self.assertRaises(RunnerStoreError):
            await self.store.update(cfg["id"], kind="docker")

    async def test_external_to_docker_with_command_ok(self):
        cfg = await self.make_external()
        updated = await self.store.update(
            cfg["id"],
            kind="docker",
            docker_command="docker run --rm --name ex img",
        )
        self.assertEqual(updated["kind"], "docker")
        self.assertIsNone(updated["base_url"])

    async def test_external_base_url_change_revalidates(self):
        cfg = await self.make_external()
        with self.assertRaises(RunnerStoreError):
            await self.store.update(cfg["id"], base_url="http://10.0.0.5:8080/v1")

    async def test_external_base_url_change_ok(self):
        cfg = await self.make_external()
        updated = await self.store.update(cfg["id"], base_url="http://10.9.9.9:1234")
        self.assertEqual(updated["base_url"], "http://10.9.9.9:1234")


class ExternalRunLifecycleTests(unittest.IsolatedAsyncioTestCase):
    """Full HTTP lifecycle through the app, with throwaway stores."""

    def setUp(self):
        tmp = fresh_tmp()
        runner_store_mod.STORE_PATH = Path(tmp) / "runner.json"
        engine_target_mod.TARGET_PATH = Path(tmp) / "engine_target.json"
        runner_mod._store = None  # force re-creation against the new path
        registry.runs.clear()
        self.tmp = tmp
        self.client = TestClient(app)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)

    def create_external(self, name="ext-cfg"):
        resp = self.client.post(
            f"{API}/runner/configs",
            json={"name": name, "kind": "external", "base_url": EXTERNAL_URL},
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        return resp.json()

    async def test_launch_external_returns_expected_shape(self):
        cfg = self.create_external()
        with mock.patch.object(
            HalogenClient, "get_health", new=mock.AsyncMock(return_value={"model": "m1"})
        ):
            resp = self.client.post(f"{API}/runner/configs/{cfg['id']}/run")
        self.assertEqual(resp.status_code, 200, resp.text)
        run = resp.json()
        self.assertEqual(run["kind"], "external")
        self.assertEqual(run["engine_url"], EXTERNAL_URL)
        self.assertIsNone(run["container_name"])
        self.assertEqual(run["status"], "running")
        self.assertEqual(run["model_name"], "m1")

    async def test_launch_persists_engine_target(self):
        cfg = self.create_external()
        with mock.patch.object(
            HalogenClient, "get_health", new=mock.AsyncMock(return_value={"model": "m1"})
        ):
            self.client.post(f"{API}/runner/configs/{cfg['id']}/run")
        saved = json.loads(
            (Path(self.tmp) / "engine_target.json").read_text(encoding="utf-8")
        )
        self.assertEqual(saved["base_url"], EXTERNAL_URL)
        self.assertEqual(saved["config_id"], cfg["id"])
        self.assertEqual(saved["model_name"], "m1")

    async def test_probe_failure_is_advisory(self):
        cfg = self.create_external(name="ext-down")
        with mock.patch.object(
            HalogenClient, "get_health", new=mock.AsyncMock(side_effect=OSError("refused"))
        ):
            resp = self.client.post(f"{API}/runner/configs/{cfg['id']}/run")
        self.assertEqual(resp.status_code, 200, resp.text)
        run = resp.json()
        self.assertEqual(run["status"], "running")
        self.assertIsNotNone(run["engine_probe_error"])

    async def test_stop_marks_external_exited(self):
        cfg = self.create_external(name="ext-stop")
        with mock.patch.object(
            HalogenClient, "get_health", new=mock.AsyncMock(return_value={})
        ):
            started = self.client.post(
                f"{API}/runner/configs/{cfg['id']}/run"
            ).json()
        stopped = self.client.post(f"{API}/runner/runs/{started['run_id']}/stop")
        self.assertEqual(stopped.status_code, 200)
        self.assertEqual(stopped.json().get("method"), "external")
        # /runner/runs lists only running runs; read the exited state
        # straight from the registry.
        self.assertEqual(registry.get_run(started["run_id"]).status, "exited")

    async def test_delete_guard_blocks_running_external(self):
        cfg = self.create_external(name="ext-guard")
        with mock.patch.object(
            HalogenClient, "get_health", new=mock.AsyncMock(return_value={})
        ):
            started = self.client.post(
                f"{API}/runner/configs/{cfg['id']}/run"
            ).json()
        blocked = self.client.delete(f"{API}/runner/configs/{cfg['id']}")
        self.assertEqual(blocked.status_code, 409)
        self.client.post(f"{API}/runner/runs/{started['run_id']}/stop")
        ok = self.client.delete(f"{API}/runner/configs/{cfg['id']}")
        self.assertEqual(ok.status_code, 200)

    async def test_start_external_is_idempotent(self):
        first = await registry.start_external(
            config_id="cfg-1", config_name="one", base_url=EXTERNAL_URL
        )
        second = await registry.start_external(
            config_id="cfg-1", config_name="one", base_url=EXTERNAL_URL
        )
        self.assertEqual(first.run_id, second.run_id)
        self.assertEqual(len(registry.get_running()), 1)

    async def test_discover_skips_external_configs(self):
        # Even with a stale docker_command naming a live container, an
        # external config must never phantom-adopt.
        cfg = {
            "id": "ext-1",
            "name": "ghost",
            "kind": "external",
            "base_url": EXTERNAL_URL,
            "docker_command": "docker run --rm --name ghost img",
            "parameters": [],
        }
        with mock.patch.object(
            run_registry_mod, "list_running_names", new=mock.AsyncMock(return_value=["ghost"])
        ):
            adopted = await registry.discover([cfg])
        self.assertEqual(adopted, [])


class CreateBodyValidationTests(unittest.TestCase):
    """Pydantic-level rejection of malformed create payloads."""

    def setUp(self):
        tmp = fresh_tmp()
        runner_store_mod.STORE_PATH = Path(tmp) / "runner.json"
        engine_target_mod.TARGET_PATH = Path(tmp) / "engine_target.json"
        runner_mod._store = None
        registry.runs.clear()

    def test_docker_kind_requires_command(self):
        resp = TestClient(app).post(
            f"{API}/runner/configs", json={"name": "x", "kind": "docker"}
        )
        self.assertEqual(resp.status_code, 422)

    def test_external_kind_requires_base_url(self):
        resp = TestClient(app).post(
            f"{API}/runner/configs", json={"name": "x", "kind": "external"}
        )
        self.assertEqual(resp.status_code, 422)

    def test_unknown_kind_rejected(self):
        resp = TestClient(app).post(
            f"{API}/runner/configs",
            json={"name": "x", "kind": "vm", "docker_command": "docker run --name a b"},
        )
        self.assertEqual(resp.status_code, 422)


if __name__ == "__main__":
    unittest.main()
