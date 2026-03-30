"""File locking utilities for transactional registry access."""

from __future__ import annotations

import json
import os
import shutil
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Generator

from .models import SCHEMA_VERSION


# ---------------------------------------------------------------------------
# Platform-specific lock helpers
# ---------------------------------------------------------------------------

_USE_FCNTL = False
_USE_MSVCRT = False

if sys.platform != "win32":
    try:
        import fcntl

        _USE_FCNTL = True
    except ImportError:
        pass
else:
    try:
        import msvcrt

        _USE_MSVCRT = True
    except ImportError:
        pass


_DEFAULT_TIMEOUT = 10  # seconds
_POLL_INTERVAL = 0.1  # seconds between non-blocking retry attempts


def _lock_path(registry_path: Path) -> Path:
    """Return the lock-file path adjacent to the given registry file."""
    return registry_path.with_suffix(registry_path.suffix + ".lock")


# ---------------------------------------------------------------------------
# Context manager
# ---------------------------------------------------------------------------


@contextmanager
def registry_lock(
    path: str | Path,
    timeout: float = _DEFAULT_TIMEOUT,
) -> Generator[Path, None, None]:
    """Acquire an exclusive file lock around *path* (the registry file).

    The lock file is ``<path>.lock``.  On POSIX systems ``fcntl.flock()`` is
    used; on Windows ``msvcrt.locking()`` is used as a fallback.  If neither
    module is available (or the platform is otherwise unsupported) the context
    manager degrades to a no-op so development on any OS is unblocked.

    The lock is always released when the context manager exits, even if the
    body raises an exception.
    """
    registry_path = Path(path)
    lock_file = _lock_path(registry_path)
    lock_file.parent.mkdir(parents=True, exist_ok=True)

    handle = open(lock_file, "w")  # noqa: SIM115 - intentionally kept open
    try:
        _acquire(handle, timeout)
        yield registry_path
    finally:
        _release(handle)
        handle.close()


# ---------------------------------------------------------------------------
# Acquire / release internals
# ---------------------------------------------------------------------------


def _acquire(handle: Any, timeout: float) -> None:
    if _USE_FCNTL:
        _acquire_fcntl(handle, timeout)
    elif _USE_MSVCRT:
        _acquire_msvcrt(handle, timeout)
    # else: no-op lock (unsupported platform)


def _release(handle: Any) -> None:
    if _USE_FCNTL:
        _release_fcntl(handle)
    elif _USE_MSVCRT:
        _release_msvcrt(handle)


# -- fcntl (Linux / macOS) -------------------------------------------------


def _acquire_fcntl(handle: Any, timeout: float) -> None:
    # Try non-blocking first.
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return
    except (IOError, OSError):
        pass

    # Fall back to blocking with a timeout implemented via polling.
    deadline = time.monotonic() + timeout
    while True:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except (IOError, OSError):
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"Could not acquire registry lock within {timeout}s"
                )
            time.sleep(_POLL_INTERVAL)


def _release_fcntl(handle: Any) -> None:
    try:
        fcntl.flock(handle, fcntl.LOCK_UN)
    except (IOError, OSError):
        pass


# -- msvcrt (Windows) ------------------------------------------------------


def _acquire_msvcrt(handle: Any, timeout: float) -> None:
    # msvcrt.locking requires a file descriptor.
    fd = handle.fileno()

    # Non-blocking attempt.
    try:
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        return
    except (IOError, OSError):
        pass

    # Blocking with timeout.
    deadline = time.monotonic() + timeout
    while True:
        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            return
        except (IOError, OSError):
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"Could not acquire registry lock within {timeout}s"
                )
            time.sleep(_POLL_INTERVAL)


def _release_msvcrt(handle: Any) -> None:
    fd = handle.fileno()
    try:
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    except (IOError, OSError):
        pass


# ---------------------------------------------------------------------------
# Backup helper
# ---------------------------------------------------------------------------


def create_backup(path: str | Path) -> Path | None:
    """Copy *path* to ``<path>.bak`` before a mutation.

    Returns the backup path on success, or ``None`` if the source does not
    exist.
    """
    source = Path(path)
    if not source.exists():
        return None
    backup = source.with_suffix(source.suffix + ".bak")
    shutil.copy2(source, backup)
    return backup


# ---------------------------------------------------------------------------
# Schema validation
# ---------------------------------------------------------------------------


class RegistryValidationError(ValueError):
    """Raised when a registry document fails schema validation."""


def validate_registry(document: Any) -> list[str]:
    """Validate a registry *document* dict.

    Returns a list of human-readable error strings.  An empty list means the
    document is valid.

    Raises ``RegistryValidationError`` if there are validation failures.
    """
    errors: list[str] = []

    if not isinstance(document, dict):
        errors.append("Registry document must be a JSON object")
        raise RegistryValidationError("; ".join(errors))

    # -- required top-level keys -------------------------------------------
    if "schemaVersion" not in document:
        errors.append("Missing required field: schemaVersion")
    elif not isinstance(document["schemaVersion"], int):
        errors.append("schemaVersion must be an integer")
    elif document["schemaVersion"] != SCHEMA_VERSION:
        errors.append(
            f"Unsupported schemaVersion {document['schemaVersion']} "
            f"(expected {SCHEMA_VERSION})"
        )

    if "updatedAt" not in document:
        errors.append("Missing required field: updatedAt")
    elif not isinstance(document["updatedAt"], str):
        errors.append("updatedAt must be a string")

    # -- servers dict ------------------------------------------------------
    if "servers" not in document:
        errors.append("Missing required field: servers")
    elif not isinstance(document["servers"], dict):
        errors.append("servers must be a JSON object (dict)")
    else:
        for name, entry in document["servers"].items():
            if not isinstance(entry, dict):
                errors.append(f"servers[{name!r}]: entry must be a JSON object")
                continue
            if not entry.get("name"):
                errors.append(f"servers[{name!r}]: missing required field 'name'")
            if not entry.get("managed_type"):
                errors.append(
                    f"servers[{name!r}]: missing required field 'managed_type'"
                )
            if not entry.get("transport"):
                errors.append(
                    f"servers[{name!r}]: missing required field 'transport'"
                )

    if errors:
        raise RegistryValidationError("; ".join(errors))

    return errors
