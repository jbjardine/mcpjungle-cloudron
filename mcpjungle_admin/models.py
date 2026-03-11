from __future__ import annotations

import copy
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1
MANAGED_TYPES = {
    "npm_package",
    "uvx_package",
    "local_bundle",
    "http_remote",
    "custom_command",
}
SUPPORTED_TRANSPORTS = {"stdio", "streamable-http", "sse"}
SERVER_CONFIG_KEYS = {
    "name",
    "description",
    "transport",
    "command",
    "args",
    "env",
    "url",
    "bearer_token",
}


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_transport(transport: str | None) -> str:
    value = (transport or "stdio").replace("_", "-")
    if value not in SUPPORTED_TRANSPORTS:
        raise ValueError(f"Unsupported transport: {transport}")
    return value


def sanitize_server_config(server_config: dict[str, Any]) -> dict[str, Any]:
    config = {
        key: copy.deepcopy(value)
        for key, value in server_config.items()
        if key in SERVER_CONFIG_KEYS and value not in (None, "")
    }
    if "transport" in config:
        config["transport"] = normalize_transport(config["transport"])
    if "env" in config and not config["env"]:
        config.pop("env")
    if "args" in config and not config["args"]:
        config.pop("args")
    return config


def server_config_from_entry(entry: dict[str, Any]) -> dict[str, Any]:
    config = {
        "name": entry["name"],
        "description": entry.get("description", ""),
        "transport": normalize_transport(entry.get("transport")),
    }
    runtime_spec = copy.deepcopy(entry.get("runtime_spec", {}))
    config.update(runtime_spec)
    return sanitize_server_config(config)


def normalize_data(data: Any) -> Any:
    if isinstance(data, dict):
        return {key: normalize_data(data[key]) for key in sorted(data)}
    if isinstance(data, list):
        return [normalize_data(item) for item in data]
    return data


def runtime_hash_from_config(server_config: dict[str, Any]) -> str:
    payload = json.dumps(
        normalize_data(sanitize_server_config(server_config)),
        separators=(",", ":"),
        sort_keys=True,
    )
    return f"sha256:{hashlib.sha256(payload.encode('utf-8')).hexdigest()}"


def runtime_hash_from_entry(entry: dict[str, Any]) -> str:
    return runtime_hash_from_config(server_config_from_entry(entry))


def new_registry_document() -> dict[str, Any]:
    return {
        "schemaVersion": SCHEMA_VERSION,
        "updatedAt": utcnow_iso(),
        "servers": {},
    }


def ensure_managed_entry(entry: dict[str, Any]) -> dict[str, Any]:
    if not entry.get("name"):
        raise ValueError("Managed entry must include a name")
    managed_type = entry.get("managed_type")
    if managed_type not in MANAGED_TYPES:
        raise ValueError(f"Unsupported managed_type: {managed_type}")

    normalized = copy.deepcopy(entry)
    normalized["managed"] = True
    normalized["transport"] = normalize_transport(entry.get("transport"))
    normalized["runtime_spec"] = sanitize_server_config(
        entry.get("runtime_spec", {})
    )
    normalized["runtime_spec"].pop("name", None)
    normalized["runtime_spec"].pop("description", None)
    normalized["runtime_spec"].pop("transport", None)
    normalized.setdefault("install_spec", {})
    normalized.setdefault("healthcheck_spec", {})
    normalized.setdefault("status", "pending")
    normalized.setdefault("last_error", "")
    normalized.setdefault("created_at", utcnow_iso())
    normalized["updated_at"] = utcnow_iso()
    return normalized


def is_path_within(parent: Path, candidate: Path) -> bool:
    try:
        candidate.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False

