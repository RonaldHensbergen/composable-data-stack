# cli/validator.py
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from .confinement import validate_confinement
from .diagnostics import Diagnostic
from .graph import validate_dependency_graph
from .image_digest_check import check_digest_staleness, collect_pinned_images_for_module
from .loader import load_yaml_file, resolve_module_file
from .resolver import (
    RequiredIfSyntaxError,
    evaluate_required_if,
    is_certificate_ref,
    is_secret_ref,
    parse_certificate_ref,
    parse_contract_ref,
    resolve_path,
    secret_name_from_ref,
)


def _load_schema(name: str) -> dict[str, Any]:
    schema_path = Path(__file__).parent / "resources" / name
    return json.loads(schema_path.read_text(encoding="utf-8"))


def validate_profile(profile_path: str, environment: str | None = None) -> list[Diagnostic]:
    # Local import: cli.overlay imports validate_loaded_profile from this
    # module, so importing it back at module scope would be circular.
    # resolve_profile() is called unconditionally (not just when environment
    # is set) because it also resolves a profile's own `extends` chain, which
    # must apply even when no --environment overlay is selected.
    from .overlay import resolve_profile

    _, _, diagnostics = resolve_profile(profile_path, environment)
    return diagnostics


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
    diagnostics.extend(validate_certificate_refs(profile, module_instances))
    diagnostics.extend(validate_contract_bindings(module_instances))
    diagnostics.extend(validate_image_source_config(module_instances))
    diagnostics.extend(validate_outputs(profile, module_instances))
    diagnostics.extend(validate_observability_config(profile, module_instances))
    diagnostics.extend(validate_backup_config(profile, module_instances))
    diagnostics.extend(validate_authentication_config(profile, module_instances))
    diagnostics.extend(validate_web_serving_mode(profile, module_instances))
    diagnostics.extend(validate_confinement_config(profile, profile_file))
    return diagnostics


def validate_web_serving_mode(
    profile: dict[str, Any], module_instances: list[dict[str, Any]]
) -> list[Diagnostic]:
    """
    Production web-serving policy (issue #216): development web servers are
    prohibited in production profiles.

    HTTP-serving modules follow the `config.webServer.mode` convention:
    `production` (the default, applied by configSchema defaults at plan time)
    or `development` (the module's built-in development server, e.g. Flask's
    or Keycloak's dev mode). Validation runs before configSchema defaults
    apply, so an absent `webServer.mode` means the module default (`production`)
    and passes; only an explicit `development` is rejected. Non-production
    profiles are untouched: local profiles may keep the development server.
    """
    diagnostics: list[Diagnostic] = []
    if profile.get("metadata", {}).get("environment") != "production":
        return diagnostics

    for inst in module_instances:
        config = inst.get("config") or {}
        web_server = config.get("webServer") if isinstance(config, dict) else None
        mode = web_server.get("mode") if isinstance(web_server, dict) else None
        if mode != "development":
            continue
        diagnostics.append(
            Diagnostic(
                "error",
                "E140",
                f'Module "{inst["id"]}" configures webServer.mode "development"; '
                "development web servers are prohibited in production profiles. "
                'Set config.webServer.mode to "production" (or remove the override) '
                "so the service runs behind its production process manager.",
                f"spec.modules[{inst['index']}].config.webServer.mode",
            )
        )
    return diagnostics


def validate_confinement_config(profile: dict[str, Any], profile_file: Path) -> list[Diagnostic]:
    """Validate spec.security.runtime.confinement and per-module security.confinement."""
    spec = profile.get("spec", {})
    base_dir = profile_file.parent
    runtime = (spec.get("security") or {}).get("runtime") or {}
    diagnostics = validate_confinement(
        runtime.get("confinement"), "spec.security.runtime.confinement", base_dir
    )
    for index, instance in enumerate(spec.get("modules") or []):
        if not isinstance(instance, dict):
            continue
        confinement = (instance.get("security") or {}).get("confinement")
        diagnostics.extend(validate_confinement(
            confinement, f"spec.modules[{index}].security.confinement", base_dir
        ))
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


