from __future__ import annotations

import json
import os
import shlex
import shutil
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .loader import load_yaml_file, resolve_module_dir
from .planner import MaxNestingDepthExceeded, apply_defaults, substitute_string


_TRACKING_FILE = Path(".cds") / "get-manifest.json"
_SKIP_DIRS = {
    ".cds",
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "htmlcov",
}
_SKIP_FILES = {
    ".coverage",
    ".coverage.xml",
    ".env",
    "docker-compose.yml",
}


class GetError(RuntimeError):
    """Raised when `cds get` cannot complete safely."""


@dataclass(frozen=True)
class CopyAction:
    source: Path
    destination: Path
    repo_relative_path: str


def fetch_profile(
    profile: str,
    *,
    remote: str | None = None,
    destination_root: Path | None = None,
    force: bool = False,
    dry_run: bool = False,
) -> tuple[list[CopyAction], Path]:
    source_repo = _resolve_source_repository(remote)
    target_root = (destination_root or Path.cwd()).expanduser().resolve()
    profile_path = _resolve_source_profile_path(source_repo, profile)
    asset_roots = _collect_asset_roots(source_repo, profile_path)

    actions = _build_copy_plan(source_repo, asset_roots, target_root)
    if dry_run:
        return actions, target_root / _TRACKING_FILE

    conflicts = _find_conflicts(actions)
    if conflicts and not force:
        rendered = ", ".join(conflicts[:5])
        extra = "" if len(conflicts) <= 5 else f" (+{len(conflicts) - 5} more)"
        raise GetError(
            "Refusing to overwrite existing files without --force: "
            f"{rendered}{extra}"
        )

    for action in actions:
        action.destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(action.source, action.destination)

    _write_tracking_manifest(
        target_root=target_root,
        requested_profile=profile,
        source_repo=source_repo,
        profile_path=profile_path,
        remote=remote,
        actions=actions,
        asset_roots=asset_roots,
    )
    return actions, target_root / _TRACKING_FILE


def format_get_plan(actions: list[CopyAction], *, destination_root: Path) -> str:
    if not actions:
        return f"No file changes required under {destination_root}."

    lines = [f"Planned {len(actions)} file(s) under {destination_root}:"]
    for action in actions[:20]:
        lines.append(f"  - {action.repo_relative_path}")
    if len(actions) > 20:
        lines.append(f"  - ... {len(actions) - 20} more")
    return "\n".join(lines)


def _resolve_source_repository(remote: str | None) -> Path:
    candidate = Path(remote).expanduser() if remote else _find_project_root()
    resolved = candidate.resolve()
    if not resolved.exists():
        raise GetError(f"Source repository does not exist: {resolved}")
    if not resolved.is_dir():
        raise GetError(f"Source repository must be a directory: {resolved}")
    if not (resolved / "profiles").exists():
        raise GetError(
            f"Source repository must contain a profiles/ directory: {resolved}"
        )
    return resolved


def _find_project_root(start: Path | None = None) -> Path:
    current = (start or Path.cwd()).resolve()
    for directory in [current, *current.parents]:
        if (directory / "pyproject.toml").exists() or (directory / ".git").exists():
            return directory
    return current


def _resolve_source_profile_path(source_repo: Path, profile: str) -> Path:
    profile_selector = Path(profile)
    candidates = [
        source_repo / profile_selector,
        source_repo / "profiles" / profile_selector,
        source_repo / "profiles" / profile_selector / "profile.yaml",
        source_repo / "profiles" / f"{profile}.yaml",
    ]

    for candidate in candidates:
        if candidate.is_dir() and (candidate / "profile.yaml").is_file():
            resolved = (candidate / "profile.yaml").resolve()
            _require_within_repo(resolved, source_repo, f'profile "{profile}"')
            return resolved
        if candidate.is_file() and candidate.suffix == ".yaml":
            resolved = candidate.resolve()
            _require_within_repo(resolved, source_repo, f'profile "{profile}"')
            return resolved

    raise GetError(
        f'Could not resolve profile "{profile}" under {source_repo / "profiles"}'
    )


