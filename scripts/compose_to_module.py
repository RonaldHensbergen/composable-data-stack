#!/usr/bin/env python3
"""Scaffold a CDS `module.yaml` from one or more services in an existing
`docker-compose.yaml`.

This automates the mechanical parts of the walkthrough in
docs/from-docker-to-cds-profile.md: lifting a compose service's `ports`/
`environment` into a module `configSchema`, replacing literal values with
`${config.*}` template placeholders, and flagging likely secret references
and cross-service dependencies for the author to finish by hand.

It deliberately does NOT try to infer `spec.provides`/`spec.consumes`
contracts, `metadata.category`, or security hardening (`read_only`,
`cap_drop`, image digest pinning, etc.) -- those require domain knowledge
this script doesn't have. The generated file is a starting point, not a
finished module; it will not pass `cds validate` until the TODOs it prints
are addressed.

Usage:
    python scripts/compose_to_module.py --compose docker-compose.yaml \\
        --service postgres-postgres --category warehouse --name postgres

    # Merge multiple compose services into one module (e.g. a webserver +
    # daemon + user-code trio that only make sense together):
    python scripts/compose_to_module.py --compose docker-compose.yaml \\
        --service dagster-user-code --service dagster-dagster-webserver \\
        --service dagster-dagster-daemon --category orchestration --name dagster

    # Preview without writing a file:
    python scripts/compose_to_module.py --compose docker-compose.yaml \\
        --service postgres-postgres --category warehouse --output -
"""

from __future__ import annotations

import argparse
import copy
import json
import re
import sys
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
MODULE_SCHEMA_PATH = REPO_ROOT / "cli" / "resources" / "module.schema.json"

_SECRET_NAME_HINT = re.compile(r"(PASSWORD|SECRET|TOKEN|PRIVATE_KEY|APIKEY|API_KEY)", re.IGNORECASE)
_ENV_INTERPOLATION = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)(:-[^}]*)?\}$")
_KNOWN_PORT_NAMES = {
    5432: "postgres",
    3306: "mysql",
    6379: "redis",
    5672: "amqp",
    9092: "kafka",
    8080: "http",
    8088: "http",
    3000: "http",
    443: "https",
    80: "http",
}

# Compose files (especially ones written before this stack existed) commonly
# bundle a plain, un-hardened database/cache image alongside the "real"
# application service(s), often pinned to an old tag. That image is almost
# never worth scaffolding as a brand-new module: this repo already has a
# hardened, contract-providing module for the common cases, so the right
# move is to bind to the existing shared/contracts/<kind>.yaml contract
# instead of duplicating the container. Match is on an image-name substring
# (before any `:tag`), checked against the *excluded* (non-selected)
# dependency, not the services the caller asked to scaffold.
_KNOWN_INFRA_CONTRACTS: dict[str, str] = {
    "postgres": "sql-database",
    "mysql": "sql-database",
    "mariadb": "sql-database",
    "redis": "cache-service",
    "keydb": "cache-service",
    "valkey": "cache-service",
}


def _find_existing_contract_providers(contract_kind: str) -> list[str]:
    """Return module ids (`<category>/<name>`) whose module.yaml already
    provides the given contract kind, so the TODO can point at a concrete
    existing module instead of just naming the contract."""
    providers: list[str] = []
    modules_root = REPO_ROOT / "modules"
    if not modules_root.is_dir():
        return providers
    for module_file in sorted(modules_root.glob("*/*/module.yaml")):
        try:
            data = yaml.safe_load(module_file.read_text(encoding="utf-8"))
        except yaml.YAMLError:
            continue
        if not isinstance(data, dict):
            continue
        provides = data.get("spec", {}).get("provides", []) if isinstance(data.get("spec"), dict) else []
        for entry in provides if isinstance(provides, list) else []:
            contract = entry.get("contract", {}) if isinstance(entry, dict) else {}
            if isinstance(contract, dict) and contract.get("kind") == contract_kind:
                providers.append(f"{module_file.parent.parent.name}/{module_file.parent.name}")
                break
    return providers


