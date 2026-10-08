#!/usr/bin/env python3
"""Build the CRA release-evidence manifest for a CLI release.

Used optionally by `.github/workflows/release.yml` after the SBOM and
release-inventory evidence already described in RELEASE.md are produced.
See docs/cra-release-evidence-gate.md (issue #733) for the manifest's
purpose and field reference.

This manifest only ever *references* evidence that already exists
elsewhere (docs, the SBOM, the release inventory, CI run URLs) -- it does
not duplicate or re-derive it, consistent with
scripts/generate_release_inventory.py's "pointer, not copy" pattern.

The manifest always sets `conformityClaimEnabled: false` and leaves
`approvals` empty. No automation in this repository ever flips either of
those fields: CDS is not currently a CRA in-scope manufacturer (see
docs/cra-scope-decision.md), so no release makes a conformity or CE-marking
claim. A human would have to deliberately edit the generated file to
enable scripts/check_cra_release_gate.py's strict checks for a specific
release, after a real conformity-assessment process. See
docs/cra-release-evidence-gate.md#6-release-gate.
"""
from __future__ import annotations

import argparse
import json
import tomllib
from pathlib import Path


def pyproject_name_and_version(pyproject_path: Path) -> tuple[str, str]:
    data = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    project = data["project"]
    return project["name"], project["version"]


_REPO_URL = "https://github.com/RonaldHensbergen/composable-data-stack"


def _repo_doc(commit_sha: str, path: str) -> dict[str, str]:
    """Evidence pointer to a repository file, plus a permalink pinned to the release commit."""
    return {"ref": path, "url": f"{_REPO_URL}/blob/{commit_sha}/{path}"}


def build_manifest(
    *,
    name: str,
    version: str,
    commit_sha: str,
    source_ref: str,
    sbom_file: str,
    release_inventory_file: str,
    generated_at: str,
    generated_by: str,
) -> dict[str, object]:
    commit_url = f"{_REPO_URL}/commit/{commit_sha}/checks"
    return {
        "schemaVersion": 1,
        "product": {"name": name, "version": version},
        "sourceCommit": commit_sha,
        "sourceRef": source_ref,
        "evidence": {
            "scopeDecision": _repo_doc(commit_sha, "docs/cra-scope-decision.md"),
            "riskAssessment": _repo_doc(commit_sha, "docs/cra-risk-assessment.md"),
            "technicalDocumentation": _repo_doc(commit_sha, "docs/cra-technical-documentation.md"),
            "sbom": {"ref": sbom_file},
            "releaseInventory": {"ref": release_inventory_file},
            "testResults": {
                "ref": commit_url,
                "note": "CI run(s) for this commit; full unit-test suite, Bandit, pip-audit (.github/workflows/ci.yml).",
            },
            "vulnerabilityStatus": {
                **_repo_doc(commit_sha, "docs/image-scanning.md"),
                "note": "Runtime images are rescanned daily and on every change; status is not a static per-release snapshot.",
            },
            "supportInformation": _repo_doc(commit_sha, "docs/security-support-policy.md"),
            "userInstructions": _repo_doc(commit_sha, "README.md"),
            "signaturesProvenance": _repo_doc(commit_sha, "docs/image-signing.md"),
        },
        "classification": {
            "category": "default",
            "rationale": (
                "CDS is a general-purpose declarative-config compiler/CLI; it "
                "does not itself implement identity/access management, VPN, "
                "SIEM, firewall, boot-manager, PKI, operating-system, network-"
                "device, or hypervisor functionality (Annex III), nor hardware "
                "security module/secure-element functionality (Annex IV). See "
                "docs/cra-release-evidence-gate.md#2-product-classification-review "
                "for the full item-by-item rationale."
            ),
            "annexesReviewed": ["Annex III", "Annex IV"],
        },
        "conformityAssessmentRoute": {
            "route": "Annex VIII Part I -- internal production control (Module A), applicable to default-category products",
            "escalationConditions": [
                "Product classification above changes from 'default' to "
                "'important' or 'critical' (see docs/cra-release-evidence-gate.md#2-product-classification-review).",
                "Any trigger in docs/cra-scope-decision.md#mandatory-reassessment-triggers occurs.",
            ],
        },
        "declarationOfConformity": {"status": "not-applicable", "ref": None},
        "approvals": [],
        "conformityClaimEnabled": False,
        "generatedAt": generated_at,
        "generatedBy": generated_by,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit-sha", required=True, help="Source commit SHA this release was built from")
    parser.add_argument("--source-ref", required=True, help="Git ref (tag) this release was built from")
    parser.add_argument("--sbom-file", default="cli-sbom.cyclonedx.json", help="Filename of the CLI SBOM for this release")
    parser.add_argument(
        "--release-inventory-file",
        default="release-inventory.json",
        help="Filename of the release artifact inventory for this release",
    )
    parser.add_argument("--generated-at", required=True, help="UTC ISO-8601 timestamp of generation")
    parser.add_argument("--generated-by", required=True, help="URL of the workflow run that generated this manifest")
    parser.add_argument(
        "--pyproject",
        type=Path,
        default=Path(__file__).resolve().parent.parent / "pyproject.toml",
        help="Path to pyproject.toml (default: repository root)",
    )
    parser.add_argument("--output", required=True, type=Path, help="Path to write the manifest JSON to")
    args = parser.parse_args()

    name, version = pyproject_name_and_version(args.pyproject)
    manifest = build_manifest(
        name=name,
        version=version,
        commit_sha=args.commit_sha,
        source_ref=args.source_ref,
        sbom_file=args.sbom_file,
        release_inventory_file=args.release_inventory_file,
        generated_at=args.generated_at,
        generated_by=args.generated_by,
    )

    args.output.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"OK: wrote CRA release-evidence manifest for {name} {version} to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
