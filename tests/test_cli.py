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


if __name__ == "__main__":
    unittest.main()
