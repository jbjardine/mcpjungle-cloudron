from __future__ import annotations

import copy
from typing import Any

from .health import HealthChecker
from .mcpjungle_client import MCPJungleClient, MCPJungleClientError
from .models import (
    resolved_server_config,
    runtime_hash_from_config,
    server_config_from_entry,
    strip_sensitive_server_config,
)
from .registry import ManagedRegistry


class Reconciler:
    def __init__(
        self,
        registry: ManagedRegistry,
        client: MCPJungleClient,
        health_checker: HealthChecker,
    ) -> None:
        self.registry = registry
        self.client = client
        self.health_checker = health_checker

    def reconcile(self, name: str | None = None) -> list[dict[str, Any]]:
        current_configs = self.client.get_server_configs()
        if name:
            entry = self.registry.require(name)
            entries = [entry]
        else:
            entries = self.registry.list_entries()

        results = []
        for entry in entries:
            if not entry.get("managed", True):
                continue
            current_config = current_configs.get(entry["name"])
            result = self._reconcile_entry(entry, current_config)
            self.registry.upsert(result["entry"])
            if result["status"] in {"healthy", "unchanged"}:
                current_configs[entry["name"]] = server_config_from_entry(result["entry"])
            results.append(result)
        return results

    def _reconcile_entry(
        self,
        entry: dict[str, Any],
        current_config: dict[str, Any] | None,
    ) -> dict[str, Any]:
        updated_entry = copy.deepcopy(entry)
        desired_config = server_config_from_entry(updated_entry)
        desired_hash = runtime_hash_from_config(desired_config)
        current_hash = (
            runtime_hash_from_config(current_config) if current_config is not None else None
        )

        if current_hash == desired_hash:
            ok, message = self.health_checker.check_entry(updated_entry)
            updated_entry["last_applied_hash"] = desired_hash
            updated_entry["last_known_good"] = strip_sensitive_server_config(desired_config)
            updated_entry["status"] = "unchanged" if ok else "error"
            updated_entry["last_error"] = "" if ok else message
            return {
                "name": updated_entry["name"],
                "status": updated_entry["status"],
                "message": message,
                "entry": updated_entry,
                "changed": False,
            }

        rollback_config = current_config or updated_entry.get("last_known_good") or None
        try:
            if current_config is not None:
                self.client.deregister_server(updated_entry["name"], ignore_missing=True)
            self.client.register_server(desired_config)

            ok, message = self.health_checker.check_entry(updated_entry)
            if not ok:
                raise RuntimeError(message)

            updated_entry["last_applied_hash"] = desired_hash
            updated_entry["last_known_good"] = strip_sensitive_server_config(desired_config)
            updated_entry["status"] = "healthy"
            updated_entry["last_error"] = ""
            return {
                "name": updated_entry["name"],
                "status": "healthy",
                "message": message,
                "entry": updated_entry,
                "changed": True,
            }
        except Exception as exc:
            rollback_message = self._rollback(updated_entry, rollback_config, desired_config)
            updated_entry["status"] = "error"
            updated_entry["last_error"] = (
                f"{exc}; {rollback_message}" if rollback_message else str(exc)
            )
            return {
                "name": updated_entry["name"],
                "status": "error",
                "message": updated_entry["last_error"],
                "entry": updated_entry,
                "changed": True,
            }

    def _rollback(
        self,
        entry: dict[str, Any],
        rollback_config: dict[str, Any] | None,
        desired_config: dict[str, Any],
    ) -> str:
        try:
            if rollback_config:
                self.client.deregister_server(entry["name"], ignore_missing=True)
                self.client.register_server(resolved_server_config(rollback_config, entry))
                return "rollback applied"

            if entry["managed_type"] != "http_remote":
                self.client.deregister_server(entry["name"], ignore_missing=True)
                return "rolled back to absence"

            return "remote config kept in error state"
        except MCPJungleClientError as exc:
            return f"rollback failed: {exc}"