class ScaffoldError(SystemExit):
    """Raised for user-facing input errors (bad CLI args, missing service, etc.)."""

    def __init__(self, message: str) -> None:
        super().__init__(f"error: {message}")


def _camel_case(name: str) -> str:
    """Convert SNAKE_CASE / kebab-case env-var-ish names to lowerCamelCase."""
    parts = re.split(r"[_\-]+", name.strip().lower())
    parts = [p for p in parts if p]
    if not parts:
        return "value"
    return parts[0] + "".join(p.capitalize() for p in parts[1:])


def _infer_json_type(value: Any) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    return "string"


def _normalize_environment(raw_env: Any) -> dict[str, Any]:
    """Compose allows `environment:` as a mapping or a `KEY=VALUE`/`KEY` list; normalize to a dict."""
    if raw_env is None:
        return {}
    if isinstance(raw_env, dict):
        return dict(raw_env)
    if isinstance(raw_env, list):
        normalized: dict[str, Any] = {}
        for entry in raw_env:
            if "=" in entry:
                key, _, value = entry.partition("=")
                normalized[key] = value
            else:
                normalized[entry] = None
        return normalized
    raise ScaffoldError(f"Unsupported `environment:` shape: {type(raw_env).__name__}")


def _parse_port_entry(entry: Any) -> tuple[int | None, int, str]:
    """Parse one `ports:` list entry into (host_port_or_None, container_port, protocol)."""
    protocol = "TCP"
    if isinstance(entry, dict):
        # Long-form: {target: 5432, published: 5432, protocol: tcp}
        container_port = int(entry["target"])
        host_port = entry.get("published")
        host_port = int(host_port) if host_port not in (None, "") else None
        protocol = str(entry.get("protocol", "tcp")).upper()
        return host_port, container_port, protocol

    text = str(entry)
    if text.endswith("/udp"):
        protocol = "UDP"
        text = text[: -len("/udp")]
    elif text.endswith("/tcp"):
        text = text[: -len("/tcp")]

    segments = text.split(":")
    if len(segments) == 1:
        return None, int(segments[0]), protocol
    # Last segment is always the container port; anything before it (host,
    # or host-ip:host-port) is the published side.
    container_port = int(segments[-1])
    host_port = int(segments[-2]) if segments[-2] else None
    return host_port, container_port, protocol


def _port_field_name(existing: set[str], container_port: int) -> str:
    base = _KNOWN_PORT_NAMES.get(container_port, f"port{container_port}")
    name = f"{base}Port" if base != "port" else "port"
    if name not in existing:
        return name
    suffix = 2
    while f"{name}{suffix}" not in existing:
        suffix += 1
    return f"{name}{suffix}"


