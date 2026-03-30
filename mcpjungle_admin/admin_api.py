"""
Admin API server for the MCPJungle-Cloudron dashboard.

Provides a JSON REST API over ``http.server.ThreadingHTTPServer`` on port
8082.  Every request must carry an ``X-Cloudron-User`` header (Cloudron's
reverse-proxy injects it for authenticated users).

Run as::

    python3 -m mcpjungle_admin.admin_api

Endpoints
---------
GET    /health                       Aggregated health status
GET    /servers                      List managed servers + health
GET    /servers/<name>               Single server details
POST   /servers                      Register a new managed server
DELETE /servers/<name>               Remove a managed server
POST   /servers/<name>/reinstall     Re-install a managed server
POST   /servers/<name>/enable        Enable a server
POST   /servers/<name>/disable       Disable a server
POST   /servers/<name>/reset-breaker Reset circuit breaker
GET    /servers/<name>/creds         List credential keys (values masked)
PUT    /servers/<name>/creds         Set a credential
GET    /api-keys                     List MCP client API keys
POST   /api-keys                     Create an API key
DELETE /api-keys/<name>              Revoke an API key
GET    /audit                        Read audit log entries
POST   /reconcile                    Trigger manual reconciliation
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from .health import HealthChecker
from .mcpjungle_client import MCPJungleClient, MCPJungleClientError
from .models import strip_sensitive_server_config
from .reconcile import Reconciler
from .registry import ManagedRegistry

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_PORT = 8082
AUDIT_LOG_PATH = Path(
    os.environ.get(
        "MCPJUNGLE_AUDIT_LOG",
        "/app/data/.mcpjungle-managed/audit.jsonl",
    )
)
MAX_REQUEST_BODY = 2 * 1024 * 1024  # 2 MiB safety limit

# Server name: alphanumeric, hyphens, underscores. No path separators, no ..
_VALID_SERVER_NAME = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$")

# ---------------------------------------------------------------------------
# Audit log helpers
# ---------------------------------------------------------------------------

_audit_lock = threading.Lock()


def _append_audit(
    user: str,
    action: str,
    target: str,
    detail: str = "",
) -> None:
    """Append a JSON-lines entry to the audit log."""
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "user": user,
        "action": action,
        "target": target,
        "detail": detail,
    }
    try:
        AUDIT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with _audit_lock:
            with AUDIT_LOG_PATH.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, separators=(",", ":")) + "\n")
    except OSError as exc:
        logger.warning("Failed to write audit log: %s", exc)


def _read_audit(limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
    """Read audit log entries (newest first) with limit/offset."""
    if not AUDIT_LOG_PATH.exists():
        return []
    lines: list[str] = []
    try:
        with AUDIT_LOG_PATH.open("r", encoding="utf-8") as fh:
            lines = fh.readlines()
    except OSError:
        return []
    # Reverse so newest comes first
    lines.reverse()
    selected = lines[offset : offset + limit]
    entries: list[dict[str, Any]] = []
    for line in selected:
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return entries


# ---------------------------------------------------------------------------
# Shared service objects (initialised once at startup)
# ---------------------------------------------------------------------------

_registry: ManagedRegistry | None = None
_client: MCPJungleClient | None = None
_health_checker: HealthChecker | None = None
_reconciler: Reconciler | None = None


def _init_services() -> (
    tuple[ManagedRegistry, MCPJungleClient, HealthChecker, Reconciler]
):
    global _registry, _client, _health_checker, _reconciler  # noqa: PLW0603
    if _registry is None:
        _registry = ManagedRegistry()
        _registry.ensure_layout()
    if _client is None:
        _client = MCPJungleClient()
    if _health_checker is None:
        _health_checker = HealthChecker(_client)
    if _reconciler is None:
        _reconciler = Reconciler(_registry, _client, _health_checker)
    return _registry, _client, _health_checker, _reconciler


# ---------------------------------------------------------------------------
# URL routing helpers
# ---------------------------------------------------------------------------

# Patterns:
#   /servers
#   /servers/<name>
#   /servers/<name>/creds
#   /health
#   /audit
#   /reconcile
_ROUTE_SERVERS = re.compile(r"^/servers/?$")
_ROUTE_SERVER_NAME = re.compile(r"^/servers/(?P<name>[A-Za-z0-9_.\-]+)/?$")
_ROUTE_SERVER_CREDS = re.compile(
    r"^/servers/(?P<name>[A-Za-z0-9_.\-]+)/creds/?$"
)
_ROUTE_SERVER_REINSTALL = re.compile(
    r"^/servers/(?P<name>[A-Za-z0-9_.\-]+)/reinstall/?$"
)
_ROUTE_SERVER_TOGGLE = re.compile(
    r"^/servers/(?P<name>[A-Za-z0-9_.\-]+)/(?P<action>enable|disable)/?$"
)
_ROUTE_SERVER_RESET_BREAKER = re.compile(
    r"^/servers/(?P<name>[A-Za-z0-9_.\-]+)/reset-breaker/?$"
)
_ROUTE_API_KEYS = re.compile(r"^/api-keys/?$")
_ROUTE_API_KEY_NAME = re.compile(r"^/api-keys/(?P<name>[A-Za-z0-9_.\-]+)/?$")
_ROUTE_HEALTH = re.compile(r"^/health/?$")
_ROUTE_AUDIT = re.compile(r"^/audit/?$")
_ROUTE_RECONCILE = re.compile(r"^/reconcile/?$")


# ---------------------------------------------------------------------------
# Request handler
# ---------------------------------------------------------------------------


class AdminAPIHandler(BaseHTTPRequestHandler):
    """Threaded HTTP request handler for the admin dashboard API."""

    # Silence default stderr logging of every request
    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        logger.debug(format, *args)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _send_json(
        self,
        status: int,
        body: Any,
        *,
        headers: dict[str, str] | None = None,
    ) -> None:
        payload = json.dumps(body, indent=2, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(payload)

    def _read_json_body(self) -> Any:
        length_str = self.headers.get("Content-Length", "0")
        try:
            length = int(length_str)
        except ValueError:
            length = 0
        if length > MAX_REQUEST_BODY:
            raise ValueError("Request body too large")
        if length == 0:
            return {}
        raw = self.rfile.read(length)
        return json.loads(raw.decode("utf-8"))

    def _get_user(self) -> str | None:
        # Check X-Cloudron-User header (set by Cloudron proxyAuth)
        user = self.headers.get("X-Cloudron-User")
        if user:
            return user
        # Fallback: check Bearer token (used by dashboard JS to bypass proxyAuth)
        auth = self.headers.get("Authorization", "")
        if auth.startswith("Bearer ") and _ADMIN_TOKEN and auth[7:] == _ADMIN_TOKEN:
            return "admin"
        return None

    def _require_user(self) -> str | None:
        """Return the authenticated user or send a 401 and return None."""
        user = self._get_user()
        if not user:
            self._send_json(401, {"error": "Authentication required"})
            return None
        return user

    def _parsed_path(self) -> str:
        return urlparse(self.path).path

    def _query_params(self) -> dict[str, list[str]]:
        return parse_qs(urlparse(self.path).query)

    def _int_param(self, params: dict[str, list[str]], key: str, default: int) -> int:
        values = params.get(key)
        if not values:
            return default
        try:
            return int(values[0])
        except ValueError:
            return default

    # ------------------------------------------------------------------
    # Dispatch
    # ------------------------------------------------------------------

    def _serve_dashboard(self) -> None:
        """Serve the admin dashboard HTML with the session token injected."""
        html_path = Path("/app/code/admin/static/index.html")
        try:
            html = html_path.read_text(encoding="utf-8")
        except OSError:
            self._send_json(404, {"error": "Dashboard HTML not found"})
            return
        html = html.replace("__ADMIN_TOKEN__", _ADMIN_TOKEN)
        data = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:  # noqa: N802
        path = self._parsed_path()
        # Serve the dashboard HTML with injected token (no auth - proxyAuth protects this)
        if path.rstrip("/") in ("", "/", "/index.html"):
            self._serve_dashboard()
            return
        user = self._require_user()
        if user is None:
            return
        try:
            if _ROUTE_HEALTH.match(path):
                self._handle_health()
            elif _ROUTE_AUDIT.match(path):
                self._handle_audit_get()
            elif _ROUTE_SERVERS.match(path):
                self._handle_servers_list()
            elif (m := _ROUTE_SERVER_CREDS.match(path)):
                self._handle_creds_get(m.group("name"))
            elif _ROUTE_API_KEYS.match(path):
                self._handle_api_keys_list()
            elif _ROUTE_SERVER_NAME.match(path):
                # GET /servers/<name> returns the single server entry
                m = _ROUTE_SERVER_NAME.match(path)
                assert m is not None
                self._handle_server_get(m.group("name"))
            else:
                self._send_json(404, {"error": "Not found"})
        except Exception:
            logger.exception("Unhandled error in GET %s", path)
            self._send_json(500, {"error": "Internal server error"})

    def do_POST(self) -> None:  # noqa: N802
        user = self._require_user()
        if user is None:
            return
        path = self._parsed_path()
        try:
            if _ROUTE_SERVERS.match(path):
                self._handle_servers_create(user)
            elif (m := _ROUTE_SERVER_TOGGLE.match(path)):
                self._handle_server_toggle(user, m.group("name"), m.group("action"))
            elif (m := _ROUTE_SERVER_RESET_BREAKER.match(path)):
                self._handle_reset_breaker(user, m.group("name"))
            elif (m := _ROUTE_SERVER_REINSTALL.match(path)):
                self._handle_server_reinstall(user, m.group("name"))
            elif _ROUTE_RECONCILE.match(path):
                self._handle_reconcile(user)
            elif _ROUTE_API_KEYS.match(path):
                self._handle_api_key_create(user)
            else:
                self._send_json(404, {"error": "Not found"})
        except Exception:
            logger.exception("Unhandled error in POST %s", path)
            self._send_json(500, {"error": "Internal server error"})

    def do_PUT(self) -> None:  # noqa: N802
        user = self._require_user()
        if user is None:
            return
        path = self._parsed_path()
        try:
            if (m := _ROUTE_SERVER_CREDS.match(path)):
                self._handle_creds_put(user, m.group("name"))
            elif (m := _ROUTE_SERVER_NAME.match(path)):
                self._handle_server_update(user, m.group("name"))
            else:
                self._send_json(404, {"error": "Not found"})
        except Exception:
            logger.exception("Unhandled error in PUT %s", path)
            self._send_json(500, {"error": "Internal server error"})

    def do_DELETE(self) -> None:  # noqa: N802
        user = self._require_user()
        if user is None:
            return
        path = self._parsed_path()
        try:
            if (m := _ROUTE_API_KEY_NAME.match(path)):
                self._handle_api_key_delete(user, m.group("name"))
            elif (m := _ROUTE_SERVER_NAME.match(path)):
                self._handle_server_delete(user, m.group("name"))
            else:
                self._send_json(404, {"error": "Not found"})
        except Exception:
            logger.exception("Unhandled error in DELETE %s", path)
            self._send_json(500, {"error": "Internal server error"})

    def do_OPTIONS(self) -> None:  # noqa: N802
        """Handle CORS preflight.

        Only allow Content-Type and Authorization headers.
        X-Cloudron-User is intentionally excluded - it's set by nginx
        from proxyAuth, never by the browser.
        """
        self.send_response(204)
        origin = self.headers.get("Origin", "")
        self.send_header("Access-Control-Allow-Origin", origin if origin else "null")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("Content-Length", "0")
        self.end_headers()

    # ------------------------------------------------------------------
    # Endpoint handlers
    # ------------------------------------------------------------------

    def _handle_health(self) -> None:
        registry, client, health_checker, _ = _init_services()
        gateway_ok, gateway_msg = health_checker.check_gateway()

        entries = registry.list_entries()
        server_statuses: list[dict[str, Any]] = []
        all_ok = gateway_ok
        for entry in entries:
            status = entry.get("status", "unknown")
            # "unchanged" means the reconcile found no changes and healthcheck passed
            is_ok = status in ("healthy", "unchanged")
            if not is_ok:
                all_ok = False
            server_statuses.append({
                "name": entry["name"],
                "status": "healthy" if status == "unchanged" else status,
                "last_error": entry.get("last_error", ""),
            })

        # Read version from CloudronManifest.json
        version = ""
        try:
            manifest_path = Path(__file__).resolve().parent.parent / "CloudronManifest.json"
            if manifest_path.exists():
                version = json.loads(manifest_path.read_text()).get("version", "")
        except Exception:
            pass

        self._send_json(200, {
            "status": "healthy" if all_ok else "degraded",
            "version": version,
            "gateway": {"ok": gateway_ok, "message": gateway_msg},
            "servers": server_statuses,
            "server_count": len(entries),
        })

    def _handle_audit_get(self) -> None:
        params = self._query_params()
        limit = self._int_param(params, "limit", 100)
        offset = self._int_param(params, "offset", 0)
        # Clamp for safety
        limit = max(1, min(limit, 1000))
        offset = max(0, offset)
        entries = _read_audit(limit=limit, offset=offset)
        self._send_json(200, {"entries": entries, "limit": limit, "offset": offset})

    def _handle_servers_list(self) -> None:
        registry, client, _, _ = _init_services()
        entries = registry.list_entries()

        # Fetch tool counts from the Go gateway API (fast HTTP, not slow CLI)
        tool_counts: dict[str, int] = {}
        try:
            tool_counts = _fetch_tool_counts()
        except Exception:
            pass  # tool counts are best-effort, don't break the list

        servers: list[dict[str, Any]] = []
        for entry in entries:
            safe = _safe_entry(entry)
            safe["tool_count"] = tool_counts.get(entry.get("name", ""), 0)
            servers.append(safe)
        self._send_json(200, {"servers": servers})

    def _handle_server_get(self, name: str) -> None:
        registry, _, _, _ = _init_services()
        entry = registry.get(name)
        if entry is None:
            self._send_json(404, {"error": f"Server {name!r} not found"})
            return
        self._send_json(200, {"server": _safe_entry(entry)})

    def _handle_servers_create(self, user: str) -> None:
        registry, client, health_checker, reconciler = _init_services()
        try:
            body = self._read_json_body()
        except (ValueError, json.JSONDecodeError) as exc:
            self._send_json(400, {"error": f"Invalid request body: {exc}"})
            return

        if not isinstance(body, dict):
            self._send_json(400, {"error": "Request body must be a JSON object"})
            return

        name = body.get("name")
        if not name:
            self._send_json(400, {"error": "Field 'name' is required"})
            return
        if not _VALID_SERVER_NAME.match(name):
            self._send_json(400, {"error": f"Invalid server name {name!r}: must match [a-zA-Z0-9][a-zA-Z0-9_-]*"})
            return

        managed_type = body.get("type") or body.get("managed_type")
        if not managed_type:
            self._send_json(400, {"error": "Field 'type' (managed_type) is required"})
            return

        # Build the managed entry from the request body
        entry: dict[str, Any] = {
            "name": name,
            "managed_type": managed_type,
            "description": body.get("description", ""),
            "transport": body.get("transport", "stdio"),
            "runtime_spec": {},
            "install_spec": {},
            "healthcheck_spec": body.get("healthcheck_spec", {}),
        }

        # Populate runtime_spec from the request body
        runtime_spec: dict[str, Any] = {}
        if body.get("command"):
            runtime_spec["command"] = body["command"]
        if body.get("args"):
            runtime_spec["args"] = body["args"]
        if body.get("env"):
            runtime_spec["env"] = body["env"]
        if body.get("url"):
            runtime_spec["url"] = body["url"]
        if body.get("bearer_token"):
            runtime_spec["bearer_token"] = body["bearer_token"]
        entry["runtime_spec"] = runtime_spec

        # Populate install_spec
        install_spec: dict[str, Any] = {}
        if body.get("package"):
            install_spec["package"] = body["package"]
        if body.get("version"):
            install_spec["version"] = body["version"]
        if body.get("url"):
            install_spec["url"] = body["url"]
        install_spec["updateStrategy"] = body.get("updateStrategy", "pinned")
        entry["install_spec"] = install_spec

        # Optional HTTP bridge port (for MCP servers with dual transport)
        bridge_port = body.get("bridge_port")
        if bridge_port is not None:
            try:
                bridge_port = int(bridge_port)
                if not (1024 <= bridge_port <= 65535):
                    raise ValueError
                if bridge_port in (8080, 8081, 8082):
                    self._send_json(400, {"error": f"Port {bridge_port} is reserved (8080=nginx, 8081=gateway, 8082=admin)"})
                    return
                entry["bridge_port"] = bridge_port
            except (TypeError, ValueError):
                self._send_json(400, {"error": "bridge_port must be an integer between 1024-65535"})
                return

        try:
            saved_entry = registry.upsert(entry)
        except (ValueError, KeyError) as exc:
            self._send_json(400, {"error": str(exc)})
            return

        _append_audit(user, "create_server", name, json.dumps(
            {"type": managed_type, "transport": entry["transport"]},
            separators=(",", ":"),
        ))

        # Submit async install+register to worker pool (returns immediately)
        reconciler.reconcile_async(name=name)

        # Regenerate nginx bridge config if this server has a bridge_port
        if entry.get("bridge_port"):
            _regenerate_nginx_bridges()

        # Return 202 Accepted - install is running in background
        saved_entry["status"] = "installing"
        self._send_json(202, {
            "server": _safe_entry(saved_entry),
            "reconcile": {"status": "installing", "message": "install submitted to worker pool"},
        })

    def _handle_server_update(self, user: str, name: str) -> None:
        """Update an existing managed server (partial update)."""
        registry, client, _, reconciler = _init_services()
        entry = registry.get(name)
        if not entry:
            self._send_json(404, {"error": f"Server {name!r} not found"})
            return

        try:
            body = self._read_json_body()
        except (ValueError, json.JSONDecodeError) as exc:
            self._send_json(400, {"error": f"Invalid request body: {exc}"})
            return

        if not isinstance(body, dict):
            self._send_json(400, {"error": "Request body must be a JSON object"})
            return

        # Update description
        if "description" in body:
            entry["description"] = body["description"]

        # Update env vars (merge into runtime_spec.env)
        if "env" in body and isinstance(body["env"], dict):
            runtime_spec = entry.get("runtime_spec") or {}
            existing_env = runtime_spec.get("env") or {}
            existing_env.update(body["env"])
            runtime_spec["env"] = existing_env
            entry["runtime_spec"] = runtime_spec

        # Update URL (for http_remote)
        if "url" in body and body["url"]:
            runtime_spec = entry.get("runtime_spec") or {}
            runtime_spec["url"] = body["url"]
            entry["runtime_spec"] = runtime_spec
            install_spec = entry.get("install_spec") or {}
            install_spec["url"] = body["url"]
            entry["install_spec"] = install_spec

        # Update bridge_port
        if "bridge_port" in body:
            bp = body["bridge_port"]
            if bp is None or bp == "" or bp == 0:
                entry.pop("bridge_port", None)
            else:
                try:
                    bp = int(bp)
                    if not (1024 <= bp <= 65535):
                        raise ValueError
                    if bp in (8080, 8081, 8082):
                        self._send_json(400, {"error": f"Port {bp} is reserved"})
                        return
                    entry["bridge_port"] = bp
                except (TypeError, ValueError):
                    self._send_json(400, {"error": "bridge_port must be 1024-65535"})
                    return

        # Update healthcheck_spec
        if "healthcheck_spec" in body and isinstance(body["healthcheck_spec"], dict):
            entry["healthcheck_spec"] = body["healthcheck_spec"]

        try:
            saved_entry = registry.upsert(entry)
        except (ValueError, KeyError) as exc:
            self._send_json(400, {"error": str(exc)})
            return

        _append_audit(user, "update_server", name)

        # Reconcile the updated server
        reconciler.reconcile_async(name=name)

        # Regenerate nginx bridges
        _regenerate_nginx_bridges()

        self._send_json(200, {"server": _safe_entry(saved_entry)})

    def _handle_server_delete(self, user: str, name: str) -> None:
        registry, client, _, _ = _init_services()
        entry = registry.get(name)
        if entry is None:
            self._send_json(404, {"error": f"Server {name!r} not found"})
            return

        # Deregister from the MCPJungle gateway first
        try:
            client.deregister_server(name, ignore_missing=True)
        except MCPJungleClientError as exc:
            logger.warning("Deregister failed for %s: %s", name, exc)

        had_bridge = bool(entry.get("bridge_port"))

        # Remove from managed registry (also deletes secret material)
        registry.remove(name)

        _append_audit(user, "delete_server", name)

        # Regenerate nginx bridge config if this server had a bridge_port
        if had_bridge:
            _regenerate_nginx_bridges()

        self._send_json(200, {"deleted": name})

    def _handle_server_reinstall(self, user: str, name: str) -> None:
        registry, _, _, reconciler = _init_services()
        entry = registry.get(name)
        if entry is None:
            self._send_json(404, {"error": f"Server {name!r} not found"})
            return
        _append_audit(user, "reinstall_server", name)
        reconciler.reconcile_async(name=name)
        self._send_json(202, {
            "server": name,
            "status": "installing",
            "message": "reinstall submitted to worker pool",
        })

    def _handle_creds_get(self, name: str) -> None:
        registry, _, _, _ = _init_services()
        entry = registry.get(name)
        if entry is None:
            self._send_json(404, {"error": f"Server {name!r} not found"})
            return

        creds: list[dict[str, str]] = []

        # Read secret env keys from the entry metadata
        secret_env_keys = entry.get("secret_env_keys", [])
        for key in secret_env_keys:
            creds.append({"key": key, "type": "env", "value": "********"})

        # Check for bearer token
        if entry.get("has_secret_bearer_token"):
            creds.append({"key": "bearer_token", "type": "bearer_token", "value": "********"})

        # Also include non-sensitive env vars from runtime_spec (unmasked)
        runtime_env = entry.get("runtime_spec", {}).get("env", {})
        for key, value in sorted(runtime_env.items()):
            creds.append({"key": key, "type": "env", "value": value})

        self._send_json(200, {"server": name, "credentials": creds})

    def _handle_creds_put(self, user: str, name: str) -> None:
        registry, _, _, reconciler = _init_services()
        entry = registry.get(name)
        if entry is None:
            self._send_json(404, {"error": f"Server {name!r} not found"})
            return

        try:
            body = self._read_json_body()
        except (ValueError, json.JSONDecodeError) as exc:
            self._send_json(400, {"error": f"Invalid request body: {exc}"})
            return

        if not isinstance(body, dict):
            self._send_json(400, {"error": "Request body must be a JSON object"})
            return

        key = body.get("key")
        value = body.get("value")
        if not key or value is None:
            self._send_json(400, {"error": "Fields 'key' and 'value' are required"})
            return

        # Apply the credential to the entry
        if key == "bearer_token":
            entry.setdefault("runtime_spec", {})["bearer_token"] = value
        else:
            entry.setdefault("runtime_spec", {}).setdefault("env", {})[key] = value

        try:
            saved_entry = registry.upsert(entry)
        except (ValueError, KeyError) as exc:
            self._send_json(400, {"error": str(exc)})
            return

        _append_audit(user, "set_credential", name, json.dumps(
            {"key": key}, separators=(",", ":"),
        ))

        # Trigger reconciliation so the new credential takes effect
        try:
            results = reconciler.reconcile(name=name)
            reconcile_status = results[0] if results else {}
        except Exception as exc:
            logger.warning("Reconcile after cred update failed for %s: %s", name, exc)
            reconcile_status = {"status": "error", "message": str(exc)}

        self._send_json(200, {
            "server": name,
            "key": key,
            "updated": True,
            "reconcile": _safe_reconcile_result(reconcile_status),
        })

    # ------------------------------------------------------------------
    # Server enable/disable
    # ------------------------------------------------------------------
    def _handle_server_toggle(self, user: str, name: str, action: str) -> None:
        """Enable or disable a server via the Go gateway API."""
        _, client, _, _ = _init_services()
        try:
            if action == "enable":
                client._run(["enable", name])
            else:
                client._run(["disable", name])
            _append_audit(user, f"{action}_server", name)
            self._send_json(200, {"name": name, "action": action, "ok": True})
        except Exception as exc:
            self._send_json(500, {"error": f"Failed to {action} {name}: {exc}"})

    def _handle_reset_breaker(self, user: str, name: str) -> None:
        """Reset circuit breaker for a server (allows boot reconcile to retry)."""
        registry, _, _, _ = _init_services()
        entry = registry.get(name)
        if entry is None:
            self._send_json(404, {"error": f"Server {name!r} not found"})
            return
        entry["consecutive_failures"] = 0
        entry["last_failure_at"] = ""
        registry.upsert(entry)
        _append_audit(user, "reset_breaker", name)
        self._send_json(200, {"name": name, "consecutive_failures": 0})

    # ------------------------------------------------------------------
    # API Keys (MCP clients) management
    # ------------------------------------------------------------------
    _RE_CLIENT_LINE = re.compile(r"^\d+\.\s+(.+)$")

    def _handle_api_keys_list(self) -> None:
        """List MCP clients (API keys) via Go CLI.

        The Go CLI ``list mcp-clients`` outputs numbered entries like::

            1. my-client-name
            Allowed servers: *

            2. other-client
            This client does not have access to any MCP servers.

        We extract only the name from lines matching ``^\\d+\\.\\s+(.+)$``.
        """
        _, client, _, _ = _init_services()
        try:
            output = client._run(["list", "mcp-clients"]).stdout
            clients = []
            for line in output.strip().splitlines():
                m = self._RE_CLIENT_LINE.match(line.strip())
                if m:
                    clients.append({"name": m.group(1).strip()})
            self._send_json(200, {"api_keys": clients})
        except Exception as exc:
            self._send_json(500, {"error": f"Failed to list API keys: {exc}"})

    def _handle_api_key_create(self, user: str) -> None:
        """Create a new MCP client (API key)."""
        try:
            body = self._read_json_body()
        except (ValueError, json.JSONDecodeError) as exc:
            self._send_json(400, {"error": f"Invalid request body: {exc}"})
            return

        name = body.get("name", "").strip()
        if not name:
            self._send_json(400, {"error": "Field 'name' is required"})
            return

        _, client, _, _ = _init_services()
        try:
            result = client._run(["create", "mcp-client", name, "--allow", "*"])
            # The Go CLI writes everything to stderr, not stdout
            output = result.stderr or result.stdout
            # Extract the access token: "Access token: <token>"
            token = ""
            for line in output.splitlines():
                if line.strip().startswith("Access token:"):
                    token = line.split(":", 1)[1].strip()
                    break
            _append_audit(user, "create_api_key", name)
            self._send_json(201, {"name": name, "token": token})
        except Exception as exc:
            self._send_json(500, {"error": f"Failed to create API key: {exc}"})

    def _handle_api_key_delete(self, user: str, name: str) -> None:
        """Delete an MCP client (API key)."""
        _, client, _, _ = _init_services()
        try:
            client._run(["delete", "mcp-client", name])
            _append_audit(user, "delete_api_key", name)
            self._send_json(200, {"name": name, "deleted": True})
        except Exception as exc:
            self._send_json(500, {"error": f"Failed to delete API key: {exc}"})

    def _handle_reconcile(self, user: str) -> None:
        _, _, _, reconciler = _init_services()

        _append_audit(user, "manual_reconcile", "*")

        try:
            results = reconciler.reconcile()
        except Exception as exc:
            logger.exception("Reconcile failed")
            self._send_json(500, {"error": f"Reconciliation failed: {exc}"})
            return

        safe_results = [_safe_reconcile_result(r) for r in results]
        self._send_json(200, {
            "reconciled": len(safe_results),
            "results": safe_results,
        })


# ---------------------------------------------------------------------------
# Response sanitization helpers
# ---------------------------------------------------------------------------


def _read_gateway_conf() -> tuple[str, str]:
    """Read gateway URL and access token from /app/data/.mcpjungle.conf."""
    conf_path = Path("/app/data/.mcpjungle.conf")
    gw_url = "http://127.0.0.1:8081"
    access_token = ""
    if conf_path.exists():
        for line in conf_path.read_text().splitlines():
            stripped = line.strip()
            key_val = stripped.split(None, 1)  # "key: value" → split on whitespace
            if len(key_val) == 2:
                key = key_val[0].rstrip(":")
                val = key_val[1]
                if key == "registry_url":
                    gw_url = val
                elif key == "access_token":
                    access_token = val
    return gw_url, access_token


def _fetch_tool_counts() -> dict[str, int]:
    """Fetch tool counts from the Go gateway via HTTP API (fast, <100ms)."""
    import urllib.request as _ur

    gw_url, access_token = _read_gateway_conf()
    req = _ur.Request(f"{gw_url}/api/v0/tools")
    if access_token:
        req.add_header("Authorization", f"Bearer {access_token}")
    with _ur.urlopen(req, timeout=5) as resp:
        tools = json.loads(resp.read())

    counts: dict[str, int] = {}
    for t in tools:
        name = t.get("name", "")
        if "__" in name:
            prefix = name.split("__")[0]
            counts[prefix] = counts.get(prefix, 0) + 1
    return counts


_SENSITIVE_PATTERNS = re.compile(
    r"(api[_-]?key|token|password|secret|bearer|authorization)[=: ]+\S+",
    re.IGNORECASE,
)
_MAX_ERROR_LEN = 500


def _sanitize_error(raw: str) -> str:
    """Truncate and redact sensitive patterns from error messages."""
    if not raw:
        return ""
    sanitized = _SENSITIVE_PATTERNS.sub(r"\1=********", raw)
    if len(sanitized) > _MAX_ERROR_LEN:
        sanitized = sanitized[:_MAX_ERROR_LEN] + "... (truncated)"
    return sanitized


def _safe_entry(entry: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of a registry entry safe for API responses (no secrets)."""
    safe: dict[str, Any] = {
        "name": entry.get("name", ""),
        "managed_type": entry.get("managed_type", ""),
        "description": entry.get("description", ""),
        "transport": entry.get("transport", ""),
        "status": "healthy" if entry.get("status") == "unchanged" else entry.get("status", "unknown"),
        "last_error": _sanitize_error(entry.get("last_error", "")),
        "managed": entry.get("managed", True),
        "created_at": entry.get("created_at", ""),
        "updated_at": entry.get("updated_at", ""),
        "install_spec": entry.get("install_spec", {}),
        "healthcheck_spec": entry.get("healthcheck_spec", {}),
        "secret_env_keys": entry.get("secret_env_keys", []),
        "has_secret_bearer_token": entry.get("has_secret_bearer_token", False),
    }
    if entry.get("bridge_port"):
        safe["bridge_port"] = entry["bridge_port"]
    if entry.get("consecutive_failures"):
        safe["consecutive_failures"] = entry["consecutive_failures"]
        safe["last_failure_at"] = entry.get("last_failure_at", "")
    # Include runtime_spec but strip any secret material from it
    runtime_spec = entry.get("runtime_spec", {})
    if runtime_spec:
        safe["runtime_spec"] = strip_sensitive_server_config(runtime_spec)
    else:
        safe["runtime_spec"] = {}
    # Include last_known_good if present (already stripped by registry)
    if entry.get("last_known_good"):
        safe["last_known_good"] = entry["last_known_good"]
    return safe


