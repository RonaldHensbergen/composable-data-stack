# pyright: reportMissingModuleSource=false
# cli/renderer.py
"""
Render docker-compose YAML from a composition plan.
"""
from __future__ import annotations

import os
import re
from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml

from .constants import MAX_NESTING_DEPTH, MaxNestingDepthExceeded
from .diagnostics import Diagnostic
from .loader import resolve_module_dir
from .utils import _atomic_write


def render_compose(
    plan: dict[str, Any],
    output_path: str | None = None,
    env_file: str | None = None,
) -> tuple[str, list[Diagnostic]]:
    """
    Render docker-compose from plan.

    Args:
        plan:        Composition plan.
        output_path: Optional output file path.
        env_file:    Reserved for compatibility; not used.

    Returns:
        Tuple of (output_yaml, diagnostics).
    """
    _ = env_file
    diagnostics: list[Diagnostic] = []
    secrets = plan.get("secrets", {})
    compose_dir = Path(output_path).resolve().parent if output_path else Path.cwd().resolve()
    profile_dir = _resolve_profile_dir(plan)
    project_root = _resolve_project_root(profile_dir)

    # Extract networks from runtime config
    runtime = plan.get("runtime", {})
    networks_config = runtime.get("networks", [])
    
    # Build networks section
    networks: dict[str, Any] = {}
    for net in networks_config:
        net_name = net.get("name", "default")
        networks[net_name] = {}
        driver = net.get("driver")
        if driver:
            networks[net_name]["driver"] = driver

    # Build the default network name from namespace or profile name
    default_network_name = runtime.get("namespace") or plan.get("metadata", {}).get("name", "cds")

    compose: dict[str, Any] = {
        "name": plan.get("metadata", {}).get("name", "cds"),
        "services": {},
        "volumes": {},
    }
    module_service_names: dict[str, list[str]] = {}
    
    # Add networks if defined
    if networks or default_network_name:
        if not networks:
            networks[default_network_name] = {}
        compose["networks"] = networks

    for module in plan.get("modules", []):
        implementation = module.get("implementation", {})

        if implementation.get("kind") != "docker-compose":
            diagnostics.append(Diagnostic(
                level="error",
                code="E070",
                message=(
                    f'Module "{module.get("id")}" has unsupported implementation '
                    f'kind "{implementation.get("kind")}".'
                ),
                path=f'module:{module.get("id")}.implementation.kind',
            ))
            continue

        compose_impl = implementation.get("compose")
        if not compose_impl:
            diagnostics.append(Diagnostic(
                level="warning",
                code="W071",
                message=(
                    f'Module "{module.get("id")}" has kind "docker-compose" '
                    f'but no "compose" definition.'
                ),
                path=f'module:{module.get("id")}.implementation.compose',
            ))
            continue

        services = compose_impl.get("services", {})
        volumes = compose_impl.get("volumes", {})

        try:
            rendered_services, unsafe_diags = _render_services(
                module,
                services,
                secrets,
                profile_dir=profile_dir,
                project_root=project_root,
                compose_dir=compose_dir,
                network_name=default_network_name,
            )
            diagnostics.extend(unsafe_diags)
            rendered_volumes = _render_volumes(module, volumes, secrets)
        except MaxNestingDepthExceeded:
            diagnostics.append(Diagnostic(
                level="error",
                code="E094",
                message=(
                    f'Module "{module.get("id")}" service/volume templates exceed the '
                    f"maximum supported nesting depth ({MAX_NESTING_DEPTH})."
                ),
                path=f'module:{module.get("id")}.implementation.compose',
            ))
            continue

        # Handle initDbEnv for postgres service (merge additional env vars)
        _merge_init_db_env(rendered_services, module, secrets)

        for service_name, service_def in rendered_services.items():
            compose_service_name = _compose_service_name(module["id"], service_name)
            compose["services"][compose_service_name] = service_def
            module_service_names.setdefault(module["id"], []).append(compose_service_name)

        for volume_name, volume_def in rendered_volumes.items():
            compose["volumes"][f'{module["id"]}-{volume_name}'] = volume_def

    if not compose["volumes"]:
        compose.pop("volumes")

    _add_cross_module_dependencies(compose, plan, module_service_names)

    output = yaml.safe_dump(compose, sort_keys=False)
    diagnostics.extend(_check_unresolved_expressions(output))

    if output_path:
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write(path, output)

    return output, diagnostics


