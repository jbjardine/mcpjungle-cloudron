from __future__ import annotations

import copy
import json
import os
import shutil
import subprocess
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from .models import (
    is_path_within,
    normalize_transport,
    runtime_hash_from_config,
    sanitize_server_config,
    utcnow_iso,
)
from .registry import ManagedRegistry


def parse_env_items(items: list[str] | None) -> dict[str, str]:
    env: dict[str, str] = {}
    for item in items or []:
        if "=" not in item:
            raise ValueError(f"Invalid env assignment {item!r}, expected KEY=VALUE")
        key, value = item.split("=", 1)
        env[key] = value
    return env


def maybe_resolve_bundle_command(command: str, bundle_path: Path) -> str:
    command_path = Path(command)
    if command_path.is_absolute():
        return str(command_path)
    if "/" in command or command.startswith("."):
        return str((bundle_path / command_path).resolve())
    return command


def resolve_latest_npm_version(package_name: str) -> str:
    result = subprocess.run(
        ["npm", "view", package_name, "version"],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            result.stderr.strip() or f"Unable to resolve latest npm version for {package_name}"
        )
    return result.stdout.strip()


def resolve_latest_pypi_version(package_name: str) -> str:
    url = f"https://pypi.org/pypi/{urllib.parse.quote(package_name)}/json"
    try:
        with urllib.request.urlopen(url, timeout=15) as response:
            payload = json.load(response)
    except Exception as exc:  # pragma: no cover - urllib errors vary
        raise RuntimeError(
            f"Unable to resolve latest PyPI version for {package_name}: {exc}"
        ) from exc
    return payload["info"]["version"]


def resolve_version(managed_type: str, package_name: str, requested_version: str | None) -> str:
    if requested_version and requested_version != "latest":
        return requested_version
    if managed_type == "npm_package":
        return resolve_latest_npm_version(package_name)
    if managed_type == "uvx_package":
        return resolve_latest_pypi_version(package_name)
    raise ValueError(f"Version resolution is unsupported for {managed_type}")


def build_entry_from_install_args(
    args: Any,
    registry: ManagedRegistry,
) -> dict[str, Any]:
    env = parse_env_items(getattr(args, "env", None))
    managed_type = args.type
    description = args.description or ""

    if managed_type == "npm_package":
        if not args.package:
            raise ValueError("--package is required for npm_package")
        version = resolve_version(managed_type, args.package, args.version)
        runtime_spec = {
            "command": "npx",
            "args": ["-y", f"{args.package}@{version}", *args.arg],
            "env": env,
        }
        install_spec = {
            "package": args.package,
            "version": version,
            "extraArgs": list(args.arg),
            "updateStrategy": "pinned",
        }
        transport = "stdio"
    elif managed_type == "uvx_package":
        if not args.package:
            raise ValueError("--package is required for uvx_package")
        version = resolve_version(managed_type, args.package, args.version)
        runtime_spec = {
            "command": "uvx",
            "args": [f"{args.package}=={version}", *args.arg],
            "env": env,
        }
        install_spec = {
            "package": args.package,
            "version": version,
            "extraArgs": list(args.arg),
            "updateStrategy": "pinned",
        }
        transport = "stdio"
    elif managed_type == "http_remote":
        if not args.url:
            raise ValueError("--url is required for http_remote")
        transport = normalize_transport(args.transport or "streamable-http")
        runtime_spec = {"url": args.url}
        if args.bearer_token:
            runtime_spec["bearer_token"] = args.bearer_token
        install_spec = {
            "url": args.url,
            "updateStrategy": "external",
        }
    elif managed_type == "custom_command":
        if not args.runtime_command:
            raise ValueError("--command is required for custom_command")
        transport = normalize_transport(args.transport or "stdio")
        runtime_spec = {
            "command": args.runtime_command,
            "args": list(args.arg),
            "env": env,
        }
        install_spec = {
            "updateStrategy": "manual",
            "manualUpdateHook": args.manual_update_hook or "",
        }
    elif managed_type == "local_bundle":
        if not args.bundle_source:
            raise ValueError("--bundle-source is required for local_bundle")
        if not args.runtime_command:
            raise ValueError("--command is required for local_bundle")
        registry.ensure_layout()
        source_path = Path(args.bundle_source).expanduser().resolve()
        if not source_path.exists():
            raise ValueError(f"Bundle source {source_path} does not exist")
        target_path = registry.bundles_root / args.name
        if target_path.exists():
            raise ValueError(
                f"Bundle target {target_path} already exists; remove it first or pick another name"
            )
        shutil.copytree(source_path, target_path)
        transport = normalize_transport(args.transport or "stdio")
        runtime_spec = {
            "command": maybe_resolve_bundle_command(args.runtime_command, target_path),
            "args": list(args.arg),
            "env": env,
        }
        install_spec = {
            "path": str(target_path),
            "sourcePath": str(source_path),
            "updateStrategy": "command" if args.update_command else "manual",
            "updateCommand": args.update_command or "",
        }
    else:  # pragma: no cover - argparse already guards this
        raise ValueError(f"Unsupported managed_type: {managed_type}")

    health_mode = args.health_mode or "list_tools"
    healthcheck_spec = {"mode": health_mode}
    if args.health_url:
        healthcheck_spec["url"] = args.health_url

    entry = {
        "name": args.name,
        "description": description,
        "transport": transport,
        "managed": True,
        "managed_type": managed_type,
        "runtime_spec": sanitize_server_config(runtime_spec),
        "install_spec": install_spec,
        "healthcheck_spec": healthcheck_spec,
        "last_applied_hash": "",
        "last_known_good": {},
        "status": "pending",
        "last_error": "",
        "created_at": utcnow_iso(),
        "updated_at": utcnow_iso(),
    }
    return entry


