from __future__ import annotations

import copy
import tempfile
from pathlib import Path
from typing import Any

from .models import chmod_if_exists, is_path_within
from .registry import ManagedRegistry


def managed_file_path(
    registry: ManagedRegistry,
    name: str,
    dest_name: str,
) -> Path:
    filename = Path(dest_name).name or "managed-file"
    return registry.secrets_root / f"{name}-{filename}"


def write_managed_file(
    registry: ManagedRegistry,
    name: str,
    source: str | Path,
    *,
    dest_name: str | None = None,
) -> Path:
    source_path = Path(source)
    if not source_path.exists():
        raise FileNotFoundError(f"Managed file source {source_path} does not exist")

    registry.ensure_layout()
    destination = managed_file_path(registry, name, dest_name or source_path.name)

    with source_path.open("rb") as source_handle, tempfile.NamedTemporaryFile(
        "wb",
        delete=False,
        dir=destination.parent,
        prefix=f"{destination.stem}-",
        suffix=destination.suffix,
    ) as tmp_handle:
        tmp_handle.write(source_handle.read())
        tmp_name = tmp_handle.name

    Path(tmp_name).replace(destination)
    chmod_if_exists(destination, 0o600)
    return destination


def configure_managed_file(
    registry: ManagedRegistry,
    entry: dict[str, Any],
    *,
    source: str | Path,
    env_key: str,
    dest_name: str | None = None,
    set_env: dict[str, str] | None = None,
    clear_env: list[str] | None = None,
    healthcheck_spec: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    updated_entry = copy.deepcopy(entry)
    runtime_spec = copy.deepcopy(updated_entry.get("runtime_spec", {}))
    env = copy.deepcopy(runtime_spec.get("env", {}))
    managed_files = set(updated_entry.get("managed_files", []))

    previous_path_value = env.get(env_key, "")
    destination = write_managed_file(
        registry,
        updated_entry["name"],
        source,
        dest_name=dest_name,
    )
    env[env_key] = str(destination)

    if previous_path_value:
        previous_path = Path(previous_path_value)
        if (
            previous_path != destination
            and previous_path.exists()
            and is_path_within(registry.secrets_root, previous_path)
        ):
            previous_path.unlink(missing_ok=True)
            managed_files.discard(str(previous_path))

    for key in clear_env or []:
        env.pop(key, None)
    for key, value in (set_env or {}).items():
        env[key] = value

    runtime_spec["env"] = env
    updated_entry["runtime_spec"] = runtime_spec

    managed_files.add(str(destination))
    updated_entry["managed_files"] = sorted(managed_files)

    if healthcheck_spec is not None:
        updated_entry["healthcheck_spec"] = healthcheck_spec

    updated_entry["status"] = "pending"
    updated_entry["last_error"] = ""

    info = {
        "env_key": env_key,
        "managed_path": str(destination),
        "source_path": str(Path(source)),
        "managed_files": updated_entry["managed_files"],
        "healthcheck_mode": updated_entry.get("healthcheck_spec", {}).get("mode", ""),
    }
    return updated_entry, info
