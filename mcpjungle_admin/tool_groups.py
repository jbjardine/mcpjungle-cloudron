"""
MCPJungle Tool Groups Manager.

Manages MCPJungle's native Tool Groups feature via REST API.
Tool Groups allow organizing tools from multiple servers into logical groups,
each exposing an MCP endpoint at /v0/groups/:name/mcp.

API endpoints (confirmed from MCPJungle Go source internal/api/tool_groups.go):
  POST   /api/v0/tool-groups       - create a group
  GET    /api/v0/tool-groups       - list all groups
  GET    /api/v0/tool-groups/:name - get single group
  DELETE /api/v0/tool-groups/:name - delete a group
  (NO PUT - groups must be deleted and recreated to update)

Group config fields: name, description, included_tools, included_servers, excluded_tools
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from typing import Any

from .runtime import load_gateway_settings, runtime_conf_path

logger = logging.getLogger(__name__)

_MANAGED_GROUP_PREFIX = "[cloudron-managed] "


class ToolGroupsError(Exception):
    """Base exception for Tool Groups operations."""


class ToolGroupsManager:
    """Manage MCPJungle Tool Groups via REST API."""

    def __init__(
        self,
        gateway_url: str = "http://127.0.0.1:8080",
        access_token: str | None = None,
    ) -> None:
        self.gateway_url = gateway_url.rstrip("/")
        self.access_token = access_token or self._load_access_token()
        if not self.access_token:
            raise ToolGroupsError(
                "Access token not provided and not found in "
                "MCPJUNGLE_ACCESS_TOKEN env or .mcpjungle.conf"
            )

    @staticmethod
    def _load_access_token() -> str | None:
        token = os.environ.get("MCPJUNGLE_ACCESS_TOKEN")
        if token:
            return token
        settings = load_gateway_settings()
        if settings["access_token"]:
            return settings["access_token"]
        config_path = runtime_conf_path()
        if not config_path.exists():
            return None
        try:
            # Config can be TOML (key = value) or YAML-like (key: value)
            text = config_path.read_text(encoding="utf-8")
            for line in text.splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                for sep in (":", "="):
                    if sep in line:
                        key, _, value = line.partition(sep)
                        key = key.strip()
                        value = value.strip().strip("\"'")
                        if key in ("accessToken", "access_token"):
                            return value
                        break
        except Exception:
            pass
        return None

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }

    @staticmethod
    def legacy_group_description(name: str) -> str:
        return f"All tools from {name}"

    @staticmethod
    def canonical_group_description(name: str) -> str:
        return f"{_MANAGED_GROUP_PREFIX}All tools from {name}"

    @classmethod
    def _is_cloudron_managed_shape(cls, group: dict[str, Any]) -> bool:
        name = group.get("name")
        if not name:
            return False
        included_servers = group.get("included_servers") or []
        included_tools = group.get("included_tools") or []
        excluded_tools = group.get("excluded_tools") or []
        description = group.get("description") or ""
        return (
            included_servers == [name]
            and not included_tools
            and not excluded_tools
            and description in {
                cls.legacy_group_description(name),
                cls.canonical_group_description(name),
            }
        )

    @classmethod
    def is_managed_server_group(
        cls,
        group: dict[str, Any],
        managed_names: set[str],
    ) -> bool:
        name = group.get("name")
        return bool(name in managed_names and cls._is_cloudron_managed_shape(group))

    def _api_request(
        self,
        method: str,
        endpoint: str,
        json_body: dict[str, Any] | None = None,
    ) -> Any:
        url = f"{self.gateway_url}/api/v0{endpoint}"
        data = json.dumps(json_body).encode("utf-8") if json_body else None
        req = urllib.request.Request(
            url, data=data, headers=self._headers(), method=method
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                body = resp.read().decode("utf-8")
                return json.loads(body) if body.strip() else {}
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", errors="replace") if e.fp else ""
            raise ToolGroupsError(
                f"API {method} {endpoint} failed ({e.code}): {raw}"
            ) from e
        except urllib.error.URLError as e:
            raise ToolGroupsError(
                f"API {method} {endpoint} failed: {e}"
            ) from e

    def list_groups(self) -> list[dict[str, Any]]:
        result = self._api_request("GET", "/tool-groups")
        if isinstance(result, dict) and "groups" in result:
            return result["groups"]
        if isinstance(result, list):
            return result
        return []

    def get_group(self, name: str) -> dict[str, Any] | None:
        try:
            return self._api_request("GET", f"/tool-groups/{name}")
        except ToolGroupsError as e:
            if "404" in str(e):
                return None
            raise

    def create_group(
        self,
        name: str,
        description: str = "",
        included_servers: list[str] | None = None,
        included_tools: list[str] | None = None,
        excluded_tools: list[str] | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"name": name, "description": description}
        if included_servers:
            body["included_servers"] = included_servers
        if included_tools:
            body["included_tools"] = included_tools
        if excluded_tools:
            body["excluded_tools"] = excluded_tools
        return self._api_request("POST", "/tool-groups", body)

    def delete_group(self, name: str) -> None:
        self._api_request("DELETE", f"/tool-groups/{name}")

    def get_group_endpoint(self, name: str) -> str:
        return f"{self.gateway_url}/v0/groups/{name}/mcp"

    def sync_tool_groups(
        self,
        server_names: list[str],
        *,
        managed_names: set[str] | None = None,
    ) -> dict[str, Any]:
        """
        Synchronize tool groups with registered MCP servers.

        Creates one tool group per server (using included_servers),
        and removes orphaned groups that no longer have a corresponding server.

        Args:
            server_names: List of currently registered server names.

        Returns:
            Summary dict with created, deleted, unchanged, and errors lists.
        """
        summary: dict[str, Any] = {
            "created": [],
            "recreated": [],
            "deleted": [],
            "unchanged": [],
            "warnings": [],
            "errors": [],
        }

        managed_names = set(managed_names or server_names)
        desired_names = set(server_names) & managed_names

        try:
            existing_groups = self.list_groups()
        except ToolGroupsError as e:
            summary["errors"].append(f"Failed to list groups: {e}")
            return summary

        existing_by_name = {
            group["name"]: group for group in existing_groups if group.get("name")
        }

        # Recreate managed groups that still use the legacy description so
        # subsequent boots can identify them unambiguously.
        for name in sorted(desired_names):
            group = existing_by_name.get(name)
            if not group or not self.is_managed_server_group(group, managed_names):
                continue
            if group.get("description") == self.canonical_group_description(name):
                continue
            try:
                self.delete_group(name)
                self.create_group(
                    name=name,
                    description=self.canonical_group_description(name),
                    included_servers=[name],
                )
                summary["recreated"].append(name)
                logger.info(
                    "Recreated auto-managed tool group '%s' with canonical description",
                    name,
                )
                existing_by_name[name] = {
                    "name": name,
                    "description": self.canonical_group_description(name),
                    "included_servers": [name],
                }
            except ToolGroupsError as e:
                summary["errors"].append(f"Failed to recreate group '{name}': {e}")
                logger.error("Failed to recreate tool group '%s': %s", name, e)

        # Create missing groups unless a custom group already claims the name.
        for name in sorted(desired_names):
            group = existing_by_name.get(name)
            if group is None:
                try:
                    self.create_group(
                        name=name,
                        description=self.canonical_group_description(name),
                        included_servers=[name],
                    )
                    summary["created"].append(name)
                    logger.info("Created auto-managed tool group '%s'", name)
                except ToolGroupsError as e:
                    summary["errors"].append(f"Failed to create group '{name}': {e}")
                    logger.error("Failed to create tool group '%s': %s", name, e)
                continue

            if self.is_managed_server_group(group, managed_names):
                if name not in summary["recreated"]:
                    summary["unchanged"].append(name)
                continue

            warning = (
                f"Skipped auto-managed group '{name}' because a custom group with the same "
                "name already exists"
            )
            summary["warnings"].append(warning)
            logger.warning("%s", warning)

        # Delete orphaned auto-managed groups, including legacy-format groups left
        # behind by older releases.
        for name, group in sorted(existing_by_name.items()):
            if name in desired_names:
                continue
            if not self._is_cloudron_managed_shape(group):
                continue
            try:
                self.delete_group(name)
                summary["deleted"].append(name)
                logger.info("Deleted orphaned auto-managed tool group '%s'", name)
            except ToolGroupsError as e:
                summary["errors"].append(f"Failed to delete group '{name}': {e}")
                logger.error("Failed to delete tool group '%s': %s", name, e)

        return summary

    def prune_managed_groups(self, managed_names: set[str]) -> dict[str, Any]:
        summary: dict[str, Any] = {
            "deleted": [],
            "unchanged": [],
            "errors": [],
        }

        try:
            existing_groups = self.list_groups()
        except ToolGroupsError as e:
            summary["errors"].append(f"Failed to list groups: {e}")
            return summary

        for group in sorted(existing_groups, key=lambda item: item.get("name", "")):
            name = group.get("name")
            if not name:
                continue
            if not self.is_managed_server_group(group, managed_names):
                summary["unchanged"].append(name)
                continue
            try:
                self.delete_group(name)
                summary["deleted"].append(name)
                logger.info(
                    "Deleted auto-managed tool group '%s' to prepare boot reconcile",
                    name,
                )
            except ToolGroupsError as e:
                summary["errors"].append(f"Failed to delete group '{name}': {e}")
                logger.error(
                    "Failed to delete auto-managed tool group '%s' before boot reconcile: %s",
                    name,
                    e,
                )

        return summary
