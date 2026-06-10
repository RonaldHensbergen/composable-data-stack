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
