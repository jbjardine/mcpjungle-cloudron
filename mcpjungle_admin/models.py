from __future__ import annotations

import copy
import hashlib
import json
import os
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
SENSITIVE_ENV_KEYWORDS = (
    "TOKEN",
    "SECRET",
    "PASSWORD",
    "PASS",
    "API_KEY",
    "AUTH",
    "SESSION",
    "COOKIE",
    "PRIVATE_KEY",
    "ACCESS_KEY",
    "CREDENTIAL",
)
SAFE_ENV_EXACT_KEYS = {
    "PATH",
    "HOME",
    "PWD",
    "PORT",
    "HOST",
    "NODE_OPTIONS",
    "OAUTH_ENABLED",
    "GOOGLE_PROJECT_ID",
    "GOOGLE_APPLICATION_CREDENTIALS",
    "WINDEV_PROXY_URL",
    "WP_API_URL",
    "N8N_API_URL",
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
    secret_material = load_secret_material(entry.get("secret_material_file"))
    env = {
        **secret_material.get("env", {}),
        **runtime_spec.get("env", {}),
    }
    if env:
        runtime_spec["env"] = env
    elif "env" in runtime_spec:
        runtime_spec.pop("env")

    bearer_token = runtime_spec.get("bearer_token") or secret_material.get("bearer_token")
    if bearer_token:
        runtime_spec["bearer_token"] = bearer_token

    config.update(runtime_spec)
    return sanitize_server_config(config)


def resolved_server_config(config: dict[str, Any], entry: dict[str, Any]) -> dict[str, Any]:
    merged = copy.deepcopy(config)
    secret_material = load_secret_material(entry.get("secret_material_file"))
    env = {
        **secret_material.get("env", {}),
        **merged.get("env", {}),
    }
    if env:
        merged["env"] = env
    bearer_token = merged.get("bearer_token") or secret_material.get("bearer_token")
    if bearer_token:
        merged["bearer_token"] = bearer_token
    return sanitize_server_config(merged)


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


def is_sensitive_env_key(key: str, value: str) -> bool:
    upper_key = key.upper()
    if upper_key in SAFE_ENV_EXACT_KEYS:
        return False
    if looks_like_filesystem_path(value):
        return False
    if looks_like_url(value):
        return False
    return any(keyword in upper_key for keyword in SENSITIVE_ENV_KEYWORDS)


def looks_like_url(value: Any) -> bool:
    return isinstance(value, str) and value.startswith(("http://", "https://"))


def looks_like_filesystem_path(value: Any) -> bool:
    return isinstance(value, str) and (
        value.startswith("/")
        or value.startswith("./")
        or value.startswith("../")
        or value.startswith("~")
        or value.startswith("\\\\")
        or (
            len(value) >= 3
            and value[0].isalpha()
            and value[1] == ":"
            and value[2] in {"/", "\\"}
        )
    )


def load_secret_material(secret_material_file: str | None) -> dict[str, Any]:
    if not secret_material_file:
        return {}
    path = Path(secret_material_file)
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    return payload if isinstance(payload, dict) else {}


def strip_sensitive_server_config(config: dict[str, Any]) -> dict[str, Any]:
    sanitized = copy.deepcopy(config)
    env = sanitized.get("env", {})
    public_env = {
        key: value
        for key, value in env.items()
        if not is_sensitive_env_key(key, value)
    }
    if public_env:
        sanitized["env"] = public_env
    elif "env" in sanitized:
        sanitized.pop("env")
    sanitized.pop("bearer_token", None)
    return sanitize_server_config(sanitized)


def chmod_if_exists(path: str | Path, mode: int) -> None:
    path_obj = Path(path)
    if path_obj.exists():
        os.chmod(path_obj, mode)


def permission_mode(path: str | Path) -> int | None:
    path_obj = Path(path)
    if not path_obj.exists():
        return None
    return path_obj.stat().st_mode & 0o777