_UNRESOLVED_EXPRESSION_PATTERN = re.compile(
    r"\$\{((?:config|bindings|service)\.[^}]*)\}"
)


def _check_unresolved_expressions(rendered_yaml: str) -> list[Diagnostic]:
    """
    Detect leftover ${config.*}/${bindings.*}/${service.*} template expressions
    that survived rendering unresolved (e.g. an optional consumed contract that
    was never bound, but is unconditionally referenced by the module's
    template). These are always a rendering bug -- unlike ${CDS_*}/${VAR}
    placeholders, CDS's own template vocabulary is meant to be fully resolved
    by render time, so leaving one in place would silently ship a broken
    Compose file instead of failing loudly.
    """
    unresolved = sorted(set(_UNRESOLVED_EXPRESSION_PATTERN.findall(rendered_yaml)))
    return [
        Diagnostic(
            level="error",
            code="E071",
            message=(
                f'Unresolved template expression "${{{expr}}}" remains in the rendered '
                "output. This usually means an optional contract binding referenced by "
                "a module's template was never satisfied by the profile."
            ),
            path=f"rendered.{expr}",
        )
        for expr in unresolved
    ]


# ---------------------------------------------------------------------------
# Internal rendering helpers
# ---------------------------------------------------------------------------

def _render_services(
    module: dict[str, Any],
    services: dict[str, Any],
    secrets: dict[str, str],
    profile_dir: Path | None,
    project_root: Path | None,
    compose_dir: Path,
    network_name: str | None = None,
) -> tuple[dict[str, Any], list[Diagnostic]]:
    rendered: dict[str, Any] = {}
    diagnostics: list[Diagnostic] = []
    context = _build_context(module, secrets)

    for service_name, service_def in services.items():
        if not isinstance(service_def, dict):
            continue

        # Top-level enabledFrom guard
        enabled_from = service_def.get("enabledFrom")
        if enabled_from and _resolve_expr(enabled_from, context) is False:
            continue

        service_copy = deepcopy(service_def)
        service_copy.pop("enabledFrom", None)

        # Conditional healthcheck
        healthcheck = service_copy.get("healthcheck")
        if isinstance(healthcheck, dict):
            cond = healthcheck.get("conditionallyEnabledFrom")
            if cond:
                hc_copy = deepcopy(healthcheck)
                hc_copy.pop("conditionallyEnabledFrom", None)
                if _resolve_expr(cond, context) is False:
                    service_copy.pop("healthcheck", None)
                else:
                    service_copy["healthcheck"] = _substitute_values(hc_copy, context)

        diagnostics.extend(
            _check_unsafe_field_type_substitutions(service_copy, module.get("id"), service_name, context)
        )
        service_copy = _substitute_values(service_copy, context)
        service_copy = _rewrite_service_volumes(
            service_copy,
            module,
            profile_dir=profile_dir,
            project_root=project_root,
            compose_dir=compose_dir,
        )
        service_copy = _rewrite_depends_on(service_copy, module)
        service_copy = _rewrite_build_context(
            service_copy,
            module,
            profile_dir=profile_dir,
            project_root=project_root,
            compose_dir=compose_dir,
        )
        service_copy = _apply_image_source(service_copy, module)

        # Attach to the network if network_name is provided
        if network_name:
            service_copy["networks"] = [network_name]
        
        rendered[service_name] = service_copy

    return rendered, diagnostics


