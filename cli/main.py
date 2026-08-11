# cli/main.py
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess  # nosec B404
import sys
import tempfile
from contextlib import suppress
from importlib.metadata import PackageNotFoundError, version as _package_version
from pathlib import Path
from typing import Any

try:
    import argcomplete  # type: ignore
except ImportError:
    argcomplete = None

from .validator import has_errors, validate_profile
from .diagnostics import Diagnostic
from .planner import build_plan
from .renderer import render_compose
from .image_updates import collect_module_images, check_image_update
from .overlay import resolve_profile
from .preflight import preflight_passed, run_preflight
from .security import PrecomputedRender, run_security_validation
from .security_common import SEVERITY_ORDER, infer_profile_class
from .image_verification import default_fixture_path, load_policy_from_env, verify_images
from .state import format_state_output, group_services_by_health, parse_compose_ps_json
from .up_runner import (
    DEFAULT_TIMEOUT_SECONDS,
    default_log_path,
    poll_state_until_settled,
    run_streamed,
    start_log_tail,
    start_up_in_background,
    stop_log_tail,
)
from .loader import load_yaml_file
import yaml


def load_env_file(env_file: str = ".env") -> None:
    """Load environment variables from a .env file."""
    env_path = Path(env_file)
    if not env_path.exists():
        return

    try:
        with open(env_path, encoding="utf-8-sig") as f:
            lines = f.readlines()
    except (OSError, UnicodeDecodeError) as exc:
        print(f"WARNING Could not read {env_path}: {exc}", file=sys.stderr)
        return

    for line in lines:
        line = line.strip()
        # Skip empty lines and comments
        if not line or line.startswith("#"):
            continue
        
        # Parse KEY=VALUE format
        if "=" in line:
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip()
            if (
                len(value) >= 2
                and value[0] == value[-1]
                and value[0] in {'"', "'"}
            ):
                value = value[1:-1]
            # Only accept CDS_* keys - arbitrary .env keys are ignored
            if not key.startswith("CDS_"):
                continue
            # Only set if not already in environment
            if key and not os.environ.get(key):
                os.environ[key] = value


def print_diagnostics(diagnostics) -> None:
    for d in diagnostics:
        prefix = "ERROR" if d.level == "error" else "WARN"
        print(f"{prefix} {d.format()}\n")


def profile_completer(prefix, parsed_args, **kwargs):
    return [name for name in list_profiles() if name.startswith(prefix)]


def get_profiles_root() -> Path:
    override = os.getenv("CDS_PROFILE_PATH")
    if override:
        return Path(override).expanduser()
    return find_project_root() / "profiles"


def get_modules_root() -> Path:
    override = os.getenv("CDS_MODULE_PATH")
    if override:
        return Path(override).expanduser()
    return find_project_root() / "modules"


def find_project_root(start: Path | None = None) -> Path:
    """
    Walk up from `start` (default: current working directory) looking for a
    project root marker (pyproject.toml or .git). Falls back to `start` itself
    if no marker is found.
    """
    current = (start or Path.cwd()).resolve()
    for directory in [current, *current.parents]:
        if (directory / "pyproject.toml").exists() or (directory / ".git").exists():
            return directory
    return current


def get_config_path() -> Path:
    """Location of the per-project CDS config file used by `cds use`."""
    override = os.getenv("CDS_CONFIG_PATH")
    if override:
        return Path(override).expanduser()
    return find_project_root() / ".cds" / "config.json"


class ConfigIOError(RuntimeError):
    """Raised when the `cds use` config file cannot be read or written."""


