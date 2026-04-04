from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

from .auto_update import auto_update
from .health import HealthChecker
from .managed_types import (
    build_entry_from_install_args,
    imported_entry_from_server_config,
    parse_env_items,
    run_update_hook,
    update_entry_version,
)
from .managed_files import configure_managed_file
from .mcpjungle_client import MCPJungleClient
from .models import is_path_within, load_secret_material, permission_mode, utcnow_iso
from .reconcile import Reconciler
from .registry import ManagedRegistry
from .runtime import (
    load_gateway_settings,
    runtime_conf_path,
    runtime_data_root,
    runtime_summary,
)


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
        choices=["gateway", "list_tools", "invoke_tool", "http", "disabled"],
    )
    install_parser.add_argument("--health-url")
    install_parser.add_argument("--health-tool")
    install_parser.add_argument("--health-input")

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
    reconcile_parser.add_argument("--force", action="store_true")
    reconcile_parser.add_argument(
        "--boot-mode", action="store_true",
        help="Fast boot reconcile: short timeout, no rollback, circuit breaker, parallel",
    )

    bind_file_parser = subparsers.add_parser(
        "bind-file",
        help="Copy a file into app-managed secrets, expose its path via runtime env, and force reapply the MCP",
    )
    bind_file_parser.add_argument("--name", required=True)
    bind_file_parser.add_argument("--source", required=True)
    bind_file_parser.add_argument("--env-key", required=True)
    bind_file_parser.add_argument("--dest-name")
    bind_file_parser.add_argument(
        "--set-env",
        action="append",
        default=[],
        help="Repeatable KEY=VALUE env overrides to apply alongside the managed file",
    )
    bind_file_parser.add_argument(
        "--clear-env",
        action="append",
        default=[],
        help="Repeatable env keys to remove before reapplying the MCP",
    )
    bind_file_parser.add_argument(
        "--health-mode",
        choices=["leave", "gateway", "list_tools", "invoke_tool", "http", "disabled"],
        default="leave",
    )
    bind_file_parser.add_argument("--health-url")
    bind_file_parser.add_argument("--health-tool")
    bind_file_parser.add_argument("--health-input")

    subparsers.add_parser("list-managed", help="List managed MCP entries")
    subparsers.add_parser("doctor", help="Inspect registry, auth config, and health")

    auto_update_parser = subparsers.add_parser(
        "auto-update", help="Check for and apply updates to managed MCPs"
    )
    auto_update_parser.add_argument(
        "--name", help="Specific server name to update; if not provided, checks all"
    )
    auto_update_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Check for updates without applying them",
    )

    subparsers.add_parser(
        "sync-groups",
        help="Synchronize Tool Groups with registered MCP servers",
    )
    subparsers.add_parser(
        "prune-managed-groups",
        help="Delete Cloudron-managed tool groups before boot reconcile",
    )

    gen_lazy_parser = subparsers.add_parser(
        "generate-lazy-config",
        help="Generate lazy-mcp servers.json configuration",
    )
    gen_lazy_parser.add_argument(
        "--public-url",
        help="Public URL of the MCPJungle gateway (defaults to CLOUDRON_APP_ORIGIN)",
    )

    creds_set_parser = subparsers.add_parser(
        "creds-set",
        help="Set a credential key-value for a managed MCP server",
    )
    creds_set_parser.add_argument("server_name", help="Name of the managed server")
    creds_set_parser.add_argument(
        "key_value",
        help="Credential in KEY=VALUE format (e.g. API_KEY=sk-123)",
    )

    creds_list_parser = subparsers.add_parser(
        "creds-list",
        help="List credential keys (masked) for a managed MCP server",
    )
    creds_list_parser.add_argument("server_name", help="Name of the managed server")

    return parser


def _resolve_registry_url() -> str:
    """Resolve the gateway URL from env, then .mcpjungle.conf, then default.

    During startup the Go gateway listens on 8081 (nginx on 8080 isn't up yet).
    The .mcpjungle.conf written at first boot stores registry_url: http://…:8081
    which is always correct.  Env override takes precedence for flexibility.
    """
    from_env = os.environ.get("MCPJUNGLE_REGISTRY_URL")
    if from_env:
        return from_env
    settings = load_gateway_settings()
    if settings["registry_url"]:
        return settings["registry_url"]
    return "http://127.0.0.1:8080"


def build_runtime() -> tuple[ManagedRegistry, MCPJungleClient, HealthChecker, Reconciler]:
    data_root = runtime_data_root()
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
        registry_url=_resolve_registry_url(),
        work_root=work_root,
        timeout=int(os.environ.get("MCPJUNGLE_CLI_TIMEOUT_SEC", "300")),
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


