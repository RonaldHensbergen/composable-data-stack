"""
Groups `docker compose ps -a --format json` output by health state for
`cds state`. Provider-neutral: reads only the generic fields Compose
itself emits (Service/Name, Health, State), no module- or
service-specific knowledge.
"""
from __future__ import annotations

import json
from typing import Any


def parse_compose_ps_json(raw_output: str) -> list[dict[str, Any]]:
    """
    Parses `docker compose ps --format json` output in either shape.
    Returns [] for blank output. Skips lines that aren't valid JSON
    objects rather than failing the whole parse on one bad line.
    """
    text = raw_output.strip()
    if not text:
        return []

    try:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            return [entry for entry in parsed if isinstance(entry, dict)]
        if isinstance(parsed, dict):
            return [parsed]
    except json.JSONDecodeError:
        pass

    services: list[dict[str, Any]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(entry, dict):
            services.append(entry)
    return services


def _bucket_for(service: dict[str, Any]) -> str:
    health = str(service.get("Health") or "").strip()
    if health:
        return health.upper()
    state = str(service.get("State") or "").strip()
    if state:
        return state.upper()
    return "UNKNOWN"


def group_services_by_health(services: list[dict[str, Any]]) -> dict[str, list[str]]:
    """
    Groups parsed compose-ps entries into {bucket_name: [service_name, ...]},
    both deterministically sorted. Bucket is Health when the service has a
    healthcheck (Compose reports "starting"/"healthy"/"unhealthy"), else
    falls back to State (e.g. "exited", "running"), else "UNKNOWN" if
    neither is present.
    """
    buckets: dict[str, set[str]] = {}
    for service in services:
        name = str(service.get("Service") or service.get("Name") or "").strip()
        if not name:
            continue
        bucket = _bucket_for(service)
        buckets.setdefault(bucket, set()).add(name)

    return {bucket: sorted(names) for bucket, names in sorted(buckets.items())}


_COLORS = {
    "HEALTHY": "\033[32m",
    "RUNNING": "\033[32m",
    "UNHEALTHY": "\033[31m",
    "EXITED": "\033[31m",
    "STARTING": "\033[33m",
    "UNKNOWN": "\033[2m",
}
_RESET = "\033[0m"


def format_state_output(grouped: dict[str, list[str]], use_color: bool = False) -> str:
    if not grouped:
        return "No services found."

    lines = []
    for bucket, names in grouped.items():
        label = f"{bucket}:"
        if use_color:
            color = _COLORS.get(bucket, "")
            label = f"{color}{label}{_RESET}" if color else label
        lines.append(label)
        for name in names:
            lines.append(f"  - {name}")
    return "\n".join(lines)