def _atomic_write_text(path: Path, content: str) -> None:
    """Write `content` to `path` atomically via a temp file + os.replace.

    Raises ConfigIOError (instead of an uncaught traceback) if the parent
    directory can't be created or the write/replace fails, e.g. because
    CDS_CONFIG_PATH points at an unwritable location or a path segment is
    actually a file.
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    except OSError as exc:
        raise ConfigIOError(f"Could not prepare {path} for writing: {exc}") from exc

    try:
        with os.fdopen(fd, "w", encoding="utf-8") as tmp_file:
            tmp_file.write(content)
        os.replace(tmp_name, path)
    except OSError as exc:
        with suppress(OSError):
            os.unlink(tmp_name)
        raise ConfigIOError(f"Could not write config file {path}: {exc}") from exc


def _read_config() -> dict:
    config_path = get_config_path()
    if not config_path.exists():
        return {}
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        print(
            f"WARNING {config_path} is not valid JSON; treating it as empty. "
            "It will be overwritten by the next `cds use <profile>`.",
            file=sys.stderr,
        )
        return {}
    except OSError as exc:
        print(f"WARNING Could not read {config_path}: {exc}", file=sys.stderr)
        return {}
    if not isinstance(data, dict):
        print(
            f"WARNING {config_path} contains valid JSON but not a mapping; treating it as empty. "
            "It will be overwritten by the next `cds use <profile>`.",
            file=sys.stderr,
        )
        return {}
    return data


def load_saved_profile() -> str | None:
    """Return the profile name saved via `cds use`, if any."""
    profile = _read_config().get("profile")
    return profile if isinstance(profile, str) and profile else None


def save_profile(profile: str) -> Path:
    """Persist `profile` as the default for this project. Returns the config path.

    Raises ConfigIOError if the config file cannot be written.
    """
    config_path = get_config_path()
    data = _read_config()
    data["profile"] = profile
    _atomic_write_text(config_path, json.dumps(data, indent=2) + "\n")
    return config_path


def clear_saved_profile() -> bool:
    """Remove a previously saved default profile. Returns True if one was cleared.

    Raises ConfigIOError if the config file cannot be updated/removed.
    """
    config_path = get_config_path()
    data = _read_config()
    if "profile" not in data:
        return False
    del data["profile"]
    try:
        if data:
            _atomic_write_text(config_path, json.dumps(data, indent=2) + "\n")
        else:
            config_path.unlink()
    except OSError as exc:
        raise ConfigIOError(f"Could not update config file {config_path}: {exc}") from exc
    return True


def _resolve_profile_root(profile_root: Path) -> str | None:
    """
    Resolve an ambient profiles root (CDS_PROFILE_PATH, or the default
    "profiles/" directory) with no explicit profile argument. Returns None if
    `profile_root` doesn't unambiguously resolve to a single profile.
    """
    if profile_root.is_file():
        return str(profile_root.resolve())

    direct_profile = profile_root / "profile.yaml"
    if direct_profile.exists():
        return str(direct_profile.resolve())

    if profile_root.is_dir():
        subdirs = [
            directory
            for directory in sorted(profile_root.iterdir())
            if directory.is_dir() and (directory / "profile.yaml").exists()
        ]
        if len(subdirs) == 1:
            return str((subdirs[0] / "profile.yaml").resolve())

    # profile_root may be set to a bare profile name rather than a path.
    # Try resolving it as a name under the default profiles/ directory.
    default_root = find_project_root() / "profiles"
    if default_root.resolve() != profile_root.resolve():
        name_candidate = default_root / profile_root.name / "profile.yaml"
        if name_candidate.exists():
            return str(name_candidate.resolve())

    return None


def resolve_profile_path(profile: str | None) -> str:
    profile_root = get_profiles_root()
    
    if profile:
        candidate = Path(profile)
        if candidate.is_file():
            return str(candidate.resolve())
        
        if candidate.suffix == ".yaml":
            return str(candidate.resolve())

        if candidate.is_dir():
            direct_profile = candidate / "profile.yaml"
            if direct_profile.exists():
                return str(direct_profile.resolve())

            subdirs = [
                directory
                for directory in sorted(candidate.iterdir())
                if directory.is_dir() and (directory / "profile.yaml").exists()
            ]
            if len(subdirs) == 1:
                return str((subdirs[0] / "profile.yaml").resolve())

        if profile_root.is_file():
            return str(profile_root.resolve())

        candidate_by_name = profile_root / profile / "profile.yaml"
        candidate_file = profile_root / f"{profile}.yaml"

        if candidate_by_name.exists():
            return str(candidate_by_name.resolve())
        if candidate_file.exists():
            return str(candidate_file.resolve())

        # CDS_PROFILE_PATH may have been set to a profile name rather than a
        # profiles root directory. Fall back to the default "profiles/" root so
        # that an explicit profile name still resolves correctly.
        default_root = find_project_root() / "profiles"
        if default_root.resolve() != profile_root.resolve():
            default_by_name = default_root / profile / "profile.yaml"
            default_by_file = default_root / f"{profile}.yaml"
            if default_by_name.exists():
                return str(default_by_name.resolve())
            if default_by_file.exists():
                return str(default_by_file.resolve())

        return str(candidate_by_name.resolve())

    # No profile argument provided. Resolution order:
    #   1. CDS_PROFILE_PATH, if explicitly set for this invocation. Env vars
    #      are per-invocation and reflect the current session more reliably
    #      than a persisted, gitignored default that's easy to forget about.
    #      This matches common CLI precedence (env var overrides persisted
    #      config, e.g. AWS CLI, Azure CLI) -- and was previously inverted
    #      here, with the saved default silently winning over the env var.
    #   2. The saved default from `cds use <profile>`.
    #   3. The single profile under the default profiles/ directory, if
    #      there is exactly one (also the fallback when CDS_PROFILE_PATH is
    #      unset, since profile_root defaults to "profiles").
    env_profile_path = os.getenv("CDS_PROFILE_PATH")
    if env_profile_path:
        resolved_from_env = _resolve_profile_root(Path(env_profile_path).expanduser())
        if resolved_from_env:
            return resolved_from_env
        print(
            f"WARNING CDS_PROFILE_PATH={env_profile_path!r} did not resolve to a single profile; "
            "falling back to saved default.",
            file=sys.stderr,
        )

    saved_profile = load_saved_profile()
    if saved_profile:
        resolved_saved_profile = resolve_profile_path(saved_profile)
        if not Path(resolved_saved_profile).is_file():
            raise ValueError(
                f"Saved default profile '{saved_profile}' no longer resolves to a file "
                f"(looked for {resolved_saved_profile}). Run `cds use --clear` to remove it, "
                "or `cds use <profile>` to save a new default."
            )
        return resolved_saved_profile

    resolved_default = _resolve_profile_root(profile_root)
    if resolved_default:
        return resolved_default

    raise ValueError(
        "No profile specified. Either provide a profile argument, run `cds use <profile>` "
        "to save a default, or set CDS_PROFILE_PATH to a profile file or directory "
        "containing a single profile."
    )


def resolve_project_root(profile_path: str) -> Path:
    """
    Resolve a project root for output artifacts.

    The resolver walks up from the selected profile location and picks the first
    directory containing either pyproject.toml or .git. If no marker is found,
    it falls back to the current working directory.
    """
    start = Path(profile_path).resolve().parent
    for directory in [start, *start.parents]:
        if (directory / "pyproject.toml").exists() or (directory / ".git").exists():
            return directory
    return Path.cwd().resolve()


def resolve_env_file_path(profile_path: str) -> Path:
    """
    Resolve the default .env location for a profile.

    Preferred location is alongside profile.yaml. For backward compatibility,
    falls back to project-root .env when profile-local .env is absent.
    """
    profile_env = Path(profile_path).resolve().parent / ".env"
    if profile_env.exists():
        return profile_env

    project_env = resolve_project_root(profile_path) / ".env"
    return project_env


def list_profiles() -> list[str]:
    profile_root = get_profiles_root()
    profiles: list[str] = []

    if profile_root.is_file():
        profiles.append(str(profile_root))
        return profiles

    if not profile_root.exists():
        return profiles

    if (profile_root / "profile.yaml").exists():
        profiles.append(profile_root.name or ".")

    for directory in sorted(profile_root.iterdir()):
        if directory.is_dir() and (directory / "profile.yaml").exists():
            profiles.append(directory.name)

    return profiles


def list_modules() -> list[str]:
    module_root = get_modules_root()
    modules: list[str] = []

    if module_root.is_file():
        return [str(module_root)]

    if not module_root.exists():
        return modules

    for module_file in sorted(module_root.rglob("module.yaml")):
        try:
            modules.append(module_file.parent.relative_to(module_root).as_posix())
        except ValueError:
            modules.append(str(module_file.parent))

    return modules


def _add_profile_arg(subparser: argparse.ArgumentParser) -> None:
    action = subparser.add_argument(
        "profile",
        nargs="?",
        help=(
            "Profile to use. Accepts a profile name (e.g. local-dagster-postgres-superset), "
            "a path to a profile.yaml file, or a path to a profiles root directory. "
            "When omitted, resolution falls back in order to: CDS_PROFILE_PATH if set, "
            "then the default profile saved via `cds use <profile>`, then the single "
            "profile under profiles/ if there is exactly one. "
            "CDS_PROFILE_PATH accepts the same forms: a profile name, a profile file path, "
            "or a profiles root directory."
        ),
    )
    if argcomplete is not None:
        action.completer = profile_completer  # type: ignore[attr-defined]


def _add_environment_arg(subparser: argparse.ArgumentParser) -> None:
    subparser.add_argument(
        "--environment",
        "-e",
        default=None,
        help=(
            "Environment overlay to apply, e.g. dev or prod. Merges "
            "profiles/<name>/environments/<environment>.yaml over the base "
            "profile before resolving. Omit to use the base profile unchanged."
        ),
    )


def _collect_profile_env_vars(
    profile_path: str, environment: str | None = None
) -> tuple[list[str], set[str]]:
    """Return (sorted env var names, subset that are true secrets).

    Env vars declared under `spec.secrets.values` hold sensitive values (passwords,
    keys) and always default to a placeholder. Env vars only referenced elsewhere in
    the profile (e.g. `${CDS_ANALYTICS_DB_NAME}`) are typically non-sensitive
    identifiers like database/user names, so callers can fill in friendlier defaults
    for them instead of a placeholder.
    """
    if environment is not None:
        from .overlay import resolve_profile

        profile, _, diags = resolve_profile(profile_path, environment)
    else:
        profile, diags = load_yaml_file(Path(profile_path))
    if profile is None:
        error_messages = [d.format() for d in diags if d.level == "error"]
        raise ValueError("Could not load profile: " + "; ".join(error_messages or ["unknown error"]))

    secret_env_vars: set[str] = set()
    spec = profile.get("spec", {})
    values = spec.get("secrets", {}).get("values", {})
    if isinstance(values, dict):
        for secret_name, secret_def in values.items():
            if not isinstance(secret_def, dict):
                continue
            env_name = secret_def.get("env")
            if isinstance(env_name, str) and env_name:
                secret_env_vars.add(env_name)
            else:
                raise ValueError(f'Secret "{secret_name}" is missing a valid env name.')

    env_vars = set(secret_env_vars) | _find_profile_env_references(spec)

    if not env_vars:
        raise ValueError("No environment variables were found in the profile.")

    return sorted(env_vars), secret_env_vars


def _find_profile_env_references(value) -> set[str]:
    if isinstance(value, dict):
        references: set[str] = set()
        for nested in value.values():
            references.update(_find_profile_env_references(nested))
        return references
    if isinstance(value, list):
        references = set()
        for nested in value:
            references.update(_find_profile_env_references(nested))
        return references
    if isinstance(value, str):
        return set(re.findall(r"\$\{(CDS_[A-Z0-9_]+)\}", value))
    return set()
def _default_env_value(env_name: str, is_secret: bool) -> str:
    """Best-effort friendly default for a non-secret env var, else the change-me placeholder."""
    if not is_secret:
        # e.g. CDS_ANALYTICS_DB_NAME / CDS_ANALYTICS_DB_USER -> "analytics"
        match = re.match(r"^CDS_([A-Z0-9]+)_DB_(?:NAME|USER)$", env_name)
        if match:
            return match.group(1).lower()
    return "change-me"


def _atomic_write_text(path: Path, content: str, encoding: str = "utf-8") -> None:
    """Write `content` to `path` atomically via a temp file + os.replace.

    Avoids leaving a truncated/partial file behind if the process is
    interrupted mid-write, and avoids races between concurrent writers.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding=encoding) as tmp_file:
            tmp_file.write(content)
        os.replace(tmp_name, path)
    except OSError:
        with suppress(OSError):
            os.unlink(tmp_name)
        raise


