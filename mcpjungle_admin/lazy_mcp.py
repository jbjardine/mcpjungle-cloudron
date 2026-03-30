"""
Lazy-MCP configuration generator for MCPJungle gateway.

lazy-mcp (npm: lazy-mcp, repo: gitlab.com/gitlab-org/ai/lazy-mcp) is a
client-side stdio proxy that aggregates MCP servers and exposes 4 meta-tools:
  list_servers, list_commands, describe_commands, invoke_command

This reduces token consumption by ~90% (from ~16K to ~1.5K tokens initially).

lazy-mcp is NOT a server daemon - it runs client-side via stdio transport.
This module generates the servers.json config file that points clients to
the MCPJungle gateway (or to individual Tool Group endpoints).

Usage:
  npx lazy-mcp@latest --config /app/data/.mcpjungle-managed/lazy-mcp-servers.json
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Default output path for the generated config
DEFAULT_CONFIG_PATH = Path("/app/data/.mcpjungle-managed/lazy-mcp-servers.json")


def generate_lazy_mcp_config(
    gateway_url: str = "http://127.0.0.1:8080",
    public_url: str | None = None,
    output_path: Path | None = None,
) -> dict[str, Any]:
    """
    Generate a lazy-mcp servers.json configuration file.

    Produces a single-entry config pointing to the MCPJungle gateway.
    lazy-mcp's meta-tools (list_servers, list_commands, etc.) handle
    per-server discovery dynamically - no need for per-group entries.

    Args:
        gateway_url: Internal MCPJungle gateway URL (for health checks).
        public_url: Public-facing URL for the gateway. If provided, used
                    in the config so clients can connect externally.
                    Falls back to CLOUDRON_APP_ORIGIN env var, then gateway_url.
        output_path: Where to write the config. Defaults to DEFAULT_CONFIG_PATH.

    Returns:
        The generated configuration dict.
    """
    base_url = (
        public_url
        or os.environ.get("CLOUDRON_APP_ORIGIN")
        or gateway_url
    ).rstrip("/")

    config: dict[str, Any] = {
        "servers": [
            {
                "name": "mcpjungle",
                "description": "MCPJungle gateway (all tools)",
                "url": f"{base_url}/mcp",
            }
        ]
    }

    # Write config file
    target = output_path or DEFAULT_CONFIG_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)
        f.write("\n")

    os.chmod(target, 0o644)
    logger.info(
        "Generated lazy-mcp config at %s (%d server entries)",
        target,
        len(config["servers"]),
    )
    return config


def get_lazy_mcp_config_path() -> Path:
    """Return the path where the lazy-mcp config is written."""
    return DEFAULT_CONFIG_PATH


def get_lazy_mcp_client_command(config_path: Path | None = None) -> list[str]:
    """
    Return the command clients should use to start lazy-mcp.

    Returns:
        List of command parts for use in MCP client configuration.
    """
    path = config_path or DEFAULT_CONFIG_PATH
    return ["npx", "lazy-mcp@latest", "--config", str(path)]