class ModuleScaffold:
    def __init__(
        self,
        name: str,
        category: str,
        service_keys: list[str],
        all_services: dict[str, Any] | None = None,
    ) -> None:
        self.name = name
        self.category = category
        self.service_keys = service_keys
        # Every service defined in the source compose file, keyed by name --
        # used to look up an *excluded* dependency's image so we can suggest
        # binding to an existing contract instead of scaffolding it.
        self.all_services = all_services or {}
        self.config_properties: dict[str, Any] = {}
        self.config_required: list[str] = []
        self.service_ports: list[dict[str, Any]] = []
        self.compose_services: dict[str, Any] = {}
        self.compose_volumes: dict[str, Any] = {}
        # Human-readable follow-ups collected while scanning, printed at the end.
        self.todos: list[str] = []

    def add_todo(self, message: str) -> None:
        if message not in self.todos:
            self.todos.append(message)

    def process_service(self, service_key: str, service_def: dict[str, Any]) -> None:
        service_def = copy.deepcopy(service_def)
        env = _normalize_environment(service_def.pop("environment", None))
        ports = service_def.pop("ports", [])

        rewritten_ports = []
        for raw_port in ports:
            host_port, container_port, protocol = _parse_port_entry(raw_port)
            field_name = _port_field_name(set(self.config_properties), container_port)
            if host_port is not None:
                self.config_properties[field_name] = {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 65535,
                    "default": host_port,
                    "description": f"Host port published for {service_key}'s container port {container_port}.",
                }
                rewritten_ports.append(f"${{config.{field_name}}}:{container_port}")
            else:
                rewritten_ports.append(str(container_port))
            self.service_ports.append(
                {"name": field_name.removesuffix("Port") or field_name, "containerPort": container_port, "protocol": protocol}
            )
        if rewritten_ports:
            service_def["ports"] = rewritten_ports

        rewritten_env: dict[str, Any] = {}
        for env_key, env_value in env.items():
            rewritten_env[env_key] = self._resolve_env_value(service_key, env_key, env_value)
        if rewritten_env:
            service_def["environment"] = rewritten_env

        depends_on = service_def.get("depends_on")
        if isinstance(depends_on, (dict, list)):
            dep_names = list(depends_on.keys()) if isinstance(depends_on, dict) else list(depends_on)
            external = [d for d in dep_names if d not in self.service_keys]
            for dep_name in external:
                self._flag_external_dependency(service_key, dep_name)

        volumes = service_def.get("volumes", [])
        for volume_entry in volumes if isinstance(volumes, list) else []:
            if isinstance(volume_entry, str) and ":" in volume_entry:
                volume_name = volume_entry.split(":", 1)[0]
                if not volume_name.startswith(("./", "/", "~")):
                    self.compose_volumes.setdefault(volume_name, {})

        self.compose_services[service_key] = service_def

    def _flag_external_dependency(self, service_key: str, dep_name: str) -> None:
        """Flag a depends_on target that isn't part of this scaffold. If it
        looks like a well-known, un-hardened infra container (a plain
        postgres/mysql/redis image, often on an old pinned tag), point at
        the existing shared contract -- and an existing provider module, if
        one is already in the repo -- instead of suggesting a new module."""
        dep_def = self.all_services.get(dep_name)
        image = str(dep_def.get("image", "")) if isinstance(dep_def, dict) else ""
        image_name = image.split(":", 1)[0].rsplit("/", 1)[-1].lower()

        contract_kind = next((kind for prefix, kind in _KNOWN_INFRA_CONTRACTS.items() if prefix in image_name), None)
        if contract_kind is None:
            self.add_todo(
                f"Service '{service_key}' depends_on external service '{dep_name}' not included in this module "
                "-- this likely belongs in spec.consumes (a contract binding) rather than "
                "spec.implementation.compose.services[...].depends_on. See docs/modules.md."
            )
            return

        providers = _find_existing_contract_providers(contract_kind)
        if providers:
            provider_hint = f"an existing provider module already in this repo: {', '.join(providers)}"
        else:
            provider_hint = (
                f"shared/contracts/{contract_kind}.yaml (no provider module found yet -- check before adding one)"
            )
        self.add_todo(
            f"Service '{service_key}' depends_on '{dep_name}' (image={image or 'unknown'!r}), which looks like a "
            f"plain, un-hardened '{contract_kind}' container -- likely on an old pinned tag from the original "
            "compose file. Don't scaffold this as a new module: instead, drop it from "
            f"spec.implementation.compose.services and add a spec.consumes entry bound to the "
            f"'{contract_kind}' contract, resolved via a profile's contractRef against {provider_hint}."
        )

    def _resolve_env_value(self, service_key: str, env_key: str, value: Any) -> Any:
        field_name = _camel_case(env_key)

        if isinstance(value, str):
            match = _ENV_INTERPOLATION.match(value.strip())
            if match:
                var_name = match.group(1)
                if _SECRET_NAME_HINT.search(var_name) or _SECRET_NAME_HINT.search(env_key):
                    secret_field = field_name if field_name.endswith("From") else f"{field_name}From"
                    self.config_properties[secret_field] = {
                        "type": "string",
                        "pattern": "^secrets\\.[a-zA-Z0-9_-]+$",
                        "description": (
                            f"Reference to the secret backing {service_key}'s {env_key} environment variable "
                            f"(originally `{value}`)."
                        ),
                    }
                    self.config_required.append(secret_field)
                    return f"${{config.{secret_field}}}"

                self.add_todo(
                    f"Service '{service_key}' env var {env_key}={value!r} looks like a reference to another "
                    "service (not a secret). This is likely a spec.consumes contract binding "
                    "(${bindings.<contract>.<field>}), not a literal config value -- see docs/modules.md."
                )
                return value

            # Not a bare "${VAR}" (full match above), but still contains an
            # interpolation or a connection-string shape (e.g. a Postgres URI
            # combining a hostname, a "${CDS_*}" secret placeholder, and a
            # port). Baking that into a configSchema *default* would leak a
            # secret placeholder and a hardcoded peer hostname into the
            # module -- exactly the "hard-coded connection string" case the
            # walkthrough maps to a provides/consumes contract instead.
            if "${" in value or "://" in value:
                self.add_todo(
                    f"Service '{service_key}' env var {env_key}={value!r} looks like a hardcoded connection "
                    "string (embeds a secret placeholder and/or another service's hostname). This should "
                    "become a spec.consumes contract binding (${bindings.<contract>.<field>}) resolved from "
                    "the producing module's spec.provides contract, not a literal/default config value -- "
                    "see docs/modules.md and the 'Hard-coded connection string' row in "
                    "docs/from-docker-to-cds-profile.md's concept-mapping table."
                )
                return value

        if value is None:
            self.config_properties.setdefault(
                field_name,
                {"type": "string", "default": "", "description": f"Value for {service_key}'s {env_key} environment variable."},
            )
            return f"${{config.{field_name}}}"

        json_type = _infer_json_type(value)
        self.config_properties[field_name] = {
            "type": json_type,
            "default": value,
            "description": f"Value for {service_key}'s {env_key} environment variable.",
        }
        return f"${{config.{field_name}}}"

    def to_module_dict(self) -> dict[str, Any]:
        primary_service = self.service_keys[0]
        config_schema: dict[str, Any] = {
            "type": "object",
            "additionalProperties": False,
        }
        if self.config_required:
            # Stable order, no duplicates, required fields first for readability.
            seen: set[str] = set()
            ordered_required = [r for r in self.config_required if not (r in seen or seen.add(r))]
            config_schema["required"] = ordered_required
        config_schema["properties"] = self.config_properties

        implementation_compose: dict[str, Any] = {"services": self.compose_services}
        if self.compose_volumes:
            implementation_compose["volumes"] = self.compose_volumes

        module: dict[str, Any] = {
            "apiVersion": "cds/v1alpha1",
            "kind": "Module",
            "metadata": {
                "name": self.name,
                "category": self.category,
                "version": "0.1.0",
                "displayName": self.name.replace("-", " ").title(),
                "description": (
                    f"TODO: describe {self.name}. Auto-generated by scripts/compose_to_module.py from "
                    f"compose service(s) {', '.join(self.service_keys)}; review before use."
                ),
            },
            "spec": {
                "runtime": {
                    "type": "container",
                    "service": {
                        "name": primary_service,
                        "ports": self.service_ports,
                    },
                },
                "configSchema": config_schema,
                "consumes": [],
                "provides": [],
                "implementation": {
                    "kind": "docker-compose",
                    "compose": implementation_compose,
                },
            },
        }
        return module


