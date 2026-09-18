#!/usr/bin/env python3
"""Build a release artifact inventory tying CLI artifacts to source and SBOM evidence.

Used by `.github/workflows/build-python-package.yml` to produce a single,
machine-readable manifest that ties every published wheel/sdist to the
source commit, version, a checksum, and the CLI SBOM generated for that
build (see `scripts/generate_cli_sbom.py` / `scripts/validate_cli_sbom.py`).

Runtime images under `images/**` are versioned and published independently
of the CLI (see docs/image-scanning.md) and already carry their own
signing/SBOM/provenance attestations (docs/image-signing.md), tracked in
`tests/fixtures/signed-images.json`. This inventory references that
existing evidence by pointer rather than duplicating it.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tomllib
from pathlib import Path


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def artifact_records(dist_dir: Path) -> list[dict[str, object]]:
    """Return a checksum/size record for every distribution file, sorted by name."""
    records: list[dict[str, object]] = []
    for path in sorted(dist_dir.glob("*")):
        if not path.is_file():
            continue
        records.append(
            {
                "file": path.name,
                "sha256": sha256_of(path),
                "size": path.stat().st_size,
            }
        )
    return records


def pyproject_name_and_version(pyproject_path: Path) -> tuple[str, str]:
    data = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    project = data["project"]
    return project["name"], project["version"]


def image_evidence_reference(fixture_path: Path) -> dict[str, object] | None:
    """Return a pointer to the existing image evidence fixture, without duplicating it.

    Returns None if the fixture is absent, so this inventory can still be
    generated (with a gap noted by the caller) rather than failing outright.
    """
    if not fixture_path.is_file():
        return None
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    return {
        "note": (
            "Runtime images are versioned and released independently of the CLI; "
            "see docs/image-scanning.md and docs/image-signing.md. This is a "
            "pointer to their existing evidence, not a copy of it."
        ),
        "referenceFile": str(fixture_path.as_posix()),
        "source": fixture.get("source"),
        "updated": fixture.get("updated"),
    }


def build_inventory(
    *,
    name: str,
    version: str,
    commit_sha: str,
    source_ref: str,
    artifacts: list[dict[str, object]],
    sbom_file: str,
    sbom_format: str,
    generated_at: str,
    generated_by: str,
    image_evidence: dict[str, object] | None,
) -> dict[str, object]:
    return {
        "schemaVersion": 1,
        "cli": {
            "name": name,
            "version": version,
            "sourceCommit": commit_sha,
            "sourceRef": source_ref,
            "artifacts": artifacts,
            "sbom": {"file": sbom_file, "format": sbom_format},
        },
        "images": image_evidence
        or {
            "note": (
                "No image evidence fixture was found at generation time; see "
                "docs/image-signing.md for how published images are signed and attested."
            )
        },
        "generatedAt": generated_at,
        "generatedBy": generated_by,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist-dir", required=True, type=Path, help="Directory containing the built wheel/sdist")
    parser.add_argument("--sbom-file", required=True, help="Filename of the CLI SBOM this build produced")
    parser.add_argument("--sbom-format", default="CycloneDX", help="SBOM format label (default: CycloneDX)")
    parser.add_argument("--commit-sha", required=True, help="Source commit SHA this build was produced from")
    parser.add_argument("--source-ref", required=True, help="Git ref (tag/branch) this build was produced from")
    parser.add_argument("--generated-at", required=True, help="UTC ISO-8601 timestamp of generation")
    parser.add_argument("--generated-by", required=True, help="URL of the workflow run that generated this inventory")
    parser.add_argument(
        "--pyproject",
        type=Path,
        default=Path(__file__).resolve().parent.parent / "pyproject.toml",
        help="Path to pyproject.toml (default: repository root)",
    )
    parser.add_argument(
        "--image-fixture",
        type=Path,
        default=Path("tests/fixtures/signed-images.json"),
        help="Path to the existing published-image evidence fixture to reference",
    )
    parser.add_argument("--output", required=True, type=Path, help="Path to write the inventory JSON to")
    args = parser.parse_args()

    name, version = pyproject_name_and_version(args.pyproject)
    artifacts = artifact_records(args.dist_dir)
    if not artifacts:
        print(f"::error::No artifacts found in {args.dist_dir}", file=sys.stderr)
        return 1

    inventory = build_inventory(
        name=name,
        version=version,
        commit_sha=args.commit_sha,
        source_ref=args.source_ref,
        artifacts=artifacts,
        sbom_file=args.sbom_file,
        sbom_format=args.sbom_format,
        generated_at=args.generated_at,
        generated_by=args.generated_by,
        image_evidence=image_evidence_reference(args.image_fixture),
    )

    args.output.write_text(json.dumps(inventory, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"OK: wrote release artifact inventory for {name} {version} to {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
