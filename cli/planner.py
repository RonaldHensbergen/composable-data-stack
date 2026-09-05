# cli/planner.py
from __future__ import annotations

import os
import re
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .constants import MAX_NESTING_DEPTH, MaxNestingDepthExceeded
from .diagnostics import Diagnostic
from .loader import load_yaml_file, resolve_module_file
from .resolver import (
    evaluate_required_if,
    is_secret_ref,
    parse_contract_ref,
    resolve_path,
    secret_name_from_ref,
)
from .secrets import load_profile_secrets


@dataclass
class SecretRef:
    """Signals that a value should be emitted as a runtime ${VAR} placeholder."""
    var_name: str

def build_plan(
    profile_path: str,
    env_file: str | None = None,
    environment: str | None = None,
    hardened: bool = False,
    image_source: str | None = None,
) -> tuple[dict[str, Any] | None, list[Diagnostic]]:
    """
    Build a resolved plan from a profile.

    Args:
        profile_path: Path to profile.yaml
        env_file: Optional path to .env file for secrets
        environment: Optional environment overlay name (e.g. "dev", "prod").
            When set, profile_path's profiles/<name>/environments/<environment>.yaml
            overlay is merged over the base profile before planning; see
            cli.overlay.resolve_profile. Value provenance for the merge is
            recorded on the returned plan under "provenance".
        hardened: When True, overrides `config.image.variant` to "hardened"
            for any module instance whose configSchema exposes an
            `image.variant` property (currently only
            modules/orchestration/dagster), leaving modules without that
            config option untouched. This is the CLI-level `--hardened`
            convenience flag (see cli/main.py's up/render/test subcommands)
            for switching to the Alpine-hardened image variant without
            hand-editing the profile YAML.
        image_source: When "build" or "registry", overrides
            `config.image.source` for any module instance whose
            configSchema exposes an `image.source` property (currently
            modules/orchestration/dagster and modules/bi/superset), leaving
            modules without that config option untouched. This is the
            CLI-level `--image-source` convenience flag (see cli/main.py's
            up/render/test subcommands and `cds config set image.source`).
            Switching to "registry" without an explicit `config.image.tag`
            re-derives the tag from the module's schema-pinned default (see
            `_apply_image_source_override`), correctly re-prefixing it for
            `--hardened`/other non-base variants.

    Returns:
        Tuple of (plan, diagnostics)
    """
    diagnostics: list[Diagnostic] = []

    profile_file = Path(profile_path)
    # Local import: cli.overlay imports from cli.validator, which this
    # module does not otherwise depend on; keep the dependency scoped to
    # avoid pulling in an import cycle for callers that never use overlays.
    from .overlay import resolve_extends, resolve_profile

    if environment is not None:
        profile, provenance, diags = resolve_profile(profile_path, environment)
    else:
        # resolve_extends() (not a bare load_yaml_file()) so a profile's own
        # `extends` chain still applies with no --environment selected, but
        # without resolve_profile()'s full validate_loaded_profile() pass,
        # which would replace build_plan()'s own defensive diagnostics for
        # malformed profiles with a single upfront validation failure.
        profile, provenance, diags = resolve_extends(profile_path)
    diagnostics.extend(diags)

    if profile is None:
        return None, diagnostics

    spec = profile.get("spec", {})
    secrets, secret_diags = load_profile_secrets(spec.get("secrets"), env_file)
    diagnostics.extend(secret_diags)

    modules = spec.get("modules", [])
    if not isinstance(modules, list):
        # Defensive guard: validate_profile() already rejects a non-list
        # spec.modules (E010), but build_plan() is a public entry point that
        # may be called directly (e.g. by tests/tools) without prior
        # validation, so it must not crash with an unhandled TypeError from
        # enumerate() on a non-iterable/scalar value.
        diagnostics.append(Diagnostic(
            level="error",
            code="E010",
            message="spec.modules must be a list.",
            path="spec.modules",
        ))
        return None, diagnostics

    profile_dir = profile_file.parent

    loaded_modules: list[dict[str, Any]] = []
    module_instances_by_id: dict[str, dict[str, Any]] = {}

    for i, module_instance in enumerate(modules):
        if not isinstance(module_instance, dict):
            # Defensive guard: validate_profile() already rejects non-object
            # module entries (E010), but build_plan() is a public entry point
            # that may be called directly (e.g. by tests/tools) without prior
            # validation, so it must not crash on malformed-but-plausible YAML.
            diagnostics.append(Diagnostic(
                level="error",
                code="E010",
                message="Module entry must be an object.",
                path=f"spec.modules[{i}]",
            ))
            continue

        if module_instance.get("enabled", True) is False:
            continue

        module_id = module_instance.get("id")
        if not module_id:
            diagnostics.append(Diagnostic(
                level="error",
                code="E010",
                message="Module id is required.",
                path=f"spec.modules[{i}].id",
            ))
            continue

        source = module_instance.get("source")
        if not source:
            diagnostics.append(Diagnostic(
                level="error",
                code="E010",
                message="Module source is required.",
                path=f"spec.modules[{i}].source",
            ))
            continue

        source_path = Path(source)
        if not source_path.is_absolute() and source_path.parts and source_path.parts[0] == ".":
            source_path = source_path.relative_to(".")

        module_root = os.getenv("CDS_MODULE_PATH")
        module_root_path = Path(module_root) if module_root else None
        module_file, diags = resolve_module_file(
            source=source,
            profile_dir=profile_dir,
            module_root=module_root_path,
            diagnostic_path=f"spec.modules[{i}].source",
        )
        diagnostics.extend(diags)
        if module_file is None:
            continue

        module_def, diags = load_yaml_file(module_file)
        diagnostics.extend(diags)

        if module_def is None:
            continue

        try:
            normalized_config = apply_defaults(
                module_instance.get("config", {}),
                module_def.get("spec", {}).get("configSchema", {})
            )
            if hardened and _supports_image_variant(module_def):
                normalized_config.setdefault("image", {})["variant"] = "hardened"
            _apply_image_source_override(normalized_config, module_def, image_source)
        except MaxNestingDepthExceeded:
            diagnostics.append(Diagnostic(
                level="error",
                code="E094",
                message=(
                    f"Module config or schema nesting exceeds the maximum "
                    f"supported depth ({MAX_NESTING_DEPTH})."
                ),
                path=f"spec.modules[{i}].config",
            ))
            continue

        # Validate secrets exist but leave "secrets.VAR" strings intact
        resolve_secret_refs(normalized_config, secrets, f"spec.modules[{i}].config", diagnostics)

        loaded = {
            "index": i,
            "id": module_id,
            "source": source,
            "version": module_instance.get("version"),
            "dependsOn": module_instance.get("dependsOn", []),
            "config": normalized_config,
            "instance": module_instance,
            "module": module_def,
            "module_file": str(module_file),
        }
        loaded_modules.append(loaded)
        module_instances_by_id[loaded["id"]] = loaded

    resolved_contracts_by_module: dict[str, dict[str, Any]] = {}
    for inst in loaded_modules:
        try:
            resolved_contracts_by_module[inst["id"]] = resolve_provided_contracts(inst, secrets)
        except MaxNestingDepthExceeded:
            diagnostics.append(Diagnostic(
                level="error",
                code="E094",
                message=(
                    f"Provided contract nesting for module \"{inst['id']}\" exceeds the "
                    f"maximum supported depth ({MAX_NESTING_DEPTH})."
                ),
                path=f"module:{inst['id']}.provides",
            ))
            resolved_contracts_by_module[inst["id"]] = {}

    planned_modules: list[dict[str, Any]] = []
    for inst in loaded_modules:
        planned_modules.append(
            {
                "id": inst["id"],
                "source": inst["source"],
                "version": inst["version"],
                "dependsOn": inst["dependsOn"],
                "config": inst["config"],
                "consumes": resolve_consumed_contracts(inst, module_instances_by_id, resolved_contracts_by_module, diagnostics, secrets),
                "provides": resolved_contracts_by_module[inst["id"]],
                "implementation": inst["module"].get("spec", {}).get("implementation", {}),
            }
        )

    plan = {
        "apiVersion": "cds/v1alpha1",
        "kind": "Plan",
        "metadata": deepcopy(profile.get("metadata", {})),
        "sourceProfile": str(profile_file),
        "environment": environment,
        "provenance": provenance,
        "runtime": spec.get("runtime", {}),
        "secrets": secrets,
        "outputs": resolve_outputs(spec.get("outputs", {}), resolved_contracts_by_module, diagnostics),
        "modules": planned_modules,
    }

    return plan, diagnostics

