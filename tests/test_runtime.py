import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from mcpjungle_admin.runtime import (
    build_runtime_path,
    canonical_runtime_env,
    load_gateway_settings,
    runtime_data_root,
)


class RuntimeTest(unittest.TestCase):
    def test_runtime_data_root_prefers_managed_env_over_home(self) -> None:
        with mock.patch.dict(
            os.environ,
            {
                "HOME": "/root",
                "APP_HOME": "/app/data",
                "MCPJUNGLE_DATA_ROOT": "/tmp/mcpjungle-data",
            },
            clear=False,
        ):
            self.assertEqual(runtime_data_root(), Path("/tmp/mcpjungle-data"))

    def test_canonical_runtime_env_sets_writable_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            env = canonical_runtime_env(
                {
                    "HOME": "/root",
                    "PATH": "/usr/local/node-18.18.0/bin:/custom/bin",
                    "MCPJUNGLE_DATA_ROOT": tmp_dir,
                }
            )

            self.assertEqual(env["HOME"], tmp_dir)
            self.assertEqual(env["APP_HOME"], tmp_dir)
            self.assertEqual(env["MCPJUNGLE_DATA_ROOT"], tmp_dir)
            self.assertEqual(env["XDG_CONFIG_HOME"], f"{tmp_dir}/.config")
            self.assertEqual(env["XDG_CACHE_HOME"], f"{tmp_dir}/.cache")
            self.assertEqual(env["XDG_DATA_HOME"], f"{tmp_dir}/.local/share")
            self.assertEqual(env["LANG"], "C.UTF-8")
            self.assertEqual(env["LC_ALL"], "C.UTF-8")
            self.assertTrue(env["PATH"].startswith("/usr/bin:"))
            self.assertIn("/usr/local/node-18.18.0/bin", env["PATH"])

    def test_load_gateway_settings_reads_canonical_conf_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            conf_path = Path(tmp_dir) / ".mcpjungle.conf"
            conf_path.write_text(
                "registry_url: http://127.0.0.1:8081\naccess_token: secret-token\n",
                encoding="utf-8",
            )
            with mock.patch.dict(
                os.environ,
                {
                    "HOME": "/root",
                    "MCPJUNGLE_DATA_ROOT": tmp_dir,
                },
                clear=False,
            ):
                settings = load_gateway_settings()

            self.assertEqual(settings["registry_url"], "http://127.0.0.1:8081")
            self.assertEqual(settings["access_token"], "secret-token")

    def test_build_runtime_path_keeps_preferred_entries_first(self) -> None:
        built = build_runtime_path("/usr/local/node-18.18.0/bin:/custom/bin:/usr/bin")
        self.assertTrue(built.startswith("/usr/bin:/usr/local/bin:"))
        self.assertTrue(built.endswith("/usr/local/node-18.18.0/bin:/custom/bin"))


if __name__ == "__main__":
    unittest.main()