def _managed_server_names(
    registry: ManagedRegistry,
    *,
    registered_names: set[str] | None = None,
) -> set[str]:
    names = {
        entry["name"]
        for entry in registry.list_entries()
        if entry.get("managed", True)
    }
    if registered_names is None:
        return names
    return names & registered_names


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
    from .managed_types import pre_install as _pre_install

    registry, _, _, reconciler = build_runtime()
    moved_legacy = registry.cleanup_legacy_server_configs()

    # Pre-install packages for all entries before reconciling
    entries = [registry.require(args.name)] if args.name else registry.list_entries()
    for entry in entries:
        if not entry.get("managed", True):
            continue
        try:
            msg = _pre_install(entry)
            print(f"pre-install {entry['name']}: {msg}")
        except Exception as exc:
            print(f"pre-install {entry['name']}: FAILED ({exc})")

    if args.boot_mode:
        results = reconciler.reconcile_boot()
    elif args.force:
        results = reconciler.reconcile_force(name=args.name)
    else:
        results = reconciler.reconcile(name=args.name)
    if args.json:
        emit({"moved_legacy_configs": moved_legacy, "results": results}, as_json=True)
        return 0 if all(result["status"] != "error" for result in results) else 1

    for item in moved_legacy:
        print(f"moved_legacy_config: {item['source']} -> {item['target']}")
    emit(results, as_json=args.json)
    return 0 if all(result["status"] != "error" for result in results) else 1