_CDS_VAR_PATTERN = re.compile(r"\$\{(CDS_[A-Z0-9_]+)\}")

def _substitute_config_env_vars(
    obj: Any,
    raw_env: dict[str, str],
    path: str,
    diagnostics: list[Diagnostic],
) -> Any:
    """Recursively substitute ${CDS_*} patterns in config values with values from .env."""
    if isinstance(obj, dict):
        return {k: _substitute_config_env_vars(v, raw_env, f"{path}.{k}", diagnostics) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_substitute_config_env_vars(v, raw_env, f"{path}[{i}]", diagnostics) for i, v in enumerate(obj)]
    if isinstance(obj, str):
        def _replace(m: re.Match) -> str:
            var = m.group(1)
            if var not in raw_env:
                diagnostics.append(Diagnostic(
                    level="error",
                    code="E083",
                    message=f'Config references env var "${{{var}}}" which is not set in .env or environment.',
                    path=path,
                ))
                return ""
            return raw_env[var]
        return _CDS_VAR_PATTERN.sub(_replace, obj)
    return obj

def _supports_image_variant(module_def: dict[str, Any]) -> bool:
    """
    True if a module's configSchema exposes a `image.variant` property
    (currently only modules/orchestration/dagster), which is what the
    `--hardened` CLI flag overrides. Modules without this config option
    (e.g. warehouse/postgres, bi/superset) are left untouched.
    """
    image_schema = (
        module_def.get("spec", {})
        .get("configSchema", {})
        .get("properties", {})
        .get("image", {})
    )
    return "variant" in image_schema.get("properties", {})


