import unittest

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


if __name__ == "__main__":
    unittest.main()
