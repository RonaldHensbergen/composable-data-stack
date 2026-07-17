# cli/loader.py
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from .diagnostics import Diagnostic


_MODULE_ROOT_MARKERS = {"modules", "modules-experimental"}


def load_yaml_file(path: Path) -> tuple[dict[str, Any] | None, list[Diagnostic]]:
    if not path.exists():
        return None, [
            Diagnostic(
                level="error",
                code="E020",
                message=f"YAML file not found: {path}",
                path=str(path),
            )
        ]

    try:
        with path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except yaml.YAMLError as e:
        return None, [
            Diagnostic(
                level="error",
                code="E001",
                message=f"Invalid YAML: {e}",
                path=str(path),
            )
        ]

    if not isinstance(data, dict):
        return None, [
            Diagnostic(
                level="error",
                code="E010",
                message="Top-level YAML document must be a mapping/object.",
                path=str(path),
            )
        ]

    return data, []


def resolve_module_file(
    source: str,
    profile_dir: Path,
    module_root: Path | None = None,
    diagnostic_path: str | None = None,
) -> tuple[Path | None, list[Diagnostic]]:
    source_path = Path(source).expanduser()
    path = diagnostic_path or str(source)

    if source_path.is_absolute():
        return None, [
            Diagnostic(
                level="error",
                code="E022",
                message=f'Module source "{source}" must be relative to an allowed modules root.',
                path=path,
            )
        ]

    if source_path.parts and source_path.parts[0] == ".":
        source_path = source_path.relative_to(".")

    if module_root is not None:
        allowed_root = module_root.expanduser()
        if allowed_root.is_file():
            allowed_root = allowed_root.parent
        allowed_root = allowed_root.resolve()
        candidate = (allowed_root / source_path / "module.yaml").resolve()
    else:
        allowed_root = _derive_allowed_module_root(profile_dir, source_path)
        if allowed_root is None:
            return None, [
                Diagnostic(
                    level="error",
                    code="E022",
                    message=(
                        f'Module source "{source}" must resolve under a "modules/" '
                        'or "modules-experimental/" directory.'
                    ),
                    path=path,
                )
            ]
        candidate = (profile_dir / source_path / "module.yaml").resolve()

    if not _is_within(candidate, allowed_root):
        return None, [
            Diagnostic(
                level="error",
                code="E022",
                message=(
                    f'Module source "{source}" resolves outside allowed module root '
                    f'"{allowed_root}".'
                ),
                path=path,
            )
        ]

    return candidate, []


def _derive_allowed_module_root(profile_dir: Path, source_path: Path) -> Path | None:
    parts = source_path.parts
    for index, part in enumerate(parts):
        if part in _MODULE_ROOT_MARKERS:
            return (profile_dir / Path(*parts[: index + 1])).resolve()
    return None


def _is_within(candidate: Path, allowed_root: Path) -> bool:
    try:
        candidate.relative_to(allowed_root)
        return True
    except ValueError:
        return False


def resolve_module_dir(
    source: str,
    profile_dir: Path | None,
    module_root: Path | None = None,
) -> Path | None:
    """
    Resolve a module's `source` field to its containing directory, enforcing
    the same allowed-root boundary as resolve_module_file().

    Unlike resolve_module_file(), this has no diagnostics list: callers (the
    renderer, when computing bases for volume/build-context path rewriting)
    already treat a None return as "not a usable base" and silently exclude
    it, so a boundary violation here fails closed the same way a missing
    module directory already does; no new diagnostic plumbing needed.
    """
    if not isinstance(source, str):
        return None

    source_path = Path(source).expanduser()

    if source_path.is_absolute():
        return None

    if source_path.parts and source_path.parts[0] == ".":
        source_path = source_path.relative_to(".")

    if module_root is not None:
        allowed_root = module_root.expanduser()
        if allowed_root.is_file():
            allowed_root = allowed_root.parent
        allowed_root = allowed_root.resolve()
        candidate = (allowed_root / source_path).resolve()
    else:
        if profile_dir is None:
            return None
        allowed_root = _derive_allowed_module_root(profile_dir, source_path)
        if allowed_root is None:
            return None
        candidate = (profile_dir / source_path).resolve()

    if not _is_within(candidate, allowed_root):
        return None

    return candidate