def validate_certificate_refs(profile: dict[str, Any], module_instances: list[dict[str, Any]]) -> list[Diagnostic]:
    """
    Validates `certificates.<alias>.cert|.key` references in module instance
    config against the profile's spec.certificates.values aliases (issue #578).

    CDS never reads certificate or key material: this only checks that the
    alias is declared and the reference names one of the two halves of the
    pair, so a typo fails here instead of silently not substituting later.
    """
    diagnostics: list[Diagnostic] = []

    certificates_values = (
        (profile.get("spec", {}).get("certificates") or {})
        .get("values", {})
    )
    known_aliases = set(certificates_values.keys())

    for inst in module_instances:
        walk_for_certificate_refs(
            obj=inst["config"],
            current_path=f"spec.modules[{inst['index']}].config",
            known_aliases=known_aliases,
            diagnostics=diagnostics,
        )

    return diagnostics


def walk_for_certificate_refs(obj: Any, current_path: str, known_aliases: set[str], diagnostics: list[Diagnostic]) -> None:
    if isinstance(obj, dict):
        for k, v in obj.items():
            walk_for_certificate_refs(v, f"{current_path}.{k}", known_aliases, diagnostics)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            walk_for_certificate_refs(v, f"{current_path}[{i}]", known_aliases, diagnostics)
    else:
        if not is_certificate_ref(obj):
            return
        parsed = parse_certificate_ref(obj)
        if parsed is None:
            diagnostics.append(
                Diagnostic(
                    level="error",
                    code="E129",
                    message=(
                        f'Certificate ref "{obj}" is malformed: expected '
                        '"certificates.<alias>.cert" or "certificates.<alias>.key" '
                        "naming one half of a declared certificate pair."
                    ),
                    path=current_path,
                )
            )
            return
        alias, _part = parsed
        if alias not in known_aliases:
            diagnostics.append(
                Diagnostic(
                    level="error",
                    code="E129",
                    message=f'Certificate ref "{obj}" is not defined in spec.certificates.values.',
                    path=current_path,
                )
            )


class CompatibilityRegistryError(Exception):
    """
    Raised when the bundled compatibility registry (or its schema) can't be
    loaded as a usable lookup: either it fails its own JSON Schema, or it
    contains a duplicate (contract, provider, consumer) pairing that the
    schema alone can't reject (JSON Schema has no way to express tuple
    uniqueness across array items). Both are packaging bugs in the bundled
    asset, not user input errors, but callers still need to turn this into a
    Diagnostic with a stable code instead of letting a raw exception surface
    as a traceback.
    """


def _load_compatibility_registry() -> dict[tuple[str, str, str], str]:
    """
    Loads and validates cli/resources/compatibility-registry.json (bundled
    with the package, unlike shared/contracts/, so it's available whether
    CDS is run from a checkout or an installed wheel), returning a lookup
    of (contract kind, provider "<category>/<name>", consumer
    "<category>/<name>") -> status ("tested" | "unsupported").

    The key is the contract *kind*, not a specific provide-name: a module
    can expose several provide-names under the same kind (e.g.
    modules/warehouse/postgres provides "sql-database", "dagster-database",
    and "superset-database", all with kind "sql-database"), and one registry
    row is intended to cover all of them for a given provider/consumer pair.
    This also means a row carries no module *version*: a "tested" row stays
    valid even after either module ships a new version (tracked as a
    follow-up, see issue #750).

    Raises CompatibilityRegistryError if the bundled registry doesn't match
    its own schema, or if it contains a duplicate (contract, provider,
    consumer) pairing -- either is a packaging bug in the bundled asset, not
    a user input error.
    """
    schema = _load_schema("compatibility-registry.schema.json")
    registry = _load_schema("compatibility-registry.json")

    validator = Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(registry), key=lambda e: list(e.path))
    if errors:
        msgs = [
            f'{".".join(str(x) for x in err.path) or "<root>"}: {err.message}'
            for err in errors
        ]
        raise CompatibilityRegistryError(
            "Compatibility registry validation failed:\n  - " + "\n  - ".join(msgs)
        )

    lookup: dict[tuple[str, str, str], str] = {}
    for index, pairing in enumerate(registry["pairings"]):
        key = (pairing["contract"], pairing["provider"], pairing["consumer"])
        if key in lookup:
            contract, provider, consumer = key
            raise CompatibilityRegistryError(
                f"Compatibility registry has a duplicate pairing at pairings[{index}]: "
                f'contract="{contract}", provider="{provider}", consumer="{consumer}" '
                "is already recorded earlier in the file."
            )
        lookup[key] = pairing["status"]

    return lookup


