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
from .security import run_security_validation
from .state import format_state_output, group_services_by_health, parse_compose_ps_json
from .up_runner import (
    DEFAULT_TIMEOUT_SECONDS,
    default_log_path,
    poll_state_until_settled,
    run_streamed,
    start_log_tail,
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
    root = os.getenv("CDS_PROFILE_PATH") or "profiles"
    return Path(root).expanduser()


def get_modules_root() -> Path:
    root = os.getenv("CDS_MODULE_PATH") or "modules"
    return Path(root).expanduser()


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
        default_root = Path("profiles")
        if default_root.resolve() != profile_root.resolve():
            default_by_name = default_root / profile / "profile.yaml"
            default_by_file = default_root / f"{profile}.yaml"
            if default_by_name.exists():
                return str(default_by_name.resolve())
            if default_by_file.exists():
                return str(default_by_file.resolve())

        return str(candidate_by_name.resolve())

    # No profile argument provided, use CDS_PROFILE_PATH
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

    # CDS_PROFILE_PATH may be set to a bare profile name rather than a path.
    # Try resolving it as a name under the default profiles/ directory.
    default_root = Path("profiles")
    if default_root.resolve() != profile_root.resolve():
        name_candidate = default_root / profile_root.name / "profile.yaml"
        if name_candidate.exists():
            return str(name_candidate.resolve())

    raise ValueError(
        "No profile specified. Either provide a profile argument or set CDS_PROFILE_PATH "
        "to a profile file or directory containing a single profile."
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
            "When omitted, CDS_PROFILE_PATH is used. "
            "CDS_PROFILE_PATH accepts the same forms: a profile name, a profile file path, "
            "or a profiles root directory. "
            "If neither is provided and only one profile exists under profiles/, "
            "it is selected automatically."
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
                if not args.detach:
                    print(
                        "Switching to the live state view; full docker compose output "
                        f"is being written to {log_path}."
                    )
                up_returncode = run_streamed(up_cmd, log_file, echo=args.detach)
                if up_returncode != 0:
                    print(f"'docker compose up' failed (exit {up_returncode}). See {log_path} for details.")
                    return up_returncode

                if args.detach:
                    print(
                        f"Stack starting in the background. Run 'cds state' to check status; "
                        f"full output in {log_path}."
                    )
                    return 0

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
                            )
                    else:
                        settled, _grouped = poll_state_until_settled(
                            output_path,
                            expected_service_count=expected_service_count,
                            timeout=args.timeout,
                            use_color=(not args.no_color) and sys.stdout.isatty(),
                        )
                except KeyboardInterrupt:
                    print(
                        f"\nStopped watching; the stack keeps running. "
                        f"Docker output logged to {log_path}."
                    )
                    return 130
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

        security_ok = False
        if validate_ok:
            try:
                findings, sec_diags = run_security_validation(
                    profile_path=Path(profile_path),
                    env_file=str(resolve_env_file_path(profile_path)),
                    environment=args.environment,
                    redact_values=not args.reveal_secrets,
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

        env_file = str(resolve_env_file_path(profile_path))
        plan = None
        plan_ok = False
        if validate_ok:
            plan, plan_diags = build_plan(profile_path, env_file=env_file, environment=args.environment)
            plan_ok = not has_errors(diagnostics + plan_diags)
            if not plan_ok:
                print_diagnostics(plan_diags)
            stages.append(("plan", "PASS" if plan_ok else "FAIL"))
        else:
            stages.append(("plan", "SKIP"))

        render_ok = False
        if validate_ok and plan_ok:
            _, render_diags = render_compose(plan, env_file=env_file)
            render_ok = not has_errors(render_diags)
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

        if not findings:
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
