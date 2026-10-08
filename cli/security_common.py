from __future__ import annotations

import re
from typing import Any

SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}

# Closed set of informational compliance control categories a rule-set.json
# rule can be tagged with (rule-schema.json's $defs.complianceCategory),
# each mapped to a short display label plus the rationale documented in
# docs/security-compliance-categories.md. Kept here, rather than only in the
# schema/docs, so cli/main.py can offer the same set as --category choices
# (dict membership works the same as a tuple's for `in`/argparse `choices`)
# and render a human-readable heading for --group-by-category, all from one
# source of truth. See docs/security-compliance-categories.md for the full
# rationale and the "not a certification" disclaimer.
COMPLIANCE_CATEGORIES = {
    "access-control": (
        "Access control",
        "Authentication being present and adequately strong (default/missing "
        "credentials, weak secret strength, secret reuse across services that "
        "weakens per-service access control).",
    ),
    "secrets-management": (
        "Secrets management",
        "How secret material is stored, referenced, and handled (hardcoded "
        "secrets, secrets embedded in DSNs/URLs, inline key material, secrets "
        "leaking into generated files or file permissions).",
    ),
    "network-exposure": (
        "Network exposure",
        "Services reachable from more of the network than intended (binding "
        "to 0.0.0.0, externally published databases, non-local interfaces in "
        "a local profile).",
    ),
    "encryption-in-transit": (
        "Encryption in transit",
        "Traffic confidentiality/integrity in transit (an authenticated "
        "service or a production endpoint served over plain HTTP without a "
        "TLS reverse-proxy).",
    ),
    "configuration-management": (
        "Configuration management",
        "Configuration correctness and integrity (insecure fallback "
        "defaults, empty required secrets, unapproved secret binding "
        "targets, profile inheritance weakening secure defaults, "
        "non-production-suitable modules used in staging/production).",
    ),
    "system-hardening": (
        "System hardening",
        "Container/runtime hardening (running as root, privileged/excess "
        "capabilities, writable root filesystem, sensitive host path "
        "mounts).",
    ),
    "logging-monitoring": (
        "Logging & monitoring",
        "Security-relevant information leaking into (or via) logs, command "
        "lines, or CLI output, which undermines the same logging/monitoring "
        "control it's meant to support.",
    ),
    "patching": (
        "Patching",
        "Image/dependency freshness, provenance, and trust (pinned or "
        "digest-verified images, signature/build-provenance verification, "
        "trusted registries).",
    ),
    "business-continuity": (
        "Business continuity",
        "Resilience of stateful services against data loss (no backup/"
        "restore target wired for a service that holds durable data).",
    ),
}

# Maps each NIS2/Cyberbeveiligingswet Article 21(2) measure letter to the
# complianceCategory values whose findings give an operator direct evidence
# for that measure, for `cds security --report nis2`. This mirrors (and must
# be kept in sync with) the gap-analysis table in
# docs/nis2-cyberbeveiligingswet-scope.md -- that doc is the narrative
# source of truth for *why* each mapping holds (and which measures are out
# of CDS's product scope entirely, e.g. (b), (e), (f), (g)), this is just
# its machine-usable form. A letter with an empty category list has no
# CDS rule-set evidence today; the report still lists it so the operator
# sees the full Article 21(2) checklist, not just the parts CDS covers.
NIS2_ARTICLE_21_MEASURES = (
    (
        "a", "Policies on risk analysis and information system security",
        (),
        "Every `cds security` finding, across all categories below, is "
        "machine-checked input to this policy -- see the full finding list "
        "and docs/security-compliance-categories.md.",
    ),
    (
        "b", "Incident handling", (),
        "Out of CDS's product scope (an operator incident-handling "
        "procedure, not a rendered-artifact property). See SECURITY.md and "
        "docs/security-support-policy.md for CDS's own process.",
    ),
    ("c", "Business continuity (backup management, disaster recovery, crisis management)", ("business-continuity",), None),
    ("d", "Supply chain security", ("patching",), None),
    (
        "e", "Security in acquisition, development and maintenance, incl. vulnerability handling", (),
        "Out of CDS's product scope. See SECURITY.md and "
        "docs/security-support-policy.md for CDS's own secure-maintenance practice.",
    ),
    (
        "f", "Policies/procedures to assess the effectiveness of risk-management measures", (),
        "Re-running `cds security`/`cds test` gives a repeatable, "
        "versionable check of whether a profile's measures still pass.",
    ),
    (
        "g", "Basic cyber hygiene practices and cybersecurity training", (),
        "Out of CDS's product scope (an operator organisational practice, "
        "not a rendered-artifact property).",
    ),
    ("h", "Policies/procedures on cryptography and encryption", ("encryption-in-transit", "secrets-management"), None),
    ("i", "Human resources security, access control policies, asset management", ("access-control",), None),
    (
        "j", "MFA/continuous authentication, secured communications", ("access-control",),
        "modules/identity/keycloak can provide MFA-capable authentication "
        "to a profile; this is opt-in, not a default.",
    ),
)

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
