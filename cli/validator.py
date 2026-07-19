# cli/validator.py
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from .diagnostics import Diagnostic
from .graph import validate_dependency_graph
from .loader import load_yaml_file, resolve_module_file
from .resolver import is_secret_ref, parse_contract_ref, resolve_path, secret_name_from_ref


def validate_profile(profile_path: str) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []

    profile_file = Path(profile_path)
    profile, diags = load_yaml_file(profile_file)
    diagnostics.extend(diags)
    if profile is None:
        return diagnostics

    diagnostics.extend(validate_profile_shape(profile))
    if has_errors(diagnostics):
        return diagnostics

    module_instances, diags = load_module_instances(profile_file, profile)
    diagnostics.extend(diags)
    if has_errors(diagnostics):
        return diagnostics

    diagnostics.extend(validate_module_configs(module_instances))
    diagnostics.extend(validate_dependencies(module_instances))
    diagnostics.extend(validate_secret_refs(profile, module_instances))
    diagnostics.extend(validate_contract_bindings(module_instances))
    diagnostics.extend(validate_outputs(profile, module_instances))

    return diagnostics


def has_errors(diagnostics: list[Diagnostic]) -> bool:
    return any(d.level == "error" for d in diagnostics)


def validate_profile_shape(profile: dict[str, Any]) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []

    if profile.get("kind") != "Profile":
        diagnostics.append(Diagnostic("error", "E010", 'Expected kind: "Profile".', "kind"))

    spec = profile.get("spec")
    if not isinstance(spec, dict):
        diagnostics.append(Diagnostic("error", "E010", "Missing or invalid spec object.", "spec"))
        return diagnostics

    modules = spec.get("modules")
    if not isinstance(modules, list):
        diagnostics.append(Diagnostic("error", "E010", "spec.modules must be a list.", "spec.modules"))
        return diagnostics

    seen_ids = set()
    for i, module in enumerate(modules):
        if not isinstance(module, dict):
            diagnostics.append(Diagnostic("error", "E010", "Module entry must be an object.", f"spec.modules[{i}]"))
            continue

        module_id = module.get("id")
        source = module.get("source")
        config = module.get("config")

        if not isinstance(module_id, str) or not module_id:
            diagnostics.append(Diagnostic("error", "E010", "Module id is required.", f"spec.modules[{i}].id"))
        elif module_id in seen_ids:
            diagnostics.append(Diagnostic("error", "E011", f'Duplicate module id "{module_id}".', f"spec.modules[{i}].id"))
        else:
            seen_ids.add(module_id)

        if not isinstance(source, str) or not source:
            diagnostics.append(Diagnostic("error", "E010", "Module source is required.", f"spec.modules[{i}].source"))

        if config is None or not isinstance(config, dict):
            diagnostics.append(Diagnostic("error", "E010", "Module config must be an object.", f"spec.modules[{i}].config"))

    return diagnostics


def load_module_instances(profile_file: Path, profile: dict[str, Any]) -> tuple[list[dict[str, Any]], list[Diagnostic]]:
    diagnostics: list[Diagnostic] = []
    instances: list[dict[str, Any]] = []

    profile_dir = profile_file.parent
    modules = profile["spec"]["modules"]

    for i, module_instance in enumerate(modules):
        if module_instance.get("enabled", True) is False:
            continue

        source = module_instance["source"]
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

        if module_def.get("kind") != "Module":
            diagnostics.append(
                Diagnostic(
                    level="error",
                    code="E021",
                    message='Expected kind: "Module".',
                    path=f"spec.modules[{i}].source",
                )
            )
            continue

        instances.append(
            {
                "index": i,
                "id": module_instance["id"],
                "source": source,
                "config": module_instance["config"],
                "dependsOn": module_instance.get("dependsOn", []),
                "instance": module_instance,
                "module": module_def,
                "module_file": str(module_file),
            }
        )

    return instances, diagnostics


def validate_module_configs(module_instances: list[dict[str, Any]]) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []

    for inst in module_instances:
        schema = inst["module"].get("spec", {}).get("configSchema")
        if not isinstance(schema, dict):
            diagnostics.append(
                Diagnostic("error", "E021", "Module is missing spec.configSchema.", f"module:{inst['id']}.spec.configSchema")
            )
            continue

        validator = Draft202012Validator(schema)
        errors = sorted(validator.iter_errors(inst["config"]), key=lambda e: list(e.path))

        for err in errors:
            subpath = ".".join(str(p) for p in err.path)
            full_path = f"spec.modules[{inst['index']}].config"
            if subpath:
                full_path += f".{subpath}"

            diagnostics.append(
                Diagnostic(
                    level="error",
                    code="E030",
                    message=err.message,
                    path=full_path,
                )
            )

    return diagnostics


def validate_dependencies(module_instances: list[dict[str, Any]]) -> list[Diagnostic]:
    module_ids = {m["id"] for m in module_instances}
    depends_on_map = {m["id"]: m.get("dependsOn", []) for m in module_instances}
    return validate_dependency_graph(module_ids, depends_on_map)


def validate_secret_refs(profile: dict[str, Any], module_instances: list[dict[str, Any]]) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []

    secrets_values = (
        profile.get("spec", {})
        .get("secrets", {})
        .get("values", {})
    )

    for inst in module_instances:
        walk_for_secret_refs(
            obj=inst["config"],
            current_path=f"spec.modules[{inst['index']}].config",
            known_secrets=set(secrets_values.keys()),
            diagnostics=diagnostics,
        )

    return diagnostics


