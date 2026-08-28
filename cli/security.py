#cli/security.py
"""
Security validation of composition profiles against a rule set.
Scans both the profile config values and .env secrets for vulnerabilities.
${secrets.*} interpolation references in the profile are intentional and skipped.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from importlib.resources import files
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator

from .diagnostics import Diagnostic
from .loader import load_yaml_file, resolve_module_file
from .planner import build_plan
from .renderer import _compose_service_name, render_compose
from .secrets import load_secrets_from_env
from .security_common import SECRET_KEY_RE, SEVERITY_ORDER, infer_profile_class

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

# Rules in this scope are matched against the *rendered* Compose service
# definitions (command/entrypoint/logging), not the profile or .env inputs.
# This is the only way to see where a module's implementation template
# actually places a secret-bearing value (e.g. a "${config.x}" reference
# used inside a "command:" list becomes a Compose-time "${CDS_*}"
# placeholder that leaks via /proc/<pid>/cmdline once docker compose
# substitutes it) -- that placement is invisible in the unrendered profile.
_RENDERED_COMPOSE_SCOPES = {
    "rendered-compose",
}

# Compose service keys where a value is exposed via process listings
# (command args / entrypoint / healthcheck probe) or captured in logging
# configuration, as opposed to "environment", which is comparatively
# better protected.
_LEAK_PRONE_SERVICE_KEYS = ("command", "entrypoint", "healthcheck", "logging")
# ---------------------------------------------------------------------------
# File I/O
# ---------------------------------------------------------------------------

def _load_json(path: Path | Traversable) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _load_yaml(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


# ---------------------------------------------------------------------------
# Rule set loading
# ---------------------------------------------------------------------------

def _validate_rule_set(
    rule_schema_path: Path | Traversable | None = None,
    rule_set_path: Path | Traversable | None = None,
) -> dict[str, Any]:
    resources = files("cli.resources")
    rule_schema_path = rule_schema_path or resources.joinpath("rule-schema.json")
    rule_set_path = rule_set_path or resources.joinpath("rule-set.json")
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


def _flatten_profile_by_module(
    profile: dict[str, Any],
    profile_dir: Path | None = None,
) -> list[tuple[str, str, Any]]:
    """
    Returns (module_id, path, value) triples from the profile.

    - Per-module config is emitted under the module's id.
    - Top-level and spec-level keys outside modules are emitted as "<profile>".
    - Disabled modules are skipped.
    - ${secrets.*} references are left in place here; filtered in rule_matches.
    - If profile_dir is given, each module's own module.yaml is resolved and
      its metadata.productionSuitable (when explicitly false) is exposed as
      a synthetic "_module.productionSuitable" entry, so CDS-SEC-073 can
      flag a non-local profile using a module that isn't production-suitable.
      Resolution failures are skipped silently here; cli/planner.py already
      reports them as validation diagnostics.
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

        if profile_dir is not None and "source" in module_instance:
            module_root = os.getenv("CDS_MODULE_PATH")
            module_root_path = Path(module_root) if module_root else None
            module_file, _diags = resolve_module_file(
                source=module_instance["source"],
                profile_dir=profile_dir,
                module_root=module_root_path,
            )
            if module_file is not None:
                module_def, _diags = load_yaml_file(module_file)
                if module_def is not None:
                    production_suitable = module_def.get("metadata", {}).get(
                        "productionSuitable", True
                    )
                    if production_suitable is False:
                        results.append((module_id, "_module.productionSuitable", False))

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


def _normalize_scan_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(Path.cwd().resolve()).as_posix()
    except ValueError:
        return resolved.as_posix()


def _flatten_env_inputs(
    secrets: dict[str, str],
    env_file: str | None,
) -> list[tuple[str, str, Any]]:
    """
    Emit both loaded .env secrets and the env file path itself when present.

    Some rules evaluate how a secret-bearing env file is located or managed
    rather than inspecting individual secret values. Represent the file path as
    a synthetic flat item so those rules can use the same matcher.
    """
    items = _flatten_env_secrets(secrets)
    env_path = Path(env_file) if env_file is not None else Path(".env")
    if env_path.exists():
        scan_path = _normalize_scan_path(env_path)
        items.append(("<env>", scan_path, scan_path))
    return items


