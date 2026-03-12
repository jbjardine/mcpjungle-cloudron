from __future__ import annotations

import urllib.request
from typing import Any

from .mcpjungle_client import MCPJungleClient, MCPJungleClientError


class HealthChecker:
    def __init__(self, client: MCPJungleClient, timeout: int = 15) -> None:
        self.client = client
        self.timeout = timeout

    def check_gateway(self) -> tuple[bool, str]:
        return self.client.gateway_health()

    def check_entry(self, entry: dict[str, Any]) -> tuple[bool, str]:
        spec = entry.get("healthcheck_spec") or {}
        mode = spec.get("mode") or "list_tools"

        if mode == "disabled":
            return True, "Healthcheck disabled"
        if mode == "gateway":
            return self.check_gateway()
        if mode == "http":
            return self._check_http(entry, spec)
        if mode == "invoke_tool":
            return self._check_invoke_tool(spec)
        if mode == "list_tools":
            return self._check_list_tools(entry["name"])
        return False, f"Unsupported healthcheck mode: {mode}"

    def _check_list_tools(self, server_name: str) -> tuple[bool, str]:
        try:
            output = self.client.list_tools(server_name).strip()
        except (MCPJungleClientError, RuntimeError) as exc:
            return False, str(exc)
        return True, output or f"Tools listed successfully for {server_name}"

    def _check_http(
        self,
        entry: dict[str, Any],
        spec: dict[str, Any],
    ) -> tuple[bool, str]:
        runtime_spec = entry.get("runtime_spec", {})
        url = spec.get("url") or runtime_spec.get("url")
        if not url:
            return False, "HTTP healthcheck requires a URL"

        request = urllib.request.Request(url)
        bearer_token = spec.get("bearer_token") or runtime_spec.get("bearer_token")
        if bearer_token:
            request.add_header("Authorization", f"Bearer {bearer_token}")

        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                status = getattr(response, "status", 200)
                if 200 <= status < 400:
                    return True, f"HTTP healthcheck passed ({status})"
                return False, f"HTTP healthcheck returned status {status}"
        except Exception as exc:  # pragma: no cover - urllib shapes differ
            return False, str(exc)

    def _check_invoke_tool(self, spec: dict[str, Any]) -> tuple[bool, str]:
        tool_name = spec.get("tool_name")
        if not tool_name:
            return False, "invoke_tool healthcheck requires tool_name"

        tool_input = spec.get("tool_input")
        try:
            output = self.client.invoke_tool(tool_name, tool_input).strip()
        except (MCPJungleClientError, RuntimeError) as exc:
            return False, str(exc)
        return True, output or f"Tool invoked successfully for {tool_name}"
