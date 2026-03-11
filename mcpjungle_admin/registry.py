from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

from .models import ensure_managed_entry, new_registry_document, utcnow_iso


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

    def ensure_layout(self) -> None:
        self.managed_root.mkdir(parents=True, exist_ok=True)
        self.work_root.mkdir(parents=True, exist_ok=True)
        self.bundles_root.mkdir(parents=True, exist_ok=True)

    def load(self) -> dict[str, Any]:
        self.ensure_layout()
        if not self.registry_path.exists():
            return new_registry_document()

        with self.registry_path.open("r", encoding="utf-8") as handle:
            document = json.load(handle)

        document.setdefault("schemaVersion", 1)
        document.setdefault("updatedAt", utcnow_iso())
        document.setdefault("servers", {})
        return document

    def save(self, document: dict[str, Any]) -> None:
        self.ensure_layout()
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
        self.save(document)
        return removed