def _apply_image_source_override(
    normalized_config: dict[str, Any],
    module_def: dict[str, Any],
    image_source: str | None,
) -> None:
    """
    Applies the CLI-level `--image-source` override (see build_plan's
    image_source parameter) to a module instance's resolved image.* config,
    mutating normalized_config in place.

    Only touches modules whose configSchema exposes an `image.source`
    property (currently modules/orchestration/dagster and
    modules/bi/superset); modules without that config option (e.g.
    warehouse/postgres) are left unchanged.

    When switching to "registry" and the module's config still holds its
    schema-default tag (i.e. no explicit config.image.tag was pinned by the
    profile), the tag is re-derived from that same schema default so it
    stays correct even when config.image.variant was also overridden (e.g.
    by --hardened) in the same command: modules that publish
    variant-prefixed registry tags (see
    .github/workflows/publish-images.yml) get "<variant>-<default-tag>"
    instead of the bare default, which only ever refers to the base
    variant. An explicitly profile-pinned tag is never overwritten.
    """
    image_schema = (
        module_def.get("spec", {})
        .get("configSchema", {})
        .get("properties", {})
        .get("image", {})
        .get("properties", {})
    )
    if not image_source or "source" not in image_schema:
        return

    image_config = normalized_config.setdefault("image", {})
    image_config["source"] = image_source
    if image_source != "registry":
        return

    default_tag = image_schema.get("tag", {}).get("default")
    current_tag = image_config.get("tag")
    if not default_tag or current_tag != default_tag:
        return

    variant = image_config.get("variant")
    if variant and variant != "base" and not str(default_tag).startswith(f"{variant}-"):
        image_config["tag"] = f"{variant}-{default_tag}"