def _collect_asset_roots(source_repo: Path, profile_path: Path) -> list[Path]:
    profile_dir = profile_path.parent
    asset_roots: set[Path] = {
        profile_dir if profile_path.name == "profile.yaml" else profile_path
    }
    profile_doc, profile_diags = load_yaml_file(profile_path)
    if profile_diags or profile_doc is None:
        raise GetError(f"Could not load source profile {profile_path}")

    spec = profile_doc.get("spec")
    modules = spec.get("modules", []) if isinstance(spec, dict) else []
    if not isinstance(modules, list):
        raise GetError(f"Profile modules must be a list in {profile_path}")

    for index, module in enumerate(modules):
        if not isinstance(module, dict):
            raise GetError(f"Profile module at index {index} must be a mapping")
        source = module.get("source")
        if not isinstance(source, str) or not source:
            raise GetError(f'Profile module "{module.get("id", index)}" is missing a source')

        module_dir = resolve_module_dir(source, profile_dir, module_root=None)
        if module_dir is None or not module_dir.exists():
            raise GetError(f'Could not resolve module source "{source}" from {profile_dir}')
        _require_within_repo(module_dir, source_repo, f'module source "{source}"')
        asset_roots.add(module_dir.resolve())
        asset_roots.update(
            _collect_module_runtime_assets(source_repo, module_dir.resolve(), module)
        )

    return sorted(asset_roots)


def _collect_module_runtime_assets(
    source_repo: Path,
    module_dir: Path,
    module_instance: dict[str, Any],
) -> set[Path]:
    module_doc, module_diags = load_yaml_file(module_dir / "module.yaml")
    if module_diags or module_doc is None:
        raise GetError(f"Could not load module {module_dir / 'module.yaml'}")

    compose = (
        module_doc.get("spec", {})
        .get("implementation", {})
        .get("compose", {})
    )
    if not isinstance(compose, dict):
        return set()

    assets: set[Path] = set()
    services = compose.get("services", {})
    if not isinstance(services, dict):
        return assets

    context = _module_interpolation_context(module_doc, module_instance)

    for service in services.values():
        if not isinstance(service, dict):
            continue
        assets.update(
            _collect_build_assets(
                source_repo,
                module_dir,
                service.get("build"),
                interpolation_context=context,
            )
        )

    return assets


def _collect_build_assets(
    source_repo: Path,
    module_dir: Path,
    build: Any,
    *,
    interpolation_context: dict[str, Any] | None,
) -> set[Path]:
    if build is None:
        return set()

    if isinstance(build, str):
        context_value = build
        dockerfile_name = "Dockerfile"
    elif isinstance(build, dict):
        context_value = build.get("context")
        dockerfile_name = build.get("dockerfile", "Dockerfile")
    else:
        return set()

    if isinstance(context_value, str):
        context_value = _resolve_template_value(context_value, interpolation_context)
    if (
        not isinstance(context_value, str)
        or not context_value
        or _contains_template(context_value)
    ):
        return set()
    if isinstance(dockerfile_name, str):
        dockerfile_name = _resolve_template_value(dockerfile_name, interpolation_context)
    if (
        not isinstance(dockerfile_name, str)
        or not dockerfile_name
        or _contains_template(dockerfile_name)
    ):
        return set()

    context_path = _resolve_existing_local_path(context_value, [module_dir, source_repo])
    if context_path is None:
        return set()
    _require_within_repo(context_path, source_repo, f'build context "{context_value}"')

    dockerfile_path = (
        Path(dockerfile_name).resolve()
        if Path(dockerfile_name).is_absolute()
        else (context_path / dockerfile_name).resolve()
    )
    _require_within_repo(dockerfile_path, source_repo, f'build.dockerfile "{dockerfile_name}"')
    if not dockerfile_path.exists():
        return {context_path}

    assets: set[Path] = {dockerfile_path.resolve()}
    assets.update(_parse_dockerfile_assets(context_path, dockerfile_path.resolve()))
    return assets


def _parse_dockerfile_assets(context_path: Path, dockerfile_path: Path) -> set[Path]:
    try:
        content = dockerfile_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise GetError(f"Could not read Dockerfile {dockerfile_path}: {exc}") from exc

    assets: set[Path] = set()
    logical_lines: list[str] = []
    current = ""
    for raw_line in content.splitlines():
        stripped = _strip_dockerfile_comment(raw_line).rstrip()
        if not stripped:
            if current:
                logical_lines.append(current.strip())
                current = ""
            continue
        if stripped.endswith("\\"):
            current = f"{current} {stripped[:-1].rstrip()}".strip()
            continue
        current = f"{current} {stripped}".strip()
        logical_lines.append(current)
        current = ""
    if current:
        logical_lines.append(current.strip())

    for line in logical_lines:
        upper = line.upper()
        if not (upper.startswith("COPY ") or upper.startswith("ADD ")):
            continue
        assets.update(
            _extract_dockerfile_instruction_sources(context_path, dockerfile_path, line)
        )

    return assets


