import os
import subprocess
import unittest
from unittest import mock

from mcpjungle_admin.mcpjungle_client import MCPJungleClient


class MCPJungleClientTest(unittest.TestCase):
    def test_native_register_uses_streamable_http_identifier(self) -> None:
        native = MCPJungleClient._config_for_native_register(
            {
                "name": "remote",
                "transport": "streamable-http",
                "url": "https://example.com/mcp",
            }
        )
        self.assertEqual(native["transport"], "streamable_http")

    def test_run_uses_canonical_runtime_env(self) -> None:
        completed = subprocess.CompletedProcess(
            ["/usr/local/bin/mcpjungle", "list", "tools"],
            0,
            stdout="ok",
            stderr="",
        )
        with mock.patch.dict(
            os.environ,
            {
                "HOME": "/root",
                "MCPJUNGLE_DATA_ROOT": "/tmp/mcpjungle-data",
                "PATH": "/usr/local/node-18.18.0/bin:/custom/bin",
            },
            clear=False,
        ), mock.patch("mcpjungle_admin.mcpjungle_client.subprocess.run", return_value=completed) as run_mock:
            client = MCPJungleClient(registry_url="http://127.0.0.1:8081", work_root="/tmp/work")
            output = client.list_tools("demo")

        self.assertEqual(output, "ok")
        env = run_mock.call_args.kwargs["env"]
        self.assertEqual(env["HOME"], "/tmp/mcpjungle-data")
        self.assertEqual(env["APP_HOME"], "/tmp/mcpjungle-data")
        self.assertEqual(env["MCPJUNGLE_DATA_ROOT"], "/tmp/mcpjungle-data")
        self.assertTrue(env["PATH"].startswith("/usr/bin:"))


if __name__ == "__main__":
    unittest.main()
