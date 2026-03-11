import json
import tempfile
import unittest
from pathlib import Path

from mcpjungle_admin.models import server_config_from_entry
from mcpjungle_admin.registry import ManagedRegistry


class RegistrySecretsTest(unittest.TestCase):
    def make_registry(self) -> ManagedRegistry:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        root = Path(temp_dir.name)
        return ManagedRegistry(
            registry_path=root / ".mcpjungle-managed" / "registry.json",
            bundles_root=root / "mcp-bundles",
            work_root=root / ".mcpjungle-managed" / "work",
        )

    def test_sensitive_env_is_moved_out_of_registry(self) -> None:
        registry = self.make_registry()
        registry.upsert(
            {
                "name": "demo",
                "description": "Demo",
                "transport": "stdio",
                "managed": True,
                "managed_type": "custom_command",
                "runtime_spec": {
                    "command": "node",
                    "args": ["server.js"],
                    "env": {
                        "PUBLIC_URL": "https://example.com",
                        "API_KEY": "super-secret",
                    },
                },
                "install_spec": {"updateStrategy": "manual"},
                "healthcheck_spec": {"mode": "disabled"},
            }
        )

        registry_payload = json.loads(registry.registry_path.read_text())
        entry = registry_payload["servers"]["demo"]
        self.assertEqual(entry["runtime_spec"]["env"], {"PUBLIC_URL": "https://example.com"})
        self.assertEqual(entry["secret_env_keys"], ["API_KEY"])
        self.assertNotIn("super-secret", registry.registry_path.read_text())

        secret_file = Path(entry["secret_material_file"])
        self.assertTrue(secret_file.exists())
        self.assertEqual(secret_file.stat().st_mode & 0o777, 0o600)
        secret_payload = json.loads(secret_file.read_text())
        self.assertEqual(secret_payload["env"]["API_KEY"], "super-secret")

        resolved = server_config_from_entry(entry)
        self.assertEqual(resolved["env"]["API_KEY"], "super-secret")
        self.assertEqual(resolved["env"]["PUBLIC_URL"], "https://example.com")

    def test_legacy_inline_secret_is_migrated_on_load(self) -> None:
        registry = self.make_registry()
        registry.ensure_layout()
        registry.registry_path.write_text(
            json.dumps(
                {
                    "schemaVersion": 1,
                    "updatedAt": "2026-03-11T00:00:00+00:00",
                    "servers": {
                        "demo": {
                            "name": "demo",
                            "description": "Demo",
                            "transport": "stdio",
                            "managed": True,
                            "managed_type": "custom_command",
                            "runtime_spec": {
                                "command": "node",
                                "args": ["server.js"],
                                "env": {"PASSWORD": "secret"},
                            },
                            "install_spec": {"updateStrategy": "manual"},
                            "healthcheck_spec": {"mode": "disabled"},
                        }
                    },
                }
            )
        )

        document = registry.load()
        entry = document["servers"]["demo"]
        self.assertIn("secret_material_file", entry)
        self.assertNotIn("PASSWORD", entry["runtime_spec"].get("env", {}))


if __name__ == "__main__":
    unittest.main()