def _extract_dockerfile_instruction_sources(
    context_path: Path,
    dockerfile_path: Path,
    line: str,
) -> set[Path]:
    instruction, remainder = line.split(None, 1)
    remainder, copy_from_stage = _strip_dockerfile_instruction_options(remainder.strip())
    if copy_from_stage or not remainder:
        return set()

    assets: set[Path] = set()
    if remainder.startswith("["):
        try:
            values = json.loads(remainder)
        except json.JSONDecodeError as exc:
            raise GetError(
                f"Could not parse {instruction.upper()} sources in {dockerfile_path}: {exc}"
            ) from exc
        if not isinstance(values, list) or len(values) < 2:
            return assets
        source_items = [value for value in values[:-1] if isinstance(value, str)]
    else:
        try:
            parts = shlex.split(remainder)
        except ValueError as exc:
            raise GetError(
                f"Could not parse {instruction.upper()} sources in {dockerfile_path}: {exc}"
            ) from exc
        if len(parts) < 2:
            return assets
        source_items = parts[:-1]

    for source in source_items:
        if not isinstance(source, str):
            continue
        if source == ".":
            assets.add(context_path.resolve())
            continue
        if source.startswith("http://") or source.startswith("https://"):
            continue
        if _contains_template(source):
            continue
        if Path(source).is_absolute():
            continue
        matches = list(context_path.glob(source))
        if not matches and "*" not in source and "?" not in source and "[" not in source:
            matches = [context_path / source]
        for match in matches:
            if match.exists():
                assets.add(match.resolve())

    return assets


def _strip_dockerfile_instruction_options(remainder: str) -> tuple[str, bool]:
    copy_from_stage = False
    while remainder.startswith("--"):
        parts = remainder.split(None, 1)
        option = parts[0]
        if option.startswith("--from="):
            copy_from_stage = True
        if len(parts) == 1:
            return "", copy_from_stage
        remainder = parts[1].strip()
    return remainder, copy_from_stage


def _contains_template(value: str) -> bool:
    return "${" in value


def _strip_dockerfile_comment(line: str) -> str:
    in_single_quote = False
    in_double_quote = False
    escaped = False

    for index, char in enumerate(line):
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char == "'" and not in_double_quote:
            in_single_quote = not in_single_quote
            continue
        if char == '"' and not in_single_quote:
            in_double_quote = not in_double_quote
            continue
        if (
            char == "#"
            and not in_single_quote
            and not in_double_quote
            and not line[:index].strip()
        ):
            return line[:index]

    return line


def _module_interpolation_context(
    module_doc: dict[str, Any],
    module_instance: dict[str, Any],
) -> dict[str, Any] | None:
    config_schema = module_doc.get("spec", {}).get("configSchema")
    if not isinstance(config_schema, dict):
        return None

    raw_config = module_instance.get("config", {})
    if raw_config is None:
        raw_config = {}
    if not isinstance(raw_config, dict):
        return None

    try:
        normalized_config = apply_defaults(raw_config, config_schema)
    except MaxNestingDepthExceeded:
        return None

    return {
        "config": normalized_config,
        "bindings": {},
        "service": {},
        "secrets": {},
    }


def _resolve_template_value(
    value: str,
    interpolation_context: dict[str, Any] | None,
) -> Any:
    if interpolation_context is None or not _contains_template(value):
        return value
    return substitute_string(value, interpolation_context)


def _resolve_existing_local_path(path_value: str, bases: list[Path]) -> Path | None:
    candidate_path = Path(path_value)
    if candidate_path.is_absolute():
        return candidate_path.resolve() if candidate_path.exists() else None

    normalized = candidate_path
    if normalized.parts and normalized.parts[0] == ".":
        normalized = normalized.relative_to(".")

    for base in bases:
        resolved = (base / normalized).resolve()
        if resolved.exists():
            return resolved
    return None


def _require_within_repo(path_value: Path, source_repo: Path, label: str) -> None:
    try:
        path_value.resolve().relative_to(source_repo.resolve())
    except ValueError as exc:
        raise GetError(f"{label} resolves outside the source repository") from exc


