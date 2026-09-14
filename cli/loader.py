# cli/loader.py
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from .diagnostics import Diagnostic
from .utils import _atomic_write

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
    except UnicodeDecodeError as e:
        return None, [
            Diagnostic(
                level="error",
                code="E001",
                message=f"File is not valid UTF-8 text: {e}",
                path=str(path),
            )
        ]
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
        candidate.resolve().relative_to(allowed_root.resolve())
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


def save_generated_profile(
    profile: dict[str, Any],
    profiles_root: Path,
    name: str | None = None,
    force: bool = False,
) -> tuple[Path | None, list[Diagnostic]]:
    """
    Persists a runtime-generated/in-memory profile dict at the normal
    profiles/<name>/profile.yaml location -- the same on-disk layout as a
    hand-authored profile -- so it can flow through the existing path-based
    validate_profile()/build_plan() entry points completely unchanged, with
    the same relative module-source resolution and extends/environment-
    overlay semantics any other profile gets (issue #349: programmatically
    composed profiles are supported by writing them to disk at their normal
    location first, not by adding a second in-memory code path to the
    planner/validator).

    `name` defaults to profile["metadata"]["name"] when not given explicitly.
    Refuses to write outside profiles_root (the same boundary
    resolve_module_file() enforces for module sources) and, unless
    force=True, refuses to silently overwrite an existing profile.yaml.

    Returns (profile_file_path, diagnostics). profile_file_path is None if
    diagnostics contains an error. A YAML-serialization failure (E117, e.g.
    a non-representable value nested in the profile) or a filesystem write
    failure (E117, e.g. permission denied, disk full) is reported as a
    diagnostic rather than raising, keeping this promise for every error
    path instead of just the validation checks above. `profiles_root`
    existing but not being a directory (E118) is reported the same way,
    rather than silently building a nonsense nested path underneath it.

    The upfront `profile_file.exists()` check below is a fast, friendly
    E116 for the common case, but it can't close the race between that
    check and the write: a second caller could create the same file in
    between. The write itself (_atomic_write(..., overwrite=force)) closes
    that race for real when force=False -- if a concurrent writer wins,
    this call fails closed with E116 instead of silently overwriting it.
    """
    # E114 groups both "unusable input document" cases below (a non-dict
    # profile, and a dict profile with no resolvable name) rather than
    # splitting into two codes: both are the same class of failure -- the
    # caller handed generate_profile() something it cannot even attempt to
    # write out yet, before any path/overwrite check applies -- mirroring
    # how E115 already groups "absolute name" and "escapes profiles_root"
    # as one "invalid destination" class below.
    if not isinstance(profile, dict):
        return None, [
            Diagnostic(
                level="error",
                code="E114",
                message=f"Generated profile must be a mapping/object, got {type(profile).__name__}.",
                path="profile",
            )
        ]

    if name is None:
        name = profile.get("metadata", {}).get("name") if isinstance(profile.get("metadata"), dict) else None

    if not isinstance(name, str) or not name.strip():
        return None, [
            Diagnostic(
                level="error",
                code="E114",
                message=(
                    "Generated profile requires a name: pass name= explicitly or set "
                    "metadata.name on the profile."
                ),
                path="metadata.name",
            )
        ]

    name_path = Path(name)
    if name_path.is_absolute() or ".." in name_path.parts:
        return None, [
            Diagnostic(
                level="error",
                code="E115",
                message=f'Generated profile name "{name}" must be a relative name without ".." segments.',
                path="metadata.name",
            )
        ]

    allowed_root = profiles_root.expanduser().resolve()

    if allowed_root.exists() and not allowed_root.is_dir():
        return None, [
            Diagnostic(
                level="error",
                code="E118",
                message=(
                    f'The profiles root "{allowed_root}" is not a directory. '
                    "Generating a profile needs a directory to create "
                    "<name>/profile.yaml under -- point CDS_PROFILE_PATH at a "
                    "profiles root directory, not a single profile file or a "
                    "bare profile name."
                ),
                path=str(allowed_root),
            )
        ]

    profile_file = (allowed_root / name_path / "profile.yaml").resolve()

    if not _is_within(profile_file, allowed_root):
        return None, [
            Diagnostic(
                level="error",
                code="E115",
                message=f'Generated profile name "{name}" resolves outside the profiles root "{allowed_root}".',
                path="metadata.name",
            )
        ]

    if profile_file.exists() and not force:
        return None, [
            Diagnostic(
                level="error",
                code="E116",
                message=f"Refusing to overwrite existing profile: {profile_file}. Pass force=True to overwrite.",
                path=str(profile_file),
            )
        ]

    try:
        serialized = yaml.safe_dump(profile, sort_keys=False)
    except yaml.representer.RepresenterError as exc:
        return None, [
            Diagnostic(
                level="error",
                code="E117",
                message=f"Generated profile contains a value that cannot be serialized to YAML: {exc}",
                path="profile",
            )
        ]

    try:
        _atomic_write(profile_file, serialized, overwrite=force)
    except FileExistsError:
        return None, [
            Diagnostic(
                level="error",
                code="E116",
                message=(
                    f"Refusing to overwrite existing profile: {profile_file}. "
                    "Pass force=True to overwrite."
                ),
                path=str(profile_file),
            )
        ]
    except OSError as exc:
        return None, [
            Diagnostic(
                level="error",
                code="E117",
                message=f"Failed to write generated profile to {profile_file}: {exc}",
                path=str(profile_file),
            )
        ]

    return profile_file, []
