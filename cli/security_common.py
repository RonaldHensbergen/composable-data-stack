from __future__ import annotations

import re
from typing import Any

SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}

# Informational compliance-control-category metadata for security findings.
#
# Every rule in cli/resources/rule-set.json declares one of these categories
# (see the "category" $def in cli/resources/rule-schema.json, which is the
# schema-enforced source of truth for the closed set of values). Findings
# produced entirely in code rather than via the declarative rule-set engine
# (CDS-K8S-* in cli/k8s_security.py, CDS-SEC-050/051/052 and CDS-VER-* in
# cli/image_verification.py -- see docs/image-signing.md for why those image
# checks aren't declarative rule-set entries) assign one of these same
# category values directly at their call sites so every finding CDS emits has
# consistent category metadata for `cds security`/`cds test` grouping and
# filtering (see docs/security-rule-categories.md).
#
# IMPORTANT: this mapping is an informational readiness aid to help users
# organize their own compliance/risk-assessment work (e.g. NIS2, Cyberbevei-
# ligingswet, internal audits) around CDS findings. It is NOT a legal or
# regulatory certification that a rule or category satisfies any specific
# framework's control requirements.
RULE_CATEGORIES = {
    "secrets": (
        "Secrets management",
        "Hardcoded, weak, reused, or leaked credentials and other secret-like values.",
    ),
    "auth": (
        "Access control",
        "Authentication/authorization posture, including credential reuse across services.",
    ),
    "network-exposure": (
        "Network exposure",
        "Published ports, plaintext endpoints, and reachability from outside the intended network boundary.",
    ),
    "file-output-safety": (
        "File output safety",
        "Unsafe permissions or unsafe destinations for rendered/generated files.",
    ),
    "config-integrity": (
        "Configuration integrity",
        "Internal consistency and safe defaults of profile/module configuration.",
    ),
    "supply-chain": (
        "Supply chain",
        "Image provenance, signature/attestation verification, registry trust, and version/digest pinning.",
    ),
    "runtime-hardening": (
        "Runtime hardening",
        "Container/pod runtime posture, e.g. non-root users, dropped capabilities, and resource limits.",
    ),
    "observability-leakage": (
        "Logging & monitoring leakage",
        "Secrets or sensitive data leaking through logs, healthchecks, or diagnostics output.",
    ),
}

SECRET_KEY_RE = re.compile(r"(?i)(password|secret|token|key|credential|passwd|pwd)")

_SECRET_KEY_WORDS = (
    "password",
    "passwd",
    "pass",
    "pwd",
    "secret",
    "token",
    "key",
    "credential",
    "apikey",
    "accesskey",
    "secretkey",
    "passphrase",
)

SECRET_KEY_SEGMENT_RE = re.compile(
    r"(?i)(?:^|[-_])(?:" + "|".join(_SECRET_KEY_WORDS) + r")(?:$|[^a-z0-9])"
)

ENVIRONMENT_TO_CLASS = {
    "local": "local",
    "development": "dev",
    "staging": "staging",
    "production": "prod",
}


def infer_profile_class(profile: dict[str, Any]) -> str:
    """Map a profile's declared environment to the security policy class."""
    environment = (profile or {}).get("metadata", {}).get("environment", "local")
    return ENVIRONMENT_TO_CLASS.get(environment, "local")
