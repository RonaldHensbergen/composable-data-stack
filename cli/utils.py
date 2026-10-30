import os
import tempfile
from contextlib import suppress
from pathlib import Path
from typing import Any


def _atomic_write(path: Path, content: str, encoding: str = "utf-8", overwrite: bool = True) -> None:
    """Write `content` to `path` atomically via a temp file, then publish it.

    Avoids leaving a truncated/partial file behind if the process is
    interrupted mid-write.

    When `overwrite` is True (the default), publishing uses `os.replace()`,
    which always succeeds and silently replaces an existing `path`.

    When `overwrite` is False, publishing instead uses `os.link()`, which
    atomically fails with `FileExistsError` if `path` already exists. This
    closes the check-then-write race a caller-side `path.exists()` guard
    can't: two concurrent writers targeting the same `path` can't both
    "win" -- the loser gets `FileExistsError` instead of silently
    clobbering the winner's file.
    """
    path = Path(path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding=encoding) as tmp_file:
            tmp_file.write(content)
        if overwrite:
            os.replace(tmp_name, path)
        else:
            os.link(tmp_name, path)
    finally:
        with suppress(OSError):
            os.unlink(tmp_name)


def _compose_service_name(module_id: str, service_name: str) -> str:
    """Normalize compose service names so module prefixes are not duplicated."""
    if service_name == module_id or service_name.startswith(f"{module_id}-"):
        return service_name
    return f"{module_id}-{service_name}"


def module_name_from_source(source: Any) -> str:
    """The module name a `source` path points at (its last path segment)."""
    return str(source or "").rstrip("/").rsplit("/", 1)[-1]


def module_dns_host(module_id: str, compose_services: Any, module_name: str | None = None) -> str:
    """
    The Compose DNS name a module instance's serving service answers to.

    The renderer names each compose service `_compose_service_name(module_id,
    service)`. A module whose compose services include exactly one service --
    or a namesake service matching the module's own name (e.g. `postgres`
    alongside its `postgres-backup`/`postgres-restore` job containers) --
    therefore renders its serving service under that rule, and endpoints
    advertised via `${service.dns}` resolve on the Compose network even when
    the instance id differs from the service name (issue #1010). Other
    multi-service modules have no single answer and keep the instance id;
    they name their serving service explicitly via the contract's
    `serviceName` field, which route generation and the E150 host check map
    through the same naming rule.
    """
    if isinstance(compose_services, dict):
        if len(compose_services) == 1:
            return _compose_service_name(module_id, next(iter(compose_services)))
        if module_name and module_name in compose_services:
            return _compose_service_name(module_id, module_name)
    return module_id
