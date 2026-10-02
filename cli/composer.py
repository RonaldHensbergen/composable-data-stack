# cli/composer.py
"""
Compose-time module wiring (#807): the follow-up to #349's
`cds generate-profile` noted in docs/roadmap.md ("allow runtime-generated
profiles for planning and composition beyond `cds generate-profile`").
`generate-profile` only persists an already-assembled profile document;
`compose_profile()` here builds that document by merging a new module
instance into an existing profile, resolving its `consumes` entries
against contracts already `provides`d by the profile's existing modules
instead of requiring the caller to hand-write the `contractRef`/
`dependsOn`/`secrets.values` wiring.
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

from .diagnostics import Diagnostic
from .loader import load_yaml_file, resolve_module_file
from .resolver import is_secret_ref, parse_contract_ref


def _collect_provided_contracts(
    profile: dict[str, Any],
    profile_dir: Path,
    modules_root: Path | None,
) -> tuple[dict[str, str], list[str], list[Diagnostic]]:
    """
    Loads every enabled module instance already in `profile` and returns a
    mapping of "<instanceId>.<providedContractName>" -> contract kind,
    plus every instance id already in use (enabled or not, for collision
    checks). Deliberately only reads each module's `provides` entries
    rather than reusing validator.load_module_instances()'s full
    config/schema validation pass, since compose only needs to know what
    contracts are available to bind against.
    """
    providers: dict[str, str] = {}
    existing_ids: list[str] = []
    diagnostics: list[Diagnostic] = []

    modules = profile.get("spec", {}).get("modules", [])
    for i, instance in enumerate(modules):
        if not isinstance(instance, dict):
            continue
        instance_id = instance.get("id")
        if isinstance(instance_id, str):
            existing_ids.append(instance_id)
        if instance.get("enabled", True) is False:
            continue
        source = instance.get("source")
        if not isinstance(source, str):
            continue

        module_file, diags = resolve_module_file(
            source=source,
            profile_dir=profile_dir,
            module_root=modules_root,
            diagnostic_path=f"spec.modules[{i}].source",
        )
        diagnostics.extend(diags)
        if module_file is None:
            continue

        module_def, diags = load_yaml_file(module_file)
        diagnostics.extend(diags)
        if module_def is None or not isinstance(instance_id, str):
            continue

        for provided in module_def.get("spec", {}).get("provides", []) or []:
            if not isinstance(provided, dict):
                continue
            name = provided.get("name")
            kind = (provided.get("contract") or {}).get("kind")
            if name and kind:
                providers[f"{instance_id}.{name}"] = kind

    return providers, existing_ids, diagnostics


def _set_nested(config: dict[str, Any], dotted_path: str, value: Any) -> None:
    """Sets `config[<dotted_path>] = value`, creating intermediate dicts as
    needed, and merging `value` into an existing dict at that path rather
    than clobbering sibling keys (so a resolved `contractRef` can coexist
    with e.g. a `driver` field set by --set on the same config object)."""
    parts = dotted_path.split(".")
    current = config
    for part in parts[:-1]:
        nxt = current.get(part)
        if not isinstance(nxt, dict):
            nxt = {}
            current[part] = nxt
        current = nxt
    last = parts[-1]
    existing = current.get(last)
    if isinstance(value, dict) and isinstance(existing, dict):
        existing.update(value)
    else:
        current[last] = value


def _iter_secret_aliases(value: Any):
    if isinstance(value, dict):
        for v in value.values():
            yield from _iter_secret_aliases(v)
    elif isinstance(value, list):
        for item in value:
            yield from _iter_secret_aliases(item)
    elif is_secret_ref(value):
        yield value.split(".", 1)[1]


def compose_profile(
    profile: dict[str, Any],
    profile_dir: Path,
    module_source: str,
    *,
    instance_id: str | None = None,
    version: str | None = None,
    enabled: bool = True,
    depends_on: list[str] | None = None,
    config_settings: list[tuple[str, Any]] | None = None,
    bindings: dict[str, str] | None = None,
    secret_env_names: dict[str, str] | None = None,
    modules_root: Path | None = None,
) -> tuple[dict[str, Any] | None, list[Diagnostic]]:
    """
    Returns (merged_profile, diagnostics). merged_profile is a deep copy of
    `profile` with a new `spec.modules` entry for `module_source` appended
    (and `spec.secrets.values` extended if the new module's config
    references an as-yet-undefined secret alias); it is None if any
    diagnostic is an error, in which case `profile` is left untouched.

    `bindings` maps a consume entry name to an explicit "<moduleId>.
    <providedContract>" ref; any consume entry not covered there is
    auto-resolved when exactly one existing enabled module instance
    provides a matching contract kind, else reported as an error (0
    candidates for a required consume, or more than 1 candidate -- both
    ambiguous without an explicit binding).

    `secret_env_names` maps a secret alias (as referenced by
    "secrets.<alias>" somewhere in the new module's resolved config) to the
    environment variable name that should back it; aliases already defined
    on `profile` are left untouched, any other alias actually referenced by
    the new config without an entry here is reported as an error.
    """
    diagnostics: list[Diagnostic] = []
    bindings = bindings or {}
    secret_env_names = secret_env_names or {}
    depends_on = list(depends_on or [])

    if not isinstance(profile, dict) or not isinstance(profile.get("spec"), dict):
        return None, [
            Diagnostic(
                level="error",
                code="E120",
                message='Profile must be a mapping with a "spec" object before composing a module into it.',
                path="spec",
            )
        ]

    module_file, diags = resolve_module_file(
        source=module_source,
        profile_dir=profile_dir,
        module_root=modules_root,
        diagnostic_path="module_source",
    )
    diagnostics.extend(diags)
    if module_file is None:
        return None, diagnostics

    module_def, diags = load_yaml_file(module_file)
    diagnostics.extend(diags)
    if module_def is None:
        return None, diagnostics

    if module_def.get("kind") != "Module":
        diagnostics.append(
            Diagnostic(
                level="error",
                code="E121",
                message=f'Expected kind: "Module" at "{module_file}".',
                path="module_source",
            )
        )
        return None, diagnostics

    providers, existing_ids, provider_diags = _collect_provided_contracts(profile, profile_dir, modules_root)
    diagnostics.extend(provider_diags)
    if any(d.level == "error" for d in diagnostics):
        return None, diagnostics

    metadata = module_def.get("metadata") if isinstance(module_def.get("metadata"), dict) else {}
    resolved_id = instance_id or metadata.get("name")
    if not isinstance(resolved_id, str) or not resolved_id.strip():
        diagnostics.append(
            Diagnostic(
                level="error",
                code="E122",
                message=(
                    "Could not determine a module instance id; pass instance_id "
                    "explicitly or ensure the module declares metadata.name."
                ),
                path="module_source",
            )
        )
        return None, diagnostics

    if resolved_id in existing_ids:
        diagnostics.append(
            Diagnostic(
                level="error",
                code="E123",
                message=f'Profile already has a module instance with id "{resolved_id}"; pass a different instance_id.',
                path=f"spec.modules.{resolved_id}",
            )
        )
        return None, diagnostics

    resolved_version = version or metadata.get("version")

    config: dict[str, Any] = {}
    for path, value in config_settings or []:
        _set_nested(config, path, value)

    for consume in module_def.get("spec", {}).get("consumes", []) or []:
        if not isinstance(consume, dict):
            continue
        consume_name = consume.get("name")
        mapped_from = consume.get("mappedFrom")
        if not consume_name or not mapped_from:
            continue

        required = consume.get("required", True)
        contract_kind = (consume.get("contract") or {}).get("kind")

        chosen = bindings.get(consume_name)
        if chosen is not None:
            if parse_contract_ref(chosen) is None:
                diagnostics.append(
                    Diagnostic(
                        level="error",
                        code="E124",
                        message=f'Invalid contract ref "{chosen}" bound for "{consume_name}".',
                        path=f"consumes.{consume_name}",
                    )
                )
                continue
            provided_kind = providers.get(chosen)
            if provided_kind is None:
                diagnostics.append(
                    Diagnostic(
                        level="error",
                        code="E124",
                        message=(
                            f'Bound contract ref "{chosen}" for "{consume_name}" does not match any '
                            "contract provided by an existing enabled module in the profile."
                        ),
                        path=f"consumes.{consume_name}",
                    )
                )
                continue
            if provided_kind != contract_kind:
                diagnostics.append(
                    Diagnostic(
                        level="error",
                        code="E124",
                        message=(
                            f'Bound contract ref "{chosen}" for "{consume_name}" provides a '
                            f'"{provided_kind}" contract, but "{consume_name}" requires "{contract_kind}".'
                        ),
                        path=f"consumes.{consume_name}",
                    )
                )
                continue
        else:
            candidates = sorted(ref for ref, kind in providers.items() if kind == contract_kind)
            if len(candidates) == 1:
                chosen = candidates[0]
            elif not candidates:
                if required:
                    diagnostics.append(
                        Diagnostic(
                            level="error",
                            code="E125",
                            message=(
                                f'No existing enabled module provides a "{contract_kind}" contract for '
                                f'required consume entry "{consume_name}"; bind one explicitly with '
                                f"--bind {consume_name}=<moduleId>.<providedContract>."
                            ),
                            path=f"consumes.{consume_name}",
                        )
                    )
                continue
            else:
                diagnostics.append(
                    Diagnostic(
                        level="error",
                        code="E125",
                        message=(
                            f'Multiple existing modules provide a "{contract_kind}" contract for consume '
                            f'entry "{consume_name}": {", ".join(candidates)}. Disambiguate with --bind '
                            f"{consume_name}=<moduleId>.<providedContract>."
                        ),
                        path=f"consumes.{consume_name}",
                    )
                )
                continue

        producer_id = chosen.split(".", 1)[0]
        if producer_id not in depends_on:
            depends_on.append(producer_id)

        relative_path = mapped_from
        prefix = "spec.config."
        if relative_path.startswith(prefix):
            relative_path = relative_path[len(prefix) :]
        _set_nested(config, relative_path, {"contractRef": chosen})

    if any(d.level == "error" for d in diagnostics):
        return None, diagnostics

    secret_values = deepcopy((profile.get("spec", {}).get("secrets") or {}).get("values") or {})
    for alias in sorted(set(_iter_secret_aliases(config))):
        if alias in secret_values:
            continue
        env_name = secret_env_names.get(alias)
        if not env_name:
            diagnostics.append(
                Diagnostic(
                    level="error",
                    code="E126",
                    message=(
                        f'Module config references secret alias "{alias}" ("secrets.{alias}") that is '
                        f"not defined on the profile; pass --secret {alias}=<ENV_VAR_NAME>."
                    ),
                    path=f"secrets.values.{alias}",
                )
            )
            continue
        secret_values[alias] = {"env": env_name, "required": True}

    if any(d.level == "error" for d in diagnostics):
        return None, diagnostics

    merged = deepcopy(profile)
    merged.setdefault("spec", {}).setdefault("modules", [])
    new_instance: dict[str, Any] = {
        "id": resolved_id,
        "source": module_source,
        "enabled": enabled,
        "config": config,
    }
    if resolved_version:
        new_instance["version"] = resolved_version
    if depends_on:
        new_instance["dependsOn"] = sorted(set(depends_on))
    merged["spec"]["modules"].append(new_instance)

    if secret_values:
        secrets_block = merged["spec"].setdefault("secrets", {"provider": {"type": "env"}})
        secrets_block.setdefault("provider", {"type": "env"})
        secrets_block["values"] = secret_values

    return merged, diagnostics
