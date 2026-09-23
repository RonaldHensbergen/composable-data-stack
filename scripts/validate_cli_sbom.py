#!/usr/bin/env python3
"""Validate a CycloneDX SBOM for the CDS CLI distribution.

Used by `.github/workflows/build-python-package.yml` after the
"Generate CLI SBOM" step (`cyclonedx-py environment ...`) produces the SBOM
for a freshly-installed wheel, so a malformed or incomplete SBOM fails the
build instead of being silently published. Also runnable locally:
`python scripts/validate_cli_sbom.py --sbom cli-sbom.json`.

Validates, independently of any particular SBOM *generator*:

- the file is well-formed CycloneDX JSON,
- its root/metadata component identifies CDS by name and declared version,
- every directly-declared runtime dependency in `pyproject.toml` appears as
  a component somewhere in the SBOM.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import tomllib
from pathlib import Path

from packaging.requirements import InvalidRequirement, Requirement

DEFAULT_PROJECT_NAME = "composable-data-stack"

_NORMALIZE_RE = re.compile(r"[-_.]+")


def normalize_name(name: str) -> str:
    """Normalize a distribution name per PEP 503 for case/separator-insensitive comparison."""
    return _NORMALIZE_RE.sub("-", name).lower()


def pyproject_project(pyproject_path: Path) -> dict[str, object]:
    data = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    try:
        return data["project"]
    except KeyError as exc:
        raise SystemExit(f"{pyproject_path} is missing [project]: {exc}") from exc


def direct_dependency_names(pyproject_path: Path) -> list[str]:
    """Return the distribution names declared under `[project].dependencies`."""
    project = pyproject_project(pyproject_path)
    names: list[str] = []
    for requirement_str in project.get("dependencies", []):
        try:
            requirement = Requirement(requirement_str)
        except InvalidRequirement as exc:
            raise SystemExit(
                f"Unparseable dependency requirement '{requirement_str}' in {pyproject_path}: {exc}"
            ) from exc
        names.append(requirement.name)
    return names


def load_sbom(sbom_path: Path) -> dict[str, object]:
    try:
        return json.loads(sbom_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"{sbom_path} is not valid JSON: {exc}") from exc


def validate_sbom(
    sbom: dict[str, object],
    *,
    expected_name: str,
    expected_version: str,
    required_dependency_names: list[str],
) -> list[str]:
    """Return a list of problems with the SBOM; empty means it is valid."""
    problems: list[str] = []

    if sbom.get("bomFormat") != "CycloneDX":
        problems.append(
            f"bomFormat is {sbom.get('bomFormat')!r}, expected 'CycloneDX'"
        )

    if not sbom.get("specVersion"):
        problems.append("specVersion is missing")

    root_component = (sbom.get("metadata") or {}).get("component")
    if not root_component:
        problems.append(
            "metadata.component is missing; the SBOM does not identify a root product component"
        )
    else:
        actual_name = root_component.get("name", "")
        if normalize_name(actual_name) != normalize_name(expected_name):
            problems.append(
                f"metadata.component.name is {actual_name!r}, expected {expected_name!r}"
            )
        actual_version = root_component.get("version", "")
        if actual_version != expected_version:
            problems.append(
                f"metadata.component.version is {actual_version!r}, expected {expected_version!r}"
            )

    component_names = {
        normalize_name(component.get("name", ""))
        for component in sbom.get("components", [])
        if isinstance(component, dict)
    }
    missing = [
        dependency
        for dependency in required_dependency_names
        if normalize_name(dependency) not in component_names
    ]
    if missing:
        problems.append(
            "direct runtime dependencies missing from SBOM components: " + ", ".join(sorted(missing))
        )

    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sbom", required=True, type=Path, help="Path to the CycloneDX SBOM JSON file")
    parser.add_argument(
        "--pyproject",
        type=Path,
        default=Path(__file__).resolve().parent.parent / "pyproject.toml",
        help="Path to pyproject.toml (default: repository root)",
    )
    parser.add_argument(
        "--name",
        default=None,
        help=f"Expected root component name (default: read from pyproject.toml, normally {DEFAULT_PROJECT_NAME!r})",
    )
    parser.add_argument(
        "--version",
        default=None,
        help="Expected root component version (default: read from pyproject.toml)",
    )
    args = parser.parse_args()

    project = pyproject_project(args.pyproject)
    expected_name = args.name or project.get("name", DEFAULT_PROJECT_NAME)
    expected_version = args.version or project.get("version", "")
    required_dependency_names = direct_dependency_names(args.pyproject)

    sbom = load_sbom(args.sbom)
    problems = validate_sbom(
        sbom,
        expected_name=expected_name,
        expected_version=expected_version,
        required_dependency_names=required_dependency_names,
    )

    if problems:
        print(f"{args.sbom} failed SBOM validation:", file=sys.stderr)
        for problem in problems:
            print(f"::error::{problem}", file=sys.stderr)
        return 1

    print(
        f"OK: {args.sbom} is a valid CycloneDX SBOM for {expected_name} {expected_version} "
        f"with {len(required_dependency_names)} direct runtime dependencies present."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