def split_package_version(package_spec: str, *, separator: str) -> tuple[str, str | None]:
    if separator == "@":
        if package_spec.startswith("@"):
            index = package_spec.rfind("@")
            if index > 0:
                return package_spec[:index], package_spec[index + 1 :]
            return package_spec, None
        if "@" in package_spec:
            package, version = package_spec.rsplit("@", 1)
            return package, version
        return package_spec, None

    if separator == "==":
        if "==" in package_spec:
            package, version = package_spec.split("==", 1)
            return package, version
        return package_spec, None

    raise ValueError(f"Unsupported separator {separator}")


def detect_managed_type(
    server_config: dict[str, Any],
    bundles_root: str | Path = "/app/data/mcp-bundles",
) -> str:
    bundles_root_path = Path(bundles_root).resolve()
    transport = normalize_transport(server_config.get("transport"))

    if transport in {"streamable-http", "sse"} and server_config.get("url"):
        return "http_remote"

    command = server_config.get("command", "")
    args = server_config.get("args", [])

    if command == "uvx":
        return "uvx_package"

    if command == "npx":
        return "npm_package"

    command_path = Path(command) if command else None
    if command_path and command_path.is_absolute() and is_path_within(
        bundles_root_path, command_path
    ):
        return "local_bundle"

    for arg in args:
        arg_path = Path(arg)
        if arg_path.is_absolute() and is_path_within(bundles_root_path, arg_path):
            return "local_bundle"

    return "custom_command"


