from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Mapping


DEFAULT_DATA_ROOT = Path("/app/data")
_PREFERRED_PATH_ENTRIES = (
    "/usr/bin",
    "/usr/local/bin",
    "/usr/local/sbin",
    "/usr/sbin",
    "/sbin",
    "/bin",
    "/root/.local/bin",
)
_RUNTIME_SUMMARY_KEYS = (
    "APP_HOME",
    "MCPJUNGLE_DATA_ROOT",
    "HOME",
    "PATH",
    "LANG",
    "LC_ALL",
    "TMPDIR",
    "XDG_CONFIG_HOME",
    "XDG_CACHE_HOME",
    "XDG_DATA_HOME",
)


def runtime_data_root(env: Mapping[str, str] | None = None) -> Path:
    source = env or os.environ
    for key in ("MCPJUNGLE_DATA_ROOT", "APP_HOME"):
        value = source.get(key)
        if value:
            return Path(value)
    return DEFAULT_DATA_ROOT


def runtime_conf_path(env: Mapping[str, str] | None = None) -> Path:
    return runtime_data_root(env) / ".mcpjungle.conf"


def build_runtime_path(current_path: str | None = None) -> str:
    merged: list[str] = []
    seen: set[str] = set()
    for entry in [*_PREFERRED_PATH_ENTRIES, *((current_path or "").split(os.pathsep))]:
        normalized = entry.strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        merged.append(normalized)
    return os.pathsep.join(merged)


def canonical_runtime_env(
    env: Mapping[str, str] | None = None,
    *,
    extra: Mapping[str, str] | None = None,
) -> dict[str, str]:
    source = dict(env or os.environ)
    data_root = runtime_data_root(source)
    merged = dict(source)
    merged["APP_HOME"] = str(data_root)
    merged["MCPJUNGLE_DATA_ROOT"] = str(data_root)
    merged["HOME"] = str(data_root)
    merged["PATH"] = build_runtime_path(source.get("PATH"))
    merged["LANG"] = "C.UTF-8"
    merged["LC_ALL"] = "C.UTF-8"
    merged["TMPDIR"] = source.get("TMPDIR") or "/tmp"
    merged["XDG_CONFIG_HOME"] = str(data_root / ".config")
    merged["XDG_CACHE_HOME"] = str(data_root / ".cache")
    merged["XDG_DATA_HOME"] = str(data_root / ".local" / "share")
    if extra:
        for key, value in extra.items():
            if value is not None:
                merged[key] = value
    return merged


def load_gateway_settings(
    env: Mapping[str, str] | None = None,
    *,
    conf_path: str | Path | None = None,
) -> dict[str, str]:
    path = Path(conf_path) if conf_path else runtime_conf_path(env)
    settings = {
        "registry_url": "",
        "access_token": "",
    }
    if not path.exists():
        return settings

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        for sep in (":", "="):
            if sep not in line:
                continue
            key, _, value = line.partition(sep)
            normalized_key = key.strip()
            normalized_value = value.strip().strip("\"'")
            if normalized_key == "registry_url":
                settings["registry_url"] = normalized_value
            elif normalized_key in {"access_token", "accessToken"}:
                settings["access_token"] = normalized_value
            break
    return settings


def resolve_executable(
    command: str,
    env: Mapping[str, str] | None = None,
) -> str | None:
    if os.path.sep in command:
        path = Path(command)
        return str(path) if path.exists() else None
    runtime_env = canonical_runtime_env(env)
    return shutil.which(command, path=runtime_env.get("PATH"))


def executable_version(
    command: str,
    env: Mapping[str, str] | None = None,
    *,
    timeout: int = 5,
) -> str:
    runtime_env = canonical_runtime_env(env)
    try:
        result = subprocess.run(
            [command, "--version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=runtime_env,
        )
    except Exception:
        return ""

    return (result.stdout or result.stderr).strip().splitlines()[0] if (result.stdout or result.stderr) else ""


def runtime_summary(
    env: Mapping[str, str] | None = None,
    *,
    include_node: bool = False,
) -> dict[str, str]:
    runtime_env = canonical_runtime_env(env)
    summary = {key: runtime_env[key] for key in _RUNTIME_SUMMARY_KEYS}
    if include_node:
        node_path = resolve_executable("node", runtime_env)
        if node_path:
            summary["node_path"] = node_path
            node_version = executable_version("node", runtime_env)
            if node_version:
                summary["node_version"] = node_version
    return summary
