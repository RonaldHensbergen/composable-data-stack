# cli/resolver.py
from __future__ import annotations

from typing import Any


def parse_contract_ref(value: str) -> tuple[str, str] | None:
    parts = value.split(".", 1)
    if len(parts) != 2 or not parts[0] or not parts[1]:
        return None
    return parts[0], parts[1]


def resolve_path(obj: dict[str, Any], path: str) -> Any:
    current: Any = obj
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            raise KeyError(path)
        current = current[part]
    return current


def is_secret_ref(value: Any) -> bool:
    return isinstance(value, str) and value.startswith("secrets.") and len(value) > len("secrets.")


def secret_name_from_ref(value: str) -> str:
    return value.split(".", 1)[1]


def evaluate_required_if(expr: str, config: dict[str, Any]) -> bool:
    """
    Evaluate a consume entry's `requiredIf` gate, e.g.
    "config.warehouseType==postgres", against a module instance's own
    config. Uses the same dotted "config.<path>==<value>" vocabulary as the
    renderer's enabledFrom equality gate (see cli/renderer.py's
    _resolve_expr) so module authors only need to learn one expression
    syntax. Shared by validate_contract_bindings (cli/validator.py) and
    resolve_consumed_contracts (cli/planner.py) so a config selecting an
    alternative target with a missing binding fails as an E041 diagnostic
    at both compile stages -- before planning and before rendering -- and
    never depends on validate having already run.
    """
    path, sep, expected = expr.partition("==")
    if not sep:
        return False
    try:
        value = resolve_path({"config": config}, path.strip())
    except KeyError:
        return False
    return value == expected.strip()