def _flatten_rendered_leak_surfaces(
    compose: dict[str, Any] | None,
    service_to_module: dict[str, str] | None = None,
) -> list[tuple[str, str, Any]]:
    """
    Flatten only the leak-prone parts of a rendered Compose document:
    each service's "command", "entrypoint", "healthcheck", and "logging"
    fields.

    Unlike the profile flattener, this operates on the fully rendered
    Compose model, so "${config.x}" module template references have
    already been resolved to their final "${CDS_*}" Compose-time
    placeholders (or literal values) -- the actual shape a rule needs to
    inspect to tell whether a secret-bearing value ends up somewhere that
    leaks via process listings (command/entrypoint/healthcheck) or log
    configuration, rather than the module.yaml source or profile config
    that produced it.

    `service_to_module` maps a rendered Compose service name (e.g.
    "vault-vault") back to the profile module id that produced it (e.g.
    "vault"), so findings attribute the same "module" identity other rules
    use. Compose service names are namespaced by the renderer
    (`_compose_service_name`) and don't always equal the module id; when no
    mapping is supplied (or a service name has none), the raw Compose
    service name is used as a documented fallback.
    """
    if not isinstance(compose, dict):
        return []

    services = compose.get("services", {})
    if not isinstance(services, dict):
        return []

    service_to_module = service_to_module or {}
    results: list[tuple[str, str, Any]] = []
    for service_name, service_def in services.items():
        if not isinstance(service_def, dict):
            continue
        module_id = service_to_module.get(service_name, service_name)
        for key in _LEAK_PRONE_SERVICE_KEYS:
            if key not in service_def:
                continue
            base_path = f"services.{service_name}.{key}"
            for path, value in _flatten(service_def[key], base_path):
                results.append((module_id, path, value))
    return results


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
        # Comparing a scanned config's string, not binding to an interface.
        if exposure == "0.0.0.0" and "0.0.0.0:" not in sval:  # nosec B104  # noqa: S104
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
        if policy == "require-digest" and "@sha256:" in sval:
            return False
        if policy == "require-tag" and (":" in sval or "@sha256:" in sval):
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

    # Collect all (path, value) pairs that look like secrets
    value_to_locations: dict[str, list[tuple[str, str]]] = {}
    for module_id, path, value in flat_items:
        if not isinstance(value, str) or not value:
            continue
        if not SECRET_KEY_RE.search(path.split(".")[-1]):
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


def _map_service_to_module(plan: dict[str, Any] | None) -> dict[str, str]:
    """
    Build a rendered-Compose-service-name -> module-id map from a plan.

    The renderer namespaces each module's Compose service keys via
    `_compose_service_name(module_id, service_name)` (e.g. module "vault"'s
    "vault" service key becomes the rendered "vault-vault" service name),
    so a rendered service name doesn't always equal its owning module id.
    Findings should attribute the same module identity every other rule
    uses, so this recomputes the same namespacing the renderer applies
    (reusing its private helper directly, rather than re-implementing the
    naming rule and risking drift) against each module's pre-render
    Compose service keys from the plan.
    """
    if not isinstance(plan, dict):
        return {}

    mapping: dict[str, str] = {}
    for module in plan.get("modules", []):
        if not isinstance(module, dict):
            continue
        module_id = module.get("id")
        if not module_id:
            continue
        compose_services = (
            module.get("implementation", {}).get("compose", {}).get("services", {})
        )
        if not isinstance(compose_services, dict):
            continue
        for service_name in compose_services:
            mapping[_compose_service_name(module_id, service_name)] = module_id
    return mapping


