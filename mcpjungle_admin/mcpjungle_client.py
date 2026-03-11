from __future__ import annotations

import json
import subprocess
import tempfile
import urllib.request
from pathlib import Path
from typing import Any

from .models import sanitize_server_config


class MCPJungleClientError(RuntimeError):
    pass


class MCPJungleClient:
    def __init__(
        self,
        cli_path: str = "/usr/local/bin/mcpjungle",
        registry_url: str = "http://127.0.0.1:8080",
        work_root: str | Path = "/app/data/.mcpjungle-managed/work",
        timeout: int = 60,
    ) -> None:
        self.cli_path = cli_path
        self.registry_url = registry_url.rstrip("/")
        self.work_root = Path(work_root)
        self.timeout = timeout

    def _run(
        self,
        args: list[str],
        *,
        cwd: str | Path | None = None,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        command = [self.cli_path, *args, "--registry", self.registry_url]
        result = subprocess.run(
            command,
            cwd=str(cwd) if cwd else None,
            check=False,
            capture_output=True,
            text=True,
            timeout=self.timeout,
        )
        if check and result.returncode != 0:
            raise MCPJungleClientError(
                (
                    f"Command {' '.join(command)} failed with exit code "
                    f"{result.returncode}: {result.stderr.strip() or result.stdout.strip()}"
                ).strip()
            )
        return result

    def register_server(self, server_config: dict[str, Any]) -> str:
        self.work_root.mkdir(parents=True, exist_ok=True)
        config = sanitize_server_config(server_config)
        config = self._config_for_native_register(config)
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            delete=False,
            dir=self.work_root,
            prefix=f"register-{config['name']}-",
            suffix=".json",
        ) as handle:
            json.dump(config, handle, indent=2, sort_keys=True)
            handle.write("\n")
            config_path = handle.name

        try:
            result = self._run(["register", "--conf", config_path])
        finally:
            Path(config_path).unlink(missing_ok=True)
        return result.stdout.strip() or f"Registered {config['name']}"

    def deregister_server(self, name: str, *, ignore_missing: bool = False) -> str:
        try:
            result = self._run(["deregister", name])
        except MCPJungleClientError as exc:
            message = str(exc).lower()
            if ignore_missing and ("not found" in message or "404" in message):
                return f"{name} was already absent"
            raise
        return result.stdout.strip() or f"Deregistered {name}"

    def list_servers_text(self) -> str:
        return self._run(["list", "servers"]).stdout

    def list_tools(self, server_name: str) -> str:
        return self._run(["list", "tools", "--server", server_name]).stdout

    def export_configurations(self, destination: str | Path) -> str:
        target = Path(destination)
        target.mkdir(parents=True, exist_ok=True)
        result = self._run(["export", "--dir", str(target)])
        return result.stdout

    def get_server_configs(self) -> dict[str, dict[str, Any]]:
        self.work_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix="export-", dir=self.work_root
        ) as export_dir:
            self.export_configurations(export_dir)
            configs: dict[str, dict[str, Any]] = {}
            for path in sorted(Path(export_dir).rglob("*.json")):
                with path.open("r", encoding="utf-8") as handle:
                    try:
                        payload = json.load(handle)
                    except json.JSONDecodeError:
                        continue
                if isinstance(payload, dict) and self._looks_like_server_config(payload):
                    config = sanitize_server_config(payload)
                    configs[config["name"]] = config
            return configs

    def gateway_health(self) -> tuple[bool, str]:
        try:
            with urllib.request.urlopen(
                f"{self.registry_url}/health",
                timeout=self.timeout,
            ) as response:
                status = getattr(response, "status", 200)
                if 200 <= status < 300:
                    return True, f"Gateway healthy ({status})"
                return False, f"Gateway returned status {status}"
        except Exception as exc:  # pragma: no cover - urllib shapes differ
            return False, str(exc)

    @staticmethod
    def _looks_like_server_config(payload: dict[str, Any]) -> bool:
        if "name" not in payload or "transport" not in payload:
            return False
        return any(key in payload for key in ("command", "url"))

    @staticmethod
    def _config_for_native_register(config: dict[str, Any]) -> dict[str, Any]:
        native_config = dict(config)
        if native_config.get("transport") == "streamable-http":
            native_config["transport"] = "streamable_http"
        return native_config
