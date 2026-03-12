import unittest

from mcpjungle_admin.cli import build_parser


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


if __name__ == "__main__":
    unittest.main()
