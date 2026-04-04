import argparse
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from mcpjungle_admin.cli import _resolve_registry_url, build_parser, cmd_doctor
from mcpjungle_admin.health import HealthChecker
from mcpjungle_admin.registry import ManagedRegistry


class CliParserTest(unittest.TestCase):
    def test_install_keeps_subcommand_name(self) -> None:
        parser = build_parser()
        args = parser.parse_args(
            [
                "install",
                "--type",
                "http_remote",
                "--name",
                "demo",
                "--url",
                "https://example.com/mcp",
            ]
        )
        self.assertEqual(args.command, "install")
        self.assertIsNone(args.runtime_command)

    def test_reconcile_accepts_force_flag(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["reconcile", "--name", "demo", "--force"])
        self.assertEqual(args.command, "reconcile")
        self.assertTrue(args.force)

    def test_bind_file_parser(self) -> None:
        parser = build_parser()
        args = parser.parse_args(
            [
                "bind-file",
                "--name",
                "demo",
                "--source",
                "/app/data/secret.json",
                "--env-key",
                "MY_SECRET_FILE",
            ]
        )
        self.assertEqual(args.command, "bind-file")
        self.assertEqual(args.name, "demo")
        self.assertEqual(args.source, "/app/data/secret.json")
        self.assertEqual(args.env_key, "MY_SECRET_FILE")


class FakeDoctorClient:
    def __init__(self) -> None:
        self.registry_url = "http://127.0.0.1:8081"

    def gateway_health(self):
        return True, "Gateway healthy (200)"

    def get_server_configs(self):
        return {
            "demo": {
                "name": "demo",
                "transport": "stdio",
                "command": "node",
            }
        }

    def list_tools(self, server_name):
        return f"Tools listed successfully for {server_name}"


class CliRuntimeTest(unittest.TestCase):
    def test_resolve_registry_url_reads_conf_from_data_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            conf_path = Path(tmp_dir) / ".mcpjungle.conf"
            conf_path.write_text(
                "registry_url: http://127.0.0.1:8081\n",
                encoding="utf-8",
            )
            with mock.patch.dict(
                os.environ,
                {"HOME": "/root", "MCPJUNGLE_DATA_ROOT": tmp_dir},
                clear=False,
            ):
                self.assertEqual(_resolve_registry_url(), "http://127.0.0.1:8081")

    def test_doctor_reports_healthy_entries_with_home_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            conf_path = root / ".mcpjungle.conf"
            conf_path.write_text(
                "registry_url: http://127.0.0.1:8081\naccess_token: doctor-token\n",
                encoding="utf-8",
            )
            os.chmod(conf_path, 0o600)
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
            client = FakeDoctorClient()
            payload: dict = {}

            def capture(report, *, as_json):
                self.assertTrue(as_json)
                payload.clear()
                payload.update(report)

            with mock.patch.dict(
                os.environ,
                {"HOME": "/root", "MCPJUNGLE_DATA_ROOT": tmp_dir},
                clear=False,
            ), mock.patch(
                "mcpjungle_admin.cli.build_runtime",
                return_value=(registry, client, HealthChecker(client), None),
            ), mock.patch("mcpjungle_admin.cli.emit", side_effect=capture):
                exit_code = cmd_doctor(argparse.Namespace(json=True))

            self.assertEqual(exit_code, 0)
            self.assertEqual(payload["auth_config_path"], str(conf_path))
            self.assertEqual(payload["runtime"]["HOME"], tmp_dir)
            self.assertEqual(payload["managed_entries"][0]["name"], "demo")
            self.assertTrue(payload["managed_entries"][0]["registered"])
            self.assertTrue(payload["managed_entries"][0]["healthy"])


if __name__ == "__main__":
    unittest.main()
