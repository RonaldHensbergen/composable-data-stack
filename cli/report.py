# cli/report.py
"""
Compliance/evidence report assembly for a rendered stack (#734).

CDS already produces evidence useful for a user's own regulatory
documentation (module/image identification, signature/SBOM/provenance
attestation via the signed-images fixture, the contract/topology graph, and
confirmation that no resolved secret value leaked into rendered output) but
never aggregates it into one exportable artifact. `build_compliance_report`
assembles that evidence for a single profile; `cli.main`'s `cds report`
command exposes it as human-readable text or machine-readable JSON.

This report only aggregates readiness evidence CDS already computes for a
*user's deployed stack* -- it is not itself a legal compliance/conformity
certification (see REPORT_DISCLAIMER, always included in the output).
"""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .diagnostics import Diagnostic
from .image_verification import (
    collect_compose_images,
    default_fixture_path,
    lookup_image_evidence,
)
from .planner import build_plan
from .renderer import render_compose
from .secrets import load_secrets_from_env
from .validator import has_errors, validate_profile

REPORT_DISCLAIMER = (
    "This report aggregates readiness evidence that CDS already computes for "
    "this rendered stack (module/image identification, linked signature/SBOM/"
    "provenance attestation status, contract topology, and a rendered-secret-"
    "leak check). It is not a legal compliance or conformity certification; "
    "confirm applicability against your own regulatory obligations."
)


def build_compliance_report(
    profile_path: str,
    env_file: str | None = None,
    environment: str | None = None,
    hardened: bool = False,
    image_source: str | None = None,
    fixture_path: Path | None = None,
) -> tuple[dict[str, Any] | None, list[Diagnostic]]:
    """
    Build a compliance/evidence report for profile_path.

    Runs the same validate -> plan -> render pipeline as `cds test`
    (compose target only; the report currently covers Compose-rendered
    stacks). Returns (None, diagnostics) if validate/plan/render fails --
    the acceptance criteria for #734 only require the report to run against
    profiles that already pass `cds validate`. Missing per-image evidence
    (e.g. a locally-built, unpublished image, or no signed-images fixture
    configured) is reported as a "not-available" entry with a warning
    diagnostic, not a hard failure.
    """
    diagnostics: list[Diagnostic] = []

    diagnostics.extend(validate_profile(profile_path, environment=environment))
    if has_errors(diagnostics):
        return None, diagnostics

    plan, plan_diags = build_plan(
        profile_path,
        env_file=env_file,
        environment=environment,
        hardened=hardened,
        image_source=image_source,
    )
    diagnostics.extend(plan_diags)
    if plan is None or has_errors(plan_diags):
        return None, diagnostics

    compose_yaml, render_diags = render_compose(plan, env_file=env_file)
    diagnostics.extend(render_diags)
    if compose_yaml is None or has_errors(render_diags):
        return None, diagnostics

    image_entries, image_diags = _build_image_evidence(compose_yaml, fixture_path)
    diagnostics.extend(image_diags)

    secret_check, secret_diags = _check_no_leaked_secrets(plan, compose_yaml, env_file)
    diagnostics.extend(secret_diags)

    report: dict[str, Any] = {
        "apiVersion": "cds/v1alpha1",
        "kind": "ComplianceReport",
        "generatedAt": datetime.now(UTC).isoformat(timespec="seconds"),
        "profile": {
            "sourceProfile": plan.get("sourceProfile"),
            "name": plan.get("metadata", {}).get("name"),
            "environment": plan.get("environment"),
        },
        "modules": _build_module_summary(plan),
        "images": image_entries,
        "topology": _build_topology_summary(plan),
        "secretLeakCheck": secret_check,
        "disclaimer": REPORT_DISCLAIMER,
    }
    return report, diagnostics


def _build_module_summary(plan: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "id": module["id"],
            "source": module.get("source"),
            "version": module.get("version"),
            "dependsOn": list(module.get("dependsOn", [])),
        }
        for module in plan.get("modules", [])
    ]


