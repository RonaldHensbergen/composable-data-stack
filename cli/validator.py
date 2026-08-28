# cli/validator.py
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from .diagnostics import Diagnostic
from .graph import validate_dependency_graph
from .loader import load_yaml_file, resolve_module_file
from .resolver import (
    is_secret_ref,
    parse_contract_ref,
    resolve_path,
    secret_name_from_ref,
)


def _load_schema(name: str) -> dict[str, Any]:
    schema_path = Path(__file__).parent / "resources" / name
    return json.loads(schema_path.read_text(encoding="utf-8"))


def validate_profile(profile_path: str, environment: str | None = None) -> list[Diagnostic]:
    if environment is not None:
        # Local import: cli.overlay imports validate_loaded_profile from this
        # module, so importing it back at module scope would be circular.
        from .overlay import resolve_profile

        _, _, diagnostics = resolve_profile(profile_path, environment)
        return diagnostics

    profile_file = Path(profile_path)
    profile, diagnostics = load_yaml_file(profile_file)
    if profile is None:
        return diagnostics

    return diagnostics + validate_loaded_profile(profile, profile_file)


def validate_loaded_profile(profile: dict[str, Any], profile_file: Path) -> list[Diagnostic]:
    """
    Runs the full validation pipeline (shape, module configs, dependencies,
    secret refs, contract bindings, outputs) against an already-loaded
    profile dict. Split out from validate_profile so callers that produce a
    profile dict some other way, e.g. the environment-overlay resolver's
    merged result, get identical validation without re-implementing this
    orchestration.
    """
    diagnostics: list[Diagnostic] = []

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
    diagnostics.extend(validate_image_source_config(module_instances))
    diagnostics.extend(validate_outputs(profile, module_instances))
    diagnostics.extend(validate_observability_config(profile, module_instances))

    return diagnostics


def has_errors(diagnostics: list[Diagnostic]) -> bool:
    return any(d.level == "error" for d in diagnostics)


def validate_profile_shape(profile: dict[str, Any]) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []

    schema = _load_schema("profile.schema.json")
    validator = Draft202012Validator(schema)
    for err in sorted(validator.iter_errors(profile), key=lambda e: list(e.path)):
        subpath = ".".join(str(p) for p in err.path)
        diagnostics.append(
            Diagnostic("error", "E010", err.message, subpath or "profile")
        )

    spec = profile.get("spec")
    if isinstance(spec, dict):
        modules = spec.get("modules")
        if isinstance(modules, list):
            seen_ids = set()
            for i, module in enumerate(modules):
                if not isinstance(module, dict):
                    continue
                module_id = module.get("id")
                if isinstance(module_id, str) and module_id in seen_ids:
                    diagnostics.append(
                        Diagnostic(
                            "error",
                            "E011",
                            f'Duplicate module id "{module_id}".',
                            f"spec.modules[{i}].id",
                        )
                    )
                elif isinstance(module_id, str):
                    seen_ids.add(module_id)

    return diagnostics


def validate_contract_document(contract: dict[str, Any]) -> list[Diagnostic]:
    """
    Validates a standalone shared-contract document (kind: Contract) against
    contract.schema.json. Contract definition files live in shared/contracts/
    and are not part of the profile pipeline, so this is exposed as a
    standalone helper rather than wired into validate_loaded_profile.
    """
    diagnostics: list[Diagnostic] = []

    schema = _load_schema("contract.schema.json")
    validator = Draft202012Validator(schema)
    for err in sorted(validator.iter_errors(contract), key=lambda e: list(e.path)):
        subpath = ".".join(str(p) for p in err.path)
        diagnostics.append(
            Diagnostic("error", "E023", err.message, subpath or "contract")
        )

    return diagnostics


def validate_contract_file(contract_path: str | Path) -> list[Diagnostic]:
    """
    Loads a shared-contract YAML file and validates it against
    contract.schema.json. Returns load diagnostics plus schema violations.
    """
    contract_file = Path(contract_path)
    contract, diagnostics = load_yaml_file(contract_file)
    if contract is None:
        return diagnostics

    return diagnostics + validate_contract_document(contract)