def _render_volumes(
    module: dict[str, Any],
    volumes: dict[str, Any],
    secrets: dict[str, str],
) -> dict[str, Any]:
    rendered: dict[str, Any] = {}
    context = _build_context(module, secrets)

    for volume_name, volume_def in volumes.items():
        if isinstance(volume_def, dict):
            enabled_from = volume_def.get("enabledFrom")
            if enabled_from and _resolve_expr(enabled_from, context) is False:
                continue
            volume_copy = deepcopy(volume_def)
            volume_copy.pop("enabledFrom", None)
            rendered[volume_name] = _substitute_values(volume_copy, context)
        else:
            rendered[volume_name] = volume_def

    return rendered


def _merge_init_db_env(
    services: dict[str, Any],
    module: dict[str, Any],
    secrets: dict[str, str],
) -> None:
    """
    Merge initDbEnv into postgres service environment variables.
    
    If a module has config.initDbEnv, merge those environment variables
    into any service named "postgres" in the rendered services.
    """
    init_db_env = module.get("config", {}).get("initDbEnv")
    if not init_db_env or not isinstance(init_db_env, dict):
        return
    
    # Find postgres service and merge env vars
    postgres_service = services.get("postgres")
    if postgres_service and isinstance(postgres_service, dict):
        env = postgres_service.setdefault("environment", {})
        if not isinstance(env, dict):
            return
        
        # Substitute values in init_db_env and merge
        context = _build_context(module, secrets)
        for key, value in init_db_env.items():
            if isinstance(value, str):
                # Resolve references like "secrets.xyz"
                env[key] = _resolve_expr(value, context)
            else:
                env[key] = value


def _build_context(
    module: dict[str, Any],
    secrets: dict[str, str] | None = None,
) -> dict[str, Any]:
    secrets = secrets or {}
    bindings: dict[str, Any] = {}

    for consume_name, consume_value in module.get("consumes", {}).items():
        contract = consume_value.get("contract", {})
        if isinstance(contract, dict):
            spec = contract.get("spec", {})
            bindings[consume_name] = spec if isinstance(spec, dict) else {}

    return {
        "config": module.get("config", {}),
        "bindings": bindings,
        "service": {"host": module.get("id")},
        "secrets": secrets,
    }


def _substitute_values(obj: Any, context: dict[str, Any], _depth: int = 0) -> Any:
    """Recursively substitute interpolation expressions in obj."""
    if _depth > MAX_NESTING_DEPTH:
        raise MaxNestingDepthExceeded(
            f"Service/volume template nesting exceeds the maximum supported depth ({MAX_NESTING_DEPTH})."
        )
    if isinstance(obj, dict):
        return {k: _substitute_values(v, context, _depth + 1) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_substitute_values(v, context, _depth + 1) for v in obj]
    if isinstance(obj, str):
        return _substitute_string(obj, context)
    return obj


def _substitute_string(value: str, context: dict[str, Any]) -> Any:
    """
    Substitute interpolations in a string.

    Supports:
    - Pure:   "${config.name}"                    → value of config.name (any type)
    - Mixed:  "db://${bindings.db.host}:5432"     → "db://postgres:5432"
    - Secret: "${secrets.alias_or_env_name}"      → "${CDS_ENV_NAME}"
    """
    _PATTERN = re.compile(r"\$\{([^}]+)\}")
    matches = _PATTERN.findall(value)

    if not matches:
        return value

    # Pure substitution: entire string is a single expression
    if len(matches) == 1 and value == f"${{{matches[0]}}}":
        result = _resolve_expr(matches[0], context)
        return result if result is not None else value

    # Mixed substitution: string-concat all expressions
    def _replace(match: re.Match) -> str:
        resolved = _resolve_expr(match.group(1), context)
        return str(resolved) if resolved is not None else match.group(0)

    return _PATTERN.sub(_replace, value)


# Compose service fields where a module template's pure `${config.*}` /
# `${bindings.*}` substitution resolving to a non-scalar type (dict/list) is
# always attacker-relevant: they control what docker-compose executes, sets
# as environment, or bind-mounts on the host, regardless of the module
# author's intent. See GHSA-gmc4-jw3j-mqcf.
_UNSAFE_TYPED_SUBSTITUTION_FIELDS = frozenset({
    "command",
    "entrypoint",
    "environment",
    "volumes",
    "cap_add",
    "cap_drop",
    "devices",
    "security_opt",
    "privileged",
    "network_mode",
    "pid",
    "ports",
    "labels",
    "tmpfs",
    "dns",
    "extra_hosts",
    "logging",
    "expose",
    "depends_on",
    "build",
})