def _write_env_file(
    output_path: Path,
    env_vars: list[str],
    secret_env_vars: set[str],
    profile_path: str,
    force: bool,
) -> None:
    if output_path.exists() and not force:
        raise FileExistsError(f"Refusing to overwrite existing file: {output_path}. Use --force to overwrite.")

    lines = [
        "# Generated by cds init",
        f"# Source profile: {profile_path}",
        "",
    ]
    lines.extend(
        f"{env_name}={_default_env_value(env_name, env_name in secret_env_vars)}" for env_name in env_vars
    )
    lines.append("")
    _atomic_write_text(output_path, "\n".join(lines))


def _cds_version() -> str:
    """Resolve the installed CDS CLI version."""
    try:
        return _package_version("composable-data-stack")
    except PackageNotFoundError:
        return "unknown"


def _completion_instructions(shell: str) -> str:
    """Return copy-pasteable shell setup instructions for cds tab-completion."""
    preamble = (
        "# cds does not modify your shell config automatically (same as kubectl, docker,\n"
        "# gh, and az completion). Copy the steps below into your shell yourself:"
    )
    if shell == "powershell":
        install_step = (
            "# 1. Install argcomplete (skip if already installed):\n"
            "python -m pip install argcomplete"
        )
        setup_step = (
            "# 2. Add to your PowerShell profile ($PROFILE), then restart your shell "
            "(or `. $PROFILE`):\n"
            "register-python-argcomplete --shell powershell cds | Out-String | Invoke-Expression"
        )
        return f"{preamble}\n\n{install_step}\n\n{setup_step}"

    install_step = (
        "# 1. Install argcomplete (skip if already installed):\n"
        "python3 -m pip install argcomplete"
    )
    if shell == "zsh":
        setup_step = (
            "# 2. Add to ~/.zshrc, then restart your shell (or `source ~/.zshrc`):\n"
            "autoload -U bashcompinit\n"
            "bashcompinit\n"
            'eval "$(register-python-argcomplete cds)"'
        )
    else:
        setup_step = (
            "# 2. Add to ~/.bashrc, then restart your shell (or `source ~/.bashrc`):\n"
            'eval "$(register-python-argcomplete cds)"'
        )
    return f"{preamble}\n\n{install_step}\n\n{setup_step}"


def _is_id_keyed_list(value: Any) -> bool:
    """True if every element is a mapping with a stable "id" key (e.g. spec.modules)."""
    return bool(value) and all(isinstance(item, dict) and "id" in item for item in value)


def _diff_values(path: str, a: Any, b: Any, changes: list[tuple[str, str, Any, Any]]) -> None:
    """
    Recursively compare two resolved profile values and append (path, kind, old,
    new) tuples to changes, kind is one of "added", "removed", "changed".

    Dicts are compared key-by-key. Lists are compared by id instead of position
    (matching cli.overlay's merge semantics, so reordering module entries alone
    is not reported as a change) only when *every* element on *both* sides is a
    mapping with a stable "id" key (e.g. spec.modules). Any list where at least
    one element on either side lacks an "id" falls back to whole-list equality
    comparison, so a heterogeneous list (some entries with "id", some without)
    is still reported in full instead of silently dropping the id-less entries.
    """
    if isinstance(a, dict) and isinstance(b, dict):
        for key in sorted(set(a) | set(b)):
            child_path = f"{path}.{key}" if path else key
            if key not in a:
                changes.append((child_path, "added", None, b[key]))
            elif key not in b:
                changes.append((child_path, "removed", a[key], None))
            else:
                _diff_values(child_path, a[key], b[key], changes)
        return

    if isinstance(a, list) and isinstance(b, list) and _is_id_keyed_list(a) and _is_id_keyed_list(b):
        a_by_id = {item["id"]: item for item in a if isinstance(item, dict) and "id" in item}
        b_by_id = {item["id"]: item for item in b if isinstance(item, dict) and "id" in item}
        for module_id in sorted(set(a_by_id) | set(b_by_id)):
            child_path = f"{path}[{module_id}]"
            if module_id not in a_by_id:
                changes.append((child_path, "added", None, b_by_id[module_id]))
            elif module_id not in b_by_id:
                changes.append((child_path, "removed", a_by_id[module_id], None))
            else:
                _diff_values(child_path, a_by_id[module_id], b_by_id[module_id], changes)
        return

    if a != b:
        changes.append((path, "changed", a, b))


