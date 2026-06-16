#cli/security.py
"""
Security validation of composition profiles against a rule set.
Scans both the profile config values and .env secrets for vulnerabilities.
${secrets.*} interpolation references in the profile are intentional and skipped.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator

from .diagnostics import Diagnostic
from .secrets import load_secrets_from_env

_PROFILE_SCOPES = {
    "profile",
    "profile-raw",
    "profile-resolved",
    "module-values",
    "bindings",
}

_ENV_SCOPES = {
    "service-env",
    "service",
    "runtime",
}
# ---------------------------------------------------------------------------
# File I/O
# ---------------------------------------------------------------------------

def _load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _load_yaml(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


# ---------------------------------------------------------------------------
# Rule set loading
# ---------------------------------------------------------------------------

def _validate_rule_set(rule_schema_path: Path, rule_set_path: Path) -> dict[str, Any]:
    schema = _load_json(rule_schema_path)
    rule_set = _load_json(rule_set_path)
    validator = Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(rule_set), key=lambda e: list(e.path))
    if errors:
        msgs = [
            f'{".".join(str(x) for x in err.path) or "<root>"}: {err.message}'
            for err in errors
        ]
        raise ValueError("Rule-set validation failed:\n  - " + "\n  - ".join(msgs))
    return rule_set


# ---------------------------------------------------------------------------
# Flattening
# ---------------------------------------------------------------------------

def _flatten(obj: Any, prefix: str = "") -> list[tuple[str, Any]]:
    """Recursively flatten a nested dict/list into (path, value) pairs."""
    items: list[tuple[str, Any]] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            path = f"{prefix}.{k}" if prefix else str(k)
            items.extend(_flatten(v, path))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            items.extend(_flatten(v, f"{prefix}[{i}]"))
    else:
        items.append((prefix, obj))
    return items


def _flatten_profile_by_module(profile: dict[str, Any]) -> list[tuple[str, str, Any]]:
    """
    Returns (module_id, path, value) triples from the profile.

    - Per-module config is emitted under the module's id.
    - Top-level and spec-level keys outside modules are emitted as "<profile>".
    - Disabled modules are skipped.
    - ${secrets.*} references are left in place here; filtered in rule_matches.
    """
    results: list[tuple[str, str, Any]] = []
    spec = profile.get("spec", {})
    modules = spec.get("modules", [])

    for module_instance in modules:
        if module_instance.get("enabled", False) is False:
            continue
        module_id = module_instance.get("id", "<unknown>")
        for path, value in _flatten(module_instance.get("config", {})):
            results.append((module_id, path, value))

    for key, value in profile.items():
        if key == "spec":
            for spec_key, spec_value in spec.items():
                if spec_key == "modules":
                    continue
                for path, v in _flatten(spec_value, spec_key):
                    results.append(("<profile>", path, v))
        else:
            for path, v in _flatten(value, key):
                results.append(("<profile>", path, v))

    return results


def _flatten_env_secrets(secrets: dict[str, str]) -> list[tuple[str, str, Any]]:
    """
    Emit .env secrets as flat items attributed to "<env>".
    Scanned directly by the rule engine for vulnerabilities in secret values.
    """
    return [("<env>", f"secrets.{key}", value) for key, value in secrets.items()]


# ---------------------------------------------------------------------------
# Secret reference detection
# ---------------------------------------------------------------------------

_SECRET_REF_RE = re.compile(
    r"^\$\{secrets\.[^}]+\}$"   # ${secrets.KEY}
    r"|^secrets\.[A-Za-z0-9_.]+$"  # secrets.KEY
)

def _is_secret_reference(value: Any) -> bool:
    """
    Returns True if value is an unresolved ${secrets.*} interpolation.
    These are intentional references to .env values, not real config values,
    so they must be excluded from rule evaluation to avoid false positives.
    """
    return isinstance(value, str) and bool(_SECRET_REF_RE.match(value))


# ---------------------------------------------------------------------------
# Profile class inference
# ---------------------------------------------------------------------------

def _infer_profile_class(profile: dict[str, Any]) -> str:
    name = str((profile or {}).get("name", "")).lower()
    if "prod" in name:
        return "prod"
    if "stag" in name:
        return "staging"
    if "dev" in name:
        return "dev"
    return "local"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _entropy_like(value: str) -> bool:
    if not isinstance(value, str) or len(value) < 16:
        return False
    classes = (
        bool(re.search(r"[a-z]", value))
        + bool(re.search(r"[A-Z]", value))
        + bool(re.search(r"\d", value))
        + bool(re.search(r"[^A-Za-z0-9]", value))
    )
    return classes >= 3


def _service_type_for_path(path: str) -> str:
    p = path.lower()
    if "superset" in p or "dagster-webserver" in p or "ui" in p:
        return "admin-ui"
    if "postgres" in p or "mysql" in p or "db" in p:
        return "database"
    return "generic"


def _path_pattern_to_regex(pattern: str) -> str:
    return "^" + re.escape(pattern).replace(r"\*", ".*") + "$"


def _path_matches_any(path: str, patterns: list[str]) -> bool:
    if not patterns:
        return True
    return any(re.match(_path_pattern_to_regex(p), path) for p in patterns)


def _redact(value: Any) -> str | None:
    if value is None:
        return None
    sval = str(value)
    if len(sval) <= 6:
        return "***"
    return sval[:2] + "***REDACTED***" + sval[-2:]


# ---------------------------------------------------------------------------
# Condition evaluation
# ---------------------------------------------------------------------------
_NON_SECRET_PATH_SUFFIXES = (
    "description",
    "name",
    "label",
    "title",
    "comment",
    "notes",
)
def _eval_condition(
    path: str,
    key: str,
    value: Any,
    cond: dict[str, Any],
    profile_class: str,
) -> bool:
    sval = "" if value is None else str(value)

    # Never flag known metadata fields as secret-like
    if cond.get("entropy") == "high" and key.lower().endswith(_NON_SECRET_PATH_SUFFIXES):
        return False
    
    if "pathPatterns" in cond and not _path_matches_any(path, cond["pathPatterns"]):
        return False
    if "keyRegex" in cond and not re.search(cond["keyRegex"], key or ""):
        return False
    if "valueRegex" in cond and not re.search(cond["valueRegex"], sval):
        return False
    if "notValueRegex" in cond and re.search(cond["notValueRegex"], sval):
        return False
    if "containsAny" in cond and not any(x in sval for x in cond["containsAny"]):
        return False
    if "equalsAny" in cond and sval not in cond["equalsAny"]:
        return False
    if "profileClasses" in cond and profile_class not in cond["profileClasses"]:
        return False
    if cond.get("envInterpolation") is True and "${" not in sval:
        return False
    if cond.get("allowEmpty") is True and sval not in ("", "None", "null"):
        return False
    if cond.get("entropy") == "high" and not _entropy_like(sval):
        return False
    if "minLength" in cond and len(sval) < cond["minLength"]:
        return False
    if "serviceTypes" in cond and _service_type_for_path(path) not in cond["serviceTypes"]:
        return False

    if "portExposure" in cond:
        exposure = cond["portExposure"]
        if exposure == "0.0.0.0" and "0.0.0.0:" not in sval:
            return False
        if exposure == "host-published" and ":" not in sval:
            return False
        if exposure == "localhost-only" and not (
            sval.startswith("127.0.0.1:") or sval.startswith("localhost:")
        ):
            return False

    if "imageTagPolicy" in cond:
        policy = cond["imageTagPolicy"]
        if policy == "forbid-latest" and not sval.endswith(":latest"):
            return False
        if policy == "require-digest" and "@sha256:" not in sval:
            return False
        if policy == "require-tag" and ":" not in sval and "@sha256:" not in sval:
            return False

    if "runtimeFlags" in cond and not any(flag in sval for flag in cond["runtimeFlags"]):
        return False
    if "fallbackPattern" in cond and not re.search(cond["fallbackPattern"], sval):
        return False

    if "secretSinkPolicy" in cond:
        forbidden_segments = [
            ".labels.",
            ".annotations.",
            ".command",
            ".args.",
            "outputs.",
            "plan.preview.",
        ]
        is_forbidden_sink = any(seg in path for seg in forbidden_segments)
        if cond["secretSinkPolicy"] == "forbidden" and not is_forbidden_sink:
            return False

    return True

# ---------------------------------------------------------------------------
# Cross-item checks (cannot be expressed as per-item rules)
# ---------------------------------------------------------------------------

def _check_secret_reuse(
    flat_items: list[tuple[str, str, Any]],
) -> list[dict[str, Any]]:
    """
    Detect the same secret value appearing under different keys.
    Ignores empty values and non-string values.
    """
    _SECRET_KEY_RE = re.compile(r"(?i)(password|secret|token|key|credential|passwd|pwd)")

    # Collect all (path, value) pairs that look like secrets
    value_to_locations: dict[str, list[tuple[str, str]]] = {}
    for module_id, path, value in flat_items:
        if not isinstance(value, str) or not value:
            continue
        if not _SECRET_KEY_RE.search(path.split(".")[-1]):
            continue
        value_to_locations.setdefault(value, []).append((module_id, path))

    findings = []
    for value, locations in value_to_locations.items():
        if len(locations) < 2:
            continue
        for module_id, path in locations:
            findings.append({
                "rule_id": "CDS-SEC-013",
                "severity": "medium",
                "module": module_id,
                "message": "The same secret appears reused across multiple services",
                "path": path,
                "value": _redact(value),  # always redact reuse findings
                "recommendation": [
                    "Use separate credentials or secrets per service.",
                    "Generate scoped secrets rather than sharing one across components.",
                ],
            })

    return findings

# ---------------------------------------------------------------------------
# Rule matching
# ---------------------------------------------------------------------------

def _rule_matches(
    rule: dict[str, Any],
    flat_items: list[tuple[str, str, Any]],
    profile_class: str,
    redact_values: bool = False,
) -> list[dict[str, Any]]:

    findings: list[dict[str, Any]] = []
    match = rule["match"]

    for module_id, path, value in flat_items:
        # Skip unresolved ${secrets.*} references in profile config.
        # They are intentional indirections, not real values.
        if _is_secret_reference(value):
            continue

        key = path.split(".")[-1] if path else ""

        if "all" in match:
            ok = all(
                _eval_condition(path, key, value, cond, profile_class)
                for cond in match["all"]
            )
        else:
            ok = any(
                _eval_condition(path, key, value, cond, profile_class)
                for cond in match["any"]
            )

        if ok:
            findings.append({
                "rule_id": rule["id"],
                "severity": rule["severity"],
                "module": module_id,
                "message": rule["message"],
                "path": path,
                "value": _redact(value) if redact_values else value,
                "recommendation": rule["recommendation"],
            })

    return findings


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

_SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}


def run_security_validation(
    profile_path: Path,
    rule_schema_path: Path,
    rule_set_path: Path,
    env_file: str | None = None,
    redact_values: bool = False,
) -> tuple[list[dict[str, Any]], list[Diagnostic]]:
    """
    Validate a profile and its .env secrets against the rule set.

    Two sources are scanned:
    - Profile config values (${secrets.*} references are skipped — they are
      intentional indirections, not real values).
    - .env secret values (scanned directly for weak/leaked secrets).

    Args:
        profile_path:     Path to the profile YAML.
        rule_schema_path: Path to the rule set JSON schema.
        rule_set_path:    Path to the rule set JSON.
        env_file:         Optional path to .env file. Defaults to .env in cwd.
        redact_values:    If True, secret-like values are redacted in findings.

    Returns:
        Tuple of (findings, diagnostics). Findings are sorted by severity,
        then rule_id, module, and path.
    """
    profile = _load_yaml(profile_path)
    rule_set = _validate_rule_set(rule_schema_path, rule_set_path)

    profile_class = _infer_profile_class(profile)

    secrets, secret_diags = load_secrets_from_env(env_file)

    flat_profile = _flatten_profile_by_module(profile)
    flat_env = _flatten_env_secrets(secrets)

    findings: list[dict[str, Any]] = []
    for rule in rule_set["rules"]:
        if not rule.get("enabled", True):
            continue

        rule_scopes = set(rule.get("scope", []))
        if rule_scopes & _PROFILE_SCOPES:
            findings.extend(_rule_matches(
                rule, flat_profile, profile_class,
                redact_values=redact_values,
            ))

        if rule_scopes & _ENV_SCOPES:
            findings.extend(_rule_matches(
                rule, flat_env, profile_class,
                redact_values=redact_values,
            ))

    findings.extend(_check_secret_reuse(flat_profile + flat_env))
    
    findings.sort(key=lambda x: (
        _SEVERITY_ORDER.get(x["severity"], 99),
        x["rule_id"],
        x["module"],
        x["path"],
    ))

    return findings, secret_diags
