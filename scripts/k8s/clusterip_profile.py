#!/usr/bin/env python3
"""Create an E2E profile whose explicitly exposed services use ClusterIP."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import yaml


def write_clusterip_profile(source: Path, target: Path) -> list[str]:
    source = source.resolve()
    target = target.resolve()
    if source.parent != target.parent:
        raise ValueError("E2E profile must stay beside its source profile")

    profile: dict[str, Any] = yaml.safe_load(source.read_text(encoding="utf-8"))
    converted: list[str] = []
    for module in profile.get("spec", {}).get("modules", []):
        config = module.get("config")
        if not isinstance(config, dict):
            continue
        service = config.get("kubernetesService")
        if not isinstance(service, dict):
            continue
        service["type"] = "ClusterIP"
        service.pop("nodePort", None)
        converted.append(str(module.get("id", "<unknown>")))

    target.write_text(yaml.safe_dump(profile, sort_keys=False), encoding="utf-8")
    return converted


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("target", type=Path)
    args = parser.parse_args()
    write_clusterip_profile(args.source, args.target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