# Fields where the field itself is already scalar (bool/str), so a dict/list
# resolution is not the only attacker-relevant outcome: a profile-controlled
# scalar value can *also* grant host-level escalation without ever changing
# the field's expected shape (e.g. `privileged: "${config.priv}"` resolving
# to `True`, or `network_mode`/`pid` resolving to `"host"`). These are
# checked against specific dangerous scalar values, in addition to the
# dict/list check above.
_UNSAFE_SCALAR_SUBSTITUTION_VALUES: dict[str, frozenset[Any]] = {
    "privileged": frozenset({True}),
    "network_mode": frozenset({"host"}),
    "pid": frozenset({"host"}),
}


def _check_unsafe_field_type_substitutions(
    service_def: dict[str, Any],
    module_id: str | None,
    service_name: str,
    context: dict[str, Any],
) -> list[Diagnostic]:
    """
    Detect a module template that uses a *pure* `${config.*}`/`${bindings.*}`
    substitution (i.e. the entire field value is a single expression, not
    string-concatenated) in a compose field position where docker-compose
    expects a fixed shape (list/dict/scalar). Pure substitution preserves the
    resolved value's original Python type, so a profile can inject arbitrary
    lists/dicts (e.g. a `command` list, `environment` map, or `volumes` bind
    mounts) through module config, bypassing the field's intended shape.

    This does not affect mixed substitution (e.g. "db://${bindings.db.host}"),
    which is always string-concatenated back into a str.

    Beyond the dict/list check, a small set of fields (`privileged`,
    `network_mode`, `pid`) are already scalar by design, so a resolved value
    is also flagged if it matches a known dangerous scalar (e.g.
    `privileged` resolving to `True`, or `network_mode`/`pid` resolving to
    `"host"`), since a profile can drive host-level escalation through
    module config without ever changing the field's shape.
    """
    diagnostics: list[Diagnostic] = []
    pure_pattern = re.compile(r"^\$\{([^}]+)\}$")

    for field in _UNSAFE_TYPED_SUBSTITUTION_FIELDS:
        raw_value = service_def.get(field)
        if not isinstance(raw_value, str):
            continue
        match = pure_pattern.match(raw_value)
        if not match:
            continue

        resolved = _resolve_expr(match.group(1), context)
        unsafe_scalars = _UNSAFE_SCALAR_SUBSTITUTION_VALUES.get(field, frozenset())
        if isinstance(resolved, (dict, list)):
            diagnostics.append(Diagnostic(
                level="error",
                code="E072",
                message=(
                    f'Module "{module_id}" service "{service_name}" field "{field}" resolves '
                    f'"${{{match.group(1)}}}" to a {type(resolved).__name__}, but pure '
                    "substitution in this field position is not permitted: a profile-supplied "
                    "config value would be spliced verbatim into the rendered Compose file, "
                    "letting an untrusted profile inject arbitrary command/environment/volumes "
                    "entries. Restructure the module template to interpolate scalar leaf values "
                    "individually instead of substituting the whole field."
                ),
                path=f"module:{module_id}.implementation.compose.services.{service_name}.{field}",
            ))
        elif resolved in unsafe_scalars:
            diagnostics.append(Diagnostic(
                level="error",
                code="E072",
                message=(
                    f'Module "{module_id}" service "{service_name}" field "{field}" resolves '
                    f'"${{{match.group(1)}}}" to {resolved!r}, a host-escalating value for this '
                    "field. A profile-supplied config value would grant host-level privileges "
                    "(privileged mode, or host network/PID namespace sharing) through what "
                    "should be module-controlled configuration. Do not let profile-controlled "
                    "config drive this field."
                ),
                path=f"module:{module_id}.implementation.compose.services.{service_name}.{field}",
            ))

    return diagnostics


