# cli/resolver.py
from __future__ import annotations

import re
from typing import Any

# Same dotted "config.<path>==<value>" vocabulary enforced by module.schema.json's
# requiredIf pattern (cli/resources/module.schema.json); kept here too as
# defense in depth so a malformed expression fails loudly even if it somehow
# reaches evaluate_required_if without having gone through schema validation
# first (e.g. a future caller, or a module loaded via CDS_MODULE_PATH).
_REQUIRED_IF_PATTERN = re.compile(r"^config(\.[A-Za-z0-9_]+)+\s*==\s*\S.*$")


class RequiredIfSyntaxError(ValueError):
    """Raised when a requiredIf expression doesn't match config.<path>==<value>."""


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

    Raises RequiredIfSyntaxError if `expr` doesn't match the
    "config.<path>==<value>" vocabulary (missing "==", or a path that
    doesn't start with "config."), so a malformed gate fails loudly instead
    of silently evaluating to False and disabling enforcement. A
    syntactically valid expression whose path simply isn't set on this
    module instance's config still evaluates to False -- that is normal,
    not malformed.
    """
    if not _REQUIRED_IF_PATTERN.match(expr):
        raise RequiredIfSyntaxError(f'Malformed requiredIf expression "{expr}"; expected "config.<path>==<value>".')
    path, _, expected = expr.partition("==")
    try:
        value = resolve_path({"config": config}, path.strip())
    except KeyError:
        return False
    return value == expected.strip()
