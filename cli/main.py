# cli/main.py
from __future__ import annotations

import argparse
import json
import os
import subprocess  # nosec B404
import sys
from pathlib import Path

try:
    import argcomplete  # type: ignore
except ImportError:
    argcomplete = None

from .validator import has_errors, validate_profile
from .planner import build_plan
from .renderer import render_compose
from .image_updates import collect_module_images, check_image_update
from .security import run_security_validation
from .loader import load_yaml_file


def load_env_file(env_file: str = ".env") -> None:
    """Load environment variables from a .env file."""
    env_path = Path(env_file)
    if not env_path.exists():
        return
    
    with open(env_path) as f:
        for line in f:
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
    root = os.getenv("CDS_PROFILE_PATH", "profiles")
    return Path(root).expanduser()


def get_modules_root() -> Path:
    root = os.getenv("CDS_MODULE_PATH", "modules")
    return Path(root).expanduser()


def resolve_profile_path(profile: str | None) -> str:
    profile_root = get_profiles_root()
    
    if profile:
        candidate = Path(profile)
        if candidate.is_file():
            return str(candidate.resolve())
        
        if candidate.suffix == ".yaml":
            return str(candidate.resolve())

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


def _collect_profile_env_vars(profile_path: str) -> list[str]:
    profile, diags = load_yaml_file(Path(profile_path))
    if profile is None:
        error_messages = [d.format() for d in diags if d.level == "error"]
        raise ValueError("Could not load profile: " + "; ".join(error_messages or ["unknown error"]))

    values = profile.get("spec", {}).get("secrets", {}).get("values", {})
    if not isinstance(values, dict) or not values:
        raise ValueError("Profile has no spec.secrets.values entries to initialize .env from.")

    env_vars: set[str] = set()
    for secret_name, secret_def in values.items():
        if not isinstance(secret_def, dict):
            continue
        env_name = secret_def.get("env")
        if isinstance(env_name, str) and env_name:
            env_vars.add(env_name)
        else:
            raise ValueError(f'Secret "{secret_name}" is missing a valid env name.')

    if not env_vars:
        raise ValueError("No environment variables were found in spec.secrets.values.")

    return sorted(env_vars)