def _module_key(module_def: dict[str, Any]) -> str | None:
    """"<category>/<name>" identity for a module, stable across profiles (unlike
    a profile-scoped instance id or a source path), used to look up
    tested/unsupported pairings in the compatibility registry."""
    metadata = module_def.get("metadata", {})
    category = metadata.get("category")
    name = metadata.get("name")
    if not isinstance(category, str) or not isinstance(name, str) or not category or not name:
        return None
    return f"{category}/{name}"


def _provider_advertises_tls(provider_contract: dict[str, Any], producer_inst: dict[str, Any]) -> bool:
    """
    Evaluate the provider's contract-spec `tls` advertisement for the
    tls:required consume check (issue #577).

    A boolean literal is used as-is. A pure "${config.<path>}" reference is
    resolved against the producer instance's profile config -- validation runs
    before the planner applies configSchema defaults, so a config-driven
    advertisement only satisfies a TLS requirement when the profile sets the
    referenced value explicitly. Anything else (an absent field, `false`, a
    missing config path, or any other template expression) means "no TLS
    advertised", so the requirement fails closed.
    """
    value = (provider_contract.get("spec") or {}).get("tls")
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.startswith("${config.") and value.endswith("}"):
        path = value[2:-1]
        try:
            resolved = resolve_path({"config": producer_inst.get("config")}, path)
        except KeyError:
            return False
        if isinstance(resolved, bool):
            return resolved
        if isinstance(resolved, str) and resolved.lower() == "true":
            return True
    return False


