import argparse
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from mcpjungle_admin.cli import build_parser, cmd_prune_managed_groups, cmd_sync_groups
from mcpjungle_admin.registry import ManagedRegistry
from mcpjungle_admin.tool_groups import ToolGroupsManager


class FakeToolGroupsManager(ToolGroupsManager):
    def __init__(self, groups: list[dict]):
        self.gateway_url = "http://127.0.0.1:8081"
        self.access_token = "test-token"
        self.groups = [dict(group) for group in groups]
        self.created: list[dict] = []
        self.deleted: list[str] = []

    def list_groups(self) -> list[dict]:
        return [dict(group) for group in self.groups]

    def create_group(
        self,
        name: str,
        description: str = "",
        included_servers: list[str] | None = None,
        included_tools: list[str] | None = None,
        excluded_tools: list[str] | None = None,
    ) -> dict:
        group = {
            "name": name,
            "description": description,
            "included_servers": included_servers or [],
            "included_tools": included_tools or [],
            "excluded_tools": excluded_tools or [],
        }
        self.created.append(group)
        self.groups = [item for item in self.groups if item.get("name") != name]
        self.groups.append(group)
        return group

    def delete_group(self, name: str) -> None:
        self.deleted.append(name)
        self.groups = [group for group in self.groups if group.get("name") != name]


class ToolGroupsManagerTest(unittest.TestCase):
    def test_is_managed_server_group_accepts_legacy_and_canonical(self) -> None:
        managed_names = {"demo"}
        legacy = {
            "name": "demo",
            "description": "All tools from demo",
            "included_servers": ["demo"],
        }
        canonical = {
            "name": "demo",
            "description": "[cloudron-managed] All tools from demo",
            "included_servers": ["demo"],
        }

        self.assertTrue(ToolGroupsManager.is_managed_server_group(legacy, managed_names))
        self.assertTrue(
            ToolGroupsManager.is_managed_server_group(canonical, managed_names)
        )

    def test_is_managed_server_group_rejects_custom_shape(self) -> None:
        managed_names = {"demo"}
        custom = {
            "name": "demo",
            "description": "Demo tools",
            "included_servers": ["demo", "other"],
            "included_tools": ["demo__custom"],
        }

        self.assertFalse(ToolGroupsManager.is_managed_server_group(custom, managed_names))

    def test_sync_tool_groups_recreates_managed_deletes_orphans_and_preserves_custom(
        self,
    ) -> None:
        manager = FakeToolGroupsManager(
            [
                {
                    "name": "analytics-mcp",
                    "description": "All tools from analytics-mcp",
                    "included_servers": ["analytics-mcp"],
                },
                {
                    "name": "wordpress-farniente",
                    "description": "Editorial tools",
                    "included_servers": ["wordpress-farniente"],
                    "included_tools": ["wordpress-farniente__publish_post"],
                },
                {
                    "name": "old-server",
                    "description": "[cloudron-managed] All tools from old-server",
                    "included_servers": ["old-server"],
                },
            ]
        )

        with self.assertLogs("mcpjungle_admin.tool_groups", level="WARNING") as logs:
            summary = manager.sync_tool_groups(
                ["analytics-mcp", "wordpress-farniente", "n8n-api"],
                managed_names={"analytics-mcp", "wordpress-farniente", "n8n-api"},
            )

        self.assertEqual(summary["recreated"], ["analytics-mcp"])
        self.assertEqual(summary["created"], ["n8n-api"])
        self.assertEqual(summary["deleted"], ["old-server"])
        self.assertEqual(summary["unchanged"], [])
        self.assertEqual(len(summary["warnings"]), 1)
        self.assertIn("wordpress-farniente", summary["warnings"][0])
        self.assertIn("custom group", logs.output[0])
        self.assertEqual(manager.deleted, ["analytics-mcp", "old-server"])
        self.assertEqual(
            [group["name"] for group in manager.created],
            ["analytics-mcp", "n8n-api"],
        )
        self.assertEqual(
            manager.created[0]["description"],
            "[cloudron-managed] All tools from analytics-mcp",
        )

    def test_prune_managed_groups_deletes_only_auto_managed_groups(self) -> None:
        manager = FakeToolGroupsManager(
            [
                {
                    "name": "analytics-mcp",
                    "description": "All tools from analytics-mcp",
                    "included_servers": ["analytics-mcp"],
                },
                {
                    "name": "wordpress-farniente",
                    "description": "Editorial tools",
                    "included_servers": ["wordpress-farniente"],
                    "included_tools": ["wordpress-farniente__publish_post"],
                },
                {
                    "name": "ad-hoc",
                    "description": "Ad-hoc tools",
                    "included_servers": ["analytics-mcp"],
                },
            ]
        )

        summary = manager.prune_managed_groups(
            {"analytics-mcp", "wordpress-farniente"}
        )

        self.assertEqual(summary["deleted"], ["analytics-mcp"])
        self.assertEqual(summary["unchanged"], ["ad-hoc", "wordpress-farniente"])
        self.assertEqual(summary["errors"], [])


