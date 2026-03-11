from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any

from .health import HealthChecker
from .managed_types import (
    build_entry_from_install_args,
    imported_entry_from_server_config,
    run_update_hook,
    update_entry_version,
)
from .mcpjungle_client import MCPJungleClient
from .models import is_path_within, permission_mode
from .reconcile import Reconciler
from .registry import ManagedRegistry


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mcpjungle-admin",
        description="App-owned MCP lifecycle management for MCPJungle Cloudron.",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON output")

    subparsers = parser.add_subparsers(dest="command", required=True)

    install_parser = subparsers.add_parser("install", help="Install and manage a new MCP")
    install_parser.add_argument(
        "--type",
        required=True,
        choices=[
            "npm_package",
            "uvx_package",
            "local_bundle",
            "http_remote",
            "custom_command",
        ],
    )
    install_parser.add_argument("--name", required=True)
    install_parser.add_argument("--description", default="")
    install_parser.add_argument("--transport")
    install_parser.add_argument("--package")
    install_parser.add_argument("--version")
    install_parser.add_argument("--url")
    install_parser.add_argument("--bearer-token")
    install_parser.add_argument("--command", dest="runtime_command")
    install_parser.add_argument("--bundle-source")
    install_parser.add_argument("--update-command")
    install_parser.add_argument("--manual-update-hook")
    install_parser.add_argument("--arg", action="append", default=[], help="Repeatable")
    install_parser.add_argument("--env", action="append", default=[], help="KEY=VALUE")
    install_parser.add_argument(
        "--health-mode",
        choices=["gateway", "list_tools", "http", "disabled"],
    )
    install_parser.add_argument("--health-url")

    import_parser = subparsers.add_parser(
        "import-existing", help="Import existing MCPJungle servers into the managed registry"
    )
    import_parser.add_argument("--all", action="store_true")

    update_parser = subparsers.add_parser("update", help="Update an existing managed MCP")
    update_parser.add_argument("name")
    update_parser.add_argument("--to", help="Explicit target version, or 'latest'")

    remove_parser = subparsers.add_parser("remove", help="Remove a managed MCP")
    remove_parser.add_argument("name")

    reconcile_parser = subparsers.add_parser(
        "reconcile", help="Reconcile app-owned state with MCPJungle"
    )
    reconcile_parser.add_argument("--name")

    subparsers.add_parser("list-managed", help="List managed MCP entries")
    subparsers.add_parser("doctor", help="Inspect registry, auth config, and health")

    return parser


def build_runtime() -> tuple[ManagedRegistry, MCPJungleClient, HealthChecker, Reconciler]:
    data_root = Path(os.environ.get("MCPJUNGLE_DATA_ROOT", "/app/data"))
    registry_path = Path(
        os.environ.get(
            "MCPJUNGLE_MANAGED_REGISTRY",
            str(data_root / ".mcpjungle-managed" / "registry.json"),
        )
    )
    bundles_root = Path(
        os.environ.get("MCPJUNGLE_BUNDLES_ROOT", str(data_root / "mcp-bundles"))
    )
    work_root = Path(
        os.environ.get(
            "MCPJUNGLE_MANAGED_WORK",
            str(registry_path.parent / "work"),
        )
    )
    registry = ManagedRegistry(
        registry_path=registry_path,
        bundles_root=bundles_root,
        work_root=work_root,
    )
    client = MCPJungleClient(
        cli_path=os.environ.get("MCPJUNGLE_CLI_PATH", "/usr/local/bin/mcpjungle"),
        registry_url=os.environ.get("MCPJUNGLE_REGISTRY_URL", "http://127.0.0.1:8080"),
        work_root=work_root,
        timeout=int(os.environ.get("MCPJUNGLE_CLI_TIMEOUT_SEC", "60")),
    )
    health_checker = HealthChecker(
        client, timeout=int(os.environ.get("MCPJUNGLE_HEALTH_TIMEOUT_SEC", "15"))
    )
    reconciler = Reconciler(registry, client, health_checker)
    return registry, client, health_checker, reconciler


