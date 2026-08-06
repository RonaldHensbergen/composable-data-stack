from __future__ import annotations

import os
import re
import shutil
import socket
import subprocess  # nosec B404
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .image_verification import default_fixture_path, load_policy_from_env, verify_images
from .security_common import SECRET_KEY_SEGMENT_RE, infer_profile_class


_ENV_REFERENCE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)([^}]*)\}")
# Docker publishes ports on every IPv4 interface when no host IP is specified.
# Preflight uses this only for a short-lived availability probe.
_DOCKER_WILDCARD_IPV4 = "0.0.0.0"  # nosec B104


@dataclass(frozen=True)
class PreflightCheck:
    status: str
    name: str
    message: str


def run_preflight(
    plan: dict[str, Any],
    compose_yaml: str,
    env_file: Path,
) -> list[PreflightCheck]:
    checks: list[PreflightCheck] = []
    checks.extend(_check_runtime(plan.get("runtime", {})))
    checks.extend(_check_environment(compose_yaml, env_file))
    checks.extend(_check_ports(compose_yaml))
    checks.extend(_check_images(plan, compose_yaml))
    return checks


def preflight_passed(checks: list[PreflightCheck]) -> bool:
    return not any(check.status == "FAIL" for check in checks)


def _check_runtime(runtime: dict[str, Any]) -> list[PreflightCheck]:
    runtime_type = runtime.get("type")
    if runtime_type != "docker-compose":
        return [
            PreflightCheck(
                "FAIL",
                "runtime",
                f'Unsupported runtime type "{runtime_type or "<missing>"}".',
            )
        ]

    docker_path = shutil.which("docker")
    if docker_path is None:
        return [
            PreflightCheck(
                "FAIL",
                "runtime.cli",
                "Docker CLI was not found. Install Docker and add it to PATH.",
            )
        ]

    checks = [
        PreflightCheck("PASS", "runtime.cli", f"Docker CLI found at {docker_path}.")
    ]
    checks.append(
        _run_runtime_command(
            ["docker", "compose", "version"],
            "runtime.compose",
            "Docker Compose is available.",
            "Docker Compose is unavailable. Install the Compose plugin.",
        )
    )
    checks.append(
        _run_runtime_command(
            ["docker", "info"],
            "runtime.daemon",
            "Docker daemon is reachable.",
            "Docker daemon is unreachable. Start Docker and verify access with `docker info`.",
        )
    )
    return checks


def _run_runtime_command(
    command: list[str],
    name: str,
    success_message: str,
    failure_message: str,
) -> PreflightCheck:
    try:
        result = subprocess.run(  # nosec B603
            command,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired):
        return PreflightCheck("FAIL", name, failure_message)

    if result.returncode != 0:
        return PreflightCheck("FAIL", name, failure_message)
    return PreflightCheck("PASS", name, success_message)


def _check_environment(compose_yaml: str, env_file: Path) -> list[PreflightCheck]:
    matches = list(_ENV_REFERENCE.finditer(compose_yaml))
    required_names = {m.group(1) for m in matches if _reference_is_required(m.group(2))}
    insecure_defaults = sorted({
        m.group(1)
        for m in matches
        if _reference_has_insecure_default(m.group(1), m.group(2))
    })

    if not required_names and not insecure_defaults:
        return [
            PreflightCheck(
                "PASS",
                "environment",
                "No required runtime environment values were declared.",
            )
        ]

    checks: list[PreflightCheck] = []
    if required_names:
        try:
            values = _load_env_values(env_file)
        except OSError:
            checks.append(
                PreflightCheck(
                    "FAIL",
                    "environment",
                    f"Environment file could not be read: {env_file}.",
                )
            )
        else:
            missing = sorted(
                name for name in required_names if not values.get(name, "").strip()
            )
            if missing:
                checks.extend(
                    PreflightCheck(
                        "FAIL",
                        f"environment.{name}",
                        f'Required environment value "{name}" is missing or empty.',
                    )
                    for name in missing
                )
            else:
                placeholders = sorted(
                    name
                    for name in required_names
                    if values[name].strip().lower() in {"change-me", "changeme"}
                    or values[name].strip().lower().startswith("change-me-")
                )
                checks.append(
                    PreflightCheck(
                        "PASS",
                        "environment",
                        f"All {len(required_names)} required runtime environment values are set.",
                    )
                )
                if placeholders:
                    checks.append(
                        PreflightCheck(
                            "WARN",
                            "environment.placeholders",
                            "Replace placeholder values for: "
                            + ", ".join(placeholders)
                            + ".",
                        )
                    )
    else:
        checks.append(
            PreflightCheck(
                "PASS",
                "environment",
                "No required runtime environment values were declared.",
            )
        )
    if insecure_defaults:
        checks.append(
            PreflightCheck(
                "WARN",
                "environment.insecure-defaults",
                "Replace insecure hardcoded defaults for: " + ", ".join(insecure_defaults) + ".",
            )
        )
    return checks


def _reference_is_required(suffix: str) -> bool:
    return not suffix or suffix.startswith(":?") or suffix.startswith("?")


def _reference_default_value(suffix: str) -> str | None:
    if suffix.startswith(":-"):
        default = suffix[2:]
    elif suffix.startswith("-"):
        default = suffix[1:]
    else:
        return None
    return default or None


def _reference_has_insecure_default(name: str, suffix: str) -> bool:
    default = _reference_default_value(suffix)
    if default is None or "$" in default:
        return False
    return bool(SECRET_KEY_SEGMENT_RE.search(name))