class ToolGroupsCliTest(unittest.TestCase):
    def test_parser_accepts_prune_managed_groups(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["prune-managed-groups"])
        self.assertEqual(args.command, "prune-managed-groups")

    def test_cmd_sync_groups_only_manages_registered_managed_servers(self) -> None:
        class FakeClient:
            def get_server_configs(self):
                return {
                    "managed-demo": {"name": "managed-demo", "transport": "stdio"},
                    "external-demo": {"name": "external-demo", "transport": "stdio"},
                }

        with tempfile.TemporaryDirectory() as tmp_dir:
            registry = ManagedRegistry(
                registry_path=Path(tmp_dir) / ".mcpjungle-managed" / "registry.json",
                bundles_root=Path(tmp_dir) / "mcp-bundles",
                work_root=Path(tmp_dir) / ".mcpjungle-managed" / "work",
            )
            registry.upsert(
                {
                    "name": "managed-demo",
                    "description": "Managed demo",
                    "transport": "stdio",
                    "managed": True,
                    "managed_type": "custom_command",
                    "runtime_spec": {"command": "node", "args": ["managed.js"]},
                    "install_spec": {"updateStrategy": "manual"},
                }
            )

            manager = mock.Mock()
            manager.sync_tool_groups.return_value = {
                "created": [],
                "recreated": [],
                "deleted": [],
                "unchanged": ["managed-demo"],
                "warnings": [],
                "errors": [],
            }

            with mock.patch(
                "mcpjungle_admin.cli.build_runtime",
                return_value=(registry, FakeClient(), None, None),
            ), mock.patch(
                "mcpjungle_admin.tool_groups.ToolGroupsManager",
                return_value=manager,
            ):
                exit_code = cmd_sync_groups(argparse.Namespace(json=True))

        self.assertEqual(exit_code, 0)
        manager.sync_tool_groups.assert_called_once_with(
            ["managed-demo"],
            managed_names={"managed-demo"},
        )

    def test_cmd_prune_managed_groups_uses_registry_managed_names(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            registry = ManagedRegistry(
                registry_path=Path(tmp_dir) / ".mcpjungle-managed" / "registry.json",
                bundles_root=Path(tmp_dir) / "mcp-bundles",
                work_root=Path(tmp_dir) / ".mcpjungle-managed" / "work",
            )
            registry.upsert(
                {
                    "name": "analytics-mcp",
                    "description": "Analytics",
                    "transport": "stdio",
                    "managed": True,
                    "managed_type": "uvx_package",
                    "runtime_spec": {"command": "uvx", "args": ["analytics-mcp"]},
                    "install_spec": {"package": "analytics-mcp"},
                }
            )

            manager = mock.Mock()
            manager.prune_managed_groups.return_value = {
                "deleted": ["analytics-mcp"],
                "unchanged": [],
                "errors": [],
            }

            with mock.patch(
                "mcpjungle_admin.cli.build_runtime",
                return_value=(registry, None, None, None),
            ), mock.patch(
                "mcpjungle_admin.tool_groups.ToolGroupsManager",
                return_value=manager,
            ):
                exit_code = cmd_prune_managed_groups(argparse.Namespace(json=True))

        self.assertEqual(exit_code, 0)
        manager.prune_managed_groups.assert_called_once_with({"analytics-mcp"})


if __name__ == "__main__":
    unittest.main()