@dataclass(frozen=True)
class PrecomputedRender:
    """
    Precomputed plan/render state a caller can hand to the security scan so
    it doesn't redundantly plan/render the same profile a second time.

    Replaces a three-argument `plan`/`rendered_compose_yaml`/
    `skip_self_plan_render` matrix (where "is None okay?" depended on
    combinations of the three) with a single object with two clear states:
    - `PrecomputedRender(plan=..., rendered_compose_yaml=...)`: the caller
      already has a successful plan and/or rendered Compose YAML to reuse.
    - `PrecomputedRender(failed=True)`: the caller already tried to plan
      and/or render the profile itself and it failed, so the scan
      shouldn't retry the same failing work.
    When no `PrecomputedRender` is passed at all, the scan does its own
    best-effort plan+render.
    """

    plan: dict[str, Any] | None = None
    rendered_compose_yaml: str | None = None
    failed: bool = False


def _try_render_compose_for_scan(
    profile_path: Path,
    env_file: str | None,
    environment: str | None,
    precomputed: PrecomputedRender | None = None,
) -> tuple[dict[str, Any] | None, dict[str, str], list[Diagnostic]]:
    """
    Resolve the rendered Compose document (and its service->module map) used
    by "rendered-compose"-scoped rules.

    Callers that already planned and/or rendered the profile for their own
    purposes (e.g. `cds test`, which runs its own "plan"/"render" stages
    right after security validation) can pass a `PrecomputedRender` in
    directly, so this doesn't redundantly plan and render the same profile
    a second time. When `precomputed` is None, this does a best-effort
    plan + render itself -- unless `precomputed.failed` is set, which tells
    this function that the caller already tried to plan/render the profile
    itself and it failed, so retrying here would just repeat the same
    failure for no benefit (e.g. `cds test`'s own "plan"/"render" stages
    already planned/rendered and reported the failure with full
    diagnostics before calling this).

    A profile that fails to plan or render is not itself a bug in this
    scan -- those failures are already surfaced with full diagnostics by
    the separate "plan"/"render" stages in `cds test` (or by the caller
    that passed in its own plan/render results), so that expected case
    returns `(None, {}, [])` quietly. Only a genuinely unexpected internal
    error (not a normal plan/render diagnostic) is worth a warning: it
    means the rendered-compose checks silently produced zero findings for
    a reason nobody surfaced, which is exactly the kind of silent gap
    #297 was about.
    """
    diagnostics: list[Diagnostic] = []
    precomputed = precomputed or PrecomputedRender()
    plan = precomputed.plan
    rendered_compose_yaml = precomputed.rendered_compose_yaml
    if precomputed.failed and rendered_compose_yaml is None:
        return None, {}, diagnostics
    try:
        if rendered_compose_yaml is None:
            if plan is None:
                plan, plan_diags = build_plan(
                    str(profile_path), env_file=env_file, environment=environment,
                )
                plan_errors = [d for d in plan_diags if d.level == "error"]
                if plan is None or plan_errors:
                    first_code = plan_errors[0].code if plan_errors else "unknown"
                    diagnostics.append(Diagnostic(
                        level="warning",
                        code="W096",
                        message=(
                            "Rendered-compose security checks (e.g. CDS-SEC-070) "
                            "were skipped because the profile could not be "
                            f"planned ({first_code}); run 'cds plan' for details."
                        ),
                        path="spec.modules",
                    ))
                    return None, {}, diagnostics

            rendered_compose_yaml, render_diags = render_compose(plan, env_file=env_file)
            render_errors = [d for d in render_diags if d.level == "error"]
            if render_errors:
                first_code = render_errors[0].code
                diagnostics.append(Diagnostic(
                    level="warning",
                    code="W096",
                    message=(
                        "Rendered-compose security checks (e.g. CDS-SEC-070) "
                        "were skipped because the profile could not be "
                        f"rendered ({first_code}); run 'cds render' for details."
                    ),
                    path="spec.modules",
                ))
                return None, {}, diagnostics

        rendered = yaml.safe_load(rendered_compose_yaml)
        service_to_module = _map_service_to_module(plan)
        return (rendered if isinstance(rendered, dict) else None), service_to_module, diagnostics
    except Exception as exc:
        diagnostics.append(Diagnostic(
            level="warning",
            code="W096",
            message=(
                "Rendered-compose security checks (e.g. CDS-SEC-070) were "
                f"skipped due to an unexpected error: {exc!r}"
            ),
            path="spec.modules",
        ))
        return None, {}, diagnostics


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def run_security_validation(
    profile_path: Path,
    rule_schema_path: Path | Traversable | None = None,
    rule_set_path: Path | Traversable | None = None,
    env_file: str | None = None,
    redact_values: bool = False,
    environment: str | None = None,
    strict: bool = False,
    precomputed_render: PrecomputedRender | None = None,
) -> tuple[list[dict[str, Any]], list[Diagnostic]]:
    """
    Validate a profile and its .env secrets against the rule set.

    Two sources are scanned:
    - Profile config values (${secrets.*} references are skipped — they are
      intentional indirections, not real values).
    - .env secret values (scanned directly for weak/leaked secrets).

    Args:
        profile_path:     Path to the profile YAML.
        rule_schema_path: Optional custom rule set JSON schema path.
        rule_set_path:    Optional custom rule set JSON path.
        env_file:         Optional path to .env file. Defaults to .env in cwd.
        redact_values:    If True, secret-like values are redacted in findings.
        environment:      Optional environment overlay name. When set, the
            profile's declared metadata.environment (and therefore the
            production security policy applied below) reflects the overlay,
            not just the base profile.
        strict:           Apply the production security rule class regardless
            of the profile's declared environment.
        precomputed_render: Optional `PrecomputedRender` used by
            "rendered-compose"-scoped rules (e.g. CDS-SEC-070). Callers that
            already planned and/or rendered the profile for their own
            purposes (e.g. `cds test`) should pass their plan/rendered
            Compose YAML in via `PrecomputedRender(plan=..., rendered_compose_yaml=...)`
            to avoid planning/rendering the profile again here, or
            `PrecomputedRender(failed=True)` if they already tried and it
            failed, so this doesn't repeat the same failing work. When
            omitted, this does its own best-effort plan+render.

    Returns:
        Tuple of (findings, diagnostics). Findings are sorted by severity,
        then rule_id, module, and path.
    """
    if environment is not None:
        # Local import: cli.overlay imports cli.validator, not cli.security,
        # so this doesn't introduce a cycle, but keep it scoped/consistent
        # with the other call sites that gained overlay support.
        from .overlay import resolve_profile

        profile, _, overlay_diags = resolve_profile(str(profile_path), environment)
        if profile is None:
            return [], overlay_diags
    else:
        profile = _load_yaml(profile_path)
        overlay_diags = []
    rule_set = _validate_rule_set(rule_schema_path, rule_set_path)

    profile_class = "prod" if strict else infer_profile_class(profile)

    secrets, secret_diags = load_secrets_from_env(env_file)

    flat_profile = _flatten_profile_by_module(profile, profile_dir=profile_path.parent)
    flat_env = _flatten_env_inputs(secrets, env_file)

    # Planning and rendering the profile is only useful when some enabled
    # rule actually declares the "rendered-compose" scope -- e.g. a custom
    # rule set may omit CDS-SEC-070 entirely, in which case doing a full
    # plan+render here would be wasted work on every security scan.
    needs_rendered_compose = any(
        rule.get("enabled", True) and set(rule.get("scope", [])) & _RENDERED_COMPOSE_SCOPES
        for rule in rule_set["rules"]
    )
    if needs_rendered_compose:
        rendered_compose, service_to_module, render_scan_diags = _try_render_compose_for_scan(
            profile_path, env_file, environment,
            precomputed=precomputed_render,
        )
    else:
        rendered_compose, service_to_module, render_scan_diags = None, {}, []
    flat_rendered = _flatten_rendered_leak_surfaces(rendered_compose, service_to_module)

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

        if rule_scopes & _RENDERED_COMPOSE_SCOPES:
            findings.extend(_rule_matches(
                rule, flat_rendered, profile_class,
                redact_values=redact_values,
            ))

    findings.extend(_check_secret_reuse(flat_profile + flat_env))
    
    findings.sort(key=lambda x: (
        SEVERITY_ORDER.get(x["severity"], 99),
        x["rule_id"],
        x["module"],
        x["path"],
    ))

    return findings, overlay_diags + secret_diags + render_scan_diags