def _safe_reconcile_result(result: dict[str, Any]) -> dict[str, Any]:
    """Return a reconciliation result safe for API responses."""
    if not result:
        return {}
    return {
        "name": result.get("name", ""),
        "status": result.get("status", "unknown"),
        "message": result.get("message", ""),
        "changed": result.get("changed", False),
    }


# ---------------------------------------------------------------------------
# Nginx bridge config generation
# ---------------------------------------------------------------------------

_NGINX_BRIDGES_PATH = Path("/app/data/.mcpjungle-managed/nginx-bridges.conf")

_BRIDGE_LOCATION_TEMPLATE = """\
# Auto-generated bridge for {name} (port {port})
location /bridge/{name}/ {{
    proxy_pass http://127.0.0.1:{port}/;
    proxy_http_version 1.1;
    proxy_set_header Connection "";
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_buffering off;
    chunked_transfer_encoding on;
    proxy_read_timeout 300s;
}}
"""


def _regenerate_nginx_bridges() -> None:
    """Regenerate nginx bridge locations from registry and reload nginx."""
    registry, _, _, _ = _init_services()
    entries = registry.list_entries()

    blocks: list[str] = []
    for entry in entries:
        port = entry.get("bridge_port")
        name = entry.get("name", "")
        if port and name:
            blocks.append(_BRIDGE_LOCATION_TEMPLATE.format(name=name, port=port))

    content = "# Generated by mcpjungle-admin - do not edit\n"
    if blocks:
        content += "\n".join(blocks)
    else:
        content += "# No servers with bridge_port configured\n"

    try:
        _NGINX_BRIDGES_PATH.write_text(content, encoding="utf-8")
        import subprocess
        subprocess.run(
            ["nginx", "-s", "reload", "-c", "/app/code/nginx.conf"],
            check=False, capture_output=True, timeout=10,
        )
        logger.info("Regenerated nginx bridges (%d entries)", len(blocks))
    except Exception as exc:
        logger.warning("Failed to regenerate nginx bridges: %s", exc)