def _load_compose(compose_path: Path) -> dict[str, Any]:
    if not compose_path.exists():
        raise ScaffoldError(f"compose file not found: {compose_path}")
    data = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or "services" not in data:
        raise ScaffoldError(f"{compose_path} does not look like a docker-compose file (no top-level 'services:')")
    return data


def _render_module_yaml(module: dict[str, Any]) -> str:
    header = (
        f"# modules/{module['metadata']['category']}/{module['metadata']['name']}/module.yaml\n"
        "# yaml-language-server: $schema=../../../cli/resources/module.schema.json\n"
    )
    body = yaml.safe_dump(module, sort_keys=False, default_flow_style=False)
    return header + body


def _self_check(module: dict[str, Any]) -> list[str]:
    """Best-effort structural check against module.schema.json (jsonschema is
    already a project dependency). This only catches shape errors -- it does
    not validate contract wiring, module isolation, or anything semantic
    that `cds validate` additionally checks."""
    try:
        from jsonschema import Draft202012Validator
    except ImportError:
        return ["jsonschema not installed -- skipped schema self-check (pip install -e .[dev])"]

    schema = json.loads(MODULE_SCHEMA_PATH.read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(module), key=lambda e: list(e.path))
    return [f"{'/'.join(str(p) for p in e.path) or '<root>'}: {e.message}" for e in errors]


