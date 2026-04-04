import tempfile
import unittest
from pathlib import Path
from subprocess import TimeoutExpired

from mcpjungle_admin.health import HealthChecker
from mcpjungle_admin.reconcile import Reconciler
from mcpjungle_admin.registry import ManagedRegistry


class FakeClient:
    def __init__(
        self,
        current_configs=None,
        should_fail_health=False,
        register_exception: Exception | None = None,
        persist_config_on_register_failure: bool = False,
    ):
        self.current_configs = current_configs or {}
        self.should_fail_health = should_fail_health
        self.register_exception = register_exception
        self.persist_config_on_register_failure = persist_config_on_register_failure
        self.registered = []
        self.deregistered = []
        self.invoked = []

    def get_server_configs(self):
        return dict(self.current_configs)

    def register_server(self, config, *, timeout=None):
        self.registered.append(config)
        if self.register_exception is not None:
            if self.persist_config_on_register_failure:
                self.current_configs[config["name"]] = config
            raise self.register_exception
        self.current_configs[config["name"]] = config
        return "registered"

    def deregister_server(self, name, ignore_missing=False):
        self.deregistered.append(name)
        self.current_configs.pop(name, None)
        return "deregistered"

    def list_tools(self, server_name):
        if self.should_fail_health:
            raise RuntimeError(f"{server_name} unhealthy")
        return "tool-a\ntool-b"

    def invoke_tool(self, tool_name, tool_input=None):
        self.invoked.append((tool_name, tool_input))
        if self.should_fail_health:
            raise RuntimeError(f"{tool_name} unhealthy")
        return "ok"

    def gateway_health(self):
        return True, "healthy"


