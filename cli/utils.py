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


def module_dns_host(module_id: str, compose_services: Any) -> str:
    """
    The Compose DNS name a module instance's services answer to.

    A module with a single compose service renders under
    `_compose_service_name(module_id, service)`, so endpoints advertised
    via `${service.host}` resolve on the Compose network even when the
    instance id differs from the service name (issue #1010). Multi-service
    modules have no single answer and keep the instance id; they name
    their serving service explicitly via the contract's `serviceName`
    field, which route generation and the E147 host check map through the
    same naming rule.
    """
    if isinstance(compose_services, dict) and len(compose_services) == 1:
        return _compose_service_name(module_id, next(iter(compose_services)))
    return module_id
