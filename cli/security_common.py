from __future__ import annotations

from typing import Any


SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}

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
