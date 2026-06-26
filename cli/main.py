# cli/main.py
from __future__ import annotations

import argparse
import json
import os
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
            modules.append(str(module_file.parent.relative_to(module_root)))
        except ValueError:
            modules.append(str(module_file.parent))

    return modules


def _add_profile_arg(subparser: argparse.ArgumentParser) -> None:
    action = subparser.add_argument(
        "profile",
        nargs="?",
        help="Profile path or identifier. Uses CDS_PROFILE_PATH if set.",
    )
    if argcomplete is not None:
        action.completer = profile_completer  # type: ignore[attr-defined]


def main() -> int:
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
            print(f"ERROR: {exc}")
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

        plan, plan_diags = build_plan(profile_path)
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

            compose_yaml, render_diags = render_compose(plan, output_path=output_path)
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

            plan, plan_diags = build_plan(profile_path)
            all_diags = diagnostics + plan_diags
            if has_errors(all_diags):
                print_diagnostics(all_diags)
                print("Cannot render because plan generation failed.")
                return 1

            output_path = args.output
            if output_path is None:
                output_path = str(resolve_project_root(profile_path) / "docker-compose.yml")

            compose_yaml, render_diags = render_compose(plan, output_path=output_path)
            all_diags = all_diags + render_diags

            if has_errors(all_diags):
                print_diagnostics(all_diags)
                print("Render failed.")
                return 1

            print(f"Rendered compose file written to {output_path}")

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

            for image_entry in images:
                info = check_image_update(
                    image_entry["image"],
                    dockerfile=image_entry.get("dockerfile"),
                )
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
                env_file=None,
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