def _build_image_evidence(
    compose_yaml: str, fixture_path: Path | None
) -> tuple[list[dict[str, Any]], list[Diagnostic]]:
    diagnostics: list[Diagnostic] = []
    resolved_fixture_path = fixture_path if fixture_path is not None else default_fixture_path()

    entries: list[dict[str, Any]] = []
    for service, image, is_local_build in collect_compose_images(compose_yaml):
        entry: dict[str, Any] = {
            "service": service,
            "image": image,
            "digestPinned": "@sha256:" in image,
        }
        if is_local_build:
            entry["evidence"] = {
                "status": "not-available",
                "reason": (
                    "Locally built image; not published, so no signature/SBOM/"
                    "provenance evidence exists yet."
                ),
            }
        else:
            fixture_entry = lookup_image_evidence(image, resolved_fixture_path)
            if fixture_entry is None:
                reason = (
                    f'No matching entry in the signed-images fixture ("{resolved_fixture_path}"); '
                    "this image's signature/SBOM/provenance could not be linked "
                    "without a live cosign/registry lookup."
                    if resolved_fixture_path is not None
                    else (
                        "No signed-images fixture is configured "
                        "(see CDS_SIGNED_IMAGES_FIXTURE/tests/fixtures/signed-images.json)."
                    )
                )
                entry["evidence"] = {"status": "not-available", "reason": reason}
                diagnostics.append(Diagnostic(
                    level="warning",
                    code="W101",
                    message=f'No signature/SBOM/provenance evidence available for "{image}" (service "{service}").',
                    path=f"services.{service}.image",
                ))
            else:
                entry["evidence"] = {
                    "status": "available",
                    "digest": fixture_entry.get("digest"),
                    "signed": bool(fixture_entry.get("signed", False)),
                    "provenanceAttested": bool(fixture_entry.get("provenanceAttested", False)),
                    "sbomAttested": bool(fixture_entry.get("sbomAttested", False)),
                    "source": str(resolved_fixture_path),
                }
        entries.append(entry)
    return entries, diagnostics


def _build_topology_summary(plan: dict[str, Any]) -> list[dict[str, Any]]:
    topology: list[dict[str, Any]] = []
    for module in plan.get("modules", []):
        provides = [
            {"name": name, "kind": contract.get("kind")}
            for name, contract in module.get("provides", {}).items()
        ]
        consumes = []
        for name, consumed in module.get("consumes", {}).items():
            contract_ref = consumed.get("contractRef")
            consumes.append({
                "name": name,
                "kind": consumed.get("contract", {}).get("kind"),
                "contractRef": contract_ref,
                "provider": contract_ref.split(".", 1)[0] if contract_ref else None,
            })
        topology.append({
            "id": module["id"],
            "dependsOn": list(module.get("dependsOn", [])),
            "provides": provides,
            "consumes": consumes,
        })
    return topology


def _check_no_leaked_secrets(
    plan: dict[str, Any], compose_yaml: str, env_file: str | None
) -> tuple[dict[str, Any], list[Diagnostic]]:
    """
    Confirm no literal secret value appears in the rendered Compose output
    (only ${CDS_*} placeholders should). CDS's plan/render pipeline never
    loads real secret values into the plan itself (only alias->env-var name
    mappings; see cli.secrets.load_profile_secrets), so this is a
    defense-in-depth check against the raw .env values, not a check on
    CDS-internal state.

    Only env vars the profile actually declared under spec.secrets.values
    are checked -- plan["secrets"] also self-maps every other CDS_*
    variable found in .env/the environment (name -> itself) so that raw
    "secrets.CDS_FOO" references keep working, but most of those are
    ordinary, non-secret config (e.g. a database *name*) that legitimately
    renders as a literal value and would otherwise false-positive here.
    """
    diagnostics: list[Diagnostic] = []
    all_env_values, secret_diags = load_secrets_from_env(Path(env_file) if env_file else None)
    diagnostics.extend(secret_diags)

    declared_env_names = {
        env_name
        for alias, env_name in plan.get("secrets", {}).items()
        if alias != env_name
    }

    checked = 0
    leaked: list[str] = []
    for name in declared_env_names:
        value = all_env_values.get(name)
        if not value:
            continue
        checked += 1
        if value in compose_yaml:
            leaked.append(name)

    if leaked:
        diagnostics.append(Diagnostic(
            level="error",
            code="E119",
            message=(
                "Resolved secret value(s) found in rendered Compose output instead of "
                f"${{CDS_*}} placeholders: {', '.join(sorted(leaked))}."
            ),
            path="services",
        ))

    return {
        "status": "fail" if leaked else "pass",
        "checkedSecretCount": checked,
        "leakedSecretNames": sorted(leaked),
    }, diagnostics
