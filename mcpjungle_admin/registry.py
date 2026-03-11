from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from .models import (
    chmod_if_exists,
    ensure_managed_entry,
    is_sensitive_env_key,
    load_secret_material,
    new_registry_document,
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

    def ensure_layout(self) -> None:
        self.managed_root.mkdir(parents=True, exist_ok=True)
        self.work_root.mkdir(parents=True, exist_ok=True)
        self.bundles_root.mkdir(parents=True, exist_ok=True)
        self.secrets_root.mkdir(parents=True, exist_ok=True)
        os.chmod(self.managed_root, 0o700)
        os.chmod(self.work_root, 0o700)
        os.chmod(self.bundles_root, 0o700)
        os.chmod(self.secrets_root, 0o700)

    def load(self) -> dict[str, Any]:
        self.ensure_layout()
        if not self.registry_path.exists():
            return new_registry_document()

        with self.registry_path.open("r", encoding="utf-8") as handle:
            document = json.load(handle)

        document.setdefault("schemaVersion", 1)
        document.setdefault("updatedAt", utcnow_iso())
        document.setdefault("servers", {})
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
            tmp_name = handle.name

        Path(tmp_name).replace(self.registry_path)
        chmod_if_exists(self.registry_path, 0o600)

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
        self._delete_secret_material(removed or {})
        self.save(document)
        return removed

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
            self._delete_secret_material(normalized)
            normalized.pop("secret_material_file", None)
            normalized.pop("secret_env_keys", None)
            normalized.pop("has_secret_bearer_token", None)

        if merged_env != public_env or bool(bearer_token):
            changed = True

        return normalized, changed

    def _secret_material_path(self, name: str) -> Path:
        return self.secrets_root / f"{name}.json"

    def _delete_secret_material(self, entry: dict[str, Any]) -> None:
        secret_material_file = entry.get("secret_material_file")
        if secret_material_file:
            Path(secret_material_file).unlink(missing_ok=True)

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
