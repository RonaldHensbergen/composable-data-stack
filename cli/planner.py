# cli/planner.py
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any
import os
import re

from .diagnostics import Diagnostic
from .loader import load_yaml_file
from .resolver import is_secret_ref, parse_contract_ref, resolve_path, secret_name_from_ref
from .secrets import load_profile_secrets

def build_plan(profile_path: str, env_file: str | None = None) -> tuple[dict[str, Any] | None, list[Diagnostic]]:
    """
    Build a resolved plan from a profile.
    
    Args:
        profile_path: Path to profile.yaml
        env_file: Optional path to .env file for secrets
        
    Returns:
        Tuple of (plan, diagnostics)
    """
    diagnostics: list[Diagnostic] = []
    
    profile_file = Path(profile_path)
    profile, diags = load_yaml_file(profile_file)
    diagnostics.extend(diags)
    
    if profile is None:
        return None, diagnostics
    
    spec = profile.get("spec", {})
    secrets, secret_diags = load_profile_secrets(spec.get("secrets"), env_file)
    diagnostics.extend(secret_diags)
    
    modules = spec.get("modules", [])
    profile_dir = profile_file.parent
    
    loaded_modules: list[dict[str, Any]] = []
    module_instances_by_id: dict[str, dict[str, Any]] = {}
    
    for i, module_instance in enumerate(modules):
        if module_instance.get("enabled", True) is False:
            continue
        
        source = module_instance["source"]
        source_path = Path(source)
        if not source_path.is_absolute() and source_path.parts and source_path.parts[0] == ".":
            source_path = source_path.relative_to(".")

        module_root = os.getenv("CDS_MODULE_PATH")
        module_file = None
        if module_root:
            module_root_path = Path(module_root).expanduser()
            if module_root_path.is_file():
                module_root_path = module_root_path.parent
            candidate = (module_root_path / source_path / "module.yaml").resolve()
            if candidate.exists():
                module_file = candidate

        if module_file is None:
            module_file = (profile_dir / source_path / "module.yaml").resolve()

        module_def, diags = load_yaml_file(module_file)
        diagnostics.extend(diags)
        
        if module_def is None:
            continue
        
        normalized_config = apply_defaults(
            module_instance.get("config", {}),
            module_def.get("spec", {}).get("configSchema", {})
        )

        normalized_config = resolve_secret_refs(normalized_config, secrets, f"spec.modules[{i}].config", diagnostics)
        normalized_config = substitute_values(normalized_config, {"secrets": secrets})

        loaded = {
            "index": i,
            "id": module_instance["id"],
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
        resolved_contracts_by_module[inst["id"]] = resolve_provided_contracts(inst, secrets)
    
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
        "runtime": spec.get("runtime", {}),
        "secrets": secrets,  # Include resolved secrets in plan
        "outputs": resolve_outputs(spec.get("outputs", {}), resolved_contracts_by_module, diagnostics),
        "modules": planned_modules,
    }
    
    return plan, diagnostics

def apply_defaults(config: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
    config_copy = deepcopy(config)
    return _apply_schema_defaults(config_copy, schema)


def _apply_schema_defaults(value: Any, schema: dict[str, Any]) -> Any:
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
                    result[prop_name] = deepcopy(prop_schema["default"])
                elif prop_schema.get("type") == "object":
                    nested_default = _apply_schema_defaults({}, prop_schema)
                    if nested_default:
                        result[prop_name] = nested_default
            else:
                result[prop_name] = _apply_schema_defaults(result[prop_name], prop_schema)

        return result

    if schema_type == "array":
        if value is None:
            return []
        if not isinstance(value, list):
            return value
        item_schema = schema.get("items", {})
        return [_apply_schema_defaults(item, item_schema) for item in value]

    return value

def resolve_provided_contracts(inst: dict[str, Any], secrets: dict[str, str] = {}) -> dict[str, Any]:
    """
    Resolve contracts provided by a module instance.
    """
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
    secrets: dict[str, str] = {},
) -> dict[str, Any]:
    consumes = inst["module"].get("spec", {}).get("consumes", [])
    resolved: dict[str, Any] = {}

    for consume in consumes:
        consume_name = consume.get("name")
        mapped_from = consume.get("mappedFrom")

        if not consume_name or not mapped_from:
            continue

        try:
            binding = resolve_path({"spec": {"config": inst["config"]}}, mapped_from)
        except KeyError:
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
        if secret_name in secrets:
            return secrets[secret_name]

        diagnostics.append(
            Diagnostic(
                level="error",
                code="E081",
                message=f'Secret ref "{obj}" could not be resolved.',
                path=current_path,
            )
        )

    return obj


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


def substitute_values(obj: Any, context: dict[str, Any]) -> Any:
    """
    Recursively substitute interpolations in object.
    Supports both pure ${...} and mixed ${...} interpolations.
    """
    if isinstance(obj, dict):
        return {k: substitute_values(v, context) for k, v in obj.items()}
    if isinstance(obj, list):
        return [substitute_values(v, context) for v in obj]
    if isinstance(obj, str):
        return substitute_string(obj, context)
    return obj


def substitute_string(value: str, context: dict[str, Any]) -> Any:
    """
    Substitute interpolations in a string.
    
    Supports:
    - Pure: ${config.name} (entire value replaced, preserves type)
    - Mixed: "prefix-${config.name}-suffix" (string concatenation)
    - Secrets: ${secrets.CDS_PASSWORD} (from .env or environment)
    
    Examples:
        "${config.name}" -> value of config.name (any type)
        "db://${config.host}:5432" -> "db://localhost:5432"
        "${secrets.CDS_DB_PASSWORD}" -> password from .env
        "host=${bindings.db.host}" -> "host=postgres"
    """
    # Check if the entire string is a single pure interpolation
    if value.startswith("${") and value.endswith("}") and value.count("${") == 1:
        expr = value[2:-1]
        result = resolve_expr(expr, context)
        if result is not None:
            return result
    
    # Handle mixed interpolation: find all ${...} patterns
    pattern = r"\$\{([^}]+)\}"
    has_interpolation = "${" in value
    
    if has_interpolation:
        def replace_expr(match: re.Match) -> str:
            expr = match.group(1)
            resolved = resolve_expr(expr, context)
            # Convert to string for concatenation
            return str(resolved) if resolved is not None else match.group(0)

        result = re.sub(pattern, replace_expr, value)
        return result
    
    return value


def resolve_expr(expr: str, context: dict[str, Any]) -> Any:
    """
    Resolve an expression like "config.database.host" or "secrets.CDS_PASSWORD".

    Returns None if path not found.
    """
    parts = expr.split(".")

    current: Any = context
    for part in parts:
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]

    return current

