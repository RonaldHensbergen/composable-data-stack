# cli/image_verification.py
"""
OCI image signature and build provenance verification.

Implements the CDS production image policy (issue #208) on top of the
publication and attestation flow in .github/workflows/publish-images.yml:

- Static policy checks over rendered Compose service images: trusted
  registry allowlist, digest pinning for production, and no floating
  ":latest" tags (reported as CDS-SEC-050/051/052 findings; these are not
  rule-engine rules).
- Cosign-compatible signature and provenance verification, pluggable via
  CDS_COSIGN_BIN. Keyless by default (OIDC issuer + certificate identity
  constraints); key-managed when CDS_COSIGN_KEY points at a key file.
- Offline verification against a known-good fixture
  (tests/fixtures/signed-images.json) so CI can verify without a registry
  round trip or a cosign binary.

The policy is gated by CDS_IMAGE_VERIFICATION (off | policy | full);
production profiles default to "policy", and "full" additionally requires
signature/provenance verification to succeed.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess  # nosec B404
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import yaml

from .image_updates import parse_image_reference
from .security_common import SEVERITY_ORDER

DEFAULT_TRUSTED_REGISTRIES = ("ghcr.io", "docker.io", "registry-1.docker.io")
DEFAULT_TRUSTED_OIDC_ISSUER = "https://token.actions.githubusercontent.com"
DEFAULT_CERT_IDENTITY_REGEXP = (
    r"^https://github\.com/RonaldHensbergen/composable-data-stack/"
    r"\.github/workflows/publish-images\.yml@refs/heads/main$"
)
DEFAULT_COSIGN_BIN = "cosign"
FIXTURE_RELATIVE_PATH = Path("tests/fixtures/signed-images.json")
_FIXTURE_ENV_VAR = "CDS_SIGNED_IMAGES_FIXTURE"

_DIGEST_PATTERN = re.compile(r"^sha256:[a-f0-9]{64}$")


@dataclass(frozen=True)
class ImagePolicy:
    """Trust constraints and tooling for image verification."""

    mode: str = "off"
    trusted_registries: tuple[str, ...] = DEFAULT_TRUSTED_REGISTRIES
    oidc_issuer: str = DEFAULT_TRUSTED_OIDC_ISSUER
    cert_identity_regexp: str = DEFAULT_CERT_IDENTITY_REGEXP
    cosign_bin: str = DEFAULT_COSIGN_BIN
    key_path: str | None = None
    require_digest: bool = False


def load_policy_from_env(
    profile_class: str = "local",
    mode_override: str | None = None,
) -> ImagePolicy:
    """
    Build an ImagePolicy from environment configuration.

    Mode resolution order: explicit mode_override (CLI flag), then
    CDS_IMAGE_VERIFICATION, then "policy" for production profiles and
    "off" everywhere else. Unknown values fall back to "policy" for
    production profiles (so a typo can never silently disable the policy)
    and "off" elsewhere.
    """
    env_mode = os.getenv("CDS_IMAGE_VERIFICATION", "").strip().lower()
    mode = mode_override or env_mode or ("policy" if profile_class == "prod" else "off")
    if mode not in ("off", "policy", "full"):
        mode = "policy" if profile_class == "prod" else "off"

    registries = tuple(
        part.strip()
        for part in os.getenv("CDS_TRUSTED_REGISTRIES", "").split(",")
        if part.strip()
    ) or DEFAULT_TRUSTED_REGISTRIES

    return ImagePolicy(
        mode=mode,
        trusted_registries=registries,
        oidc_issuer=os.getenv("CDS_TRUSTED_OIDC_ISSUER", "").strip()
        or DEFAULT_TRUSTED_OIDC_ISSUER,
        cert_identity_regexp=os.getenv("CDS_TRUSTED_CERT_IDENTITY_REGEXP", "").strip()
        or DEFAULT_CERT_IDENTITY_REGEXP,
        cosign_bin=os.getenv("CDS_COSIGN_BIN", "").strip() or DEFAULT_COSIGN_BIN,
        key_path=os.getenv("CDS_COSIGN_KEY", "").strip() or None,
        require_digest=profile_class == "prod",
    )


def default_fixture_path() -> Path | None:
    """Resolve the signed-images fixture path from env or the repo checkout."""
    explicit = os.getenv(_FIXTURE_ENV_VAR, "").strip()
    if explicit:
        return Path(explicit)
    candidate = FIXTURE_RELATIVE_PATH
    return candidate if candidate.is_file() else None


def collect_compose_images(compose_yaml: str) -> list[tuple[str, str, bool]]:
    """Return service name, image reference, and local-build status."""
    try:
        compose = yaml.safe_load(compose_yaml) or {}
    except yaml.YAMLError:
        return []
    services = compose.get("services", {}) if isinstance(compose, dict) else {}
    if not isinstance(services, dict):
        return []
    return [
        (
            str(name),
            service["image"],
            service["image"].startswith("local/") and _has_local_build(service),
        )
        for name, service in services.items()
        if isinstance(service, dict) and isinstance(service.get("image"), str)
    ]


def _has_local_build(service: dict[str, Any]) -> bool:
    build = service.get("build")
    if isinstance(build, str):
        return bool(build.strip())
    if not isinstance(build, dict):
        return False
    return any(
        isinstance(build.get(key), str) and bool(build[key].strip())
        for key in ("context", "dockerfile", "dockerfile_inline")
    )


def _registry_is_trusted(registry: str, policy: ImagePolicy) -> bool:
    trusted_registries = {trusted.casefold() for trusted in policy.trusted_registries}
    return registry.casefold() in trusted_registries


def _finding(
    rule_id: str,
    severity: str,
    service: str,
    image: str,
    message: str,
    recommendation: list[str],
) -> dict[str, Any]:
    return {
        "rule_id": rule_id,
        "severity": severity,
        "module": service,
        "message": message,
        "path": f"services.{service}.image",
        "value": image,
        "recommendation": recommendation,
    }


def _static_findings(
    images: list[tuple[str, str, bool]],
    policy: ImagePolicy,
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for service, image, is_local_build in images:
        if is_local_build:
            continue

        ref = parse_image_reference(image)
        registry = cast(str, ref["registry"])
        if not _registry_is_trusted(registry, policy):
            findings.append(_finding(
                "CDS-SEC-052",
                "medium",
                service,
                image,
                f"Image registry '{ref['registry']}' is not in the trusted registry allowlist",
                [
                    "Restrict images to trusted registries.",
                    "Maintain an explicit allowlist per environment via CDS_TRUSTED_REGISTRIES.",
                ],
            ))

        if "@sha256:" not in image and ref["tag"] == "latest":
            findings.append(_finding(
                "CDS-SEC-050",
                "medium",
                service,
                image,
                "Container image uses the latest tag",
                [
                    "Pin images to explicit versions.",
                    "Prefer immutable digests for critical services.",
                ],
            ))

        if policy.require_digest and "@sha256:" not in image:
            findings.append(_finding(
                "CDS-SEC-051",
                "medium",
                service,
                image,
                "Critical service image is not pinned by digest",
                [
                    "Use image digests for critical services.",
                    "Define policy exceptions only when necessary.",
                ],
            ))
    return findings


def _verify_with_cosign(
    image_ref: str,
    policy: ImagePolicy,
    attestation_type: str | None,
) -> tuple[bool, str]:
    """Run cosign verify / verify-attestation and return (ok, detail)."""
    cosign_path = shutil.which(policy.cosign_bin)
    if cosign_path is None:
        return False, (
            f"'{policy.cosign_bin}' was not found on PATH; install cosign or "
            "set CDS_COSIGN_BIN"
        )

    command = [cosign_path]
    if attestation_type is not None:
        command += ["verify-attestation", "--type", attestation_type]
    else:
        command += ["verify"]
    if policy.key_path:
        command += ["--key", policy.key_path]
    else:
        command += [
            "--certificate-identity-regexp",
            policy.cert_identity_regexp,
            "--certificate-oidc-issuer",
            policy.oidc_issuer,
        ]
    command.append(image_ref)

    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=120)  # nosec B603
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"cosign invocation failed: {exc}"

    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        return False, detail or "cosign rejected the image"
    return True, ""


def _load_fixture(path: Path | None) -> tuple[dict[str, Any] | None, str | None]:
    """
    Load the signed-images fixture.

    Returns (data, None) on success, (None, error) when a configured fixture
    path could not be loaded, and (None, None) when no fixture was configured
    (callers then fall back to live cosign verification).
    """
    if path is None:
        return None, None
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError) as exc:
        return None, f"could not be loaded: {exc}"
    if not isinstance(data, dict):
        return None, "does not contain a JSON object"
    return data, None


def _fixture_entry(
    fixture: dict[str, Any] | None,
    image_ref: str,
) -> dict[str, Any] | None:
    """Return the fixture entry whose repository matches image_ref, if any."""
    if fixture is None:
        return None
    images = fixture.get("images", {})
    if not isinstance(images, dict):
        return None
    folded_image_ref = image_ref.casefold()
    for entry in images.values():
        if not isinstance(entry, dict):
            continue
        repository = entry.get("repository")
        if not isinstance(repository, str):
            continue
        folded_repository = repository.casefold()
        if folded_image_ref == folded_repository or folded_image_ref.startswith(
            (folded_repository + "@", folded_repository + ":")
        ):
            return entry
    return None


def _verification_findings(
    images: list[tuple[str, str, bool]],
    policy: ImagePolicy,
    fixture: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for service, image, is_local_build in images:
        if is_local_build:
            continue

        entry = _fixture_entry(fixture, image)
        registry = cast(str, parse_image_reference(image)["registry"])
        if entry is not None and _registry_is_trusted(registry, policy):
            ref_digest = image.rsplit("@", 1)[1] if "@sha256:" in image else None
            entry_digest = entry.get("digest")
            if ref_digest is None:
                findings.append(_finding(
                    "CDS-VER-003",
                    "high",
                    service,
                    image,
                    "Tagged image reference cannot be verified against the signed-images fixture; pin the image by digest",
                    [
                        "Reference the image by its published digest (@sha256:...).",
                        "Refresh tests/fixtures/signed-images.json from the latest publish-images run.",
                    ],
                ))
                continue
            if ref_digest != entry_digest:
                findings.append(_finding(
                    "CDS-VER-003",
                    "high",
                    service,
                    image,
                    "Image digest does not match the signed-images fixture entry",
                    [
                        "Pull the image by its published digest.",
                        "Refresh tests/fixtures/signed-images.json from the latest publish-images run.",
                    ],
                ))
                continue
            if entry.get("signed") is not True:
                findings.append(_finding(
                    "CDS-VER-001",
                    "high",
                    service,
                    image,
                    "Image has no verifiable signature in the signed-images fixture",
                    [
                        "Re-publish the image through publish-images.yml so it is signed.",
                        "Verify with cosign verify before deploying.",
                    ],
                ))
            if entry.get("provenanceAttested") is not True:
                findings.append(_finding(
                    "CDS-VER-002",
                    "high",
                    service,
                    image,
                    "Image has no verifiable build provenance attestation",
                    [
                        "Re-publish the image so the SLSA provenance attestation is attached.",
                        "Verify with cosign verify-attestation --type slsaprovenance.",
                    ],
                ))
            continue

        ok, detail = _verify_with_cosign(image, policy, attestation_type=None)
        if not ok:
            findings.append(_finding(
                "CDS-VER-001",
                "high",
                service,
                image,
                f"Image signature could not be verified: {detail}",
                [
                    "Ensure the image was signed by the trusted publish-images workflow.",
                    "Provide a known-good signed-images fixture for offline verification.",
                ],
            ))
            continue

        ok, detail = _verify_with_cosign(image, policy, attestation_type="slsaprovenance")
        if not ok:
            findings.append(_finding(
                "CDS-VER-002",
                "high",
                service,
                image,
                f"Build provenance attestation could not be verified: {detail}",
                [
                    "Re-publish the image through publish-images.yml so provenance is attested.",
                    "Verify with cosign verify-attestation --type slsaprovenance.",
                ],
            ))
    return findings


def verify_images(
    compose_yaml: str,
    policy: ImagePolicy,
    fixture: Path | None = None,
) -> list[dict[str, Any]]:
    """
    Run the image policy over a rendered compose file.

    Returns findings in the same shape as cli.security findings, sorted by
    severity then rule id. Returns [] when the policy mode is "off".
    """
    if policy.mode == "off":
        return []

    images = collect_compose_images(compose_yaml)
    findings: list[dict[str, Any]] = _static_findings(images, policy)
    if policy.mode == "full":
        fixture_data, fixture_error = _load_fixture(fixture)
        if fixture_error is not None:
            findings.append(_finding(
                "CDS-VER-004",
                "high",
                "<profile>",
                str(fixture),
                f"Configured signed-images fixture '{fixture}' {fixture_error}; "
                "failing closed instead of silently falling back to live cosign "
                "verification with different trust constraints",
                [
                    "Fix the path in CDS_SIGNED_IMAGES_FIXTURE or unset it to use the bundled fixture.",
                    "Restore tests/fixtures/signed-images.json if it was removed.",
                ],
            ))
        else:
            findings.extend(_verification_findings(images, policy, fixture_data))

    findings.sort(key=lambda x: (
        SEVERITY_ORDER.get(x["severity"], 99),
        x["rule_id"],
        x["path"],
    ))
    return findings


def validate_fixture(fixture: dict[str, Any]) -> list[str]:
    """
    Structural validation for a signed-images fixture. Returns error strings,
    or an empty list when the fixture is well-formed.
    """
    errors: list[str] = []
    if fixture.get("schemaVersion") != 1:
        errors.append("schemaVersion must be 1")
    trust_root = fixture.get("trustRoot", {})
    if not isinstance(trust_root, dict):
        errors.append("trustRoot must be an object")
    else:
        for key in ("oidcIssuer", "certificateIdentityRegexp"):
            if not isinstance(trust_root.get(key), str) or not trust_root[key]:
                errors.append(f"trustRoot.{key} must be a non-empty string")
        registries = trust_root.get("registries")
        if not isinstance(registries, list) or not registries:
            errors.append("trustRoot.registries must be a non-empty list")

    images = fixture.get("images", {})
    if not isinstance(images, dict) or not images:
        errors.append("images must be a non-empty object")
    else:
        for name, entry in images.items():
            if not isinstance(entry, dict):
                errors.append(f"images.{name} must be an object")
                continue
            if not isinstance(entry.get("repository"), str) or not entry["repository"]:
                errors.append(f"images.{name}.repository must be a non-empty string")
            digest = entry.get("digest")
            if not isinstance(digest, str) or not _DIGEST_PATTERN.match(digest):
                errors.append(f"images.{name}.digest must match sha256:<64 hex chars>")
            elif digest == "sha256:" + "0" * 64:
                errors.append(
                    f"images.{name}.digest is the all-zero placeholder; copy the real "
                    "digest from the latest publish-images run (docs/image-signing.md)"
                )
            for flag in ("signed", "provenanceAttested", "sbomAttested"):
                if not isinstance(entry.get(flag), bool):
                    errors.append(f"images.{name}.{flag} must be a boolean")
    return errors
