import tempfile
import unittest
from pathlib import Path

from mcpjungle_admin.managed_files import configure_managed_file
from mcpjungle_admin.registry import ManagedRegistry


class ManagedFilesTest(unittest.TestCase):
    def make_registry(self) -> ManagedRegistry:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        root = Path(temp_dir.name)
        return ManagedRegistry(
            registry_path=root / ".mcpjungle-managed" / "registry.json",
            bundles_root=root / "mcp-bundles",
            work_root=root / ".mcpjungle-managed" / "work",
        )

    def make_entry(self) -> dict:
        return {
            "name": "demo",
            "description": "Demo",
            "transport": "stdio",
            "managed": True,
            "managed_type": "custom_command",
            "runtime_spec": {
                "command": "node",
                "args": ["server.js"],
                "env": {"PUBLIC_URL": "https://example.com"},
            },
            "install_spec": {"updateStrategy": "manual"},
            "healthcheck_spec": {"mode": "list_tools"},
        }

    def test_configure_managed_file_binds_path_and_healthcheck(self) -> None:
        registry = self.make_registry()
        entry = registry.upsert(self.make_entry())

        source_path = registry.work_root / "secret.txt"
        source_path.parent.mkdir(parents=True, exist_ok=True)
        source_path.write_text("super-secret")

        updated_entry, info = configure_managed_file(
            registry,
            entry,
            source=source_path,
            env_key="MY_SECRET_FILE",
            set_env={"MODE": "prod"},
            healthcheck_spec={
                "mode": "invoke_tool",
                "tool_name": "demo__ping",
                "tool_input": {},
            },
        )

        managed_path = Path(updated_entry["runtime_spec"]["env"]["MY_SECRET_FILE"])
        self.assertTrue(managed_path.exists())
        self.assertEqual(managed_path.read_text(), "super-secret")
        self.assertEqual(managed_path.parent, registry.secrets_root)
        self.assertEqual(updated_entry["runtime_spec"]["env"]["MODE"], "prod")
        self.assertEqual(updated_entry["managed_files"], [str(managed_path)])
        self.assertEqual(updated_entry["healthcheck_spec"]["tool_name"], "demo__ping")
        self.assertEqual(info["env_key"], "MY_SECRET_FILE")

    def test_registry_remove_deletes_managed_files(self) -> None:
        registry = self.make_registry()
        entry = registry.upsert(self.make_entry())

        source_path = registry.work_root / "secret.txt"
        source_path.parent.mkdir(parents=True, exist_ok=True)
        source_path.write_text("super-secret")

        updated_entry, _ = configure_managed_file(
            registry,
            entry,
            source=source_path,
            env_key="MY_SECRET_FILE",
        )
        saved_entry = registry.upsert(updated_entry)
        managed_path = Path(saved_entry["managed_files"][0])
        self.assertTrue(managed_path.exists())

        registry.remove("demo")
        self.assertFalse(managed_path.exists())


if __name__ == "__main__":
    unittest.main()
