import json
import re
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator


def load_json(path: Path):
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def load_yaml(path: Path):
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def validate_rule_set(rule_schema_path: Path, rule_set_path: Path):
    schema = load_json(rule_schema_path)
    rule_set = load_json(rule_set_path)
    validator = Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(rule_set), key=lambda e: list(e.path))
    if errors:
        msgs = []
        for err in errors:
            loc = ".".join(str(x) for x in err.path) or "<root>"
            msgs.append(f"{loc}: {err.message}")
        raise ValueError("Rule-set validation failed:\n  - " + "\n  - ".join(msgs))
    return rule_set


def flatten(obj, prefix=""):
    items = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            path = f"{prefix}.{k}" if prefix else str(k)
            items.extend(flatten(v, path))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            path = f"{prefix}[{i}]"
            items.extend(flatten(v, path))
    else:
        items.append((prefix, obj))
    return items


def flatten_profile_by_module(profile: dict) -> list[tuple[str, str, Any]]:
    """
    Returns a list of (module_id, path, value) tuples.

    - Module config keys are flattened under their module id.
    - Top-level profile keys outside spec.modules are emitted with
      module_id = "<profile>" so they are never silently dropped.
    """
    results: list[tuple[str, str, Any]] = []
    spec = profile.get("spec", {})
    modules = spec.get("modules", [])

    # Per-module config
    for module_instance in modules:
        if module_instance.get("enabled", False) is False:
            continue
        module_id = module_instance.get("id", "<unknown>")
        config = module_instance.get("config", {})
        for path, value in flatten(config):
            results.append((module_id, path, value))

    # Top-level profile keys (runtime, secrets, outputs, metadata, etc.)
    for key, value in profile.items():
        if key == "spec":
            # Flatten spec-level keys that are NOT modules
            for spec_key, spec_value in spec.items():
                if spec_key == "modules":
                    continue
                for path, value_ in flatten(spec_value, spec_key):
                    results.append(("<profile>", path, value_))
        else:
            for path, value_ in flatten(value, key):
                results.append(("<profile>", path, value_))

    return results


def entropy_like(value: str) -> bool:
    if not isinstance(value, str) or len(value) < 16:
        return False
    classes = 0
    classes += bool(re.search(r"[a-z]", value))
    classes += bool(re.search(r"[A-Z]", value))
    classes += bool(re.search(r"\d", value))
    classes += bool(re.search(r"[^A-Za-z0-9]", value))
    return classes >= 3


def service_type_for_path(path: str) -> str:
    p = path.lower()
    if "superset" in p or "dagster-webserver" in p or "ui" in p:
        return "admin-ui"
    if "postgres" in p or "mysql" in p or "db" in p:
        return "database"
    return "generic"


def path_pattern_to_regex(pattern: str) -> str:
    escaped = re.escape(pattern)
    escaped = escaped.replace(r"\*", ".*")
    return "^" + escaped + "$"


def path_matches_any(path, patterns):
    if not patterns:
        return True
    return any(re.match(path_pattern_to_regex(p), path) for p in patterns)


def redact(value):
    if value is None:
        return None
    sval = str(value)
    if len(sval) <= 6:
        return "***"
    return sval[:2] + "***REDACTED***" + sval[-2:]


def infer_profile_class(profile):
    name = str((profile or {}).get("name", "")).lower()
    if "prod" in name:
        return "prod"
    if "stag" in name:
        return "staging"
    if "dev" in name:
        return "dev"
    return "local"


def eval_condition(path, key, value, cond, profile_class):
    sval = "" if value is None else str(value)

    if "pathPatterns" in cond and not path_matches_any(path, cond["pathPatterns"]):
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
    if cond.get("entropy") == "high" and not entropy_like(sval):
        return False
    if "minLength" in cond and len(sval) < cond["minLength"]:
        return False

    if "serviceTypes" in cond:
        if service_type_for_path(path) not in cond["serviceTypes"]:
            return False

    if "portExposure" in cond:
        if cond["portExposure"] == "0.0.0.0" and "0.0.0.0:" not in sval:
            return False
        if cond["portExposure"] == "host-published" and ":" not in sval:
            return False
        if cond["portExposure"] == "localhost-only" and not (
            sval.startswith("127.0.0.1:") or sval.startswith("localhost:")
        ):
            return False

    if "imageTagPolicy" in cond:
        if cond["imageTagPolicy"] == "forbid-latest" and not sval.endswith(":latest"):
            return False
        if cond["imageTagPolicy"] == "require-digest" and "@sha256:" not in sval:
            return False
        if cond["imageTagPolicy"] == "require-tag" and ":" not in sval and "@sha256:" not in sval:
            return False

    if "runtimeFlags" in cond and not any(flag in sval for flag in cond["runtimeFlags"]):
        return False

    if "fallbackPattern" in cond and not re.search(cond["fallbackPattern"], sval):
        return False

    if "secretSinkPolicy" in cond:
        forbidden = [
            ".labels.",
            ".annotations.",
            ".command",
            ".args.",
            "outputs.",
            "plan.preview."
        ]
        is_forbidden = any(x in path for x in forbidden)
        if cond["secretSinkPolicy"] == "forbidden" and not is_forbidden:
            return False

    return True


def rule_matches(rule, flat_items, profile_class):
    findings = []
    match = rule["match"]

    for module_id, path, value in flat_items:
        key = path.split(".")[-1] if path else ""

        if "all" in match:
            ok = all(eval_condition(path, key, value, cond, profile_class) for cond in match["all"])
        else:
            ok = any(eval_condition(path, key, value, cond, profile_class) for cond in match["any"])

        if ok:
            findings.append(
                {
                    "rule_id": rule["id"],
                    "severity": rule["severity"],
                    "module": module_id,
                    "message": rule["message"],
                    "path": path,
                    "value": redact(value),
                    "recommendation": rule["recommendation"],
                }
            )
    return findings

def run_security_validation(profile_path: Path, rule_schema_path: Path, rule_set_path: Path):
    profile = load_yaml(profile_path)
    rule_set = validate_rule_set(rule_schema_path, rule_set_path)
    profile_class = infer_profile_class(profile)
    flat_items = flatten_profile_by_module(profile)   # ← replaces flatten(profile)

    findings = []
    for rule in rule_set["rules"]:
        if rule.get("enabled", True):
            findings.extend(rule_matches(rule, flat_items, profile_class))

    findings.sort(key=lambda x: ({"high": 0, "medium": 1, "low": 2}[x["severity"]], x["rule_id"], x["module"], x["path"]))
    return findings