def validate_contract_bindings(module_instances: list[dict[str, Any]]) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []

    by_id = {m["id"]: m for m in module_instances}
    try:
        compatibility_registry: dict[tuple[str, str, str], str] | None = _load_compatibility_registry()
    except CompatibilityRegistryError as exc:
        diagnostics.append(
            Diagnostic(
                level="error",
                code="E092",
                message=f"Compatibility registry could not be loaded: {exc}",
                path="cli/resources/compatibility-registry.json",
            )
        )
        compatibility_registry = None

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
            required_if = consume.get("requiredIf")
            if required_if:
                try:
                    conditionally_required = evaluate_required_if(required_if, inst["config"])
                except RequiredIfSyntaxError as err:
                    diagnostics.append(
                        Diagnostic(
                            level="error",
                            code="E021",
                            message=f'Consume entry "{name}" in module "{inst["id"]}" has a malformed requiredIf: {err}',
                            path=f'module:{inst["id"]}.spec.consumes',
                        )
                    )
                    conditionally_required = False
            else:
                conditionally_required = False
            effective_required = required or conditionally_required

            try:
                value = resolve_path({"spec": {"config": inst["config"]}}, mapped_from)
            except KeyError:
                if not effective_required:
                    continue
                diagnostics.append(
                    Diagnostic(
                        level="error",
                        code="E041",
                        message=(
                            f'Path "{mapped_from}" could not be resolved in module instance config'
                            + (f' (required because {required_if}).' if conditionally_required and not required else ".")
                        ),
                        path=f"spec.modules[{inst['index']}].config",
                    )
                )
                continue

            if not isinstance(value, dict) or "contractRef" not in value:
                if not effective_required and not value:
                    continue
                diagnostics.append(
                    Diagnostic(
                        level="error",
                        code="E041",
                        message=(
                            f'Consume binding "{name}" must resolve to an object with "contractRef"'
                            + (f' (required because {required_if}).' if conditionally_required and not required else ".")
                        ),
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
                continue

            if consume.get("tls") == "required" and not _provider_advertises_tls(
                matched.get("contract", {}), producer
            ):
                diagnostics.append(
                    Diagnostic(
                        level="error",
                        code="E128",
                        message=(
                            f'Consume entry "{name}" in module "{inst["id"]}" requires a '
                            f'TLS-protected endpoint (tls: required), but contract ref '
                            f'"{contract_ref}" resolves to "{producer_id}", whose "{provide_name}" '
                            'contract does not advertise one. Providers opt in by setting '
                            '"tls: true" in the provide entry\'s contract spec (either a boolean '
                            'literal or a "${config.<path>}" reference that resolves to true '
                            'against the producer instance\'s profile config).'
                        ),
                        path=f"spec.modules[{inst['index']}].config",
                    )
                )

            provider_key = _module_key(producer["module"])
            consumer_key = _module_key(inst["module"])
            if compatibility_registry is not None and provider_key and consumer_key:
                status = compatibility_registry.get((expected_kind, provider_key, consumer_key))
                if status == "unsupported":
                    diagnostics.append(
                        Diagnostic(
                            level="error",
                            code="E043",
                            message=(
                                f'Contract ref "{contract_ref}" pairs "{consumer_key}" (consumer) with '
                                f'"{provider_key}" (provider) for contract "{expected_kind}": this pairing '
                                'matches structurally but is recorded as unsupported in the compatibility '
                                'registry (cli/resources/compatibility-registry.json).'
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
        config = inst.get("config")
        if not isinstance(config, dict):
            continue
        image_config = config.get("image")
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


def validate_pinned_image_digests(module_instances: list[dict[str, Any]]) -> list[Diagnostic]:
    """
    Checks a profile's digest-pinned images (e.g. `postgres:18@sha256:...`
    in a module's compose template) against the digest the upstream
    registry currently serves for that same tag (issue #736).

    This performs one network call per distinct pinned image, so it is
    opt-in: callers must gate invocation on
    `image_digest_check.is_enabled(...)` (a `--check-image-digests` flag or
    CDS_CHECK_IMAGE_DIGESTS=1) and are expected to skip calling this
    function entirely otherwise, so `validate`/`render`/`up` stay
    network-free by default. A lookup that cannot complete (offline, auth
    failure, unsupported registry, ...) is silently skipped rather than
    reported, per the issue's acceptance criteria: this check must never
    fail a profile that would otherwise pass.
    """
    diagnostics: list[Diagnostic] = []
    cache: dict[str, dict[str, Any]] = {}

    for inst in module_instances:
        module = inst.get("module")
        if not isinstance(module, dict):
            continue
        for entry in collect_pinned_images_for_module(module):
            image = entry["image"]
            if image not in cache:
                cache[image] = check_digest_staleness(image)
            result = cache[image]

            if result["status"] != "stale":
                continue

            latest_digest = result.get("latest_digest")
            diagnostics.append(
                Diagnostic(
                    level="warning",
                    code="W100",
                    message=(
                        f'Pinned image "{image}" in module "{inst["id"]}" is behind the '
                        f"digest currently published for that tag ({latest_digest}). "
                        "Rebuild/refresh the pin to pick up the patched image, or confirm "
                        "the older digest is an intentional, still-supported pin."
                    ),
                    path=f"module:{inst['id']}.spec.implementation.compose.services.{entry['service']}.image",
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


_BACKUP_CATEGORY_WORKLOAD_CLASSES = {
    "warehouse": "warehouse",
    "orchestration": "orchestration-metadata",
    "bi": "bi-metadata",
    "identity": "identity-metadata",
    "secrets": "secrets-metadata",
}


def required_backup_workload_classes(module_instances: list[dict[str, Any]]) -> list[str]:
    """
    Workload classes a production profile must cover: `configuration` always,
    plus one class per enabled module category that holds state worth backing
    up (warehouse data, orchestration metadata, BI metadata, identity
    provider metadata such as Keycloak's realm/user database, and secret
    provider state such as Vault's storage). The `cache` category is
    deliberately unmapped: cache contents are disposable by design.
    """
    required = {"configuration"}
    for inst in module_instances:
        if inst.get("instance", {}).get("enabled") is False:
            continue
        category = inst["module"].get("metadata", {}).get("category")
        workload_class = _BACKUP_CATEGORY_WORKLOAD_CLASSES.get(category)
        if workload_class:
            required.add(workload_class)
    return sorted(required)


def validate_backup_config(profile: dict[str, Any], module_instances: list[dict[str, Any]]) -> list[Diagnostic]:
    """
    Validates the optional spec.backup block (see #210 / docs/backup-restore.md).

    Shape (types, bounds, enums) is enforced by profile.schema.json (E010);
    this checks the cross-document rules: encryption keys must reference a
    declared secret alias, a pinned target must resolve to a backup-target
    provider, and production profiles must declare encrypted, restore-verified
    backup policies for every workload class they run.
    """
    diagnostics: list[Diagnostic] = []
    spec = profile.get("spec", {})
    backup = spec.get("backup")
    is_production = profile.get("metadata", {}).get("environment") == "production"

    if backup is None:
        if is_production:
            diagnostics.append(
                Diagnostic(
                    "error",
                    "E133",
                    "Production profiles must declare spec.backup with a policy per workload class "
                    f"({', '.join(required_backup_workload_classes(module_instances))}).",
                    "spec.backup",
                )
            )
        return diagnostics

    if not isinstance(backup, dict):
        return diagnostics  # shape error already reported as E010

    seen: set[str] = set()
    for index, policy in enumerate(backup.get("policies", [])):
        if not isinstance(policy, dict):
            continue
        workload_class = policy.get("workloadClass")
        if workload_class in seen:
            diagnostics.append(
                Diagnostic(
                    "error",
                    "E130",
                    f'Duplicate backup policy for workload class "{workload_class}".',
                    f"spec.backup.policies[{index}].workloadClass",
                )
            )
        seen.add(workload_class)

    encryption = backup.get("encryption")
    if isinstance(encryption, dict) and is_secret_ref(encryption.get("keyFrom")):
        alias = secret_name_from_ref(encryption["keyFrom"])
        if alias not in spec.get("secrets", {}).get("values", {}):
            diagnostics.append(
                Diagnostic(
                    "error",
                    "E131",
                    f'Backup encryption key ref "{encryption["keyFrom"]}" is not defined in spec.secrets.values.',
                    "spec.backup.encryption.keyFrom",
                )
            )

    target = backup.get("target")
    backup_target_contract: dict[str, Any] | None = None
    backup_target_producer: dict[str, Any] | None = None
    if isinstance(target, dict) and isinstance(target.get("contractRef"), str):
        backup_target_contract, backup_target_producer, target_diags = _resolve_backup_target(
            target["contractRef"], module_instances
        )
        diagnostics.extend(target_diags)

    if is_production:
        path = "spec.backup"
        if not isinstance(encryption, dict):
            diagnostics.append(
                Diagnostic(
                    "error",
                    "E134",
                    "Production backups must be encrypted: declare spec.backup.encryption.keyFrom.",
                    f"{path}.encryption",
                )
            )
        elif (
            is_secret_ref(encryption.get("keyFrom"))
            and secret_name_from_ref(encryption["keyFrom"]) in spec.get("secrets", {}).get("values", {})
            and spec.get("secrets", {}).get("values", {})
            .get(secret_name_from_ref(encryption["keyFrom"]), {})
            .get("required") is not True
        ):
            alias = secret_name_from_ref(encryption["keyFrom"])
            diagnostics.append(
                Diagnostic(
                    "error",
                    "E134",
                    f'Production backup encryption key alias "{alias}" must be declared '
                    "`required: true` so a missing key fails closed at startup.",
                    f"{path}.encryption.keyFrom",
                )
            )
        if backup_target_contract is not None and backup_target_producer is not None:
            target_spec = backup_target_contract.get("contract", {}).get("spec") or {}
            contract_ref = target["contractRef"]
            if not _backup_capability_enabled(target_spec.get("encrypted"), backup_target_producer):
                diagnostics.append(
                    Diagnostic(
                        "error",
                        "E132",
                        f'Backup target contractRef "{contract_ref}" does not advertise client-side '
                        "encryption (its contract spec does not set `encrypted` to true); production "
                        "backups must be encrypted.",
                        "spec.backup.target.contractRef",
                    )
                )
            if not _backup_capability_enabled(
                target_spec.get("supportsRestoreVerification"), backup_target_producer
            ):
                diagnostics.append(
                    Diagnostic(
                        "error",
                        "E132",
                        f'Backup target contractRef "{contract_ref}" does not advertise restore '
                        "verification (its contract spec does not set `supportsRestoreVerification` "
                        "to true); production backup policies require restore verification.",
                        "spec.backup.target.contractRef",
                    )
                )
        missing = [c for c in required_backup_workload_classes(module_instances) if c not in seen]
        if missing:
            diagnostics.append(
                Diagnostic(
                    "error",
                    "E133",
                    f"Production backup policies are missing workload classes: {', '.join(missing)}.",
                    f"{path}.policies",
                )
            )
        for index, policy in enumerate(backup.get("policies", [])):
            if isinstance(policy, dict) and "restoreVerification" not in policy:
                diagnostics.append(
                    Diagnostic(
                        "error",
                        "E135",
                        f'Production backup policy "{policy.get("workloadClass")}" must declare restoreVerification.',
                        f"{path}.policies[{index}].restoreVerification",
                    )
                )

    return diagnostics


def _backup_capability_enabled(value: Any, producer_inst: dict[str, Any]) -> bool:
    """
    Evaluate a backup-target contract capability (`encrypted`,
    `supportsRestoreVerification`) the same way the TLS advertisement check
    works: a boolean literal is used as-is; a pure `${config.<path>}`
    reference is resolved against the producer instance's profile config
    (validation runs before the planner applies configSchema defaults, so a
    config-driven advertisement only counts when the profile sets the value
    explicitly). Anything else means the capability is not advertised, so
    the production requirement fails closed.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.startswith("${config.") and value.endswith("}"):
        try:
            resolved = resolve_path({"config": producer_inst.get("config")}, value[2:-1])
        except KeyError:
            return False
        if isinstance(resolved, bool):
            return resolved
        if isinstance(resolved, str) and resolved.lower() == "true":
            return True
    return False


def _resolve_backup_target(
    contract_ref: str, module_instances: list[dict[str, Any]]
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, list[Diagnostic]]:
    """
    Resolve spec.backup.target.contractRef to (matched provide entry,
    producer instance, diagnostics). The matched entry is None whenever the
    reference is invalid; callers use it for production capability checks.
    """
    path = "spec.backup.target.contractRef"
    parsed = parse_contract_ref(contract_ref)
    if parsed is None:
        return None, None, [
            Diagnostic(
                "error",
                "E132",
                f'Invalid backup target contractRef "{contract_ref}". Expected "<module-id>.<contract-name>".',
                path,
            )
        ]

    module_id, provide_name = parsed
    producer = next((m for m in module_instances if m["id"] == module_id), None)
    if producer is None:
        return None, None, [
            Diagnostic(
                "error", "E132", f'Backup target contractRef "{contract_ref}" points to unknown module "{module_id}".', path
            )
        ]

    provides = producer["module"].get("spec", {}).get("provides", [])
    matched = next((p for p in provides if p.get("name") == provide_name), None)
    if matched is None:
        return None, None, [
            Diagnostic(
                "error",
                "E132",
                f'Backup target contractRef "{contract_ref}" points to module "{module_id}", '
                f'but it does not provide "{provide_name}".',
                path,
            )
        ]

    actual_kind = matched.get("contract", {}).get("kind")
    if actual_kind != "backup-target":
        return None, None, [
            Diagnostic(
                "error",
                "E132",
                f'Backup target contractRef "{contract_ref}" resolves to contract kind "{actual_kind}", '
                'expected "backup-target".',
                path,
            )
        ]
    return matched, producer, []


_AUTHENTICATION_CONTRACT_KINDS = ("sql-database", "cache-service")
_AUTH_UNAUTHENTICATED_VALUES = {"", "none", "false", "optional", "disabled", "trust"}
_CREDENTIAL_FIELD_PATTERN = re.compile(r"(?i)(password|passwd|pwd|credential|secret|token)")


def _resolve_authentication_advertisement(value: Any, producer_inst: dict[str, Any]) -> Any:
    """
    Resolve a provide entry's `authentication` advertisement (issue #206)
    against the producer instance's profile config, mirroring how the TLS
    advertisement is evaluated: a literal is used as-is, a pure
    `${config.<path>}` reference resolves against the producer's config, and
    a `${ifNonempty:<path>,<prefix>,<suffix>}` expression (used by cache
    providers whose authentication is credential-driven) yields "" when the
    referenced credential is absent. Validation runs before the planner
    applies configSchema defaults, so a config-driven advertisement only
    counts when the profile sets the referenced value explicitly; anything
    unresolvable means "not advertised" and the production requirement fails
    closed.
    """
    if value is None or isinstance(value, bool):
        return value
    if not isinstance(value, str):
        return None
    if value.startswith("${ifNonempty:") and value.endswith("}"):
        args = value[2:-1][len("ifNonempty:") :].split(",", 2)
        if len(args) != 3:
            return None
        path, prefix, suffix = args
        resolved = _resolve_authentication_advertisement(f"${{{path}}}", producer_inst)
        if resolved is None or (isinstance(resolved, str) and not resolved.strip()):
            return ""
        return f"{prefix}{resolved}{suffix}"
    if value.startswith("${config.") and value.endswith("}"):
        try:
            return resolve_path({"config": producer_inst.get("config")}, value[2:-1])
        except KeyError:
            return None
    return value


def _advertisement_authenticated(resolved: Any) -> bool:
    """Whether a resolved `authentication` advertisement describes a service
    that rejects unauthenticated clients: boolean true, or a non-empty string
    outside the "no authentication" vocabulary above."""
    if resolved is True:
        return True
    return isinstance(resolved, str) and resolved.strip().lower() not in _AUTH_UNAUTHENTICATED_VALUES


def validate_authentication_config(
    profile: dict[str, Any], module_instances: list[dict[str, Any]]
) -> list[Diagnostic]:
    """
    Production authentication policy for relational database and cache
    providers (issue #206):

    - every sql-database provider must advertise an explicit host
      authentication method, and that method must not be trust;
    - every cache-service provider must advertise that it rejects
      unauthenticated clients;
    - every credential-named config field of those providers must reference
      a secret alias declared `required: true` (so a missing value fails
      closed at startup) instead of a plaintext literal or a `${CDS_*}`
      profile input, whose value CDS would resolve and embed.

    Non-production profiles are untouched: a development cache may stay
    unauthenticated, exactly as before.
    """
    diagnostics: list[Diagnostic] = []
    if profile.get("metadata", {}).get("environment") != "production":
        return diagnostics

    spec = profile.get("spec", {})
    secret_values = (spec.get("secrets") or {}).get("values") or {}

    for inst in module_instances:
        provides_auth_scoped = False
        for provided in inst["module"].get("spec", {}).get("provides", []):
            kind = (provided.get("contract") or {}).get("kind")
            if kind not in _AUTHENTICATION_CONTRACT_KINDS:
                continue
            provides_auth_scoped = True
            provide_name = provided.get("name") or "<unnamed>"
            path = f"spec.modules[{inst['index']}].config"
            advertisement = ((provided.get("contract") or {}).get("spec") or {}).get("authentication")
            resolved = _resolve_authentication_advertisement(advertisement, inst)

            if kind == "sql-database":
                if isinstance(resolved, str) and resolved.strip().lower() == "trust":
                    diagnostics.append(
                        Diagnostic(
                            "error",
                            "E139",
                            f'Module "{inst["id"]}" provides contract "{provide_name}" (sql-database) with '
                            'host authentication method "trust"; trust authentication is prohibited in '
                            "production profiles. Configure an explicit password method such as "
                            "scram-sha-256.",
                            path,
                        )
                    )
                elif not _advertisement_authenticated(resolved):
                    diagnostics.append(
                        Diagnostic(
                            "error",
                            "E139",
                            f'Module "{inst["id"]}" provides contract "{provide_name}" (sql-database) '
                            "without an explicit host authentication advertisement; production relational "
                            "databases must advertise a modern password authentication method "
                            "(e.g. scram-sha-256), and trust authentication is prohibited.",
                            path,
                        )
                    )
                elif isinstance(resolved, str) and resolved.strip().lower() == "md5":
                    diagnostics.append(
                        Diagnostic(
                            "warning",
                            "W105",
                            f'Module "{inst["id"]}" provides contract "{provide_name}" (sql-database) with '
                            "legacy md5 host authentication; prefer scram-sha-256 for production profiles.",
                            path,
                        )
                    )
            else:  # cache-service
                if not _advertisement_authenticated(resolved):
                    diagnostics.append(
                        Diagnostic(
                            "error",
                            "E137",
                            f'Module "{inst["id"]}" provides contract "{provide_name}" (cache-service) that '
                            "accepts unauthenticated clients; production cache services must require "
                            'authentication (advertise "required" once a password credential is '
                            "configured).",
                            path,
                        )
                    )

        if provides_auth_scoped:
            diagnostics.extend(_validate_provider_credential_refs(inst, secret_values))

    return diagnostics


def _validate_provider_credential_refs(
    inst: dict[str, Any], secret_values: dict[str, Any]
) -> list[Diagnostic]:
    """
    Credential plumbing for production database/cache providers: every
    credential-named config field must be a `secrets.<alias>` reference
    whose alias is declared `required: true`. Plaintext literals, empty
    values, and `${CDS_*}` profile inputs (which CDS resolves from the
    environment and embeds into the plan) are rejected; `config.*`
    references are skipped here because the field they name is itself
    validated. Undeclared aliases are left to validate_secret_refs (E050)
    instead of being reported twice.
    """
    diagnostics: list[Diagnostic] = []
    base_path = f"spec.modules[{inst['index']}].config"

    for field_path, key, value in _walk_config_fields(inst["config"], base_path):
        if not isinstance(value, str) or not _CREDENTIAL_FIELD_PATTERN.search(key):
            continue
        if value.startswith("config.") or value.startswith("${config."):
            continue
        if value.startswith("secrets."):
            alias = secret_name_from_ref(value)
            definition = secret_values.get(alias)
            if isinstance(definition, dict) and definition.get("required") is not True:
                diagnostics.append(
                    Diagnostic(
                        "error",
                        "E138",
                        f'Credential field "{field_path}" references secret alias "{alias}", which is '
                        "not declared `required: true`; a production credential must fail closed when "
                        "the environment value is missing.",
                        field_path,
                    )
                )
            continue
        if value == "":
            diagnostics.append(
                Diagnostic(
                    "error",
                    "E138",
                    f'Credential field "{field_path}" is empty; production credentials must be supplied '
                    "through a secret reference (secrets.<alias>) declared `required: true`.",
                    field_path,
                )
            )
        elif "${CDS_" in value:
            diagnostics.append(
                Diagnostic(
                    "error",
                    "E138",
                    f'Credential field "{field_path}" uses a ${{CDS_*}} profile input, whose value CDS '
                    "resolves and embeds into the plan; production credentials must be supplied through "
                    "a secret reference (secrets.<alias>) declared `required: true`.",
                    field_path,
                )
            )
        else:
            diagnostics.append(
                Diagnostic(
                    "error",
                    "E138",
                    f'Credential field "{field_path}" contains a plaintext value; production credentials '
                    "must be supplied through a secret reference (secrets.<alias>) declared "
                    "`required: true`.",
                    field_path,
                )
            )

    return diagnostics


def _walk_config_fields(obj: Any, path: str):
    """
    Yield (path, key, scalar value) for every leaf and nested key reachable
    from a module instance's config, with profile-paths matching the
    diagnostic vocabulary used elsewhere in this module.
    """
    if isinstance(obj, dict):
        for key, value in obj.items():
            child_path = f"{path}.{key}"
            yield child_path, str(key), value
            yield from _walk_config_fields(value, child_path)
    elif isinstance(obj, list):
        for index, value in enumerate(obj):
            yield from _walk_config_fields(value, f"{path}[{index}]")
