#!/usr/bin/env python3
"""Fail-closed gate for a CRA release-evidence manifest.

See docs/cra-release-evidence-gate.md (issue #733) and
scripts/build_cra_release_evidence_manifest.py, which produces the
manifest this script reads.

**Disabled by default, on purpose.** If the manifest's
`conformityClaimEnabled` field is `false` or absent, this script does
nothing except print that the gate is inactive and exit `0`. CDS is not
currently a CRA in-scope manufacturer (docs/cra-scope-decision.md), so no
release should make a conformity or CE-marking claim, and this script must
never turn that lack of a claim into a build failure.

Only when a human has deliberately set `conformityClaimEnabled: true` in a
specific manifest (a manual edit, never done by automation -- see
scripts/build_cra_release_evidence_manifest.py's module docstring) does
this script enforce that every required evidence pointer, the product
classification, the conformity-assessment route, an "approved" EU
declaration of conformity reference, and at least one named approval are
all present. Missing any of these fails the check (exit `1`) and lists
exactly what's missing, so a conformity claim can never be made silently
or by accident.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_REQUIRED_EVIDENCE_REFS = [
    "scopeDecision",
    "riskAssessment",
    "technicalDocumentation",
    "sbom",
    "releaseInventory",
    "testResults",
    "vulnerabilityStatus",
    "supportInformation",
    "userInstructions",
    "signaturesProvenance",
]

_VALID_CLASSIFICATIONS = {"default", "important", "critical"}


def check_manifest(manifest: dict[str, object]) -> list[str]:
    """Return a list of human-readable problems; empty means the gate passes.

    Only called when the manifest has opted into strict checking (see
    `gate_is_enabled`); callers must check that first.
    """
    problems: list[str] = []

    evidence = manifest.get("evidence")
    if not isinstance(evidence, dict):
        problems.append("'evidence' is missing or not an object")
        evidence = {}
    for key in _REQUIRED_EVIDENCE_REFS:
        entry = evidence.get(key)
        if not isinstance(entry, dict) or not entry.get("ref"):
            problems.append(f"evidence.{key}.ref is missing or empty")

    classification = manifest.get("classification")
    if not isinstance(classification, dict):
        problems.append("'classification' is missing or not an object")
        classification = {}
    category = classification.get("category")
    if category not in _VALID_CLASSIFICATIONS:
        problems.append(
            "classification.category must be one of "
            f"{sorted(_VALID_CLASSIFICATIONS)}, got {category!r}"
        )
    if not classification.get("rationale"):
        problems.append("classification.rationale is missing or empty")

    route = manifest.get("conformityAssessmentRoute")
    if not isinstance(route, dict) or not route.get("route"):
        problems.append("conformityAssessmentRoute.route is missing or empty")

    declaration = manifest.get("declarationOfConformity")
    if not isinstance(declaration, dict):
        problems.append("'declarationOfConformity' is missing or not an object")
        declaration = {}
    if declaration.get("status") != "approved":
        problems.append(
            "declarationOfConformity.status must be 'approved' to make a "
            f"conformity claim, got {declaration.get('status')!r}"
        )
    if not declaration.get("ref"):
        problems.append("declarationOfConformity.ref is missing or empty")

    approvals = manifest.get("approvals")
    if not isinstance(approvals, list) or not approvals:
        problems.append("'approvals' must be a non-empty list")
    else:
        for index, approval in enumerate(approvals):
            if not isinstance(approval, dict):
                problems.append(f"approvals[{index}] must be an object")
                continue
            for field in ("name", "role", "date"):
                if not approval.get(field):
                    problems.append(f"approvals[{index}].{field} is missing or empty")

    return problems


def gate_is_enabled(manifest: dict[str, object]) -> bool:
    return bool(manifest.get("conformityClaimEnabled"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path, help="Path to a CRA release-evidence manifest JSON file")
    args = parser.parse_args()

    if not args.manifest.is_file():
        print(f"::error::manifest not found: {args.manifest}", file=sys.stderr)
        return 1

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))

    if not gate_is_enabled(manifest):
        print(
            "CRA release-evidence gate: disabled (conformityClaimEnabled is "
            "false/absent). No conformity or CE-marking claim is made for "
            "this release. See docs/cra-release-evidence-gate.md#6-release-gate."
        )
        return 0

    problems = check_manifest(manifest)
    if problems:
        print(
            "::error::CRA release-evidence gate: conformityClaimEnabled is "
            "true but required evidence is incomplete:",
            file=sys.stderr,
        )
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1

    print("CRA release-evidence gate: all required evidence present.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
