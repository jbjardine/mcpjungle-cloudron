"""Automatic update functionality for managed MCP servers."""

from __future__ import annotations

import logging
from typing import Any

from .managed_types import (
    resolve_latest_npm_version,
    resolve_latest_pypi_version,
    update_entry_version,
)
from .registry import ManagedRegistry


logger = logging.getLogger(__name__)


def auto_update(
    registry: ManagedRegistry,
    name: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """
    Check for and apply updates to managed MCP servers.

    Args:
        registry: ManagedRegistry instance
        name: Optional specific server name to update; if None, updates all
        dry_run: If True, check for updates but don't apply them

    Returns:
        Dictionary with summary of updates.
    """
    summary: dict[str, Any] = {
        "checked": [],
        "updated": [],
        "skipped": [],
        "errors": [],
        "total_checked": 0,
        "total_updated": 0,
    }

    try:
        if name:
            entry = registry.get(name)
            if entry is None:
                summary["errors"].append(f"Server '{name}' not found in registry")
                return summary
            entries_to_check = [entry]
        else:
            entries_to_check = registry.list_entries()

        if not entries_to_check:
            return summary

        for entry in entries_to_check:
            entry_name = entry["name"]
            summary["total_checked"] += 1
            summary["checked"].append(entry_name)

            managed_type = entry.get("managed_type")
            install_spec = entry.get("install_spec", {})

            # Only npm_package and uvx_package support auto-update
            if managed_type not in ("npm_package", "uvx_package"):
                summary["skipped"].append(
                    {
                        "name": entry_name,
                        "reason": f"managed_type '{managed_type}' doesn't support auto-update",
                    }
                )
                continue

            # Check update strategy
            update_strategy = install_spec.get("updateStrategy", "pinned")
            if update_strategy in ("manual", "external"):
                summary["skipped"].append(
                    {"name": entry_name, "reason": f"updateStrategy is '{update_strategy}'"}
                )
                continue

            # Get current version
            current_version = install_spec.get("version")
            if not current_version:
                summary["skipped"].append(
                    {"name": entry_name, "reason": "No version specified in install_spec"}
                )
                continue

            # Resolve latest version
            package_name = install_spec.get("package")
            if not package_name:
                summary["skipped"].append(
                    {"name": entry_name, "reason": "No package name in install_spec"}
                )
                continue

            latest_version = None
            try:
                if managed_type == "npm_package":
                    latest_version = resolve_latest_npm_version(package_name)
                elif managed_type == "uvx_package":
                    latest_version = resolve_latest_pypi_version(package_name)
            except Exception as e:
                error_msg = f"Failed to resolve latest version for '{entry_name}': {e}"
                logger.error(error_msg)
                summary["errors"].append(error_msg)
                continue

            if not latest_version:
                summary["skipped"].append(
                    {"name": entry_name, "reason": "Could not determine latest version"}
                )
                continue

            if latest_version == current_version:
                summary["skipped"].append(
                    {
                        "name": entry_name,
                        "reason": f"Already at latest version ({current_version})",
                    }
                )
                continue

            # Version update available
            if not dry_run:
                try:
                    updated = update_entry_version(entry, latest_version)
                    registry.upsert(updated)
                    summary["total_updated"] += 1
                    summary["updated"].append(
                        {
                            "name": entry_name,
                            "from": current_version,
                            "to": latest_version,
                        }
                    )
                except Exception as e:
                    error_msg = (
                        f"Failed to update '{entry_name}' "
                        f"from {current_version} to {latest_version}: {e}"
                    )
                    logger.error(error_msg)
                    summary["errors"].append(error_msg)
            else:
                summary["total_updated"] += 1
                summary["updated"].append(
                    {
                        "name": entry_name,
                        "from": current_version,
                        "to": latest_version,
                        "dry_run": True,
                    }
                )

    except Exception as e:
        error_msg = f"Unexpected error during auto-update: {e}"
        logger.error(error_msg)
        summary["errors"].append(error_msg)

    return summary
