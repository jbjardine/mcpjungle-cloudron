import tempfile
import unittest
from pathlib import Path

from mcpjungle_admin.managed_types import (
    detect_managed_type,
    imported_entry_from_server_config,
    split_package_version,
)


class ManagedTypesTest(unittest.TestCase):
    def test_split_scoped_package_version(self) -> None:
        package, version = split_package_version("@scope/pkg@1.2.3", separator="@")
        self.assertEqual(package, "@scope/pkg")
        self.assertEqual(version, "1.2.3")

    def test_detect_http_remote(self) -> None:
        managed_type = detect_managed_type(
            {
                "name": "remote",
                "transport": "streamable-http",
                "url": "https://example.com/mcp",
            }
        )
        self.assertEqual(managed_type, "http_remote")

    def test_detect_local_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            bundles_root = Path(tmp_dir)
            managed_type = detect_managed_type(
                {
                    "name": "bundle",
                    "transport": "stdio",
                    "command": str(bundles_root / "bundle" / "server.sh"),
                },
                bundles_root=bundles_root,
            )
            self.assertEqual(managed_type, "local_bundle")

    def test_imported_entry_preserves_last_known_good(self) -> None:
        entry = imported_entry_from_server_config(
            {
                "name": "wordpress-farniente",
                "description": "WordPress adapter",
                "transport": "stdio",
                "command": "npx",
                "args": ["-y", "@automattic/mcp-wordpress-remote@1.2.3"],
            }
        )
        self.assertEqual(entry["managed_type"], "npm_package")
        self.assertEqual(entry["install_spec"]["package"], "@automattic/mcp-wordpress-remote")
        self.assertEqual(entry["install_spec"]["version"], "1.2.3")
        self.assertTrue(entry["last_applied_hash"].startswith("sha256:"))


if __name__ == "__main__":
    unittest.main()