def _unverifiable_image_finding(message: str) -> dict[str, Any]:
    return {
        "rule_id": "CDS-VER-004",
        "severity": "high",
        "module": "<profile>",
        "message": message,
        "path": "spec.modules",
        "value": None,
        "recommendation": [
            "Fix the plan/render errors so image verification can run.",
            "Re-run cds security --verify-images after fixing the profile.",
        ],
    }


def _run_image_verification(profile_path: str, environment: str | None) -> list[dict[str, Any]]:
    """
    Render the profile and verify service images against the CDS image policy.

    Verification runs in "full" mode: static supply-chain checks plus
    cosign-based signature/provenance verification (or the signed-images
    fixture when available for offline verification). Fails closed with a
    high-severity finding when verification was requested but cannot run.
    """
    try:
        profile, _, _ = resolve_profile(profile_path, environment)
        env_file = str(resolve_env_file_path(profile_path))
        plan, plan_diags = build_plan(profile_path, env_file=env_file, environment=environment)
        if has_errors(plan_diags) or plan is None:
            print_diagnostics(plan_diags)
            print("Cannot verify images because plan generation failed.")
            return [
                _unverifiable_image_finding(
                    "Image verification could not run because plan generation failed"
                )
            ]
        compose_yaml, render_diags = render_compose(plan, env_file=env_file)
        if has_errors(render_diags):
            print_diagnostics(render_diags)
            print("Cannot verify images because render failed.")
            return [
                _unverifiable_image_finding(
                    "Image verification could not run because rendering failed"
                )
            ]
        profile_class = infer_profile_class(profile) if profile is not None else "local"
        policy = load_policy_from_env(profile_class, mode_override="full")
        return verify_images(compose_yaml, policy, fixture=default_fixture_path())
    except Exception as e:
        print(Diagnostic(
            level="error",
            code="E095",
            message=f"Image verification failed unexpectedly: {e}",
            path="spec.modules",
        ).format(), file=sys.stderr)
        return [_unverifiable_image_finding(f"Image verification failed unexpectedly: {e}")]