def _resolve_expr(expr: str, context: dict[str, Any]) -> Any:
    """
    Resolve a dot-notation expression against context.

    Secrets are emitted as Docker Compose runtime placeholders (${VAR}).
    Raw secret values are never returned.

    Returns None if the path is not found.
    """
    if expr.startswith("secrets."):
        alias = expr.split(".", 1)[1]
        secret_map = context.get("secrets", {})
        env_name = secret_map.get(alias, alias)
        return f"${{{env_name}}}"

    current: Any = context
    for part in expr.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]

    if isinstance(current, str) and current.startswith("secrets."):
        alias = current.split(".", 1)[1]
        secret_map = context.get("secrets", {})
        env_name = secret_map.get(alias, alias)
        return f"${{{env_name}}}"

    return current


def _rewrite_service_volumes(
    service_def: dict[str, Any],
    module: dict[str, Any],
    profile_dir: Path | None,
    project_root: Path | None,
    compose_dir: Path,
) -> dict[str, Any]:
    volumes = service_def.get("volumes")
    if not isinstance(volumes, list):
        return service_def

    rewritten: list[Any] = []
    for item in volumes:
        if isinstance(item, str):
            parts = item.split(":", 1)
            if len(parts) == 2 and _is_named_volume(parts[0]):
                item = f"{module['id']}-{parts[0]}:{parts[1]}"
            elif len(parts) >= 2:
                source = parts[0]
                rewritten_source = _rewrite_local_path(
                    source,
                    module=module,
                    profile_dir=profile_dir,
                    project_root=project_root,
                    compose_dir=compose_dir,
                )
                if rewritten_source != source:
                    item = f"{rewritten_source}:{parts[1]}"
        elif isinstance(item, dict):
            item_copy = deepcopy(item)
            if item_copy.get("type") == "bind" and isinstance(item_copy.get("source"), str):
                item_copy["source"] = _rewrite_local_path(
                    item_copy["source"],
                    module=module,
                    profile_dir=profile_dir,
                    project_root=project_root,
                    compose_dir=compose_dir,
                )
            elif item_copy.get("type") == "volume" and isinstance(item_copy.get("source"), str):
                source = item_copy["source"]
                if _is_named_volume(source):
                    item_copy["source"] = f"{module['id']}-{source}"
            item = item_copy
        rewritten.append(item)

    return {**service_def, "volumes": rewritten}


def _rewrite_local_path(
    path_value: str,
    module: dict[str, Any],
    profile_dir: Path | None,
    project_root: Path | None,
    compose_dir: Path,
) -> str:
    if Path(path_value).is_absolute() or _looks_remote_context(path_value) or "${" in path_value:
        return path_value

    candidates: list[Path] = []
    for base in _local_path_bases(module, profile_dir, project_root, compose_dir):
        candidate = (base / path_value).resolve()
        if candidate not in candidates:
            candidates.append(candidate)

    if not candidates:
        return path_value

    chosen = _choose_best_local_path_candidate(candidates)
    if project_root is not None and not _path_is_within_root(compose_dir, project_root):
        try:
            return Path(chosen).relative_to(project_root).as_posix()
        except ValueError:
            pass
    try:
        rel = Path(chosen).relative_to(compose_dir)
    except ValueError:
        # relative_to() only works for descendants; relpath preserves ../ segments.
        try:
            rel = Path(os.path.relpath(chosen, compose_dir))
        except ValueError:
            # On Windows, relpath raises when chosen and compose_dir are on
            # different drives (e.g. C:\ vs D:\), no relative path can
            # express that. Fall back to the absolute path, same as the
            # is_absolute() short-circuit above.
            return Path(chosen).as_posix()
    return rel.as_posix()


def _local_path_bases(
    module: dict[str, Any],
    profile_dir: Path | None,
    project_root: Path | None,
    compose_dir: Path,
) -> list[Path]:
    bases: list[Path] = []

    if project_root is not None:
        bases.append(project_root)

    if profile_dir is not None:
        bases.append(profile_dir)

    module_dir = _resolve_module_dir(module, profile_dir)
    if module_dir is not None:
        bases.append(module_dir)

    bases.append(compose_dir)

    if project_root is not None:
        bases.append((project_root / "build").resolve())

    return bases


