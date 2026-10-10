"""Opt-in check for a newer published CDS CLI release (issue #841).

Never runs by default (CDS has no default-on network calls). It runs only
for the explicit `cds check-update` command or when CDS_CHECK_UPDATES=1.
The only request made is an anonymous GET of the public PyPI JSON index
for this package; no profile or project content is transmitted.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

ENV_VAR = "CDS_CHECK_UPDATES"
PACKAGE = "composable-data-stack"
PYPI_URL = f"https://pypi.org/pypi/{PACKAGE}/json"
CHANGELOG_URL = "https://github.com/RonaldHensbergen/composable-data-stack/blob/main/CHANGELOG.md"
RELEASES_URL = "https://github.com/RonaldHensbergen/composable-data-stack/releases"
COOLDOWN_SECONDS = 24 * 60 * 60
_TIMEOUT = 3


def is_enabled() -> bool:
    return os.getenv(ENV_VAR, "").strip().lower() in {"1", "true", "yes", "on"}


def _cache_path() -> Path:
    base = os.getenv("XDG_CACHE_HOME")
    if base:
        root = Path(base)
    elif sys.platform == "win32" and os.getenv("LOCALAPPDATA"):
        root = Path(os.environ["LOCALAPPDATA"])
    else:
        root = Path.home() / ".cache"
    return root / "cds" / "update-check.json"


def _parse_version(value: str) -> tuple[int, ...] | None:
    match = re.fullmatch(r"v?(\d+(?:\.\d+)*)", value.strip())
    if not match:
        return None
    return tuple(int(part) for part in match.group(1).split("."))


def is_newer(latest: str, current: str) -> bool:
    """True when `latest` is a strictly newer final release than `current`."""
    latest_v, current_v = _parse_version(latest), _parse_version(current)
    if latest_v is None or current_v is None:
        return False
    width = max(len(latest_v), len(current_v))
    pad = lambda v: v + (0,) * (width - len(v))  # noqa: E731
    return pad(latest_v) > pad(current_v)


def fetch_latest_version() -> str | None:
    """Return the latest PyPI release, or None on any failure."""
    try:
        request = Request(PYPI_URL, headers={"Accept": "application/json"})  # noqa: S310
        with urlopen(request, timeout=_TIMEOUT) as response:  # nosec B310  # noqa: S310
            data = json.loads(response.read().decode("utf-8"))
        latest = data["info"]["version"]
    except (OSError, URLError, ValueError, KeyError, TypeError):
        return None
    return latest if isinstance(latest, str) else None


def _cached_latest() -> str | None:
    try:
        data = json.loads(_cache_path().read_text(encoding="utf-8"))
        if time.time() - float(data["checkedAt"]) < COOLDOWN_SECONDS:
            return data["latest"] if isinstance(data["latest"], str) else None
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None


def _store_cache(latest: str) -> None:
    try:
        path = _cache_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"checkedAt": time.time(), "latest": latest}), encoding="utf-8")
    except OSError:
        pass


def latest_version(use_cache: bool = True) -> str | None:
    if use_cache:
        cached = _cached_latest()
        if cached:
            return cached
    latest = fetch_latest_version()
    if latest:
        _store_cache(latest)
    return latest


def format_notice(current: str, latest: str) -> str:
    return (
        f"NOTICE A newer CDS release is available: {latest} (installed {current}). "
        f"See {CHANGELOG_URL} or {RELEASES_URL}."
    )


def maybe_notify(current: str) -> None:
    """Print a non-failing stderr notice when opted in and outdated.

    "Non-failing" means a PyPI lookup failure/timeout is always swallowed
    and never affects the calling command's output or exit code. On a
    cache miss this still makes a synchronous network request (bounded by
    `_TIMEOUT`) before returning, so it is not asynchronous/non-blocking.
    """
    if not is_enabled() or current == "unknown":
        return
    latest = latest_version()
    if latest and is_newer(latest, current):
        print(format_notice(current, latest), file=sys.stderr)
