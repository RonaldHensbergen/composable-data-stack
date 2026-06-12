# cli/planner.py
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

from .diagnostics import Diagnostic
from .loader import load_yaml_file
from .resolver import parse_contract_ref, resolve_path


def build_plan(profile_path: str) -> tuple[dict[str, Any] | None, list[Diagnostic]]:
    diagnostics: list[Diagnostic] = []

    profile_file = Path(profile_path)
    profile, diags = load_yaml_file(profile_file)
    diagnostics.extend(diags)
    if profile is None:
        return None, diagnostics

    spec = profile.get("spec", {})
    modules = spec.get("modules", [])
    profile_dir = profile_file.parent

    loaded_modules: list[dict[str, Any]] = []
    module_instances_by_id: dict[str, dict[str, Any]] = {}

    for i, module_instance in enumerate(modules):
        if module_instance.get("enabled", True) is False:
            continue

        source = module_instance["source"]
        module_file = (profile_dir / source / "module.yaml").resolve()

        module_def, diags = load_yaml_file(module_file)
        diagnostics.extend(diags)
        if module_def is None:
            continue

        normalized_config = apply_defaults(
            module_instance.get("config", {}),
            module_def.get("spec", {}).get("configSchema", {})
        )

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
        resolved_contracts_by_module[inst["id"]] = resolve_provided_contracts(inst)

    planned_modules: list[dict[str, Any]] = []
    for inst in loaded_modules:
        planned_modules.append(
            {
                "id": inst["id"],
                "source": inst["source"],
                "version": inst["version"],
                "dependsOn": inst["dependsOn"],
                "config": inst["config"],
                "consumes": resolve_consumed_contracts(inst, module_instances_by_id, diagnostics),
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
        "secrets": spec.get("secrets", {}),
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


def resolve_provided_contracts(inst: dict[str, Any]) -> dict[str, Any]:
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
                "secrets": {},
                "bindings": {},
            },
        )
        if provide_name:
            resolved[provide_name] = contract

    return resolved


def resolve_consumed_contracts(
    inst: dict[str, Any],
    modules_by_id: dict[str, dict[str, Any]],
    diagnostics: list[Diagnostic],
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

        provides = producer["module"].get("spec", {}).get("provides", [])
        matched = next((p for p in provides if p.get("name") == provide_name), None)
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

        contract = deepcopy(matched.get("contract", {}))
        contract = substitute_values(
            contract,
            context={
                "config": producer["config"],
                "service": {"host": producer["id"]},
                "secrets": {},
                "bindings": {},
            },
        )

        resolved[consume_name] = {
            "source": binding["contractRef"],
            "contract": contract,
        }

    return resolved


def resolve_outputs(
    outputs: dict[str, Any],
    resolved_contracts_by_module: dict[str, dict[str, Any]],
    diagnostics: list[Diagnostic],
) -> dict[str, Any]:
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
    if isinstance(obj, dict):
        return {k: substitute_values(v, context) for k, v in obj.items()}
    if isinstance(obj, list):
        return [substitute_values(v, context) for v in obj]
    if isinstance(obj, str):
        return substitute_string(obj, context)
    return obj


def substitute_string(value: str, context: dict[str, Any]) -> Any:
    if value.startswith("${") and value.endswith("}"):
        expr = value[2:-1]
        parts = expr.split(".")
        current: Any = context
        for part in parts:
            if not isinstance(current, dict) or part not in current:
                return value
            current = current[part]
        return current
    return value
