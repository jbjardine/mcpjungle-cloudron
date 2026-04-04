import json
import os
import tempfile
import unittest
from pathlib import Path

from mcpjungle_admin.models import resolved_server_config, server_config_from_entry
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
        if os.name != "nt":
            self.assertEqual(secret_file.stat().st_mode & 0o777, 0o600)
        secret_payload = json.loads(secret_file.read_text())
        self.assertEqual(secret_payload["env"]["API_KEY"], "super-secret")

        resolved = server_config_from_entry(entry)
        self.assertEqual(resolved["env"]["API_KEY"], "super-secret")
        self.assertEqual(resolved["env"]["PUBLIC_URL"], "https://example.com")

    def test_stdio_runtime_env_injects_canonical_home_and_xdg(self) -> None:
        entry = {
            "name": "demo",
            "description": "Demo",
            "transport": "stdio",
            "managed": True,
            "managed_type": "custom_command",
            "runtime_spec": {
                "command": "node",
                "args": ["server.js"],
            },
            "install_spec": {"updateStrategy": "manual"},
            "healthcheck_spec": {"mode": "disabled"},
        }

        resolved = server_config_from_entry(entry)
        self.assertEqual(resolved["env"]["HOME"], "/app/data")
        self.assertEqual(resolved["env"]["APP_HOME"], "/app/data")
        self.assertEqual(resolved["env"]["MCPJUNGLE_DATA_ROOT"], "/app/data")
        self.assertEqual(resolved["env"]["XDG_CONFIG_HOME"], "/app/data/.config")
        self.assertEqual(resolved["env"]["XDG_CACHE_HOME"], "/app/data/.cache")
        self.assertEqual(resolved["env"]["XDG_DATA_HOME"], "/app/data/.local/share")

    def test_stdio_runtime_env_keeps_explicit_overrides(self) -> None:
        entry = {
            "name": "demo",
            "description": "Demo",
            "transport": "stdio",
            "managed": True,
            "managed_type": "custom_command",
            "runtime_spec": {
                "command": "node",
                "args": ["server.js"],
                "env": {
                    "HOME": "/custom-home",
                    "PATH": "/custom-bin",
                },
            },
            "install_spec": {"updateStrategy": "manual"},
            "healthcheck_spec": {"mode": "disabled"},
        }

        resolved = server_config_from_entry(entry)
        self.assertEqual(resolved["env"]["HOME"], "/custom-home")
        self.assertEqual(resolved["env"]["PATH"], "/custom-bin")

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

    def test_last_known_good_is_stripped_but_runtime_can_be_restored(self) -> None:
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
                    "env": {"PASSWORD": "secret"},
                },
                "install_spec": {"updateStrategy": "manual"},
                "healthcheck_spec": {"mode": "disabled"},
                "last_known_good": {
                    "name": "demo",
                    "transport": "stdio",
                    "command": "node",
                    "args": ["server.js"],
                    "env": {"PASSWORD": "secret"},
                },
            }
        )

        entry = registry.require("demo")
        self.assertNotIn("PASSWORD", entry["last_known_good"].get("env", {}))
        restored = resolved_server_config(entry["last_known_good"], entry)
        self.assertEqual(restored["env"]["PASSWORD"], "secret")

    def test_windows_style_path_env_is_not_treated_as_secret(self) -> None:
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
                    "env": {"RUNTIME_SECRET_FILE": r"C:\temp\cred.json"},
                },
                "install_spec": {"updateStrategy": "manual"},
                "healthcheck_spec": {"mode": "disabled"},
            }
        )

        entry = registry.require("demo")
        self.assertEqual(
            entry["runtime_spec"]["env"]["RUNTIME_SECRET_FILE"],
            r"C:\temp\cred.json",
        )
        self.assertNotIn("secret_material_file", entry)

    def test_cleanup_moves_legacy_server_configs_out_of_data_root(self) -> None:
        registry = self.make_registry()
        registry.data_root = registry.registry_path.parent.parent
        registry.upsert(
            {
                "name": "demo",
                "description": "Demo",
                "transport": "stdio",
                "managed": True,
                "managed_type": "custom_command",
                "runtime_spec": {"command": "node", "args": ["server.js"]},
                "install_spec": {"updateStrategy": "manual"},
                "healthcheck_spec": {"mode": "disabled"},
            }
        )
        legacy_path = registry.data_root / "demo.json"
        legacy_path.write_text(
            json.dumps(
                {
                    "name": "demo",
                    "transport": "stdio",
                    "command": "node",
                    "args": ["server.js"],
                }
            )
        )

        moved = registry.cleanup_legacy_server_configs()
        self.assertEqual(len(moved), 1)
        self.assertFalse(legacy_path.exists())
        moved_path = Path(moved[0]["target"])
        self.assertTrue(moved_path.exists())
        self.assertEqual(moved_path.parent, registry.legacy_configs_root)
        self.assertEqual(registry.list_legacy_server_configs(), [])


if __name__ == "__main__":
    unittest.main()