def imported_entry_from_server_config(
    server_config: dict[str, Any],
    bundles_root: str | Path = "/app/data/mcp-bundles",
) -> dict[str, Any]:
    config = sanitize_server_config(server_config)
    managed_type = detect_managed_type(config, bundles_root=bundles_root)
    runtime_spec = {
        key: copy.deepcopy(value)
        for key, value in config.items()
        if key not in {"name", "description", "transport"}
    }

    install_spec: dict[str, Any]
    if managed_type == "npm_package":
        package_spec, package_index = _extract_primary_arg(config.get("args", []))
        package_name, version = split_package_version(package_spec, separator="@")
        install_spec = {
            "package": package_name,
            "version": version or "latest",
            "extraArgs": config.get("args", [])[package_index + 1 :],
            "updateStrategy": "pinned",
        }
    elif managed_type == "uvx_package":
        package_spec, package_index = _extract_primary_arg(config.get("args", []))
        package_name, version = split_package_version(package_spec, separator="==")
        install_spec = {
            "package": package_name,
            "version": version or "latest",
            "extraArgs": config.get("args", [])[package_index + 1 :],
            "updateStrategy": "pinned",
        }
    elif managed_type == "local_bundle":
        install_spec = {
            "path": _infer_bundle_path(config, bundles_root),
            "updateStrategy": "manual",
        }
    elif managed_type == "http_remote":
        install_spec = {
            "url": config.get("url", ""),
            "updateStrategy": "external",
        }
    else:
        install_spec = {
            "updateStrategy": "manual",
            "manualUpdateHook": "",
        }

    return {
        "name": config["name"],
        "description": config.get("description", ""),
        "transport": normalize_transport(config["transport"]),
        "managed": True,
        "managed_type": managed_type,
        "runtime_spec": runtime_spec,
        "install_spec": install_spec,
        "healthcheck_spec": {"mode": "list_tools"},
        "last_applied_hash": runtime_hash_from_config(config),
        "last_known_good": config,
        "status": "imported",
        "last_error": "",
        "created_at": utcnow_iso(),
        "updated_at": utcnow_iso(),
    }


def update_entry_version(entry: dict[str, Any], target_version: str | None) -> dict[str, Any]:
    managed_type = entry["managed_type"]
    updated = copy.deepcopy(entry)
    install_spec = updated.setdefault("install_spec", {})
    runtime_spec = updated.setdefault("runtime_spec", {})

    if managed_type == "npm_package":
        package_name = install_spec["package"]
        version = resolve_version(managed_type, package_name, target_version)
        install_spec["version"] = version
        extra_args = install_spec.get("extraArgs", [])
        runtime_spec["command"] = "npx"
        runtime_spec["args"] = ["-y", f"{package_name}@{version}", *extra_args]
    elif managed_type == "uvx_package":
        package_name = install_spec["package"]
        version = resolve_version(managed_type, package_name, target_version)
        install_spec["version"] = version
        extra_args = install_spec.get("extraArgs", [])
        runtime_spec["command"] = "uvx"
        runtime_spec["args"] = [f"{package_name}=={version}", *extra_args]
    elif managed_type == "http_remote":
        install_spec["updateStrategy"] = "external"
    else:
        raise ValueError(
            f"Version updates are not supported for managed_type {managed_type}"
        )

    updated["status"] = "pending"
    updated["updated_at"] = utcnow_iso()
    return updated


def run_update_hook(entry: dict[str, Any], target_version: str | None = None) -> str:
    install_spec = entry.get("install_spec", {})
    command = ""
    if entry["managed_type"] == "local_bundle":
        command = install_spec.get("updateCommand", "")
    elif entry["managed_type"] == "custom_command":
        command = install_spec.get("manualUpdateHook", "")

    if not command:
        raise ValueError(f"No update hook configured for {entry['name']}")

    env = {
        **os.environ,
        "MCP_NAME": entry["name"],
        "MCP_MANAGED_TYPE": entry["managed_type"],
        "MCP_TARGET_VERSION": target_version or "",
    }
    result = subprocess.run(
        ["/bin/sh", "-lc", command],
        cwd=install_spec.get("path") or None,
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or command)
    return result.stdout.strip() or command


def _extract_primary_arg(args: list[str]) -> tuple[str, int]:
    for index, arg in enumerate(args):
        if arg.startswith("-") and not arg.startswith("@"):
            continue
        return arg, index
    raise ValueError(f"Unable to infer package spec from args: {args}")


def _infer_bundle_path(
    server_config: dict[str, Any],
    bundles_root: str | Path,
) -> str:
    bundles_root_path = Path(bundles_root).resolve()
    command = server_config.get("command")
    if command:
        command_path = Path(command)
        if command_path.is_absolute() and is_path_within(bundles_root_path, command_path):
            return str(command_path.parent)
    for arg in server_config.get("args", []):
        arg_path = Path(arg)
        if arg_path.is_absolute() and is_path_within(bundles_root_path, arg_path):
            return str(arg_path.parent)
    return ""
