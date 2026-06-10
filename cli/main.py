# cli/main.py
from __future__ import annotations

import argparse
import sys

from .validator import has_errors, validate_profile


def main() -> int:
    parser = argparse.ArgumentParser(prog="cds")
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate_parser = subparsers.add_parser("validate", help="Validate a profile")
    validate_parser.add_argument("profile", help="Path to profile.yaml")

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

    return 0


if __name__ == "__main__":
    sys.exit(main())
