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
        if candidate.is_file() or candidate.suffix == ".yaml":
            return str(candidate)

        if profile_root.is_file():
            return str(profile_root)

        candidate_by_name = profile_root / profile / "profile.yaml"
        candidate_file = profile_root / f"{profile}.yaml"

        if candidate_by_name.exists():
            return str(candidate_by_name)
        if candidate_file.exists():
            return str(candidate_file)
        return str(candidate_by_name)

    if profile_root.is_file():
        return str(profile_root)

    direct_profile = profile_root / "profile.yaml"
    if direct_profile.exists():
        return str(direct_profile)

    if profile_root.is_dir():
        subdirs = [
            directory
            for directory in sorted(profile_root.iterdir())
            if directory.is_dir() and (directory / "profile.yaml").exists()
        ]
        if len(subdirs) == 1:
            return str(subdirs[0] / "profile.yaml")

    raise ValueError(
        "No profile specified and CDS_PROFILE_PATH is not a specific profile file. "
        "Set CDS_PROFILE_PATH to a profile file or provide a profile identifier."
    )


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
    plan_parser.add_argument("--json", action="store_true", help="Output plan as JSON")

    render_parser = subparsers.add_parser("render", help="Render docker compose from a profile")
    _add_profile_arg(render_parser)
    render_parser.add_argument("--output", "-o", help="Output file path for rendered output")

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

        plan, plan_diags = build_plan(profile_path)
        all_diags = diagnostics + plan_diags

        if has_errors(all_diags):
            for d in all_diags:
                prefix = "ERROR" if d.level == "error" else "WARN"
                print(f"{prefix} {d.format()}\n")
            print("Plan generation failed.")
            return 1

        if args.json:
            print(json.dumps(plan, indent=2))
        else:
            print(json.dumps(plan, indent=2))

        return 0

    if args.command == "render":
        try:
            profile_path = resolve_profile_path(args.profile)
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

        compose_yaml, render_diags = render_compose(plan, output_path=args.output)
        all_diags = all_diags + render_diags

        if has_errors(all_diags):
            print_diagnostics(all_diags)
            print("Render failed.")
            return 1

        if args.output:
            print(f"Rendered compose file written to {args.output}")
        else:
            print(compose_yaml)

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

