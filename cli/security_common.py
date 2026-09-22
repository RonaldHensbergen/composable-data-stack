from __future__ import annotations

import re
from typing import Any

SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}

# Closed set of informational compliance control categories a rule-set.json
# rule can be tagged with (rule-schema.json's $defs.complianceCategory).
# Kept here, rather than only in the schema, so cli/main.py can offer the
# same set as --category choices without parsing the bundled JSON schema
# just for that. See docs/security-compliance-categories.md for the
# rationale behind each category and the "not a certification" disclaimer.
COMPLIANCE_CATEGORIES = (
    "access-control",
    "secrets-management",
    "network-exposure",
    "encryption-in-transit",
    "configuration-management",
    "system-hardening",
    "logging-monitoring",
    "patching",
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
