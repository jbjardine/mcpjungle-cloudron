import tempfile
import unittest
from pathlib import Path

from mcpjungle_admin.registry import ManagedRegistry


class RegistryTest(unittest.TestCase):
    def test_upsert_and_remove(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            registry = ManagedRegistry(
                registry_path=root / ".mcpjungle-managed" / "registry.json",
                bundles_root=root / "mcp-bundles",
                work_root=root / ".mcpjungle-managed" / "work",
            )
            registry.upsert(
                {
                    "name": "demo",
                    "description": "Demo",
                    "transport": "stdio",
                    "managed": True,
                    "managed_type": "custom_command",
                    "runtime_spec": {"command": "node", "args": ["server.js"]},
                    "install_spec": {"updateStrategy": "manual"},
                    "healthcheck_spec": {"mode": "list_tools"},
                }
            )

            self.assertEqual(len(registry.list_entries()), 1)
            self.assertIsNotNone(registry.get("demo"))

            removed = registry.remove("demo")
            self.assertEqual(removed["name"], "demo")
            self.assertEqual(registry.list_entries(), [])


if __name__ == "__main__":
    unittest.main()