class ReconcileTest(unittest.TestCase):
    def make_registry(self) -> ManagedRegistry:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        root = Path(temp_dir.name)
        return ManagedRegistry(
            registry_path=root / ".mcpjungle-managed" / "registry.json",
            bundles_root=root / "mcp-bundles",
            work_root=root / ".mcpjungle-managed" / "work",
        )

    def test_reconcile_marks_entry_healthy(self) -> None:
        registry = self.make_registry()
        registry.upsert(
            {
                "name": "demo",
                "description": "Demo server",
                "transport": "stdio",
                "managed": True,
                "managed_type": "custom_command",
                "runtime_spec": {"command": "node", "args": ["server.js"]},
                "install_spec": {"updateStrategy": "manual"},
                "healthcheck_spec": {"mode": "list_tools"},
            }
        )

        client = FakeClient()
        reconciler = Reconciler(registry, client, HealthChecker(client))
        result = reconciler.reconcile(name="demo")[0]

        self.assertEqual(result["status"], "healthy")
        saved = registry.require("demo")
        self.assertEqual(saved["status"], "healthy")
        self.assertEqual(len(client.registered), 1)

    def test_reconcile_result_does_not_expose_sensitive_env(self) -> None:
        registry = self.make_registry()
        registry.upsert(
            {
                "name": "demo",
                "description": "Demo server",
                "transport": "stdio",
                "managed": True,
                "managed_type": "custom_command",
                "runtime_spec": {
                    "command": "node",
                    "args": ["server.js"],
                    "env": {"PASSWORD": "secret"},
                },
                "install_spec": {"updateStrategy": "manual"},
                "healthcheck_spec": {"mode": "list_tools"},
            }
        )

        client = FakeClient()
        reconciler = Reconciler(registry, client, HealthChecker(client))
        result = reconciler.reconcile(name="demo")[0]

        self.assertEqual(result["status"], "healthy")
        self.assertNotIn("PASSWORD", result["entry"]["last_known_good"].get("env", {}))

    def test_reconcile_rolls_back_on_health_failure(self) -> None:
        registry = self.make_registry()
        previous_config = {
            "name": "demo",
            "description": "Old",
            "transport": "stdio",
            "command": "node",
            "args": ["old.js"],
        }
        registry.upsert(
            {
                "name": "demo",
                "description": "New",
                "transport": "stdio",
                "managed": True,
                "managed_type": "custom_command",
                "runtime_spec": {"command": "node", "args": ["new.js"]},
                "install_spec": {"updateStrategy": "manual"},
                "healthcheck_spec": {"mode": "list_tools"},
                "last_known_good": previous_config,
            }
        )

        client = FakeClient(current_configs={"demo": previous_config}, should_fail_health=True)
        reconciler = Reconciler(registry, client, HealthChecker(client))
        result = reconciler.reconcile(name="demo")[0]

        self.assertEqual(result["status"], "error")
        self.assertGreaterEqual(len(client.registered), 2)
        self.assertEqual(client.registered[-1]["args"], ["old.js"])
        saved = registry.require("demo")
        self.assertEqual(saved["status"], "error")
        self.assertIn("rollback applied", saved["last_error"])

    def test_reconcile_force_re_registers_even_if_hash_is_unchanged(self) -> None:
        registry = self.make_registry()
        config = {
            "name": "demo",
            "description": "Demo server",
            "transport": "stdio",
            "command": "node",
            "args": ["server.js"],
        }
        registry.upsert(
            {
                "name": "demo",
                "description": "Demo server",
                "transport": "stdio",
                "managed": True,
                "managed_type": "custom_command",
                "runtime_spec": {"command": "node", "args": ["server.js"]},
                "install_spec": {"updateStrategy": "manual"},
                "healthcheck_spec": {"mode": "list_tools"},
                "last_known_good": config,
            }
        )

        client = FakeClient(current_configs={"demo": config})
        reconciler = Reconciler(registry, client, HealthChecker(client))
        result = reconciler.reconcile_force(name="demo")[0]

        self.assertEqual(result["status"], "healthy")
        self.assertEqual(len(client.deregistered), 1)
        self.assertEqual(len(client.registered), 1)

    def test_invoke_tool_healthcheck_is_supported(self) -> None:
        registry = self.make_registry()
        registry.upsert(
            {
                "name": "demo",
                "description": "Demo",
                "transport": "stdio",
                "managed": True,
                "managed_type": "custom_command",
                "runtime_spec": {"command": "node", "args": ["server.js"]},
                "install_spec": {"updateStrategy": "manual"},
                "healthcheck_spec": {
                    "mode": "invoke_tool",
                    "tool_name": "demo__ping",
                    "tool_input": {},
                },
            }
        )

        client = FakeClient()
        reconciler = Reconciler(registry, client, HealthChecker(client))
        result = reconciler.reconcile(name="demo")[0]

        self.assertEqual(result["status"], "healthy")
        self.assertEqual(
            client.invoked,
            [("demo__ping", {})],
        )

    def test_reconcile_recovers_when_register_times_out_but_server_is_healthy(self) -> None:
        registry = self.make_registry()
        registry.upsert(
            {
                "name": "demo",
                "description": "Demo server",
                "transport": "stdio",
                "managed": True,
                "managed_type": "custom_command",
                "runtime_spec": {"command": "node", "args": ["server.js"]},
                "install_spec": {"updateStrategy": "manual"},
                "healthcheck_spec": {"mode": "list_tools"},
            }
        )

        client = FakeClient(
            register_exception=TimeoutExpired(["mcpjungle", "register"], timeout=60),
            persist_config_on_register_failure=True,
        )
        reconciler = Reconciler(registry, client, HealthChecker(client))

        result = reconciler.reconcile(name="demo")[0]

        self.assertEqual(result["status"], "healthy")
        self.assertEqual(result["entry"]["last_error"], "")
        self.assertEqual(result["entry"]["consecutive_failures"], 0)
        self.assertEqual(len(client.registered), 1)

    def test_boot_reconcile_recovers_when_register_times_out_but_server_is_healthy(self) -> None:
        registry = self.make_registry()
        registry.upsert(
            {
                "name": "demo",
                "description": "Demo server",
                "transport": "stdio",
                "managed": True,
                "managed_type": "custom_command",
                "runtime_spec": {"command": "node", "args": ["server.js"]},
                "install_spec": {"updateStrategy": "manual"},
                "healthcheck_spec": {"mode": "list_tools"},
            }
        )

        client = FakeClient(
            register_exception=TimeoutExpired(["mcpjungle", "register"], timeout=60),
            persist_config_on_register_failure=True,
        )
        reconciler = Reconciler(registry, client, HealthChecker(client))

        result = reconciler.reconcile_boot()[0]

        self.assertEqual(result["status"], "healthy")
        saved = registry.require("demo")
        self.assertEqual(saved["status"], "healthy")
        self.assertEqual(saved["last_error"], "")


if __name__ == "__main__":
    unittest.main()
