# cli/renderer.py
"""
Render docker-compose YAML from a composition plan.
"""
from __future__ import annotations

import re
import yaml
from copy import deepcopy
from pathlib import Path
from typing import Any

from .diagnostics import Diagnostic


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

    compose: dict[str, Any] = {
        "name": plan.get("metadata", {}).get("name", "cds"),
        "services": {},
        "volumes": {},
    }

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

        rendered_services = _render_services(module, services, secrets)
        rendered_volumes = _render_volumes(module, volumes, secrets)

        for service_name, service_def in rendered_services.items():
            compose["services"][f'{module["id"]}-{service_name}'] = service_def

        for volume_name, volume_def in rendered_volumes.items():
            compose["volumes"][f'{module["id"]}-{volume_name}'] = volume_def

    if not compose["volumes"]:
        compose.pop("volumes")

    output = yaml.safe_dump(compose, sort_keys=False)

    if output_path:
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(output, encoding="utf-8")

    return output, diagnostics


# ---------------------------------------------------------------------------
# Internal rendering helpers
# ---------------------------------------------------------------------------

def _render_services(
    module: dict[str, Any],
    services: dict[str, Any],
    secrets: dict[str, str],
) -> dict[str, Any]:
    rendered: dict[str, Any] = {}
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

        service_copy = _substitute_values(service_copy, context)
        service_copy = _rewrite_service_volumes(service_copy, module["id"])
        service_copy = _rewrite_depends_on(service_copy, module)
        rendered[service_name] = service_copy

    return rendered


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


def _substitute_values(obj: Any, context: dict[str, Any]) -> Any:
    """Recursively substitute interpolation expressions in obj."""
    if isinstance(obj, dict):
        return {k: _substitute_values(v, context) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_substitute_values(v, context) for v in obj]
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
    module_id: str,
) -> dict[str, Any]:
    volumes = service_def.get("volumes")
    if not isinstance(volumes, list):
        return service_def

    rewritten: list[Any] = []
    for item in volumes:
        if isinstance(item, str):
            parts = item.split(":", 1)
            if len(parts) == 2 and _is_named_volume(parts[0]):
                item = f"{module_id}-{parts[0]}:{parts[1]}"
        rewritten.append(item)

    return {**service_def, "volumes": rewritten}


def _rewrite_depends_on(
    service_def: dict[str, Any],
    module: dict[str, Any],
) -> dict[str, Any]:
    depends_on = service_def.get("depends_on")
    if depends_on is None:
        return service_def

    if isinstance(depends_on, list):
        rewritten = {
            f"{module['id']}-{dep}": {"condition": "service_started"}
            for dep in depends_on
        }
    elif isinstance(depends_on, dict):
        rewritten = {
            f"{module['id']}-{dep}": val
            for dep, val in depends_on.items()
        }
    else:
        return service_def

    return {**service_def, "depends_on": rewritten}


def _is_named_volume(value: str) -> bool:
    if value.startswith((".", "/", "~")):
        return False
    return "/" not in value and "\\" not in value