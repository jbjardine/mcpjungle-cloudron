from __future__ import annotations

import copy
import logging
import os
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor, Future, as_completed
from typing import Any

from .health import HealthChecker
from .managed_types import diagnose_stdio_startup, pre_install
from .mcpjungle_client import MCPJungleClient, MCPJungleClientError
from .models import (
    resolved_server_config,
    runtime_hash_from_config,
    server_config_from_entry,
    strip_sensitive_server_config,
    utcnow_iso,
)
from .registry import ManagedRegistry

logger = logging.getLogger(__name__)

_MAX_INSTALL_WORKERS = 4
_BOOT_TIMEOUT = int(os.environ.get("MCPJUNGLE_BOOT_TIMEOUT_SEC", "60"))
_CIRCUIT_BREAKER_THRESHOLD = 3


class Reconciler:
    def __init__(
        self,
        registry: ManagedRegistry,
        client: MCPJungleClient,
        health_checker: HealthChecker,
    ) -> None:
        self.registry = registry
        self.client = client
        self.health_checker = health_checker
        self._pool = ThreadPoolExecutor(max_workers=_MAX_INSTALL_WORKERS)
        self._locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()

    def _server_lock(self, name: str) -> threading.Lock:
        with self._locks_guard:
            if name not in self._locks:
                self._locks[name] = threading.Lock()
            return self._locks[name]

    def _healthy_result(
        self,
        entry: dict[str, Any],
        desired_config: dict[str, Any],
        desired_hash: str,
        message: str,
        *,
        changed: bool,
    ) -> dict[str, Any]:
        entry["last_applied_hash"] = desired_hash
        entry["last_known_good"] = strip_sensitive_server_config(desired_config)
        entry["status"] = "healthy"
        entry["last_error"] = ""
        entry["consecutive_failures"] = 0
        return {
            "name": entry["name"],
            "status": "healthy",
            "message": message,
            "entry": entry,
            "changed": changed,
        }

    def _recover_register_success(
        self,
        entry: dict[str, Any],
        desired_config: dict[str, Any],
        desired_hash: str,
        exc: Exception,
    ) -> tuple[bool, str]:
        """Treat timed/ambiguous register attempts as success if state is already good.

        Some upstream `mcpjungle register` invocations can overrun the local CLI timeout
        even though the server is already present in the gateway and responds to health
        checks. When that happens, prefer the effective runtime state over the local CLI
        exit path so managed status does not stay stuck in a false error state.
        """
        try:
            current_config = self.client.get_server_configs().get(entry["name"])
            if current_config is None:
                return False, ""

            current_hash = runtime_hash_from_config(current_config)
            if current_hash != desired_hash:
                return False, ""

            ok, message = self.health_checker.check_entry(entry)
            if not ok:
                return False, ""

            logger.warning(
                "register for %s reported %s but server is present and healthy; "
                "treating reconcile as successful",
                entry["name"],
                exc,
            )
            return True, message
        except Exception as verify_exc:
            logger.warning(
                "post-register verification failed for %s after %s: %s",
                entry["name"],
                exc,
                verify_exc,
            )
            return False, ""

    # ------------------------------------------------------------------
    # Async install: submit to worker pool, return immediately
    # ------------------------------------------------------------------
    def reconcile_async(self, name: str) -> Future:
        """Submit a single server install+register to the background pool."""
        return self._pool.submit(self._install_and_reconcile, name)

    def _install_and_reconcile(self, name: str) -> dict[str, Any]:
        """Worker: pre-install → register → healthcheck (per-server lock)."""
        lock = self._server_lock(name)
        if not lock.acquire(blocking=False):
            logger.info("install already in progress for %s, skipping", name)
            return {"name": name, "status": "skipped", "message": "already installing"}
        try:
            entry = self.registry.require(name)

            # Mark as installing
            entry["status"] = "installing"
            entry["updated_at"] = utcnow_iso()
            self.registry.upsert(entry)

            # Step 1: pre-install package
            try:
                msg = pre_install(entry)
                logger.info("pre-install OK for %s: %s", name, msg)
            except Exception as exc:
                entry["status"] = "install_failed"
                entry["last_error"] = str(exc)
                entry["updated_at"] = utcnow_iso()
                self.registry.upsert(entry)
                logger.warning("pre-install FAILED for %s: %s", name, exc)
                return {"name": name, "status": "install_failed", "message": str(exc)}

            # Step 2: register + healthcheck (existing reconcile logic)
            results = self.reconcile(name=name)
            result = results[0] if results else {"status": "error", "message": "no result"}
            return result
        finally:
            lock.release()

    # ------------------------------------------------------------------
    # Boot reconcile: fast, parallel, no rollback, circuit breaker
    # ------------------------------------------------------------------
    def reconcile_boot(self) -> list[dict[str, Any]]:
        """Boot-mode reconcile: parallel, short timeout, no rollback, circuit breaker."""
        current_configs = self.client.get_server_configs()
        entries = self.registry.list_entries()

        # Filter out non-managed and circuit-broken servers
        to_reconcile = []
        skipped = []
        for entry in entries:
            if not entry.get("managed", True):
                continue
            failures = entry.get("consecutive_failures", 0)
            if failures >= _CIRCUIT_BREAKER_THRESHOLD:
                logger.warning(
                    "CIRCUIT BREAKER: skip %s (%d consecutive failures)",
                    entry["name"], failures,
                )
                skipped.append({
                    "name": entry["name"],
                    "status": "skipped",
                    "message": f"circuit breaker: {failures} consecutive failures",
                    "entry": entry,
                    "changed": False,
                })
                continue
            to_reconcile.append(entry)

        # Parallel boot reconcile
        results = list(skipped)
        futures: dict[Future, dict[str, Any]] = {}
        for entry in to_reconcile:
            current_config = current_configs.get(entry["name"])
            future = self._pool.submit(
                self._reconcile_entry_boot, entry, current_config
            )
            futures[future] = entry

        for future in as_completed(futures):
            entry = futures[future]
            try:
                result = future.result()
                self.registry.upsert(result["entry"])
                results.append(result)
            except Exception as exc:
                logger.error("boot reconcile failed for %s: %s", entry["name"], exc)
                entry["status"] = "error"
                entry["last_error"] = str(exc)
                entry["consecutive_failures"] = entry.get("consecutive_failures", 0) + 1
                entry["last_failure_at"] = utcnow_iso()
                entry["updated_at"] = utcnow_iso()
                self.registry.upsert(entry)
                results.append({
                    "name": entry["name"],
                    "status": "error",
                    "message": str(exc),
                    "entry": entry,
                    "changed": True,
                })

        return results

    def _reconcile_entry_boot(
        self,
        entry: dict[str, Any],
        current_config: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Boot-mode reconcile for a single entry: short timeout, no rollback."""
        updated_entry = copy.deepcopy(entry)
        desired_config = server_config_from_entry(updated_entry)
        desired_hash = runtime_hash_from_config(desired_config)
        current_hash = (
            runtime_hash_from_config(current_config) if current_config is not None else None
        )

        # If already registered with same config, just health-check
        if current_hash == desired_hash:
            ok, message = self.health_checker.check_entry(updated_entry)
            updated_entry["status"] = "unchanged" if ok else "error"
            updated_entry["last_error"] = "" if ok else message
            if ok:
                updated_entry["consecutive_failures"] = 0
            return {
                "name": updated_entry["name"],
                "status": updated_entry["status"],
                "message": message,
                "entry": updated_entry,
                "changed": False,
            }

        # Register with short boot timeout - NO rollback on failure
        try:
            if current_config is not None:
                self.client.deregister_server(updated_entry["name"], ignore_missing=True)
            self.client.register_server(desired_config, timeout=_BOOT_TIMEOUT)

            ok, message = self.health_checker.check_entry(updated_entry)
            if not ok:
                raise RuntimeError(message)

            return self._healthy_result(
                updated_entry,
                desired_config,
                desired_hash,
                message,
                changed=True,
            )
        except (subprocess.TimeoutExpired, MCPJungleClientError, RuntimeError) as exc:
            recovered, message = self._recover_register_success(
                updated_entry,
                desired_config,
                desired_hash,
                exc,
            )
            if recovered:
                return self._healthy_result(
                    updated_entry,
                    desired_config,
                    desired_hash,
                    message,
                    changed=True,
                )

            error_msg = str(exc)
            updated_entry["status"] = "error"
            updated_entry["last_error"] = error_msg
            updated_entry["consecutive_failures"] = entry.get("consecutive_failures", 0) + 1
            updated_entry["last_failure_at"] = utcnow_iso()
            logger.warning(
                "BOOT reconcile FAILED for %s (attempt %d): %s",
                updated_entry["name"],
                updated_entry["consecutive_failures"],
                error_msg[:200],
            )
            return {
                "name": updated_entry["name"],
                "status": "error",
                "message": error_msg,
                "entry": updated_entry,
                "changed": True,
            }

    def reconcile(self, name: str | None = None) -> list[dict[str, Any]]:
        return self._reconcile(name=name, force=False)

    def _reconcile(self, name: str | None = None, *, force: bool) -> list[dict[str, Any]]:
        current_configs = self.client.get_server_configs()
        if name:
            entry = self.registry.require(name)
            entries = [entry]
        else:
            entries = self.registry.list_entries()

        results = []
        for entry in entries:
            if not entry.get("managed", True):
                continue
            current_config = current_configs.get(entry["name"])
            result = self._reconcile_entry(entry, current_config, force=force)
            self.registry.upsert(result["entry"])
            if result["status"] in {"healthy", "unchanged"}:
                current_configs[entry["name"]] = server_config_from_entry(result["entry"])
            results.append(result)
        return results

    def reconcile_force(self, name: str | None = None) -> list[dict[str, Any]]:
        return self._reconcile(name=name, force=True)

    def _reconcile_entry(
        self,
        entry: dict[str, Any],
        current_config: dict[str, Any] | None,
        *,
        force: bool,
    ) -> dict[str, Any]:
        updated_entry = copy.deepcopy(entry)
        desired_config = server_config_from_entry(updated_entry)
        desired_hash = runtime_hash_from_config(desired_config)
        current_hash = (
            runtime_hash_from_config(current_config) if current_config is not None else None
        )

        if current_hash == desired_hash and not force:
            ok, message = self.health_checker.check_entry(updated_entry)
            updated_entry["last_applied_hash"] = desired_hash
            updated_entry["last_known_good"] = strip_sensitive_server_config(desired_config)
            updated_entry["status"] = "unchanged" if ok else "error"
            if not ok:
                # Run startup diagnostic for stdio servers that fail health checks
                transport = updated_entry.get("transport", "stdio")
                if transport == "stdio":
                    try:
                        diag = diagnose_stdio_startup(updated_entry)
                        logger.warning(
                            "STDIO DIAGNOSTIC for %s: %s",
                            updated_entry["name"],
                            diag,
                        )
                        message = f"{message} [DIAG: {diag}]"
                    except Exception as diag_exc:
                        logger.warning(
                            "diagnostic failed for %s: %s",
                            updated_entry["name"],
                            diag_exc,
                        )
            updated_entry["last_error"] = "" if ok else message
            return {
                "name": updated_entry["name"],
                "status": updated_entry["status"],
                "message": message,
                "entry": updated_entry,
                "changed": False,
            }

        rollback_config = current_config or updated_entry.get("last_known_good") or None
        try:
            if current_config is not None:
                self.client.deregister_server(updated_entry["name"], ignore_missing=True)
            self.client.register_server(desired_config)

            ok, message = self.health_checker.check_entry(updated_entry)
            if not ok:
                raise RuntimeError(message)

            return self._healthy_result(
                updated_entry,
                desired_config,
                desired_hash,
                message,
                changed=True,
            )
        except Exception as exc:
            recovered, message = self._recover_register_success(
                updated_entry,
                desired_config,
                desired_hash,
                exc,
            )
            if recovered:
                return self._healthy_result(
                    updated_entry,
                    desired_config,
                    desired_hash,
                    message,
                    changed=True,
                )

            rollback_message = self._rollback(updated_entry, rollback_config, desired_config)
            error_msg = str(exc)

            # Run startup diagnostic for stdio servers that fail silently
            transport = updated_entry.get("transport", "stdio")
            if transport == "stdio":
                try:
                    diag = diagnose_stdio_startup(updated_entry)
                    logger.warning(
                        "STDIO DIAGNOSTIC for %s: %s",
                        updated_entry["name"],
                        diag,
                    )
                    error_msg = f"{exc} [DIAG: {diag}]"
                except Exception as diag_exc:
                    logger.warning(
                        "diagnostic failed for %s: %s",
                        updated_entry["name"],
                        diag_exc,
                    )

            updated_entry["status"] = "error"
            updated_entry["last_error"] = (
                f"{error_msg}; {rollback_message}" if rollback_message else error_msg
            )
            return {
                "name": updated_entry["name"],
                "status": "error",
                "message": updated_entry["last_error"],
                "entry": updated_entry,
                "changed": True,
            }

    def _rollback(
        self,
        entry: dict[str, Any],
        rollback_config: dict[str, Any] | None,
        desired_config: dict[str, Any],
    ) -> str:
        try:
            if rollback_config:
                self.client.deregister_server(entry["name"], ignore_missing=True)
                self.client.register_server(resolved_server_config(rollback_config, entry))
                return "rollback applied"

            if entry["managed_type"] != "http_remote":
                self.client.deregister_server(entry["name"], ignore_missing=True)
                return "rolled back to absence"

            return "remote config kept in error state"
        except MCPJungleClientError as exc:
            return f"rollback failed: {exc}"