def build_scaffold(
    compose: dict[str, Any],
    service_keys: list[str],
    name: str,
    category: str,
) -> ModuleScaffold:
    services = compose["services"]
    missing = [key for key in service_keys if key not in services]
    if missing:
        raise ScaffoldError(
            f"service(s) not found in compose file: {missing!r}. Available: {sorted(services)!r}"
        )

    scaffold = ModuleScaffold(name=name, category=category, service_keys=service_keys, all_services=services)
    for key in service_keys:
        scaffold.process_service(key, services[key])
    return scaffold


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--compose", required=True, type=Path, help="Path to the source docker-compose.yaml")
    parser.add_argument(
        "--service",
        action="append",
        dest="services",
        required=True,
        help="Compose service key to include; repeat to merge several services into one module",
    )
    parser.add_argument("--name", required=True, help="Module name (metadata.name / modules/<category>/<name>/)")
    parser.add_argument("--category", required=True, help="Module category (metadata.category), e.g. warehouse, bi")
    parser.add_argument(
        "--output",
        help="Output path for module.yaml (default: modules/<category>/<name>/module.yaml). Use '-' for stdout.",
    )
    parser.add_argument("--force", action="store_true", help="Overwrite an existing output file")
    args = parser.parse_args(argv)

    compose = _load_compose(args.compose)
    scaffold = build_scaffold(compose, args.services, args.name, args.category)
    module = scaffold.to_module_dict()
    rendered = _render_module_yaml(module)

    if args.output == "-":
        print(rendered)
    else:
        output_path = Path(args.output) if args.output else REPO_ROOT / "modules" / args.category / args.name / "module.yaml"
        if output_path.exists() and not args.force:
            raise ScaffoldError(f"{output_path} already exists (pass --force to overwrite)")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(rendered, encoding="utf-8")
        print(f"Wrote {output_path}", file=sys.stderr)

    schema_errors = _self_check(module)
    if schema_errors:
        print("\nSchema self-check found issues (fix these before `cds validate`):", file=sys.stderr)
        for err in schema_errors:
            print(f"  - {err}", file=sys.stderr)

    if scaffold.todos:
        print("\nManual follow-ups (this script cannot infer these):", file=sys.stderr)
        for todo in scaffold.todos:
            print(f"  - {todo}", file=sys.stderr)

    print(
        "\nThis is a starting scaffold, not a finished module. Before it will pass `cds validate`/`cds test`, "
        "review: metadata.category/description, spec.provides/consumes contracts (see shared/contracts/ and "
        "docs/modules.md), secret wiring in a profile's spec.secrets.values, image digest pinning, and "
        "container hardening (read_only, cap_drop, security_opt) per existing modules like "
        "modules/secrets/vault/module.yaml. See docs/from-docker-to-cds-profile.md for the full walkthrough.",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