def _build_copy_plan(
    source_repo: Path,
    asset_roots: list[Path],
    destination_root: Path,
) -> list[CopyAction]:
    actions_by_destination: dict[Path, CopyAction] = {}

    for asset_root in asset_roots:
        if asset_root.is_file():
            _add_copy_action(
                source_repo=source_repo,
                source_file=asset_root,
                destination_root=destination_root,
                actions_by_destination=actions_by_destination,
            )
            continue

        for root, dirs, files in os.walk(asset_root):
            dirs[:] = [name for name in dirs if name not in _SKIP_DIRS]
            for file_name in sorted(files):
                if file_name in _SKIP_FILES:
                    continue
                source_file = Path(root) / file_name
                _add_copy_action(
                    source_repo=source_repo,
                    source_file=source_file,
                    destination_root=destination_root,
                    actions_by_destination=actions_by_destination,
                )

    return sorted(actions_by_destination.values(), key=lambda action: action.repo_relative_path)


def _add_copy_action(
    *,
    source_repo: Path,
    source_file: Path,
    destination_root: Path,
    actions_by_destination: dict[Path, CopyAction],
) -> None:
    repo_relative = source_file.resolve().relative_to(source_repo.resolve())
    destination = (destination_root / repo_relative).resolve()
    existing = actions_by_destination.get(destination)
    if existing is None:
        actions_by_destination[destination] = CopyAction(
            source=source_file.resolve(),
            destination=destination,
            repo_relative_path=repo_relative.as_posix(),
        )
        return
    if existing.source.resolve() != source_file.resolve():
        raise GetError(
            f"Multiple source files would map to the same destination: {destination}"
        )


def _find_conflicts(actions: list[CopyAction]) -> list[str]:
    conflicts: list[str] = []
    for action in actions:
        destination = action.destination
        if not destination.exists():
            continue
        if destination.is_dir():
            conflicts.append(action.repo_relative_path)
            continue
        if destination.read_bytes() != action.source.read_bytes():
            conflicts.append(action.repo_relative_path)
    return conflicts


def _write_tracking_manifest(
    *,
    target_root: Path,
    requested_profile: str,
    source_repo: Path,
    profile_path: Path,
    remote: str | None,
    actions: list[CopyAction],
    asset_roots: list[Path],
) -> None:
    manifest_path = target_root / _TRACKING_FILE
    manifest = _read_tracking_manifest(manifest_path)

    entry = {
        "requestedProfile": requested_profile,
        "sourceProfile": profile_path.relative_to(source_repo).as_posix(),
        "remote": remote or str(source_repo),
        "fetchedAt": datetime.now(UTC).isoformat(),
        "assetRoots": [
            _asset_root_relative_path(asset_root, source_repo) for asset_root in asset_roots
        ],
        "files": [action.repo_relative_path for action in actions],
    }

    profiles = manifest.setdefault("profiles", {})
    if not isinstance(profiles, dict):
        profiles = {}
        manifest["profiles"] = profiles
    profiles[requested_profile] = entry

    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


def _read_tracking_manifest(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"version": 1, "profiles": {}}

    malformed_reason: str | None = None
    try:
        raw_text = path.read_text(encoding="utf-8")
    except OSError as exc:
        malformed_reason = f"could not read file: {exc}"
    else:
        try:
            data = json.loads(raw_text)
        except json.JSONDecodeError as exc:
            malformed_reason = f"invalid JSON: {exc}"
        else:
            if not isinstance(data, dict):
                malformed_reason = "manifest root must be a JSON object"
            else:
                data.setdefault("version", 1)
                data.setdefault("profiles", {})
                return data

    _backup_malformed_manifest(path, malformed_reason)
    return {"version": 1, "profiles": {}}


def _backup_malformed_manifest(path: Path, reason: str | None) -> None:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    backup_path = path.with_name(f"{path.name}.corrupt-{timestamp}")
    try:
        shutil.copy2(path, backup_path)
        backup_message = f"backed up to {backup_path}"
    except OSError as exc:
        backup_message = f"backup failed: {exc}"

    print(
        f"WARNING {path} is malformed ({reason}); resetting tracking manifest "
        f"({backup_message}).",
        file=sys.stderr,
    )


def _asset_root_relative_path(asset_root: Path, source_repo: Path) -> str:
    try:
        return asset_root.resolve().relative_to(source_repo.resolve()).as_posix()
    except ValueError:
        return asset_root.resolve().as_posix()