def _check_images(plan: dict[str, Any], compose_yaml: str) -> list[PreflightCheck]:
    """
    Enforce the CDS image policy (registry allowlist, digest pins, and, in
    full mode, cosign-verified signatures and provenance attestations).

    Disabled when the policy mode resolves to "off"; production profiles
    default to "policy" so static supply-chain checks run by default.
    """
    policy = load_policy_from_env(infer_profile_class(plan))
    if policy.mode == "off":
        return []

    findings = verify_images(compose_yaml, policy, fixture=default_fixture_path())
    if not findings:
        return [
            PreflightCheck(
                "PASS",
                "images",
                "All service images comply with the image verification policy.",
            )
        ]

    return [
        PreflightCheck(
            "FAIL",
            f"images.{finding['path']}",
            f"{finding['rule_id']}: {finding['message']}",
        )
        for finding in findings
    ]


def _load_env_values(env_file: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if env_file.exists():
        with env_file.open(encoding="utf-8") as handle:
            for line in handle:
                stripped = line.strip()
                if not stripped or stripped.startswith("#") or "=" not in stripped:
                    continue
                key, value = stripped.split("=", 1)
                key = key.strip()
                value = value.strip()
                if (
                    len(value) >= 2
                    and value[0] == value[-1]
                    and value[0] in {'"', "'"}
                ):
                    value = value[1:-1]
                if key:
                    values[key] = value

    values.update(os.environ)
    return values


def _check_ports(compose_yaml: str) -> list[PreflightCheck]:
    compose = yaml.safe_load(compose_yaml) or {}
    services = compose.get("services", {})
    declared_ports: list[tuple[str, str, int, str]] = []

    if isinstance(services, dict):
        for service_name, service in services.items():
            if not isinstance(service, dict):
                continue
            ports = service.get("ports", [])
            if not isinstance(ports, list):
                continue
            for port_entry in ports:
                for host, port, protocol in _published_ports(port_entry):
                    declared_ports.append(
                        (str(service_name), host, port, protocol)
                    )

    if not declared_ports:
        return [
            PreflightCheck("PASS", "ports", "No host ports were declared.")
        ]

    checks: list[PreflightCheck] = []
    seen: list[tuple[str, int, str, str]] = []
    for service_name, host, port, protocol in declared_ports:
        conflict = next(
            (
                previous_service
                for previous_host, previous_port, previous_protocol, previous_service in seen
                if previous_port == port
                and previous_protocol == protocol
                and _hosts_overlap(previous_host, host)
            ),
            None,
        )
        if conflict is not None:
            checks.append(
                PreflightCheck(
                    "FAIL",
                    f"ports.{service_name}",
                    f"Host port {host}:{port}/{protocol} conflicts with the port declared by {conflict}.",
                )
            )
            continue
        seen.append((host, port, protocol, service_name))

        available = _port_is_available(host, port, protocol)
        if available is None:
            checks.append(
                PreflightCheck(
                    "WARN",
                    f"ports.{service_name}",
                    f"Host port {host}:{port}/{protocol} uses an unsupported protocol and was not checked.",
                )
            )
        elif available:
            checks.append(
                PreflightCheck(
                    "PASS",
                    f"ports.{service_name}",
                    f"Host port {host}:{port}/{protocol} is available.",
                )
            )
        else:
            checks.append(
                PreflightCheck(
                    "FAIL",
                    f"ports.{service_name}",
                    f"Host port {host}:{port}/{protocol} is unavailable. Stop the process using it or change the profile port.",
                )
            )
    return checks


def _hosts_overlap(first: str, second: str) -> bool:
    wildcard_hosts = {_DOCKER_WILDCARD_IPV4, "::", ""}
    return first == second or first in wildcard_hosts or second in wildcard_hosts


def _published_ports(port_entry: Any) -> list[tuple[str, int, str]]:
    if isinstance(port_entry, int):
        return []

    if isinstance(port_entry, dict):
        published = port_entry.get("published")
        if published is None:
            return []
        ports = _expand_port_range(str(published))
        host = str(port_entry.get("host_ip") or _DOCKER_WILDCARD_IPV4)
        protocol = str(port_entry.get("protocol") or "tcp").lower()
        return [(host, port, protocol) for port in ports]

    if not isinstance(port_entry, str):
        return []

    if "/" in port_entry:
        value, protocol = port_entry.rsplit("/", 1)
        protocol = protocol.lower()
    else:
        value = port_entry
        protocol = "tcp"
    parts = value.rsplit(":", 2)
    if len(parts) == 1:
        return []
    elif len(parts) == 2:
        host = _DOCKER_WILDCARD_IPV4
        published = parts[0]
    else:
        host = parts[0].strip("[]") or _DOCKER_WILDCARD_IPV4
        published = parts[1]

    return [
        (host, port, protocol)
        for port in _expand_port_range(published)
    ]


def _expand_port_range(value: str) -> list[int]:
    parts = value.split("-", 1)
    try:
        start = int(parts[0])
        end = int(parts[1]) if len(parts) == 2 else start
    except ValueError:
        return []
    if not 1 <= start <= end <= 65535:
        return []
    return list(range(start, end + 1))


def _port_is_available(host: str, port: int, protocol: str) -> bool | None:
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    if protocol == "tcp":
        socket_type = socket.SOCK_STREAM
    elif protocol == "udp":
        socket_type = socket.SOCK_DGRAM
    else:
        return None
    try:
        with socket.socket(family, socket_type) as listener:
            listener.bind((host, port))
    except (OSError, OverflowError):
        return False
    return True