def load_module_instances(profile_file: Path, profile: dict[str, Any]) -> tuple[list[dict[str, Any]], list[Diagnostic]]:
    diagnostics: list[Diagnostic] = []
    instances: list[dict[str, Any]] = []

    profile_dir = profile_file.parent
    modules = profile["spec"]["modules"]

    module_schema = _load_schema("module.schema.json")
    module_validator = Draft202012Validator(module_schema)

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

        module_errors = sorted(
            module_validator.iter_errors(module_def), key=lambda e: list(e.path)
        )
        if module_errors:
            for err in module_errors:
                subpath = ".".join(str(p) for p in err.path)
                full_path = f"spec.modules[{i}]"
                if subpath:
                    full_path += f".{subpath}"
                diagnostics.append(
                    Diagnostic(
                        level="error",
                        code="E021",
                        message=err.message,
                        path=full_path,
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


def validate_image_source_config(module_instances: list[dict[str, Any]]) -> list[Diagnostic]:
    """
    Modules that support pulling a pre-built image (`config.image.source:
    registry`, e.g. modules/orchestration/dagster) need `config.image.tag`
    set to something pullable; the module's own configSchema can't express
    "tag is required only when source is registry" as a plain JSON Schema
    constraint, so that cross-field rule is enforced here instead. A
    `tag: "latest"` is accepted but discouraged, since it defeats the
    reproducibility that `source: registry` is meant to buy over `latest`
    silently drifting between deploys.
    """
    diagnostics: list[Diagnostic] = []

    for inst in module_instances:
        image_config = inst["config"].get("image")
        if not isinstance(image_config, dict) or image_config.get("source") != "registry":
            continue

        tag = image_config.get("tag")
        if not isinstance(tag, str) or not tag:
            diagnostics.append(
                Diagnostic(
                    level="error",
                    code="E103",
                    message=(
                        'config.image.tag is required and must be a non-empty string '
                        'when config.image.source is "registry".'
                    ),
                    path=f"spec.modules[{inst['index']}].config.image.tag",
                )
            )
            continue

        if tag == "latest":
            diagnostics.append(
                Diagnostic(
                    level="warning",
                    code="W097",
                    message=(
                        'config.image.tag is "latest" with config.image.source "registry". '
                        "Pin an explicit published version instead for reproducible deploys."
                    ),
                    path=f"spec.modules[{inst['index']}].config.image.tag",
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


def validate_observability_config(profile: dict[str, Any], module_instances: list[dict[str, Any]]) -> list[Diagnostic]:
    """
    Validates the optional spec.observability block (see #174 / docs/observability.md).

    This block is intentionally module-agnostic: a profile can opt into log
    shipping and declare retention tiers without naming which module collects
    logs. `sink.contractRef` is only required when the profile wants to pin
    a specific provider of the shared `log-sink` contract.
    """
    diagnostics: list[Diagnostic] = []

    observability = profile.get("spec", {}).get("observability")
    if observability is None:
        return diagnostics

    if not isinstance(observability, dict):
        return [Diagnostic("error", "E100", "spec.observability must be an object.", "spec.observability")]

    log_shipping = observability.get("logShipping")
    if log_shipping is None:
        return diagnostics

    if not isinstance(log_shipping, dict):
        return [
            Diagnostic(
                "error", "E100", "spec.observability.logShipping must be an object.", "spec.observability.logShipping"
            )
        ]

    if "enabled" not in log_shipping:
        diagnostics.append(
            Diagnostic(
                "error",
                "E100",
                "spec.observability.logShipping.enabled is required.",
                "spec.observability.logShipping.enabled",
            )
        )
    elif not isinstance(log_shipping["enabled"], bool):
        diagnostics.append(
            Diagnostic(
                "error",
                "E100",
                "spec.observability.logShipping.enabled must be a boolean.",
                "spec.observability.logShipping.enabled",
            )
        )

    retention = log_shipping.get("retention")
    if retention is not None:
        if not isinstance(retention, dict):
            diagnostics.append(
                Diagnostic(
                    "error",
                    "E100",
                    "spec.observability.logShipping.retention must be an object.",
                    "spec.observability.logShipping.retention",
                )
            )
        else:
            raw_days = retention.get("rawDays")
            structured_days = retention.get("structuredDays")

            def _is_positive_int(value: Any) -> bool:
                return isinstance(value, int) and not isinstance(value, bool) and value > 0

            for field_name, value in (("rawDays", raw_days), ("structuredDays", structured_days)):
                if value is not None and not _is_positive_int(value):
                    diagnostics.append(
                        Diagnostic(
                            "error",
                            "E101",
                            f"spec.observability.logShipping.retention.{field_name} must be a positive integer.",
                            f"spec.observability.logShipping.retention.{field_name}",
                        )
                    )

            if _is_positive_int(raw_days) and _is_positive_int(structured_days) and structured_days < raw_days:
                diagnostics.append(
                    Diagnostic(
                        "error",
                        "E101",
                        "spec.observability.logShipping.retention.structuredDays must be >= rawDays "
                        "(structured events are the long-retention tier; raw logs are short-retention).",
                        "spec.observability.logShipping.retention.structuredDays",
                    )
                )

    sink = log_shipping.get("sink")
    if sink is None:
        return diagnostics

    if not isinstance(sink, dict) or not isinstance(sink.get("contractRef"), str):
        diagnostics.append(
            Diagnostic(
                "error",
                "E100",
                "spec.observability.logShipping.sink must be an object with a string contractRef.",
                "spec.observability.logShipping.sink",
            )
        )
        return diagnostics

    contract_ref = sink["contractRef"]
    parsed = parse_contract_ref(contract_ref)
    if parsed is None:
        diagnostics.append(
            Diagnostic(
                "error",
                "E102",
                f'Invalid sink contractRef "{contract_ref}". Expected "<module-id>.<contract-name>".',
                "spec.observability.logShipping.sink.contractRef",
            )
        )
        return diagnostics

    module_id, provide_name = parsed
    by_id = {m["id"]: m for m in module_instances}
    producer = by_id.get(module_id)
    if producer is None:
        diagnostics.append(
            Diagnostic(
                "error",
                "E102",
                f'Sink contractRef "{contract_ref}" points to unknown module "{module_id}".',
                "spec.observability.logShipping.sink.contractRef",
            )
        )
        return diagnostics

    provides = producer["module"].get("spec", {}).get("provides", [])
    matched = next((p for p in provides if p.get("name") == provide_name), None)
    if matched is None:
        diagnostics.append(
            Diagnostic(
                "error",
                "E102",
                f'Sink contractRef "{contract_ref}" points to module "{module_id}", '
                f'but it does not provide "{provide_name}".',
                "spec.observability.logShipping.sink.contractRef",
            )
        )
        return diagnostics

    actual_kind = matched.get("contract", {}).get("kind")
    if actual_kind != "log-sink":
        diagnostics.append(
            Diagnostic(
                "error",
                "E102",
                f'Sink contractRef "{contract_ref}" resolves to contract kind "{actual_kind}", expected "log-sink".',
                "spec.observability.logShipping.sink.contractRef",
            )
        )

    return diagnostics