def walk_for_secret_refs(obj: Any, current_path: str, known_secrets: set[str], diagnostics: list[Diagnostic]) -> None:
    if isinstance(obj, dict):
        for k, v in obj.items():
            walk_for_secret_refs(v, f"{current_path}.{k}", known_secrets, diagnostics)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            walk_for_secret_refs(v, f"{current_path}[{i}]", known_secrets, diagnostics)
    else:
        if is_secret_ref(obj):
            secret_name = secret_name_from_ref(obj)
            if secret_name not in known_secrets:
                diagnostics.append(
                    Diagnostic(
                        level="error",
                        code="E050",
                        message=f'Secret ref "{obj}" is not defined in spec.secrets.values.',
                        path=current_path,
                    )
                )


def validate_contract_bindings(module_instances: list[dict[str, Any]]) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []

    by_id = {m["id"]: m for m in module_instances}

    for inst in module_instances:
        consumes = inst["module"].get("spec", {}).get("consumes", [])
        for consume in consumes:
            name = consume.get("name")
            expected_kind = consume.get("contract", {}).get("kind")
            mapped_from = consume.get("mappedFrom")

            if not name or not expected_kind or not mapped_from:
                diagnostics.append(
                    Diagnostic(
                        level="error",
                        code="E021",
                        message=f'Consume entry in module "{inst["id"]}" is missing name, contract.kind, or mappedFrom.',
                        path=f'module:{inst["id"]}.spec.consumes',
                    )
                )
                continue

            required = consume.get("required", True)

            try:
                value = resolve_path({"spec": {"config": inst["config"]}}, mapped_from)
            except KeyError:
                if not required:
                    continue
                diagnostics.append(
                    Diagnostic(
                        level="error",
                        code="E041",
                        message=f'Path "{mapped_from}" could not be resolved in module instance config.',
                        path=f"spec.modules[{inst['index']}].config",
                    )
                )
                continue

            if not isinstance(value, dict) or "contractRef" not in value:
                if not required and not value:
                    continue
                diagnostics.append(
                    Diagnostic(
                        level="error",
                        code="E041",
                        message=f'Consume binding "{name}" must resolve to an object with "contractRef".',
                        path=f"spec.modules[{inst['index']}].config",
                    )
                )
                continue

            contract_ref = value["contractRef"]
            parsed = parse_contract_ref(contract_ref)
            if parsed is None:
                diagnostics.append(
                    Diagnostic(
                        level="error",
                        code="E041",
                        message=f'Invalid contract ref "{contract_ref}". Expected "<module-id>.<contract-name>".',
                        path=f"spec.modules[{inst['index']}].config",
                    )
                )
                continue

            producer_id, provide_name = parsed
            producer = by_id.get(producer_id)
            if producer is None:
                diagnostics.append(
                    Diagnostic(
                        level="error",
                        code="E041",
                        message=f'Contract ref "{contract_ref}" points to unknown module "{producer_id}".',
                        path=f"spec.modules[{inst['index']}].config",
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
                        message=f'Contract ref "{contract_ref}" points to module "{producer_id}", but it does not provide "{provide_name}".',
                        path=f"spec.modules[{inst['index']}].config",
                    )
                )
                continue

            actual_kind = matched.get("contract", {}).get("kind")
            if actual_kind != expected_kind:
                diagnostics.append(
                    Diagnostic(
                        level="error",
                        code="E042",
                        message=(
                            f'Contract kind mismatch for "{contract_ref}": '
                            f'consumer expects "{expected_kind}", provider exposes "{actual_kind}".'
                        ),
                        path=f"spec.modules[{inst['index']}].config",
                    )
                )

    return diagnostics


def validate_outputs(profile: dict[str, Any], module_instances: list[dict[str, Any]]) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []

    by_id = {m["id"]: m for m in module_instances}
    outputs = profile.get("spec", {}).get("outputs", {}).get("contracts", {})

    if not isinstance(outputs, dict):
        return diagnostics

    for output_name, output_value in outputs.items():
        if not isinstance(output_value, dict) or "from" not in output_value:
            diagnostics.append(
                Diagnostic(
                    level="error",
                    code="E060",
                    message=f'Output "{output_name}" must be an object with a "from" field.',
                    path=f"spec.outputs.contracts.{output_name}",
                )
            )
            continue

        ref = output_value["from"]
        parsed = parse_contract_ref(ref)
        if parsed is None:
            diagnostics.append(
                Diagnostic(
                    level="error",
                    code="E060",
                    message=f'Invalid output ref "{ref}". Expected "<module-id>.<contract-name>".',
                    path=f"spec.outputs.contracts.{output_name}.from",
                )
            )
            continue

        module_id, provide_name = parsed
        producer = by_id.get(module_id)
        if producer is None:
            diagnostics.append(
                Diagnostic(
                    level="error",
                    code="E060",
                    message=f'Output ref "{ref}" points to unknown module "{module_id}".',
                    path=f"spec.outputs.contracts.{output_name}.from",
                )
            )
            continue

        provides = producer["module"].get("spec", {}).get("provides", [])
        matched = next((p for p in provides if p.get("name") == provide_name), None)
        if matched is None:
            diagnostics.append(
                Diagnostic(
                    level="error",
                    code="E060",
                    message=f'Output ref "{ref}" points to module "{module_id}", but it does not provide "{provide_name}".',
                    path=f"spec.outputs.contracts.{output_name}.from",
                )
            )

    return diagnostics
