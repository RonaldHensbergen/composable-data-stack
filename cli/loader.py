# cli/loader.py
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from .diagnostics import Diagnostic


def load_yaml_file(path: Path) -> tuple[dict[str, Any] | None, list[Diagnostic]]:
    if not path.exists():
        return None, [
            Diagnostic(
                level="error",
                code="E020",
                message=f"YAML file not found: {path}",
                path=str(path),
            )
        ]

    try:
        with path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except yaml.YAMLError as e:
        return None, [
            Diagnostic(
                level="error",
                code="E001",
                message=f"Invalid YAML: {e}",
                path=str(path),
            )
        ]

    if not isinstance(data, dict):
        return None, [
            Diagnostic(
                level="error",
                code="E010",
                message="Top-level YAML document must be a mapping/object.",
                path=str(path),
            )
        ]

    return data, []
