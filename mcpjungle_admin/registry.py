from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from .locking import registry_lock, create_backup
from .models import (
    chmod_if_exists,
    ensure_managed_entry,
    is_path_within,
    is_sensitive_env_key,
    load_secret_material,
    new_registry_document,
    strip_sensitive_server_config,
    utcnow_iso,
)


class ManagedRegistry:
    def __init__(
        self,
        registry_path: str | Path | None = None,
        bundles_root: str | Path | None = None,
        work_root: str | Path | None = None,
    ) -> None:
        data_root = Path("/app/data")
        self.registry_path = Path(
            registry_path or data_root / ".mcpjungle-managed" / "registry.json"
        )
        self.managed_root = self.registry_path.parent
        self.bundles_root = Path(bundles_root or data_root / "mcp-bundles")
        self.work_root = Path(work_root or self.managed_root / "work")
        self.secrets_root = self.managed_root / "secrets"
        self.legacy_configs_root = self.managed_root / "legacy-configs"
        self.data_root = data_root

    def ensure_layout(self) -> None:
        self.managed_root.mkdir(parents=True, exist_ok=True)
        self.work_root.mkdir(parents=True, exist_ok=True)
        self.bundles_root.mkdir(parents=True, exist_ok=True)
        self.secrets_root.mkdir(parents=True, exist_ok=True)
        self.legacy_configs_root.mkdir(parents=True, exist_ok=True)
        os.chmod(self.managed_root, 0o700)
        os.chmod(self.work_root, 0o700)
        os.chmod(self.bundles_root, 0o700)
        os.chmod(self.secrets_root, 0o700)
        os.chmod(self.legacy_configs_root, 0o700)

    def load(self) -> dict[str, Any]:
        self.ensure_layout()
        if not self.registry_path.exists():
            return new_registry_document()

        with registry_lock(self.registry_path):
            with self.registry_path.open("r", encoding="utf-8") as handle:
                document = json.load(handle)

        document.setdefault("schemaVersion", 1)
        document.setdefault("updatedAt", utcnow_iso())
        document.setdefault("servers", {})

        if document["schemaVersion"] == 1:
            document = self.migrate_v1_to_v2(document)

        changed = self._protect_document(document)
        chmod_if_exists(self.registry_path, 0o600)
        if changed:
            self.save(document)
        return document

    def save(self, document: dict[str, Any]) -> None:
        self.ensure_layout()
        self._protect_document(document)
        document["updatedAt"] = utcnow_iso()
        document["servers"] = {
            name: document["servers"][name] for name in sorted(document["servers"])
        }

        with registry_lock(self.registry_path):
            create_backup(self.registry_path)

            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                delete=False,
                dir=self.managed_root,
                prefix="registry-",
                suffix=".json",
            ) as handle:
                json.dump(document, handle, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
                tmp_name = handle.name

            Path(tmp_name).replace(self.registry_path)
            chmod_if_exists(self.registry_path, 0o600)

    def migrate_v1_to_v2(self, document: dict[str, Any]) -> dict[str, Any]:
        document["schemaVersion"] = 2
        for name, entry in document.get("servers", {}).items():
            entry.setdefault("auth_type", "api_key")
            entry.setdefault("created_at", utcnow_iso())
            entry.setdefault("last_rotated_at", None)
            entry.setdefault("rotation_status", None)
            entry.setdefault("expires_at", None)
        self.save(document)
        return document

    def list_entries(self) -> list[dict[str, Any]]:
        document = self.load()
        return [document["servers"][name] for name in sorted(document["servers"])]

    def get(self, name: str) -> dict[str, Any] | None:
        document = self.load()
        return document["servers"].get(name)

    def require(self, name: str) -> dict[str, Any]:
        entry = self.get(name)
        if entry is None:
            raise KeyError(f"Managed MCP {name!r} not found")
        return entry

    def upsert(self, entry: dict[str, Any]) -> dict[str, Any]:
        document = self.load()
        normalized = ensure_managed_entry(entry)
        document["servers"][normalized["name"]] = normalized
        self.save(document)
        return normalized

    def remove(self, name: str) -> dict[str, Any] | None:
        document = self.load()
        removed = document["servers"].pop(name, None)
        self._delete_secret_material(removed or {}, delete_managed_files=True)
        self.save(document)
        return removed

    def cleanup_legacy_server_configs(
        self,
        managed_names: set[str] | None = None,
    ) -> list[dict[str, str]]:
        self.ensure_layout()
        if managed_names is None:
            managed_names = {entry["name"] for entry in self.list_entries()}

        moved: list[dict[str, str]] = []
        for path in sorted(self.data_root.glob("*.json")):
            if path.name == self.registry_path.name:
                continue
            try:
                with path.open("r", encoding="utf-8") as handle:
                    payload = json.load(handle)
            except (OSError, json.JSONDecodeError):
                continue

            if not isinstance(payload, dict):
                continue

            name = payload.get("name")
            transport = payload.get("transport")
            if not name or not transport or name not in managed_names:
                continue

            target_path = self.legacy_configs_root / path.name
            if target_path.exists():
                target_path = self.legacy_configs_root / f"{name}-{path.name}"
            path.replace(target_path)
            chmod_if_exists(target_path, 0o600)
            moved.append({"source": str(path), "target": str(target_path), "name": name})

        return moved

    def list_legacy_server_configs(self) -> list[dict[str, str]]:
        legacy_files: list[dict[str, str]] = []
        for path in sorted(self.data_root.glob("*.json")):
            try:
                with path.open("r", encoding="utf-8") as handle:
                    payload = json.load(handle)
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(payload, dict) and payload.get("name") and payload.get("transport"):
                legacy_files.append({"path": str(path), "name": payload["name"]})
        return legacy_files

    def _protect_document(self, document: dict[str, Any]) -> bool:
        changed = False
        for name in list(document["servers"]):
            protected_entry, entry_changed = self._protect_entry(document["servers"][name])
            document["servers"][name] = protected_entry
            changed = changed or entry_changed
        return changed

    def _protect_entry(self, entry: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        normalized = ensure_managed_entry(entry)
        runtime_spec = normalized.setdefault("runtime_spec", {})
        merged_secret_material = load_secret_material(normalized.get("secret_material_file"))
        merged_env = {
            **merged_secret_material.get("env", {}),
            **runtime_spec.get("env", {}),
        }
        secret_env: dict[str, Any] = {}
        public_env: dict[str, Any] = {}
        for key, value in merged_env.items():
            if is_sensitive_env_key(key, value):
                secret_env[key] = value
            else:
                public_env[key] = value

        bearer_token = runtime_spec.get("bearer_token") or merged_secret_material.get("bearer_token")
        secret_material: dict[str, Any] = {}
        if secret_env:
            secret_material["env"] = secret_env
        if bearer_token:
            secret_material["bearer_token"] = bearer_token

        if public_env:
            runtime_spec["env"] = public_env
        else:
            runtime_spec.pop("env", None)
        runtime_spec.pop("bearer_token", None)
        if normalized.get("last_known_good"):
            normalized["last_known_good"] = strip_sensitive_server_config(
                normalized["last_known_good"]
            )

        changed = False
        if secret_material:
            secret_path = self._secret_material_path(normalized["name"])
            self._write_json(secret_path, secret_material, mode=0o600)
            if normalized.get("secret_material_file") != str(secret_path):
                changed = True
            normalized["secret_material_file"] = str(secret_path)
            normalized["secret_env_keys"] = sorted(secret_env)
            normalized["has_secret_bearer_token"] = bool(bearer_token)
        else:
            if normalized.get("secret_material_file"):
                changed = True
            self._delete_secret_material(normalized, delete_managed_files=False)
            normalized.pop("secret_material_file", None)
            normalized.pop("secret_env_keys", None)
            normalized.pop("has_secret_bearer_token", None)

        if merged_env != public_env or bool(bearer_token):
            changed = True

        return normalized, changed

    def _secret_material_path(self, name: str) -> Path:
        return self.secrets_root / f"{name}.json"

    def _delete_secret_material(
        self,
        entry: dict[str, Any],
        *,
        delete_managed_files: bool,
    ) -> None:
        secret_material_file = entry.get("secret_material_file")
        if secret_material_file:
            Path(secret_material_file).unlink(missing_ok=True)
        if delete_managed_files:
            for path_value in entry.get("managed_files", []):
                path = Path(path_value)
                if path.exists() and is_path_within(self.secrets_root, path):
                    path.unlink(missing_ok=True)

    @staticmethod
    def _write_json(path: Path, payload: dict[str, Any], mode: int) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            delete=False,
            dir=path.parent,
            prefix=f"{path.stem}-",
            suffix=path.suffix,
        ) as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            tmp_name = handle.name
        Path(tmp_name).replace(path)
        chmod_if_exists(path, mode)