def apply_defaults(config: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
    config_copy = deepcopy(config)
    return _apply_schema_defaults(config_copy, schema)


def _apply_schema_defaults(value: Any, schema: dict[str, Any], _depth: int = 0) -> Any:
    if _depth > MAX_NESTING_DEPTH:
        raise MaxNestingDepthExceeded(
            f"Schema/config nesting exceeds the maximum supported depth ({MAX_NESTING_DEPTH})."
        )

    schema_type = schema.get("type")

    if value is None and "default" in schema:
        value = deepcopy(schema["default"])

    if schema_type == "object":
        if value is None:
            value = {}
        if not isinstance(value, dict):
            return value

        props = schema.get("properties", {})
        result = deepcopy(value)

        for prop_name, prop_schema in props.items():
            if prop_name not in result:
                if "default" in prop_schema:
                    # Recurse so nested properties of an object-typed default
                    # (e.g. healthcheck: {type: object, default: {}, properties:
                    # {enabled: {default: true}}}) get their own defaults filled
                    # in too, instead of stopping at the raw literal default.
                    result[prop_name] = _apply_schema_defaults(
                        deepcopy(prop_schema["default"]), prop_schema, _depth + 1
                    )
                elif prop_schema.get("type") == "object":
                    nested_default = _apply_schema_defaults({}, prop_schema, _depth + 1)
                    if nested_default:
                        result[prop_name] = nested_default
            else:
                result[prop_name] = _apply_schema_defaults(result[prop_name], prop_schema, _depth + 1)

        return result

    if schema_type == "array":
        if value is None:
            return []
        if not isinstance(value, list):
            return value
        item_schema = schema.get("items", {})
        return [_apply_schema_defaults(item, item_schema, _depth + 1) for item in value]

    return value

def resolve_provided_contracts(
    inst: dict[str, Any], secrets: dict[str, str] | None = None
) -> dict[str, Any]:
    """
    Resolve contracts provided by a module instance.
    """
    secrets = secrets if secrets is not None else {}
    provides = inst["module"].get("spec", {}).get("provides", [])
    resolved: dict[str, Any] = {}
    service_name = inst["id"]

    for provided in provides:
        provide_name = provided.get("name")
        contract = deepcopy(provided.get("contract", {}))

        contract = substitute_values(
            contract,
            context={
                "config": inst["config"],
                "service": {"host": service_name},
                "secrets": secrets,
                "bindings": {},
            },
        )

        if provide_name:
            resolved[provide_name] = contract

    return resolved


def resolve_consumed_contracts(
    inst: dict[str, Any],
    modules_by_id: dict[str, dict[str, Any]],
    resolved_contracts_by_module: dict[str, dict[str, Any]],
    diagnostics: list[Diagnostic],
    secrets: dict[str, str] | None = None,
) -> dict[str, Any]:
    secrets = secrets if secrets is not None else {}
    consumes = inst["module"].get("spec", {}).get("consumes", [])
    resolved: dict[str, Any] = {}

    for consume in consumes:
        consume_name = consume.get("name")
        mapped_from = consume.get("mappedFrom")

        if not consume_name or not mapped_from:
            continue

        required = consume.get("required", True)
        required_if = consume.get("requiredIf")
        conditionally_required = bool(required_if) and evaluate_required_if(required_if, inst["config"])
        effective_required = required or conditionally_required

        try:
            binding = resolve_path({"spec": {"config": inst["config"]}}, mapped_from)
        except KeyError:
            if not effective_required:
                continue
            diagnostics.append(
                Diagnostic(
                    level="error",
                    code="E041",
                    message=f'Path "{mapped_from}" could not be resolved in planned config.',
                    path=f'module:{inst["id"]}.consumes.{consume_name}',
                )
            )
            continue

        if not isinstance(binding, dict) or "contractRef" not in binding:
            if not effective_required and not binding:
                continue
            diagnostics.append(
                Diagnostic(
                    level="error",
                    code="E041",
                    message=f'Binding for "{consume_name}" must contain "contractRef".',
                    path=f'module:{inst["id"]}.consumes.{consume_name}',
                )
            )
            continue

        parsed = parse_contract_ref(binding["contractRef"])
        if parsed is None:
            diagnostics.append(
                Diagnostic(
                    level="error",
                    code="E041",
                    message=f'Invalid contract ref "{binding["contractRef"]}".',
                    path=f'module:{inst["id"]}.consumes.{consume_name}',
                )
            )
            continue

        producer_id, provide_name = parsed
        producer = modules_by_id.get(producer_id)
        if producer is None:
            diagnostics.append(
                Diagnostic(
                    level="error",
                    code="E041",
                    message=f'Unknown producer module "{producer_id}".',
                    path=f'module:{inst["id"]}.consumes.{consume_name}',
                )
            )
            continue

        provider_contracts = resolved_contracts_by_module.get(producer_id, {})
        matched = provider_contracts.get(provide_name)
        if matched is None:
            diagnostics.append(
                Diagnostic(
                    level="error",
                    code="E041",
                    message=f'Module "{producer_id}" does not provide "{provide_name}".',
                    path=f'module:{inst["id"]}.consumes.{consume_name}',
                )
            )
            continue

        resolved[consume_name] = {
            "contractRef": binding["contractRef"],
            "contract": deepcopy(matched),
        }

    return resolved

def resolve_secret_refs(obj: Any, secrets: dict[str, str], current_path: str, diagnostics: list[Diagnostic]) -> Any:
    """
    Validate that all secrets.* references in obj exist in the secrets dict.
    Emits E081 diagnostics for missing secrets.
    Does NOT resolve the references — values are left as "secrets.VAR_NAME" strings
    so that substitute_string can emit ${VAR_NAME} for Docker Compose runtime resolution.
    """
    if isinstance(obj, dict):
        return {
            key: resolve_secret_refs(value, secrets, f"{current_path}.{key}", diagnostics)
            for key, value in obj.items()
        }

    if isinstance(obj, list):
        return [
            resolve_secret_refs(value, secrets, f"{current_path}[{index}]", diagnostics)
            for index, value in enumerate(obj)
        ]

    if isinstance(obj, str) and is_secret_ref(obj):
        secret_name = secret_name_from_ref(obj)
        if secret_name not in secrets:
            diagnostics.append(
                Diagnostic(
                    level="error",
                    code="E081",
                    message=f'Secret ref "{obj}" could not be resolved.',
                    path=current_path,
                )
            )

    return obj  # always return unchanged


def resolve_outputs(
    outputs: dict[str, Any],
    resolved_contracts_by_module: dict[str, dict[str, Any]],
    diagnostics: list[Diagnostic],
) -> dict[str, Any]:
    """
    Resolve output contracts.
    """
    contracts = outputs.get("contracts", {})
    resolved: dict[str, Any] = {"contracts": {}}
    
    for name, value in contracts.items():
        ref = value.get("from")
        
        if not isinstance(ref, str):
            continue
        
        parsed = parse_contract_ref(ref)
        
        if parsed is None:
            diagnostics.append(
                Diagnostic(
                    level="error",
                    code="E060",
                    message=f'Invalid output ref "{ref}".',
                    path=f"spec.outputs.contracts.{name}.from",
                )
            )
            continue
        
        module_id, provide_name = parsed
        contract = resolved_contracts_by_module.get(module_id, {}).get(provide_name)
        
        if contract is None:
            diagnostics.append(
                Diagnostic(
                    level="error",
                    code="E060",
                    message=f'Output ref "{ref}" could not be resolved.',
                    path=f"spec.outputs.contracts.{name}.from",
                )
            )
            continue
        
        resolved["contracts"][name] = {
            "from": ref,
            "contract": contract,
        }
    
    return resolved


def substitute_values(obj: Any, context: dict[str, Any], _depth: int = 0) -> Any:
    """
    Recursively substitute interpolations in object.
    Supports both pure ${...} and mixed ${...} interpolations.
    """
    if _depth > MAX_NESTING_DEPTH:
        raise MaxNestingDepthExceeded(
            f"Contract/config nesting exceeds the maximum supported depth ({MAX_NESTING_DEPTH})."
        )
    if isinstance(obj, dict):
        return {k: substitute_values(v, context, _depth + 1) for k, v in obj.items()}
    if isinstance(obj, list):
        return [substitute_values(v, context, _depth + 1) for v in obj]
    if isinstance(obj, str):
        return substitute_string(obj, context)
    return obj

def substitute_string(value: str, context: dict[str, Any]) -> Any:
    """
    Substitute interpolations in a string.

    Supports:
    - Pure: ${config.name} (entire value replaced, preserves type)
    - Mixed: "prefix-${config.name}-suffix" (string concatenation)
    - Secrets: ${config.passwordFrom} or ${secrets.VAR} (emits ${VAR_NAME} for Docker Compose runtime resolution)
    - Escape: $${config.name} (emit literal ${config.name} without substitution)

    Examples:
        "${config.name}" -> value of config.name (any type)
        "db://${config.host}:5432" -> "db://localhost:5432"
        "${config.passwordFrom}" -> "${CDS_DB_PASSWORD}" (var name, resolved at runtime by Docker Compose)
        "${secrets.CDS_DB_PASSWORD}" -> "${CDS_DB_PASSWORD}"
        "host=${bindings.db.host}" -> "host=postgres"
        "$${config.name}" -> "${config.name}" (literal, not substituted)
    """
    if "${" not in value:
        return value


    ESCAPE_PLACEHOLDER = "\x00ESC\x00"
    value = value.replace("$$", ESCAPE_PLACEHOLDER)

    def replace_match(match: re.Match) -> str:
        expr = match.group(1)
        resolved = resolve_expr(expr, context)
        if resolved is None:
            return match.group(0)
        # resolve_expr already returns "${VAR_NAME}" strings for secrets —
        # str() is safe here for both secret placeholders and normal string values
        return str(resolved)

    # Pure interpolation: entire value is a single ${...} — preserves non-string types
    if value.startswith("${") and value.endswith("}") and value.count("${") == 1:
        expr = value[2:-1]
        resolved = resolve_expr(expr, context)
        if resolved is not None:
            if isinstance(resolved, str):
                return resolved.replace(ESCAPE_PLACEHOLDER, "$")
            return resolved  # int, bool, list, dict, etc.

    # Mixed interpolation: one or more ${...} embedded in a larger string
    value = re.sub(r"\$\{([^}]+)\}", replace_match, value)
    return value.replace(ESCAPE_PLACEHOLDER, "$")

def resolve_expr(expr: str, context: dict[str, Any]) -> Any:
    """
    Resolve a dotted expression against context.

    secrets.* references and config fields holding "secrets.*" strings
    always emit ${CDS_VAR_NAME} for Docker Compose runtime resolution.
    Raw secret values are never returned.
    """
    if expr.startswith("ifNonempty:"):
        path, prefix, suffix = expr[len("ifNonempty:"):].split(",", 2)
        value = resolve_expr(path, context)
        if value is None or (isinstance(value, str) and not value.strip()):
            return ""
        return f"{prefix}{value}{suffix}"

    # Direct secrets.* reference: secrets.analytics_postgres_password → ${CDS_ANALYTICS_POSTGRES_PASSWORD}
    if expr.startswith("secrets."):
        alias = expr.split(".", 1)[1]
        secrets = context.get("secrets", {})
        # alias maps to CDS_* name; fall back to alias itself if not mapped
        cds_name = secrets.get(alias, alias)
        return f"${{{cds_name}}}"

    # Walk dotted path
    parts = expr.split(".")
    value = context
    for part in parts:
        if not isinstance(value, dict) or part not in value:
            return None
        value = value[part]

    # Walked value is itself a secrets.* ref: emit ${CDS_VAR_NAME}
    if isinstance(value, str) and value.startswith("secrets."):
        alias = value.split(".", 1)[1]
        secrets = context.get("secrets", {})
        cds_name = secrets.get(alias, alias)
        return f"${{{cds_name}}}"

    return value