def emit(payload: Any, *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return

    if isinstance(payload, list):
        for item in payload:
            print(item)
        return
    if isinstance(payload, dict):
        for key, value in payload.items():
            print(f"{key}: {value}")
        return
    print(payload)


def cmd_install(args: argparse.Namespace) -> int:
    registry, _, _, reconciler = build_runtime()
    entry = build_entry_from_install_args(args, registry)
    registry.upsert(entry)
    results = reconciler.reconcile(name=entry["name"])
    result = results[0]
    emit(result, as_json=args.json)
    return 0 if result["status"] in {"healthy", "unchanged"} else 1


def cmd_import_existing(args: argparse.Namespace) -> int:
    registry, client, _, _ = build_runtime()
    current_configs = client.get_server_configs()
    imported = []
    skipped = []
    for name, config in sorted(current_configs.items()):
        if registry.get(name):
            skipped.append(name)
            continue
        entry = imported_entry_from_server_config(config, bundles_root=registry.bundles_root)
        registry.upsert(entry)
        imported.append(name)

    moved_legacy = registry.cleanup_legacy_server_configs(
        managed_names=set(imported) | set(skipped)
    )
    payload = {
        "imported": imported,
        "skipped": skipped,
        "count": len(imported),
        "mode": "all" if args.all else "default",
        "moved_legacy_configs": moved_legacy,
    }
    emit(payload, as_json=args.json)
    return 0


def cmd_update(args: argparse.Namespace) -> int:
    registry, _, _, reconciler = build_runtime()
    entry = registry.require(args.name)

    if entry["managed_type"] in {"npm_package", "uvx_package"}:
        updated_entry = update_entry_version(entry, args.to)
    elif entry["managed_type"] in {"local_bundle", "custom_command"}:
        run_update_hook(entry, args.to)
        updated_entry = json.loads(json.dumps(entry))
        updated_entry["status"] = "pending"
        updated_entry["last_error"] = ""
    elif entry["managed_type"] == "http_remote":
        updated_entry = json.loads(json.dumps(entry))
        updated_entry.setdefault("install_spec", {})["updateStrategy"] = "external"
        updated_entry["status"] = "pending"
        updated_entry["last_error"] = ""
    else:  # pragma: no cover - registry guards this
        raise ValueError(f"Unsupported managed_type: {entry['managed_type']}")

    registry.upsert(updated_entry)
    result = reconciler.reconcile(name=args.name)[0]
    emit(result, as_json=args.json)
    return 0 if result["status"] in {"healthy", "unchanged"} else 1


def cmd_remove(args: argparse.Namespace) -> int:
    registry, client, _, _ = build_runtime()
    entry = registry.require(args.name)
    client.deregister_server(args.name, ignore_missing=True)
    registry.remove(args.name)

    install_path = entry.get("install_spec", {}).get("path")
    if entry["managed_type"] == "local_bundle" and install_path:
        bundle_path = Path(install_path)
        if bundle_path.exists() and is_path_within(registry.bundles_root, bundle_path):
            shutil.rmtree(bundle_path)

    payload = {"removed": args.name}
    emit(payload, as_json=args.json)
    return 0


def cmd_reconcile(args: argparse.Namespace) -> int:
    registry, _, _, reconciler = build_runtime()
    moved_legacy = registry.cleanup_legacy_server_configs()
    results = reconciler.reconcile(name=args.name)
    if args.json:
        emit({"moved_legacy_configs": moved_legacy, "results": results}, as_json=True)
        return 0 if all(result["status"] != "error" for result in results) else 1

    for item in moved_legacy:
        print(f"moved_legacy_config: {item['source']} -> {item['target']}")
    emit(results, as_json=args.json)
    return 0 if all(result["status"] != "error" for result in results) else 1


def cmd_list_managed(args: argparse.Namespace) -> int:
    registry, _, _, _ = build_runtime()
    entries = registry.list_entries()
    if args.json:
        emit(entries, as_json=True)
        return 0

    lines = []
    for entry in entries:
        lines.append(
            f"{entry['name']}\t{entry['managed_type']}\t{entry.get('status', 'unknown')}"
        )
    emit(lines or ["No managed MCP entries"], as_json=False)
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    registry, client, health_checker, _ = build_runtime()
    data_root = Path(os.environ.get("MCPJUNGLE_DATA_ROOT", "/app/data"))
    auth_config = Path(
        os.environ.get("MCPJUNGLE_AUTH_CONFIG", str(data_root / ".mcpjungle.conf"))
    )
    gateway_ok, gateway_message = health_checker.check_gateway()

    report = {
        "registry_path": str(registry.registry_path),
        "bundles_root": str(registry.bundles_root),
        "work_root": str(registry.work_root),
        "secrets_root": str(registry.secrets_root),
        "auth_config_exists": auth_config.exists(),
        "auth_config_mode": permission_mode(auth_config),
        "registry_mode": permission_mode(registry.registry_path),
        "legacy_configs_root": str(registry.legacy_configs_root),
        "legacy_config_files": registry.list_legacy_server_configs(),
        "gateway_healthy": gateway_ok,
        "gateway_message": gateway_message,
        "managed_entries": [],
        "issues": [],
    }

    if not auth_config.exists():
        report["issues"].append(
            f"Missing auth config at {auth_config}; boot-time reconcile will be skipped"
        )
    elif permission_mode(auth_config) != 0o600:
        report["issues"].append(
            f"Insecure permissions on {auth_config}: expected 0o600, got {oct(permission_mode(auth_config) or 0)}"
        )

    if permission_mode(registry.registry_path) not in {None, 0o600}:
        report["issues"].append(
            f"Insecure permissions on {registry.registry_path}: expected 0o600, got {oct(permission_mode(registry.registry_path) or 0)}"
        )
    if report["legacy_config_files"]:
        report["issues"].append(
            f"Legacy server config files still exist in {data_root}; run reconcile or import-existing to move them under {registry.legacy_configs_root}"
        )

    try:
        current_configs = client.get_server_configs() if gateway_ok else {}
    except Exception as exc:
        current_configs = {}
        report["issues"].append(f"Unable to export current MCPJungle state: {exc}")

    for entry in registry.list_entries():
        healthy, message = health_checker.check_entry(entry) if gateway_ok else (False, "Gateway unavailable")
        report["managed_entries"].append(
            {
                "name": entry["name"],
                "managed_type": entry["managed_type"],
                "status": entry.get("status", "unknown"),
                "registered": entry["name"] in current_configs,
                "healthy": healthy,
                "message": message,
                "secret_env_keys": entry.get("secret_env_keys", []),
                "has_secret_bearer_token": entry.get("has_secret_bearer_token", False),
            }
        )
        if not healthy:
            report["issues"].append(f"{entry['name']}: {message}")

    emit(report, as_json=args.json)
    return 0 if not report["issues"] else 1


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    commands = {
        "install": cmd_install,
        "import-existing": cmd_import_existing,
        "update": cmd_update,
        "remove": cmd_remove,
        "reconcile": cmd_reconcile,
        "list-managed": cmd_list_managed,
        "doctor": cmd_doctor,
    }
    return commands[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