def _choose_best_local_path_candidate(candidates: list[Path]) -> Path:
    for candidate in candidates:
        if candidate.exists():
            return candidate

    return candidates[0]


def _rewrite_depends_on(
    service_def: dict[str, Any],
    module: dict[str, Any],
) -> dict[str, Any]:
    depends_on = service_def.get("depends_on")
    if depends_on is None:
        return service_def

    if isinstance(depends_on, list):
        rewritten = {
            _compose_service_name(module["id"], dep): {"condition": "service_started"}
            for dep in depends_on
        }
    elif isinstance(depends_on, dict):
        rewritten = {
            _compose_service_name(module["id"], dep): val
            for dep, val in depends_on.items()
        }
    else:
        return service_def

    return {**service_def, "depends_on": rewritten}


def _apply_image_source(
    service_def: dict[str, Any],
    module: dict[str, Any],
) -> dict[str, Any]:
    """
    Swap a rendered service's build: block for a registry image: reference
    when the module's config.image.source is "registry".

    When config.image.source is "registry", any "build" key is dropped and
    "image" is rewritten to
    "docker.io/ronaldsoeverein/<module id>:<config.image.tag>". Emitting
    a registry reference without a tag would silently produce an untagged
    image, so the service is left unchanged (with the build: block intact)
    if no tag is configured; validator enforcement of a required tag is
    tracked separately (issue #533).
    """
    image_config = module.get("config", {}).get("image", {})
    if not isinstance(image_config, dict) or image_config.get("source") != "registry":
        return service_def

    tag = image_config.get("tag")
    if not tag:
        return service_def

    service_copy = dict(service_def)
    service_copy.pop("build", None)
    service_copy["image"] = f"docker.io/ronaldsoeverein/{module.get('id')}:{tag}"
    return service_copy


def _rewrite_build_context(
    service_def: dict[str, Any],
    module: dict[str, Any],
    profile_dir: Path | None,
    project_root: Path | None,
    compose_dir: Path,
) -> dict[str, Any]:
    build = service_def.get("build")
    if build is None:
        return service_def

    if isinstance(build, str):
        rewritten = _resolve_context_path(
            context=build,
            dockerfile=None,
            module=module,
            profile_dir=profile_dir,
            project_root=project_root,
            compose_dir=compose_dir,
        )
        return {**service_def, "build": rewritten}

    if isinstance(build, dict):
        context = build.get("context")
        if not isinstance(context, str):
            return service_def

        dockerfile = build.get("dockerfile")
        rewritten = _resolve_context_path(
            context=context,
            dockerfile=dockerfile if isinstance(dockerfile, str) else None,
            module=module,
            profile_dir=profile_dir,
            project_root=project_root,
            compose_dir=compose_dir,
        )
        return {**service_def, "build": {**build, "context": rewritten}}

    return service_def


def _resolve_context_path(
    context: str,
    dockerfile: str | None,
    module: dict[str, Any],
    profile_dir: Path | None,
    project_root: Path | None,
    compose_dir: Path,
) -> str:
    # Keep absolute paths and remote contexts unchanged.
    if Path(context).is_absolute() or _looks_remote_context(context) or "${" in context:
        return context

    candidates: list[Path] = []
    for base in _context_bases(module, profile_dir, project_root, compose_dir):
        candidate = (base / context).resolve()
        if candidate not in candidates:
            candidates.append(candidate)

    if not candidates:
        return context

    chosen = _choose_best_context_candidate(candidates, dockerfile)
    if project_root is not None and not _path_is_within_root(compose_dir, project_root):
        try:
            return Path(chosen).relative_to(project_root).as_posix()
        except ValueError:
            pass
    try:
        rel = Path(chosen).relative_to(compose_dir)
    except ValueError:
        # relative_to() only works for descendants; relpath preserves ../ segments.
        try:
            rel = Path(os.path.relpath(chosen, compose_dir))
        except ValueError:
            # On Windows, relpath raises when chosen and compose_dir are on
            # different drives (e.g. C:\ vs D:\), no relative path can
            # express that. Fall back to the absolute path, same as the
            # is_absolute() short-circuit above.
            return Path(chosen).as_posix()
    return Path(rel).as_posix()