def _write_env_file(output_path: Path, env_vars: list[str], profile_path: str, force: bool) -> None:
    if output_path.exists() and not force:
        raise FileExistsError(f"Refusing to overwrite existing file: {output_path}. Use --force to overwrite.")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Generated by cds init",
        f"# Source profile: {profile_path}",
        "",
    ]
    lines.extend([f"{env_name}=change-me" for env_name in env_vars])
    lines.append("")
    output_path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    # Load .env file if it exists
    load_env_file()
    
    parser = argparse.ArgumentParser(prog="cds")
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate_parser = subparsers.add_parser("validate", help="Validate a profile")
    _add_profile_arg(validate_parser)

    plan_parser = subparsers.add_parser("plan", help="Build a resolved plan from a profile")
    _add_profile_arg(plan_parser)
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
    up_parser.add_argument(
        "--detach",
        "-d",
        action="store_true",
        help="Run containers in the background (passed through to docker compose up)",
    )
    up_parser.add_argument(
        "--no-build",
        action="store_true",
        help="Skip docker compose build before starting services",
    )

    test_parser = subparsers.add_parser(
        "test",
        help="One-shot smoke validation: validate, security, plan, and render",
    )
    _add_profile_arg(test_parser)

    init_parser = subparsers.add_parser(
        "init",
        help="Initialize a .env file from profile secret definitions",
    )
    _add_profile_arg(init_parser)
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

    if argcomplete is not None:
        argcomplete.autocomplete(parser)

    args = parser.parse_args()
    
    if args.command == "validate":
        try:
            profile_path = resolve_profile_path(args.profile)
        except ValueError as exc:
            print(f"ERROR {exc}")
            return 1

        diagnostics = validate_profile(profile_path)

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

        diagnostics = validate_profile(profile_path)
        if has_errors(diagnostics):
            print_diagnostics(diagnostics)
            print("Cannot build plan because validation failed.")
            return 1

        env_file = str(resolve_env_file_path(profile_path))
        plan, plan_diags = build_plan(profile_path, env_file=env_file)
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
            output_file.parent.mkdir(parents=True, exist_ok=True)
            output_file.write_text(plan_json)
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
                    plan_content = json.loads(candidate_path.read_text())
                    if isinstance(plan_content, dict) and plan_content.get("apiVersion") == "cds/v1alpha1":
                        is_plan_file = True
                        plan = plan_content
                        plan_path = candidate_path
                except (json.JSONDecodeError, IOError):
                    pass

        if is_plan_file:
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

            diagnostics = validate_profile(profile_path)
            if has_errors(diagnostics):
                print_diagnostics(diagnostics)
                print("Cannot render because validation failed.")
                return 1

            env_file = str(resolve_env_file_path(profile_path))
            plan, plan_diags = build_plan(profile_path, env_file=env_file)
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

        diagnostics = validate_profile(profile_path)
        if has_errors(diagnostics):
            print_diagnostics(diagnostics)
            print("Cannot start stack because validation failed.")
            return 1

        env_file = str(resolve_env_file_path(profile_path))
        plan, plan_diags = build_plan(profile_path, env_file=env_file)
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

        up_cmd = ["docker", "compose", "-f", output_path, "up"]
        if args.detach:
            up_cmd.append("--detach")

        try:
            if not args.no_build:
                build_cmd = ["docker", "compose", "-f", output_path, "build"]
                print(f"Running: {' '.join(build_cmd)}")
                # Fixed argv list, not a shell string; no user input concatenated in.
                build_result = subprocess.run(build_cmd)  # nosec B603
                if build_result.returncode != 0:
                    return build_result.returncode

            print(f"Running: {' '.join(up_cmd)}")
            # Fixed argv list, not a shell string; no user input concatenated in.
            up_result = subprocess.run(up_cmd)  # nosec B603
        except FileNotFoundError:
            print("ERROR docker was not found. Install Docker and ensure it is on your PATH.")
            return 1

        return up_result.returncode

    if args.command == "test":
        try:
            profile_path = resolve_profile_path(args.profile)
        except ValueError as exc:
            print(f"ERROR {exc}")
            return 1

        print(f"== cds test: {args.profile} ==\n")
        stages: list[tuple[str, str]] = []

        diagnostics = validate_profile(profile_path)
        validate_ok = not has_errors(diagnostics)
        stages.append(("validate", "PASS" if validate_ok else "FAIL"))
        if not validate_ok:
            print_diagnostics(diagnostics)

        security_ok = False
        if validate_ok:
            repo_root = Path(__file__).resolve().parents[1]
            rule_schema_path = repo_root / "security" / "rule-schema.json"
            rule_set_path = repo_root / "security" / "rule-set.json"
            try:
                findings, sec_diags = run_security_validation(
                    profile_path=Path(profile_path),
                    rule_schema_path=rule_schema_path,
                    rule_set_path=rule_set_path,
                    env_file=str(resolve_env_file_path(profile_path)),
                )
                for diag in sec_diags:
                    print(diag.format(), file=sys.stderr)
                for f in findings:
                    print(f"[{f['severity'].upper()}] {f['rule_id']} {f['message']}")
                security_ok = not any(f["severity"] == "high" for f in findings)
            except Exception as e:
                print(str(e), file=sys.stderr)
                security_ok = False
            stages.append(("security", "PASS" if security_ok else "FAIL"))
        else:
            stages.append(("security", "SKIP"))

        env_file = str(resolve_env_file_path(profile_path))
        plan = None
        plan_ok = False
        if validate_ok:
            plan, plan_diags = build_plan(profile_path, env_file=env_file)
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

    if args.command == "init":
        try:
            profile_path = resolve_profile_path(args.profile)
        except ValueError as exc:
            print(f"ERROR {exc}")
            return 1

        try:
            env_vars = _collect_profile_env_vars(profile_path)
        except ValueError as exc:
            print(f"ERROR {exc}")
            return 1

        output_path = Path(args.output) if args.output else (resolve_project_root(profile_path) / ".env")
        try:
            _write_env_file(output_path, env_vars, profile_path, args.force)
        except FileExistsError as exc:
            print(f"ERROR {exc}")
            return 1

        print(f"Initialized environment for {args.profile}.\nPlease edit the values to set your secrets in the .env file before running any commands.")
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

        diagnostics = validate_profile(profile_path)
        if has_errors(diagnostics):
            print_diagnostics(diagnostics)
            print("Cannot run security validation because profile validation failed.")
            return 1

        repo_root = Path(__file__).resolve().parents[1]
        rule_schema_path = repo_root / "security" / "rule-schema.json"
        rule_set_path = repo_root / "security" / "rule-set.json"

        try:
            findings, diagnostics = run_security_validation(
                profile_path=Path(profile_path),
                rule_schema_path=rule_schema_path,
                rule_set_path=rule_set_path,
                env_file=str(resolve_env_file_path(profile_path)),
            )
        except Exception as e:
            print(str(e), file=sys.stderr)
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

    print("Base validation not shown here.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