def cmd_bind_file(args: argparse.Namespace) -> int:
    registry, _, _, reconciler = build_runtime()
    entry = registry.require(args.name)
    healthcheck_spec = None
    if args.health_mode != "leave":
        healthcheck_spec = {"mode": args.health_mode}
        if args.health_url:
            healthcheck_spec["url"] = args.health_url
        if args.health_tool:
            healthcheck_spec["tool_name"] = args.health_tool
        if args.health_input:
            healthcheck_spec["tool_input"] = json.loads(args.health_input)

    updated_entry, info = configure_managed_file(
        registry,
        entry,
        source=args.source,
        env_key=args.env_key,
        dest_name=args.dest_name,
        set_env=parse_env_items(args.set_env),
        clear_env=args.clear_env,
        healthcheck_spec=healthcheck_spec,
    )
    registry.upsert(updated_entry)
    reconcile_result = reconciler.reconcile_force(name=args.name)[0]
    payload = {
        "binding": info,
        "reconcile": reconcile_result,
    }
    emit(payload, as_json=args.json)
    return 0 if reconcile_result["status"] in {"healthy", "unchanged"} else 1


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
    data_root = runtime_data_root()
    auth_config = Path(
        os.environ.get("MCPJUNGLE_AUTH_CONFIG", str(runtime_conf_path()))
    )
    gateway_ok, gateway_message = health_checker.check_gateway()
    runtime_info = runtime_summary(include_node=True)
    auth_config_source = (
        "MCPJUNGLE_AUTH_CONFIG"
        if os.environ.get("MCPJUNGLE_AUTH_CONFIG")
        else "MCPJUNGLE_DATA_ROOT/.mcpjungle.conf"
    )

    report = {
        "auth_config_path": str(auth_config),
        "auth_config_source": auth_config_source,
        "registry_path": str(registry.registry_path),
        "bundles_root": str(registry.bundles_root),
        "work_root": str(registry.work_root),
        "secrets_root": str(registry.secrets_root),
        "data_root": str(data_root),
        "registry_url": client.registry_url,
        "runtime": runtime_info,
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


def cmd_auto_update(args: argparse.Namespace) -> int:
    registry, _, _, reconciler = build_runtime()
    
    # Run auto-update check and apply
    summary = auto_update(
        registry=registry,
        name=args.name,
        dry_run=args.dry_run,
    )
    
    # If there were updates and not a dry-run, trigger reconciliation
    if not args.dry_run and summary["total_updated"] > 0:
        try:
            reconcile_summary = reconciler.reconcile(name=args.name)
            summary["reconciliation"] = reconcile_summary
        except Exception as e:
            summary["errors"].append(f"Reconciliation failed: {str(e)}")
            emit(summary, as_json=args.json)
            return 1
    
    emit(summary, as_json=args.json)
    return 0 if not summary["errors"] else 1


def cmd_sync_groups(args: argparse.Namespace) -> int:
    from .tool_groups import ToolGroupsError, ToolGroupsManager

    registry, client, _, _ = build_runtime()

    try:
        manager = ToolGroupsManager(
            gateway_url=_resolve_registry_url(),
        )
    except ToolGroupsError as e:
        emit({"error": str(e)}, as_json=args.json)
        return 1

    try:
        current_configs = client.get_server_configs()
    except Exception as e:
        emit({"error": f"Failed to list servers: {e}"}, as_json=args.json)
        return 1

    managed_names = _managed_server_names(
        registry,
        registered_names=set(current_configs),
    )
    summary = manager.sync_tool_groups(
        sorted(managed_names),
        managed_names=managed_names,
    )
    emit(summary, as_json=args.json)
    return 0 if not summary["errors"] else 1


def cmd_prune_managed_groups(args: argparse.Namespace) -> int:
    from .tool_groups import ToolGroupsError, ToolGroupsManager

    registry, _, _, _ = build_runtime()

    try:
        manager = ToolGroupsManager(
            gateway_url=_resolve_registry_url(),
        )
    except ToolGroupsError as e:
        emit({"error": str(e)}, as_json=args.json)
        return 1

    managed_names = _managed_server_names(registry)
    summary = manager.prune_managed_groups(managed_names)
    emit(summary, as_json=args.json)
    return 0 if not summary["errors"] else 1


def cmd_generate_lazy_config(args: argparse.Namespace) -> int:
    from .lazy_mcp import generate_lazy_mcp_config

    gateway_url = _resolve_registry_url()

    config = generate_lazy_mcp_config(
        gateway_url=gateway_url,
        public_url=args.public_url,
    )
    emit(config, as_json=args.json)
    return 0


def cmd_creds_set(args: argparse.Namespace) -> int:
    registry, _, _, reconciler = build_runtime()
    entry = registry.require(args.server_name)

    if "=" not in args.key_value:
        emit({"error": "Credential must be in KEY=VALUE format"}, as_json=args.json)
        return 1
    key, value = args.key_value.split("=", 1)

    secret_material_file = entry.get("secret_material_file")
    secret_material = load_secret_material(secret_material_file)
    secret_material.setdefault("env", {})[key] = value

    # Write updated secret material
    secret_path = registry.secrets_root / f"{args.server_name}.json"
    secret_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        delete=False,
        dir=secret_path.parent,
        prefix=f"{secret_path.stem}-",
        suffix=secret_path.suffix,
    ) as handle:
        json.dump(secret_material, handle, indent=2, sort_keys=True)
        handle.write("\n")
        tmp_name = handle.name
    Path(tmp_name).replace(secret_path)
    os.chmod(secret_path, 0o600)

    entry["secret_material_file"] = str(secret_path)
    registry.upsert(entry)

    # Trigger reconcile for this server
    results = reconciler.reconcile(name=args.server_name)

    # Audit log
    audit_entry = {
        "action": "creds-set",
        "server": args.server_name,
        "key": key,
        "timestamp": utcnow_iso(),
    }
    payload = {
        "audit": audit_entry,
        "reconcile": results[0] if results else {},
    }
    emit(payload, as_json=args.json)
    return 0 if results and results[0]["status"] in {"healthy", "unchanged"} else 1


def _mask_value(value: str) -> str:
    if len(value) <= 4:
        return "*****"
    return value[:4] + "*****"


def cmd_creds_list(args: argparse.Namespace) -> int:
    registry, _, _, _ = build_runtime()
    entry = registry.require(args.server_name)

    secret_material = load_secret_material(entry.get("secret_material_file"))
    env = secret_material.get("env", {})

    credentials = []
    for key in sorted(env):
        credentials.append({
            "key": key,
            "value_masked": _mask_value(str(env[key])),
        })

    payload = {
        "server": args.server_name,
        "auth_type": entry.get("auth_type", "api_key"),
        "created_at": entry.get("created_at"),
        "last_rotated_at": entry.get("last_rotated_at"),
        "credentials": credentials,
    }
    emit(payload, as_json=args.json)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    commands = {
        "install": cmd_install,
        "import-existing": cmd_import_existing,
        "update": cmd_update,
        "remove": cmd_remove,
        "reconcile": cmd_reconcile,
        "bind-file": cmd_bind_file,
        "list-managed": cmd_list_managed,
        "doctor": cmd_doctor,
        "auto-update": cmd_auto_update,
        "sync-groups": cmd_sync_groups,
        "prune-managed-groups": cmd_prune_managed_groups,
        "generate-lazy-config": cmd_generate_lazy_config,
        "creds-set": cmd_creds_set,
        "creds-list": cmd_creds_list,
    }
    return commands[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