def _context_bases(
    module: dict[str, Any],
    profile_dir: Path | None,
    project_root: Path | None,
    compose_dir: Path,
) -> list[Path]:
    bases: list[Path] = []

    if project_root is not None:
        bases.append(project_root)

    bases.append(compose_dir)

    module_dir = _resolve_module_dir(module, profile_dir)
    if module_dir is not None:
        bases.append(module_dir)

    if project_root is not None:
        # Legacy compose path in this repo used to be project_root/build.
        bases.append((project_root / "build").resolve())

    return bases


def _choose_best_context_candidate(candidates: list[Path], dockerfile: str | None) -> Path:
    if dockerfile:
        for candidate in candidates:
            if candidate.exists() and (candidate / dockerfile).exists():
                return candidate

    for candidate in candidates:
        if candidate.exists():
            return candidate

    return candidates[0]


def _looks_remote_context(value: str) -> bool:
    return "://" in value or value.startswith("git@")


def _path_is_within_root(path_value: Path, root: Path) -> bool:
    try:
        path_value.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def _resolve_profile_dir(plan: dict[str, Any]) -> Path | None:
    source_profile = plan.get("sourceProfile")
    if not isinstance(source_profile, str):
        return None
    return Path(source_profile).resolve().parent


def _resolve_project_root(profile_dir: Path | None) -> Path | None:
    if profile_dir is None:
        return None

    for directory in [profile_dir, *profile_dir.parents]:
        if (directory / "pyproject.toml").exists() or (directory / ".git").exists():
            return directory

    return None


def _resolve_module_dir(module: dict[str, Any], profile_dir: Path | None) -> Path | None:
    source = module.get("source")
    if not isinstance(source, str):
        return None

    module_root = os.getenv("CDS_MODULE_PATH")
    module_root_path = Path(module_root) if module_root else None

    return resolve_module_dir(source, profile_dir, module_root=module_root_path)


def _is_named_volume(value: str) -> bool:
    if value.startswith((".", "/", "~")):
        return False
    return "/" not in value and "\\" not in value


def _compose_service_name(module_id: str, service_name: str) -> str:
    """Normalize compose service names so module prefixes are not duplicated."""
    if service_name == module_id or service_name.startswith(f"{module_id}-"):
        return service_name
    return f"{module_id}-{service_name}"


def _add_cross_module_dependencies(
    compose: dict[str, Any],
    plan: dict[str, Any],
    module_service_names: dict[str, list[str]],
) -> None:
    """
    Add explicit cross-module dependencies to docker-compose services.
    
    For each module that has dependsOn declarations, add depends_on entries
    to all its services, referencing all services from the dependent modules.
    """
    modules = plan.get("modules", [])
    services = compose.get("services", {})
    
    # For each module, add depends_on for its dependencies
    for module in modules:
        module_id = module.get("id")
        depends_on = module.get("dependsOn", [])
        
        if not depends_on or not module_id:
            continue
        
        # Find all services belonging to this module
        current_module_service_names = module_service_names.get(module_id, [])
        
        # For each service in this module, add depends_on entries
        for service_name in current_module_service_names:
            service_def = services.get(service_name)
            if not service_def:
                continue
            
            # Collect all services from dependent modules
            for dep_module_id in depends_on:
                dep_services = module_service_names.get(dep_module_id, [])
                
                for dep_service_name in dep_services:
                    # Initialize depends_on if not present
                    if "depends_on" not in service_def:
                        service_def["depends_on"] = {}
                    
                    # Add the dependency with a started condition
                    if isinstance(service_def["depends_on"], dict):
                        service_def["depends_on"][dep_service_name] = {
                            "condition": "service_healthy"
                        }
                    elif isinstance(service_def["depends_on"], list):
                        if dep_service_name not in service_def["depends_on"]:
                            service_def["depends_on"].append(dep_service_name)
