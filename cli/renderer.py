# cli/renderer.py
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml

from .diagnostics import Diagnostic


def render_compose(plan: dict[str, Any], output_path: str | None = None) -> tuple[str, list[Diagnostic]]:
    diagnostics: list[Diagnostic] = []

    compose: dict[str, Any] = {
        "name": plan.get("metadata", {}).get("name", "cds"),
        "services": {},
        "volumes": {},
    }

    for module in plan.get("modules", []):
        implementation = module.get("implementation", {})
        if implementation.get("kind") != "docker-compose":
            diagnostics.append(
                Diagnostic(
                    level="error",
                    code="E070",
                    message=f'Module "{module.get("id")}" has unsupported implementation kind "{implementation.get("kind")}".',
                    path=f'module:{module.get("id")}.implementation.kind',
                )
            )
            continue

        compose_impl = implementation.get("compose", {})
        services = compose_impl.get("services", {})
        volumes = compose_impl.get("volumes", {})

        rendered_services = render_services(module, services)
        rendered_volumes = render_volumes(module, volumes)

        for service_name, service_def in rendered_services.items():
            final_name = f'{module["id"]}-{service_name}'
            compose["services"][final_name] = service_def

        for volume_name, volume_def in rendered_volumes.items():
            final_name = f'{module["id"]}-{volume_name}'
            compose["volumes"][final_name] = volume_def

    if not compose["volumes"]:
        compose.pop("volumes")

    output = yaml.safe_dump(compose, sort_keys=False)

    if output_path:
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(output, encoding="utf-8")

    return output, diagnostics


def render_services(module: dict[str, Any], services: dict[str, Any]) -> dict[str, Any]:
    rendered: dict[str, Any] = {}

    context = build_context(module)

    for service_name, service_def in services.items():
        if not isinstance(service_def, dict):
            continue

        enabled_from = service_def.get("enabledFrom")
        if enabled_from:
            enabled = resolve_expr(enabled_from, context)
            if enabled is False:
                continue

        service_copy = deepcopy(service_def)
        service_copy.pop("enabledFrom", None)

        healthcheck = service_copy.get("healthcheck")
        if isinstance(healthcheck, dict):
            cond = healthcheck.get("conditionallyEnabledFrom")
            if cond:
                enabled = resolve_expr(cond, context)
                healthcheck = deepcopy(healthcheck)
                healthcheck.pop("conditionallyEnabledFrom", None)
                if enabled is False:
                    service_copy.pop("healthcheck", None)
                else:
                    service_copy["healthcheck"] = substitute_values(healthcheck, context)

        service_copy = substitute_values(service_copy, context)

        service_copy = rewrite_service_volumes(service_copy, module["id"])
        service_copy = rewrite_depends_on(service_copy, module)

        rendered[service_name] = service_copy

    return rendered


def render_volumes(module: dict[str, Any], volumes: dict[str, Any]) -> dict[str, Any]:
    rendered: dict[str, Any] = {}
    context = build_context(module)

    for volume_name, volume_def in volumes.items():
        if isinstance(volume_def, dict):
            enabled_from = volume_def.get("enabledFrom")
            if enabled_from:
                enabled = resolve_expr(enabled_from, context)
                if enabled is False:
                    continue

            volume_copy = deepcopy(volume_def)
            volume_copy.pop("enabledFrom", None)
            rendered[volume_name] = substitute_values(volume_copy, context)
        else:
            rendered[volume_name] = volume_def

    return rendered


def build_context(module: dict[str, Any]) -> dict[str, Any]:
    bindings = {}

    for consume_name, consume_value in module.get("consumes", {}).items():
        contract = consume_value.get("contract", {})
        if isinstance(contract, dict):
            spec = contract.get("spec", {})
            bindings[consume_name] = spec if isinstance(spec, dict) else {}

    return {
        "config": module.get("config", {}),
        "bindings": bindings,
        "service": {"host": module.get("id")},
        "secrets": {},
    }


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
        return resolve_expr(expr, context, default=value)
    return value


def resolve_expr(expr: str, context: dict[str, Any], default: Any = None) -> Any:
    current: Any = context
    for part in expr.split("."):
        if not isinstance(current, dict) or part not in current:
            return default
        current = current[part]
    return current


def rewrite_service_volumes(service_def: dict[str, Any], module_id: str) -> dict[str, Any]:
    volumes = service_def.get("volumes")
    if not isinstance(volumes, list):
        return service_def

    rewritten: list[Any] = []
    for item in volumes:
        if not isinstance(item, str):
            rewritten.append(item)
            continue

        parts = item.split(":", 1)
        if len(parts) == 2 and is_named_volume(parts[0]):
            rewritten.append(f"{module_id}-{parts[0]}:{parts[1]}")
        else:
            rewritten.append(item)

    service_def["volumes"] = rewritten
    return service_def


def rewrite_depends_on(service_def: dict[str, Any], module: dict[str, Any]) -> dict[str, Any]:
    depends_on = service_def.get("depends_on")
    if depends_on is None:
        return service_def

    rewritten = {}

    if isinstance(depends_on, list):
        for dep in depends_on:
            rewritten[f"{module['id']}-{dep}"] = {"condition": "service_started"}
        service_def["depends_on"] = rewritten
        return service_def

    if isinstance(depends_on, dict):
        for dep, dep_value in depends_on.items():
            rewritten[f"{module['id']}-{dep}"] = dep_value
        service_def["depends_on"] = rewritten
        return service_def

    return service_def


def is_named_volume(value: str) -> bool:
    if value.startswith(".") or value.startswith("/") or value.startswith("~"):
        return False
    return "/" not in value and "\\" not in value