# ---------------------------------------------------------------------------
# Server startup
# ---------------------------------------------------------------------------


_ADMIN_TOKEN: str = ""


def _load_admin_token() -> str:
    """Load admin token from env var (set by start.sh) or file."""
    global _ADMIN_TOKEN  # noqa: PLW0603
    _ADMIN_TOKEN = os.environ.get("MCPJUNGLE_ADMIN_TOKEN", "")
    if not _ADMIN_TOKEN:
        token_path = Path("/app/data/.mcpjungle-managed/admin-token")
        try:
            _ADMIN_TOKEN = token_path.read_text(encoding="utf-8").strip()
        except OSError:
            pass
    if _ADMIN_TOKEN:
        logger.info("Admin token loaded (%d chars)", len(_ADMIN_TOKEN))
    else:
        logger.warning("No admin token found - Bearer auth disabled")
    return _ADMIN_TOKEN


def run_server(
    host: str = "127.0.0.1",
    port: int = DEFAULT_PORT,
) -> None:
    """Start the admin API server (blocking)."""
    _init_services()
    _load_admin_token()
    server = ThreadingHTTPServer((host, port), AdminAPIHandler)
    logger.info("MCPJungle Admin API listening on %s:%d", host, port)
    print(f"MCPJungle Admin API listening on {host}:{port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        logger.info("Admin API server shut down.")


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    run_server()