def main() -> int:
    # Load .env file if it exists
    load_env_file()
    
    parser = argparse.ArgumentParser(
        prog="cds",
        description=(
            "Composable Data Stack (CDS): a compiler and CLI for declarative data "
            "platforms. Define reusable modules (orchestrators, warehouses, BI tools, "
            "caches, secrets providers) and wire them together in a profile; cds "
            "validates, plans, and renders the stack to Docker Compose."
        ),
    )
    parser.add_argument(
        "-v",
        "--version",
        action="version",
        version=f"%(prog)s {_cds_version()}",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate_parser = subparsers.add_parser("validate", help="Validate a profile")
    _add_profile_arg(validate_parser)
    _add_environment_arg(validate_parser)

    plan_parser = subparsers.add_parser("plan", help="Build a resolved plan from a profile")
    _add_profile_arg(plan_parser)
    _add_environment_arg(plan_parser)
    plan_parser.add_argument(
        "--output",
        "-o",
        help="Save plan to file (default: print to stdout)",
    )
    plan_parser.add_argument("--json", action="store_true", help="Output plan as JSON (default when printing to stdout)")

    render_parser = subparsers.add_parser(
        "render",
        help="Render docker compose from a profile or plan file",
    )
    render_parser.add_argument(
        "profile_or_plan",
        nargs="?",
        help="Profile path/identifier or path to saved plan file. Uses CDS_PROFILE_PATH if set.",
    )
    _add_environment_arg(render_parser)
    render_parser.add_argument(
        "--output",
        "-o",
        help="Output file path for rendered output (default: <project-root>/docker-compose.yml)",
    )

    up_parser = subparsers.add_parser(
        "up",
        help="Validate, plan, render, build, and run the profile with docker compose",
    )
    _add_profile_arg(up_parser)
    _add_environment_arg(up_parser)
    up_parser.add_argument(
        "--detach",
        "-d",
        action="store_true",
        help="Return as soon as the stack starts, skipping the live state view "
        "(docker compose always runs detached internally)",
    )
    up_parser.add_argument(
        "--no-build",
        action="store_true",
        help="Skip docker compose build before starting services",
    )
    up_parser.add_argument(
        "--log-file",
        help="Path to write docker compose build/up/logs output to "
        "(default: .cds/logs/up-<profile>-<timestamp>.log)",
    )
    up_parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT_SECONDS,
        help=f"Seconds to wait for services to settle before giving up "
        f"(default: {int(DEFAULT_TIMEOUT_SECONDS)}; ignored with --detach)",
    )
    up_parser.add_argument(
        "--no-color",
        action="store_true",
        help="Disable colored labels in the live state view",
    )

    test_parser = subparsers.add_parser(
        "test",
        help="One-shot smoke validation: validate, security, plan, and render",
    )
    _add_profile_arg(test_parser)
    _add_environment_arg(test_parser)
    test_parser.add_argument(
        "--reveal-secrets",
        action="store_true",
        help=(
            "Print full, unredacted values in security findings (e.g. secrets embedded in a DSN/URL). "
            "By default, values are redacted to avoid echoing real secrets to stdout/CI logs."
        ),
    )

    preflight_parser = subparsers.add_parser(
        "preflight",
        help="Check runtime prerequisites without starting the profile",
    )
    _add_profile_arg(preflight_parser)
    _add_environment_arg(preflight_parser)

    state_parser = subparsers.add_parser(
        "state",
        help="Show running service status grouped by health",
    )
    _add_profile_arg(state_parser)
    state_parser.add_argument(
        "--no-color",
        action="store_true",
        help="Disable colored health labels even on a color-capable terminal",
    )

    init_parser = subparsers.add_parser(
        "init",
        help="Initialize a .env file from profile secret definitions",
    )
    _add_profile_arg(init_parser)
    _add_environment_arg(init_parser)
    init_parser.add_argument(
        "--output",
        "-o",
        help="Output path for generated env file (default: <project-root>/.env)",
    )
    init_parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite output file if it already exists",
    )

    list_parser = subparsers.add_parser("list", help="List available profiles or modules")
    list_subparsers = list_parser.add_subparsers(dest="list_command", required=True)
    list_subparsers.add_parser("profiles", help="List available profiles")
    list_subparsers.add_parser("modules", help="List available module sources")
    list_subparsers.add_parser("images", help="List images from module templates and check for newer versions")

    security_parser = subparsers.add_parser("security", help="Run security validation on a profile")
    _add_profile_arg(security_parser)
    _add_environment_arg(security_parser)
    security_parser.add_argument(
        "--reveal-secrets",
        action="store_true",
        help=(
            "Print full, unredacted values in findings (e.g. secrets embedded in a DSN/URL). "
            "By default, values are redacted to avoid echoing real secrets to stdout/CI logs."
        ),
    )
    security_parser.add_argument(
        "--verify-images",
        action="store_true",
        help=(
            "Also verify OCI image signatures and build provenance against the CDS "
            "image policy. Uses cosign (keyless OIDC by default, CDS_COSIGN_KEY for "
            "key-managed) or the signed-images fixture (CDS_SIGNED_IMAGES_FIXTURE / "
            "tests/fixtures/signed-images.json) for offline verification."
        ),
    )

    diff_parser = subparsers.add_parser(
        "diff",
        help="Show effective configuration differences between two environment overlays",
    )
    _add_profile_arg(diff_parser)
    diff_parser.add_argument(
        "--from",
        dest="from_environment",
        required=True,
        help="Environment overlay to use as the baseline (e.g. dev).",
    )
    diff_parser.add_argument(
        "--to",
        dest="to_environment",
        required=True,
        help="Environment overlay to compare against the baseline (e.g. prod).",
    )

    use_parser = subparsers.add_parser(
        "use",
        help="Save (or show/clear) a default profile so it doesn't have to be passed on every command",
    )
    use_action = use_parser.add_argument(
        "profile",
        nargs="?",
        help="Profile name to save as the default. Omit to show the currently saved default.",
    )
    use_parser.add_argument(
        "--clear",
        action="store_true",
        help="Clear the saved default profile instead of setting one",
    )
    if argcomplete is not None:
        use_action.completer = profile_completer  # type: ignore[attr-defined]

    completion_parser = subparsers.add_parser(
        "completion",
        help="Print shell setup instructions for cds tab-completion",
    )
    completion_parser.add_argument(
        "shell",
        choices=["bash", "zsh", "powershell"],
        help="Shell to print setup instructions for",
    )

    if argcomplete is not None:
        argcomplete.autocomplete(parser)

    args = parser.parse_args()
    
    if args.command == "validate":
        try:
            profile_path = resolve_profile_path(args.profile)
        except ValueError as exc:
            print(f"ERROR {exc}")
            return 1

        diagnostics = validate_profile(profile_path, environment=args.environment)

        if diagnostics:
            error_count = sum(1 for d in diagnostics if d.level == "error")
            warning_count = sum(1 for d in diagnostics if d.level == "warning")

            for d in diagnostics:
                prefix = "ERROR" if d.level == "error" else "WARN"
                print(f"{prefix} {d.format()}\n")

            print(f"Validation completed with {error_count} error(s), {warning_count} warning(s).")
        else:
            print("Profile is valid.")

        return 1 if has_errors(diagnostics) else 0

    if args.command == "plan":
        try:
            profile_path = resolve_profile_path(args.profile)
        except ValueError as exc:
            print(f"ERROR {exc}")
            return 1

        diagnostics = validate_profile(profile_path, environment=args.environment)
        if has_errors(diagnostics):
            print_diagnostics(diagnostics)
            print("Cannot build plan because validation failed.")
            return 1

        env_file = str(resolve_env_file_path(profile_path))
        plan, plan_diags = build_plan(profile_path, env_file=env_file, environment=args.environment)
        all_diags = diagnostics + plan_diags

        if has_errors(all_diags):
            for d in all_diags:
                prefix = "ERROR" if d.level == "error" else "WARN"
                print(f"{prefix} {d.format()}\n")
            print("Plan generation failed.")
            return 1

        plan_json = json.dumps(plan, indent=2)
        
        if args.output:
            # Save plan to file
            output_file = Path(args.output)
            _atomic_write_text(output_file, plan_json)
            print(f"Plan saved to {args.output}")
        else:
            # Output to stdout
            print(plan_json)

        return 0

    if args.command == "render":
        # Determine if input is a plan file or profile
        profile_or_plan = args.profile_or_plan
        plan = None
        plan_path = None
        profile_path = None
        all_diags = []

        # Try to detect if it's a plan file
        is_plan_file = False
        if profile_or_plan:
            candidate_path = Path(profile_or_plan)
            if candidate_path.exists() and candidate_path.is_file():
                # Try to load as plan
                try:
                    plan_content = json.loads(candidate_path.read_text(encoding="utf-8"))
                    if isinstance(plan_content, dict) and plan_content.get("apiVersion") == "cds/v1alpha1":
                        is_plan_file = True
                        plan = plan_content
                        plan_path = candidate_path
                except (json.JSONDecodeError, OSError, UnicodeDecodeError):
                    pass

        if is_plan_file:
            if args.environment is not None:
                print(
                    "ERROR --environment is not supported when rendering a saved Plan file; "
                    "the environment overlay was already applied when the Plan was built."
                )
                return 1

            # Render from saved plan file
            if plan is None:
                print(f"ERROR Failed to load plan from {plan_path}")
                return 1

            output_path = args.output
            if output_path is None:
                # Use project root from plan's sourceProfile, or cwd
                source_profile = Path(plan.get("sourceProfile", "."))
                output_path = str(resolve_project_root(str(source_profile)) / "docker-compose.yml")

            env_file = str(resolve_env_file_path(str(source_profile)))
            compose_yaml, render_diags = render_compose(plan, output_path=output_path, env_file=env_file)
            all_diags = render_diags

            if has_errors(all_diags):
                print_diagnostics(all_diags)
                print("Render failed.")
                return 1

            print(f"Rendered compose file written to {output_path}")
            return 0
        else:
            # Render from profile (original behavior)
            try:
                profile_path = resolve_profile_path(profile_or_plan)
            except ValueError as exc:
                print(f"ERROR {exc}")
                return 1

            diagnostics = validate_profile(profile_path, environment=args.environment)
            if has_errors(diagnostics):
                print_diagnostics(diagnostics)
                print("Cannot render because validation failed.")
                return 1

            env_file = str(resolve_env_file_path(profile_path))
            plan, plan_diags = build_plan(profile_path, env_file=env_file, environment=args.environment)
            all_diags = diagnostics + plan_diags
            if has_errors(all_diags):
                print_diagnostics(all_diags)
                print("Cannot render because plan generation failed.")
                return 1

            output_path = args.output
            if output_path is None:
                output_path = str(resolve_project_root(profile_path) / "docker-compose.yml")

            compose_yaml, render_diags = render_compose(plan, output_path=output_path, env_file=env_file)
            all_diags = all_diags + render_diags

            if has_errors(all_diags):
                print_diagnostics(all_diags)
                print("Render failed.")
                return 1

            print(f"Rendered compose file written to {output_path}")

            return 0

    if args.command == "up":
        try:
            profile_path = resolve_profile_path(args.profile)
        except ValueError as exc:
            print(f"ERROR {exc}")
            return 1

        diagnostics = validate_profile(profile_path, environment=args.environment)
        if has_errors(diagnostics):
            print_diagnostics(diagnostics)
            print("Cannot start stack because validation failed.")
            return 1

        env_file = str(resolve_env_file_path(profile_path))
        plan, plan_diags = build_plan(profile_path, env_file=env_file, environment=args.environment)
        all_diags = diagnostics + plan_diags
        if has_errors(all_diags):
            print_diagnostics(all_diags)
            print("Cannot start stack because plan generation failed.")
            return 1

        output_path = str(resolve_project_root(profile_path) / "docker-compose.yml")
        compose_yaml, render_diags = render_compose(plan, output_path=output_path, env_file=env_file)
        all_diags = all_diags + render_diags
        if has_errors(all_diags):
            print_diagnostics(all_diags)
            print("Cannot start stack because render failed.")
            return 1

        print(f"Rendered compose file written to {output_path}")

        up_cmd = ["docker", "compose", "-f", output_path, "up", "--detach"]

        expected_service_count = None
        service_to_image: dict[str, str] = {}
        try:
            compose_doc = yaml.safe_load(compose_yaml) or {}
            services = compose_doc.get("services", {})
            expected_service_count = len(services)
            service_to_image = {
                name: definition["image"]
                for name, definition in services.items()
                if isinstance(definition, dict) and definition.get("image")
            }
        except yaml.YAMLError:
            pass

        if args.log_file:
            log_path = Path(args.log_file)
        else:
            log_path = default_log_path(Path(profile_path).parent.name)
        log_path.parent.mkdir(parents=True, exist_ok=True)

        log_tail_process = None
        settled = True
        try:
            with open(log_path, "a", encoding="utf-8") as log_file:
                if not args.no_build:
                    build_cmd = ["docker", "compose", "-f", output_path, "build"]
                    print(f"Running: {' '.join(build_cmd)}")
                    build_returncode = run_streamed(
                        build_cmd,
                        log_file,
                        group_by_image=True,
                        service_to_image=service_to_image,
                        use_color=not args.no_color,
                    )
                    if build_returncode != 0:
                        print(f"Build failed (exit {build_returncode}). See {log_path} for details.")
                        return build_returncode

                print(f"Running: {' '.join(up_cmd)}")
                if args.detach:
                    up_returncode = run_streamed(up_cmd, log_file, echo=args.detach)
                    if up_returncode != 0:
                        print(f"'docker compose up' failed (exit {up_returncode}). See {log_path} for details.")
                        return up_returncode
                    print(
                        f"Stack starting in the background. Run 'cds state' to check status; "
                        f"full output in {log_path}."
                    )
                    return 0

                print(
                    "Switching to the live state view; full docker compose output "
                    f"is being written to {log_path}."
                )
                # `docker compose up --detach` can itself block for a long time
                # waiting on healthcheck-gated `depends_on` dependencies before
                # it returns control, even with --detach. Run it in the
                # background so the live state view (driven by `docker compose
                # ps`, which is fast regardless) starts rendering immediately
                # instead of appearing only after `up` finishes.
                up_process = start_up_in_background(up_cmd, log_file)

                # Deferred until `up` finishes (see on_up_finished below):
                # starting this immediately would have it write container
                # logs to log_file at the same time `up`'s own subprocess is
                # still writing its transcript there, interleaving output
                # mid-line.
                def _begin_log_tail(_up_exit_code: int) -> None:
                    nonlocal log_tail_process
                    log_tail_process = start_log_tail(output_path, log_file)

                try:
                    use_rich = sys.stdout.isatty() and not args.no_color
                    if use_rich:
                        from rich.live import Live
                        from rich.text import Text

                        with Live(auto_refresh=True, refresh_per_second=4, vertical_overflow="visible") as live:
                            settled, _grouped = poll_state_until_settled(
                                output_path,
                                expected_service_count=expected_service_count,
                                timeout=args.timeout,
                                use_color=True,
                                redraw_fn=lambda text: live.update(
                                    Text.from_ansi(text),
                                ),
                                up_done_fn=up_process.poll,
                                on_up_finished=_begin_log_tail,
                            )
                    else:
                        settled, _grouped = poll_state_until_settled(
                            output_path,
                            expected_service_count=expected_service_count,
                            timeout=args.timeout,
                            use_color=(not args.no_color) and sys.stdout.isatty(),
                            up_done_fn=up_process.poll,
                            on_up_finished=_begin_log_tail,
                        )
                except KeyboardInterrupt:
                    # Don't wait on `up` here: it may still be blocked on a
                    # healthcheck for a long time, and we're no longer
                    # watching it once we've stopped polling, so blocking
                    # process exit on it would be surprising. The stack (and
                    # `up`) keep running in the background regardless.
                    print(
                        f"\nStopped watching; the stack keeps running. "
                        f"Docker output logged to {log_path}."
                    )
                    return 130

                while True:
                    try:
                        up_returncode = up_process.wait(timeout=0.5)
                        break
                    except subprocess.TimeoutExpired:
                        continue
                if up_returncode != 0:
                    print(f"'docker compose up' failed (exit {up_returncode}). See {log_path} for details.")
                    return up_returncode
        except KeyboardInterrupt:
            print(
                f"\nInterrupted. The stack keeps running; "
                f"Docker output logged to {log_path}."
            )
            return 130
        except FileNotFoundError:
            print("ERROR docker was not found. Install Docker and ensure it is on your PATH.")
            return 1
        finally:
            if log_tail_process is not None:
                stop_log_tail(log_tail_process)

        if not settled:
            print(
                f"\nStack did not settle within {args.timeout:.0f}s, or a service is unhealthy. "
                f"Run 'cds state' for the latest status; full output in {log_path}."
            )
            return 1

        print(f"\nStack is up. Full output in {log_path}.")
        return 0

    if args.command == "test":
        try:
            profile_path = resolve_profile_path(args.profile)
        except ValueError as exc:
            print(f"ERROR {exc}")
            return 1

        print(f"== cds test: {args.profile} ==\n")
        stages: list[tuple[str, str]] = []

        diagnostics = validate_profile(profile_path, environment=args.environment)
        validate_ok = not has_errors(diagnostics)
        stages.append(("validate", "PASS" if validate_ok else "FAIL"))
        if not validate_ok:
            print_diagnostics(diagnostics)

        # Plan and render are computed once here (rather than once more per
        # stage) so the "security" stage's "rendered-compose"-scoped rules
        # (e.g. CDS-SEC-070) can reuse the same plan/rendered Compose the
        # later "plan"/"render" stages report on, instead of planning and
        # rendering the same profile a second time internally.
        env_file = str(resolve_env_file_path(profile_path))
        plan = None
        plan_diags: list[Diagnostic] = []
        plan_ok = False
        compose_yaml = None
        render_diags: list[Diagnostic] = []
        render_ok = False
        if validate_ok:
            plan, plan_diags = build_plan(profile_path, env_file=env_file, environment=args.environment)
            plan_ok = not has_errors(diagnostics + plan_diags)
            if plan_ok:
                compose_yaml, render_diags = render_compose(plan, env_file=env_file)
                render_ok = not has_errors(render_diags)

        security_ok = False
        if validate_ok:
            try:
                findings, sec_diags = run_security_validation(
                    profile_path=Path(profile_path),
                    env_file=env_file,
                    environment=args.environment,
                    redact_values=not args.reveal_secrets,
                    precomputed_render=PrecomputedRender(
                        plan=plan if plan_ok else None,
                        rendered_compose_yaml=compose_yaml if render_ok else None,
                        failed=not (plan_ok and render_ok),
                    ),
                )
                for diag in sec_diags:
                    print(diag.format(), file=sys.stderr)
                for f in findings:
                    print(f"[{f['severity'].upper()}] {f['rule_id']} {f['message']}")
                security_ok = not any(f["severity"] == "high" for f in findings)
            except Exception as e:
                print(Diagnostic(
                    level="error",
                    code="E095",
                    message=f"Security validation failed unexpectedly: {e}",
                    path="spec.modules",
                ).format(), file=sys.stderr)
                security_ok = False
            stages.append(("security", "PASS" if security_ok else "FAIL"))
        else:
            stages.append(("security", "SKIP"))

        if validate_ok:
            if not plan_ok:
                print_diagnostics(plan_diags)
            stages.append(("plan", "PASS" if plan_ok else "FAIL"))
        else:
            stages.append(("plan", "SKIP"))

        if validate_ok and plan_ok:
            if not render_ok:
                print_diagnostics(render_diags)
            stages.append(("render", "PASS" if render_ok else "FAIL"))
        else:
            stages.append(("render", "SKIP"))

        print("\n-- Summary --")
        for name, status in stages:
            print(f"[{status}] {name}")

        all_passed = all(status == "PASS" for _, status in stages)
        print("\nAll stages passed." if all_passed else "\nOne or more stages failed.")
        return 0 if all_passed else 1

    if args.command == "preflight":
        try:
            profile_path = resolve_profile_path(args.profile)
        except ValueError as exc:
            print(f"ERROR {exc}")
            return 1

        diagnostics = validate_profile(profile_path, environment=args.environment)
        if has_errors(diagnostics):
            print_diagnostics(diagnostics)
            print("Cannot run preflight because validation failed.")
            return 1

        env_file = resolve_env_file_path(profile_path)
        plan, plan_diags = build_plan(profile_path, env_file=str(env_file), environment=args.environment)
        all_diags = diagnostics + plan_diags
        if has_errors(all_diags) or plan is None:
            print_diagnostics(all_diags)
            print("Cannot run preflight because plan generation failed.")
            return 1

        compose_yaml, render_diags = render_compose(
            plan,
            env_file=str(env_file),
        )
        all_diags += render_diags
        if has_errors(all_diags):
            print_diagnostics(all_diags)
            print("Cannot run preflight because render failed.")
            return 1

        checks = run_preflight(plan, compose_yaml, env_file)
        for check in checks:
            print(f"[{check.status}] {check.name}: {check.message}")

        if preflight_passed(checks):
            print("\nPreflight passed.")
            return 0

        print("\nPreflight failed.")
        return 1

    if args.command == "state":
        try:
            profile_path = resolve_profile_path(args.profile)
        except ValueError as exc:
            print(f"ERROR {exc}")
            return 1

        compose_path = resolve_project_root(profile_path) / "docker-compose.yml"
        if not compose_path.exists():
            print(f"ERROR {compose_path} not found. Run 'cds up' first.")
            return 1

        ps_cmd = ["docker", "compose", "-f", str(compose_path), "ps", "-a", "--format", "json"]
        try:
            ps_result = subprocess.run(ps_cmd, capture_output=True, text=True)  # nosec B603
        except FileNotFoundError:
            print("ERROR docker was not found. Install Docker and ensure it is on your PATH.")
            return 1

        if ps_result.returncode != 0:
            print(ps_result.stderr or "ERROR docker compose ps failed.")
            return ps_result.returncode

        services = parse_compose_ps_json(ps_result.stdout)
        grouped = group_services_by_health(services)
        use_color = (not args.no_color) and sys.stdout.isatty()
        print(format_state_output(grouped, use_color=use_color))
        return 0

    if args.command == "init":
        try:
            profile_path = resolve_profile_path(args.profile)
        except ValueError as exc:
            print(f"ERROR {exc}")
            return 1

        try:
            env_vars, secret_env_vars = _collect_profile_env_vars(profile_path, environment=args.environment)
        except ValueError as exc:
            print(f"ERROR {exc}")
            return 1

        output_path = Path(args.output) if args.output else (resolve_project_root(profile_path) / ".env")
        try:
            _write_env_file(output_path, env_vars, secret_env_vars, profile_path, args.force)
        except FileExistsError as exc:
            print(f"ERROR {exc}")
            return 1

        print(
            f"Initialized environment for {args.profile}.\n"
            "Please edit the values in the .env file, then run "
            f"`cds preflight {args.profile or Path(profile_path).parent.name}`."
        )
        return 0

    if args.command == "list":
        if args.list_command == "profiles":
            for profile_name in list_profiles():
                print(profile_name)
            return 0

        if args.list_command == "modules":
            for module_source in list_modules():
                print(module_source)
            return 0

        if args.list_command == "images":
            module_root = get_modules_root()
            images = collect_module_images(module_root)
            if not images:
                print("No images found in modules.")
                return 0

            update_cache: dict[tuple[str, str | None], dict[str, object]] = {}

            for image_entry in images:
                dockerfile = image_entry.get("dockerfile")
                cache_key = (image_entry["image"], str(dockerfile) if dockerfile is not None else None)
                if cache_key not in update_cache:
                    update_cache[cache_key] = check_image_update(
                        image_entry["image"],
                        dockerfile=dockerfile,
                    )
                info = update_cache[cache_key]
                status = info["status"]
                if status == "update-available":
                    print(
                        f"{image_entry['module']}::{image_entry['service']}: {info['image']} -> update available: {info['latest']}"
                    )
                elif status == "up-to-date":
                    print(
                        f"{image_entry['module']}::{image_entry['service']}: {info['image']} -> up to date"
                    )
                elif status == "local":
                    print(
                        f"{image_entry['module']}::{image_entry['service']}: {info['image']} -> local image, no remote check"
                    )
                elif status == "unsupported-registry":
                    print(
                        f"{image_entry['module']}::{image_entry['service']}: {info['image']} -> unsupported registry"
                    )
                elif status == "lookup-failed":
                    print(
                        f"{image_entry['module']}::{image_entry['service']}: {info['image']} -> registry lookup failed"
                    )
                else:
                    print(
                        f"{image_entry['module']}::{image_entry['service']}: {info['image']} -> unknown status"
                    )
            return 0

    if args.command == "security":
        try:
            profile_path = resolve_profile_path(args.profile)
        except ValueError as exc:
            print(f"ERROR {exc}")
            return 1

        diagnostics = validate_profile(profile_path, environment=args.environment)
        if has_errors(diagnostics):
            print_diagnostics(diagnostics)
            print("Cannot run security validation because profile validation failed.")
            return 1

        try:
            findings, diagnostics = run_security_validation(
                profile_path=Path(profile_path),
                env_file=str(resolve_env_file_path(profile_path)),
                environment=args.environment,
                redact_values=not args.reveal_secrets,
            )
        except Exception as e:
            print(Diagnostic(
                level="error",
                code="E095",
                message=f"Security validation failed unexpectedly: {e}",
                path="spec.modules",
            ).format(), file=sys.stderr)
            return 2

        for diag in diagnostics:
            print(diag.format(), file=sys.stderr)

        # A W096 warning means some rendered-compose-scoped rules (e.g.
        # CDS-SEC-070) were silently skipped because the profile couldn't be
        # planned/rendered. Unlike `cds test`, this command has no separate
        # plan/render stage to surface that failure, so treat it as a
        # non-zero exit rather than reporting "No security findings." as if
        # the scan were complete.
        render_scan_skipped = any(d.code == "W096" for d in diagnostics)

        if args.verify_images:
            image_findings = _run_image_verification(profile_path, args.environment)
            findings.extend(image_findings)
            findings.sort(key=lambda f: (
                SEVERITY_ORDER.get(f["severity"], 99),
                f["rule_id"],
                f["path"],
            ))

        if not findings:
            if render_scan_skipped:
                print("No security findings (some checks were skipped; see warnings above).")
                return 1
            print("No security findings.")
            return 0

        for f in findings:
            print(f"[{f['severity'].upper()}] {f['rule_id']} {f['message']}")
            print(f"  object: {f['path']}")
            print(f"  module: {f['module']}")
            if f["value"] is not None:
                print(f"  value: {f['value']}")
            for rec in f["recommendation"]:
                print(f"  fix: {rec}")
            print()

        return 1 if any(f["severity"] == "high" for f in findings) else 0

    if args.command == "use":
        if args.clear and args.profile:
            print(f"ERROR --clear cannot be combined with a profile argument ('{args.profile}').")
            return 1

        if args.clear:
            try:
                cleared = clear_saved_profile()
            except ConfigIOError as exc:
                print(f"ERROR {exc}")
                return 1
            if cleared:
                print(f"Cleared saved default profile ({get_config_path()}).")
            else:
                print("No saved default profile to clear.")
            return 0

        if not args.profile:
            saved_profile = load_saved_profile()
            if saved_profile:
                print(saved_profile)
            else:
                print("No default profile saved. Run `cds use <profile>` to set one.")
            return 0

        try:
            resolved = resolve_profile_path(args.profile)
        except ValueError as exc:
            print(f"ERROR {exc}")
            return 1

        if not Path(resolved).is_file():
            print(f"ERROR Profile '{args.profile}' could not be found (looked for {resolved}).")
            return 1

        # When CDS_PROFILE_PATH points directly at a single profile.yaml
        # file, resolve_profile_path() returns that file for *any* name
        # argument (there's no profiles directory to look names up under),
        # which would otherwise let `cds use <typo>` succeed silently and
        # save a bogus name as if it had been validated. Require the given
        # name to plausibly identify this profile before accepting it.
        profile_root = get_profiles_root()
        if profile_root.is_file() and Path(resolved).resolve() == profile_root.resolve():
            expected_names = {profile_root.stem, profile_root.parent.name}
            given_matches_file = Path(args.profile).resolve() == profile_root.resolve()
            if args.profile not in expected_names and not given_matches_file:
                print(
                    f"ERROR CDS_PROFILE_PATH points to a single profile file ({profile_root}); "
                    f"'{args.profile}' does not identify it. Pass the file path directly, "
                    f"or use '{profile_root.stem}' or '{profile_root.parent.name}'."
                )
                return 1

        try:
            config_path = save_profile(resolved)
        except ConfigIOError as exc:
            print(f"ERROR {exc}")
            return 1
        print(f"Saved default profile: {args.profile} (resolves to {resolved})")
        print(f"Stored in {config_path}")
        return 0

    if args.command == "completion":
        print(_completion_instructions(args.shell))
        return 0

    if args.command == "diff":
        try:
            profile_path = resolve_profile_path(args.profile)
        except ValueError as exc:
            print(f"ERROR {exc}")
            return 1

        from_profile, _, from_diags = resolve_profile(profile_path, args.from_environment)
        to_profile, _, to_diags = resolve_profile(profile_path, args.to_environment)
        all_diags = from_diags + to_diags

        if from_profile is None or to_profile is None:
            print_diagnostics(all_diags)
            print("Cannot diff because one or both environments failed to resolve.")
            return 1
        if all_diags:
            print_diagnostics(all_diags)

        # Profiles only ever hold secret *references* (e.g. "secrets.db_password"),
        # never resolved secret values, so diffing the resolved profile dicts
        # directly cannot leak a secret value.
        changes: list[tuple[str, str, Any, Any]] = []
        _diff_values("", from_profile, to_profile, changes)

        if not changes:
            print(f"No differences between environment '{args.from_environment}' and '{args.to_environment}'.")
            return 0

        print(f"Differences from '{args.from_environment}' to '{args.to_environment}':\n")
        for path, kind, old, new in sorted(changes, key=lambda c: c[0]):
            if kind == "added":
                print(f"  + {path}: {json.dumps(new)}")
            elif kind == "removed":
                print(f"  - {path}: {json.dumps(old)}")
            else:
                print(f"  ~ {path}: {json.dumps(old)} -> {json.dumps(new)}")

        return 0


    print("Base validation not shown here.")
    return 0



if __name__ == "__main__":
    sys.exit(main())
