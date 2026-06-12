# cli/main.py
from __future__ import annotations

import argparse
import json
import sys

from .validator import has_errors, validate_profile
from .planner import build_plan
from .renderer import render_compose


def print_diagnostics(diagnostics) -> None:
    for d in diagnostics:
        prefix = "ERROR" if d.level == "error" else "WARN"
        print(f"{prefix} {d.format()}\n")
        
def main() -> int:
    parser = argparse.ArgumentParser(prog="cds")
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate_parser = subparsers.add_parser("validate", help="Validate a profile")
    validate_parser.add_argument("profile", help="Path to profile.yaml")

    plan_parser = subparsers.add_parser("plan", help="Build a resolved plan from a profile")
    plan_parser.add_argument("profile", help="Path to profile.yaml")
    plan_parser.add_argument("--json", action="store_true", help="Output plan as JSON")

    render_parser = subparsers.add_parser("render", help="Render docker compose from a profile")
    render_parser.add_argument("profile", help="Path to profile.yaml")
    render_parser.add_argument("--output", "-o", help="Output file path for rendered output")

    args = parser.parse_args()

    if args.command == "validate":
        diagnostics = validate_profile(args.profile)

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
        diagnostics = validate_profile(args.profile)
        if has_errors(diagnostics):
            for d in diagnostics:
                prefix = "ERROR" if d.level == "error" else "WARN"
                print(f"{prefix} {d.format()}\n")
            print("Cannot build plan because validation failed.")
            return 1

        plan, plan_diags = build_plan(args.profile)
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
        diagnostics = validate_profile(args.profile)
        if has_errors(diagnostics):
            print_diagnostics(diagnostics)
            print("Cannot render because validation failed.")
            return 1

        plan, plan_diags = build_plan(args.profile)
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

    return 0


if __name__ == "__main__":
    sys.exit(main())
