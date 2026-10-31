# pyright: reportMissingModuleSource=false
# cli/renderer.py
"""
Render docker-compose YAML from a composition plan.
"""
from __future__ import annotations

import os
import re
from collections.abc import Callable
from copy import deepcopy
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import yaml

from .confinement import (
    apply_confinement,
    effective_confinement,
    profile_confinement,
    validate_confinement,
)
from .constants import MAX_NESTING_DEPTH, MaxNestingDepthExceeded
from .diagnostics import Diagnostic
from .loader import resolve_module_dir
from .resolver import parse_contract_ref
from .secret_leak import (
    check_generated_text,
    declared_secret_env_names,
    resolved_secret_values,
)
from .utils import (
    _atomic_write,
    _compose_service_name,
    module_dns_host,
    module_name_from_source,
    module_service_dns_names,
)
from .validator import validate_plan_marker_shapes


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
        env_file:    .env path used to look up resolved secret values so the
                     output file is never written if it would contain one.

    Returns:
        Tuple of (output_yaml, diagnostics).
    """
    diagnostics: list[Diagnostic] = []
    secrets = plan.get("secrets", {})
    certificates = plan.get("certificates", {})
    compose_dir = Path(output_path).resolve().parent if output_path else Path.cwd().resolve()
    profile_dir = _resolve_profile_dir(plan)
    project_root = _resolve_project_root(profile_dir)

    # Extract networks from runtime config
    runtime = plan.get("runtime", {})
    networks_config = runtime.get("networks", [])
    
    # Build networks section
    networks: dict[str, Any] = {}
    for net in networks_config:
        net_name = net.get("name", "default")
        networks[net_name] = {}
        driver = net.get("driver")
        if driver:
            networks[net_name]["driver"] = driver

    # Build the default network name from namespace or profile name
    default_network_name = runtime.get("namespace") or plan.get("metadata", {}).get("name", "cds")

    compose: dict[str, Any] = {
        "name": plan.get("metadata", {}).get("name", "cds"),
        "services": {},
        "volumes": {},
    }
    module_service_names: dict[str, list[str]] = {}
    
    # Add networks if defined
    if networks or default_network_name:
        if not networks:
            networks[default_network_name] = {}
        compose["networks"] = networks

    profile_confinement_diags = validate_confinement(
        profile_confinement(plan), "spec.security.runtime.confinement", profile_dir
    )
    diagnostics.extend(profile_confinement_diags)

    is_production = _is_production_plan(plan)

    # Compose volume names are namespaced per module id ("<module-id>-
    # <volume-name>") when they are written to the top-level volumes map, so
    # a service that mounts another module's volume must reference that
    # fully rendered name. Such cross-module references (e.g. a backup
    # engine mounting a producer's backup-source volumeName, issue #668)
    # must not be namespaced again with the consuming module's id.
    external_volume_names: set[str] = set()
    for module in plan.get("modules", []):
        if not isinstance(module, dict):
            continue
        impl = module.get("implementation", {})
        compose_impl = impl.get("compose") if isinstance(impl, dict) else None
        volumes = compose_impl.get("volumes") if isinstance(compose_impl, dict) else None
        if isinstance(volumes, dict):
            for volume_name in volumes:
                if isinstance(volume_name, str) and volume_name:
                    external_volume_names.add(f'{module.get("id")}-{volume_name}')

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

        # Plans can be loaded from files, so re-check what validate already
        # checked: marker shapes the renderer cannot honor would silently
        # leak into the rendered output.
        marker_diags = validate_plan_marker_shapes([module])
        if marker_diags:
            diagnostics.extend(marker_diags)
            continue

        services = apply_production_gates(
            compose_impl.get("services", {}), is_production
        )
        volumes = apply_production_gates(
            compose_impl.get("volumes", {}), is_production
        )

        try:
            rendered_services, unsafe_diags = _render_services(
                module,
                services,
                secrets,
                profile_dir=profile_dir,
                project_root=project_root,
                compose_dir=compose_dir,
                network_name=default_network_name,
                certificates=certificates,
                is_production=is_production,
                external_volume_names=external_volume_names,
            )
            diagnostics.extend(unsafe_diags)
            rendered_volumes = _render_volumes(module, volumes, secrets, certificates)
        except MaxNestingDepthExceeded:
            diagnostics.append(Diagnostic(
                level="error",
                code="E094",
                message=(
                    f'Module "{module.get("id")}" service/volume templates exceed the '
                    f"maximum supported nesting depth ({MAX_NESTING_DEPTH})."
                ),
                path=f'module:{module.get("id")}.implementation.compose',
            ))
            continue

        confinement_path = f'module:{module.get("id")}.security.confinement'
        # Plans can be loaded from files, so re-check what validate already checked.
        confinement_diags = list(profile_confinement_diags)
        confinement_diags.extend(validate_confinement(
            (module.get("security") or {}).get("confinement"), confinement_path, profile_dir
        ))
        if not confinement_diags:
            module_confinement = effective_confinement(
                profile_confinement(plan),
                (module.get("security") or {}).get("confinement"),
            )
            for service_def in rendered_services.values():
                confinement_diags.extend(apply_confinement(
                    service_def, module_confinement, compose_dir, profile_dir, confinement_path
                ))
                if confinement_diags:
                    break
        if confinement_diags:
            diagnostics.extend(d for d in confinement_diags if d not in profile_confinement_diags)
            continue

        # Handle initDbEnv for postgres service (merge additional env vars)
        _merge_init_db_env(rendered_services, module, secrets, certificates)

        for service_name, service_def in rendered_services.items():
            compose_service_name = _compose_service_name(module["id"], service_name)
            compose["services"][compose_service_name] = service_def
            module_service_names.setdefault(module["id"], []).append(compose_service_name)

        for volume_name, volume_def in rendered_volumes.items():
            compose["volumes"][f'{module["id"]}-{volume_name}'] = volume_def

    if not compose["volumes"]:
        compose.pop("volumes")

    diagnostics.extend(_validate_provided_contract_hosts(plan, module_service_names))

    _add_cross_module_dependencies(compose, plan, module_service_names)

    output = yaml.safe_dump(compose, sort_keys=False)
    diagnostics.extend(_check_unresolved_expressions(output))

    if output_path:
        # CDS-SEC-030: scan before writing so a leaking file never lands on disk.
        leak_diags = check_generated_text(
            output,
            output_path,
            resolved_secret_values(declared_secret_env_names(plan), env_file),
        )
        diagnostics.extend(leak_diags)
        if not leak_diags:
            path = Path(output_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            _atomic_write(path, output)

    route_documents, route_diags = _generate_reverse_proxy_routes(plan, module_service_names)
    diagnostics.extend(route_diags)
    if output_path and not any(d.level == "error" for d in diagnostics):
        _write_reverse_proxy_route_documents(route_documents, compose_dir)

    return output, diagnostics


_REVERSE_PROXY_ROUTES_FILE = "traefik/dynamic/cds-routes-{module}.yml"


def _is_valid_backend_port(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, str):
        return value.isdigit() and 1 <= int(value) <= 65535
    return isinstance(value, int) and 1 <= value <= 65535


_DNS_LABEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")


def _endpoint_hostname(value: Any) -> str | None:
    """
    Extract the network hostname a contract endpoint advertises.

    Bare values are hostnames; URL-shaped endpoints contribute only their
    hostname part. Anything else (external DNS names containing dots,
    host:port pairs, empty values) is outside the Compose network's
    namespace and cannot be cross-checked against rendered services.
    """
    if not isinstance(value, str) or not value:
        return None
    if "://" in value:
        try:
            return urlsplit(value).hostname
        except ValueError:
            return None
    return value


def _provided_contract_host_candidates(
    kind: Any, spec: dict[str, Any], module_id: str
) -> list[tuple[str, str | None]]:
    """
    The (field, host) pairs a provided contract's spec advertises as network
    addresses, per contract kind. Shared by both render targets' E150
    cross-checks (issues #1010, #1029, #1035).

    Sink kinds (`lineage-sink`, `log-sink`) are dialed by consumers through
    both `host` and the pre-composed `connectionUri`, so both are checked;
    a `serviceName` advertisement (as on `http-service`) is checked as
    well, mapped through the renderer's service-naming rule.
    """
    candidates: list[tuple[str, str | None]] = []
    if kind == "http-service":
        advertised_service = spec.get("serviceName")
        if isinstance(advertised_service, str) and advertised_service:
            candidates.append(
                ("serviceName", _compose_service_name(module_id, advertised_service))
            )
        else:
            candidates.append(("host", spec.get("host")))
    elif kind in ("sql-database", "cache-service"):
        candidates.append(("host", spec.get("host")))
    elif kind == "object-storage":
        candidates.append(("endpoint", _endpoint_hostname(spec.get("endpoint"))))
    elif kind in ("lineage-sink", "log-sink"):
        advertised_service = spec.get("serviceName")
        if isinstance(advertised_service, str) and advertised_service:
            candidates.append(
                ("serviceName", _compose_service_name(module_id, advertised_service))
            )
        candidates.append(("host", spec.get("host")))
        candidates.append(("connectionUri", _endpoint_hostname(spec.get("connectionUri"))))
    return candidates


def _validate_provided_contract_hosts_against(
    modules: Any,
    rendered_by_module: dict[str, list[str]],
    *,
    service_kind: str,
    fix_hint: str,
    resolve_service_name: Callable[[str, str], str] | None = None,
) -> list[Diagnostic]:
    """
    E150 cross-check core shared by both render targets (issues #1010,
    #1035): every provided contract's advertised network host must match
    a service the render produced for that module, else the render fails
    closed instead of shipping a broken endpoint. `service_kind` and
    `fix_hint` word the diagnostic for the target ("compose service" vs
    "Kubernetes Service"); `resolve_service_name` optionally maps a
    `serviceName` advertisement to the name the workload actually renders
    (the helm target's workload-level `serviceName` overrides). Hosts
    outside the cluster namespace (dots, ports, credentials) are not
    checked, and modules whose services failed to render are left to
    their own diagnostics.
    """
    diagnostics: list[Diagnostic] = []
    for module in modules:
        if not isinstance(module, dict):
            continue
        module_id = str(module.get("id"))
        rendered = rendered_by_module.get(module_id) or []
        if not rendered:
            continue
        provides = module.get("provides")
        if not isinstance(provides, dict):
            continue
        for provide_name, provided in provides.items():
            if not isinstance(provided, dict):
                continue
            kind = provided.get("kind")
            spec = provided.get("spec")
            if not isinstance(spec, dict):
                continue
            for field, host in _provided_contract_host_candidates(kind, spec, module_id):
                if not isinstance(host, str) or not _DNS_LABEL_RE.match(host):
                    continue
                if field == "serviceName" and resolve_service_name is not None:
                    host = resolve_service_name(module_id, host)
                if host in rendered:
                    continue
                diagnostics.append(Diagnostic(
                    level="error",
                    code="E150",
                    message=(
                        f'Provided contract "{provide_name}" of module "{module_id}" '
                        f'advertises network host "{host}", which is not a {service_kind} '
                        "the render produced for that module (rendered "
                        f'services: {", ".join(rendered)}); consumers of the contract '
                        "would resolve a hostname nothing answers. " + fix_hint
                    ),
                    path=f"module:{module_id}.provides.{provide_name}.spec.{field}",
                ))
    return diagnostics


def _validate_provided_contract_hosts(
    plan: dict[str, Any],
    module_service_names: dict[str, list[str]],
) -> list[Diagnostic]:
    """
    Cross-check every provided contract's advertised network host against
    the compose services the render actually produced (issue #1010).

    Mirrors the E136 route check: a contract advertising a host nothing
    answers on the Compose network fails closed with E150 instead of
    shipping a broken endpoint. Multi-service modules advertise their
    serving service via `serviceName` (or point the endpoint at a specific
    compose service via `${service.services.<name>}`); that name is mapped
    through the same service-naming rule the renderer uses.
    """
    return _validate_provided_contract_hosts_against(
        plan.get("modules", []),
        module_service_names,
        service_kind="compose service",
        fix_hint=(
            "Instantiate the module with an id matching its compose service "
            "name, advertise the serving service via a serviceName field, or "
            "build the endpoint from ${service.dns} or ${service.services.<name>}."
        ),
    )


def _generate_reverse_proxy_routes(
    plan: dict[str, Any], module_service_names: dict[str, list[str]]
) -> tuple[dict[str, dict[str, Any]], list[Diagnostic]]:
    """
    Generate Traefik file-provider dynamic route documents from profile
    bindings instead of Docker label autodiscovery (issue #581).

    For every module providing a `reverse-proxy` contract, each
    `config.routes` entry targets a profile instance that provides an
    `http-service` contract -- a bare module id routes to that module's
    first http-service provide, while `<module-id>.<contract-name>` picks a
    specific provide entry when the module exposes several; the backend
    address comes from the resolved contract's `host`/`port` fields, mapped through the renderer's
    compose service-naming rule when the provider advertises a
    `serviceName` (multi-service modules such as dagster name the serving
    service, since the instance id alone does not resolve on the Compose
    network), and is cross-checked against the services the render
    actually produced -- an address nothing answers fails closed instead
    of 502ing at runtime. The optional `certificate` alias is validated
    against the plan's certificate mapping (issue #578) and, for TLS-enabled
    routers, wired as the router's certificate files. Invalid inputs -- an unknown target, an
    unresolvable endpoint (host missing, or a port outside 1-65535), a backend
    address that matches no rendered
    service, an entrypoint the proxy does not declare, an undeclared
    certificate alias, or a host duplicated by an
    earlier route of the same proxy -- produce E136 diagnostics
    instead of silently generating broken config; routes are emitted in
    sorted order so repeated renders are byte-identical.
    """
    diagnostics: list[Diagnostic] = []
    modules = plan.get("modules", [])
    certificates = plan.get("certificates", {})
    modules_by_id = {m.get("id"): m for m in modules if isinstance(m, dict)}
    documents: dict[str, dict[str, Any]] = {}

    for module in modules:
        if not isinstance(module, dict):
            continue
        proxy_id = module.get("id")
        resolved_provides = module.get("provides") or {}
        if not any(p.get("kind") == "reverse-proxy" for p in resolved_provides.values()):
            continue

        routes = (module.get("config") or {}).get("routes") or []
        if not routes:
            continue

        routers: dict[str, dict[str, Any]] = {}
        services: dict[str, dict[str, Any]] = {}
        tls_certificates: dict[str, dict[str, str]] = {}

        # Entrypoints the proxy actually declares (e.g. traefik's
        # config.entryPoints keys web/websecure). A route naming anything
        # else would render a router no entrypoint serves -- traffic
        # silently 404s -- so those fail closed with E136 below. Providers
        # without an entryPoints config mapping declare nothing here and
        # their routes are not checked.
        proxy_entry_points = (module.get("config") or {}).get("entryPoints")
        known_entry_points = (
            set(proxy_entry_points)
            if isinstance(proxy_entry_points, dict) and proxy_entry_points
            else None
        )

        # Duplicate hosts per proxy are rejected up front, in profile order:
        # two routers with the same Host() rule would make Traefik pick one
        # by priority, silently. Only the later occurrence is flagged so
        # the diagnostic points at the entry to fix; the first stays valid.
        duplicate_route_indexes: set[int] = set()
        hosts_seen: dict[str, int] = {}
        for route_index, route in enumerate(routes):
            if not isinstance(route, dict):
                continue
            route_host = str(route.get("host"))
            if route_host in hosts_seen:
                duplicate_route_indexes.add(route_index)
            else:
                hosts_seen[route_host] = route_index

        # Sort by (host, target) so the generated document is
        # deterministic; diagnostics keep the route's ORIGINAL profile
        # index so the path points at the entry the user wrote.
        ordered_routes = sorted(
            (
                (original_index, route)
                for original_index, route in enumerate(routes)
                if isinstance(route, dict)
            ),
            key=lambda item: (str(item[1].get("host", "")), str(item[1].get("target", ""))),
        )

        for sorted_index, (original_index, route) in enumerate(ordered_routes):
            path_prefix = f"module:{proxy_id}.config.routes[{original_index}]"
            target_id = route.get("target")
            host = route.get("host")

            if original_index in duplicate_route_indexes:
                diagnostics.append(Diagnostic(
                    level="error",
                    code="E136",
                    message=(
                        f'Reverse-proxy route host "{host}" duplicates an earlier route of '
                        f'proxy "{proxy_id}"; every route must expose a unique host (two '
                        "routers with the same Host rule would make the proxy pick one "
                        "by priority, silently)."
                    ),
                    path=f"{path_prefix}.host",
                ))
                continue

            # `target` accepts either a bare module id (its first
            # http-service provide wins, mirroring the contractRef
            # vocabulary's module half) or `<module-id>.<contract-name>` to
            # pick a specific endpoint when the target provides several.
            parsed_target = parse_contract_ref(target_id) if isinstance(target_id, str) else None
            target_module_id = parsed_target[0] if parsed_target else target_id
            provide_name = parsed_target[1] if parsed_target else None

            target = modules_by_id.get(target_module_id)
            if target is None:
                diagnostics.append(Diagnostic(
                    level="error",
                    code="E136",
                    message=f'Reverse-proxy route host "{host}" targets unknown module "{target_module_id}".',
                    path=f"{path_prefix}.target",
                ))
                continue

            target_provides = (target.get("provides") or {})
            if provide_name is not None:
                http_entry = target_provides.get(provide_name)
                if http_entry is None or http_entry.get("kind") != "http-service":
                    diagnostics.append(Diagnostic(
                        level="error",
                        code="E136",
                        message=(
                            f'Reverse-proxy route host "{host}" targets module "{target_module_id}", '
                            f'which does not provide an http-service contract named "{provide_name}" '
                            '(a "<module-id>.<contract-name>" target must name an existing '
                            "http-service provide entry)."
                        ),
                        path=f"{path_prefix}.target",
                    ))
                    continue
            else:
                http_entry = next(
                    (p for p in target_provides.values() if p.get("kind") == "http-service"),
                    None,
                )
            spec = (http_entry or {}).get("spec") or {}
            advertised_host = spec.get("host")
            backend_port = spec.get("port")
            if (
                http_entry is None
                or not isinstance(advertised_host, str)
                or not advertised_host
                or not _is_valid_backend_port(backend_port)
            ):
                diagnostics.append(Diagnostic(
                    level="error",
                    code="E136",
                    message=(
                        f'Reverse-proxy route host "{host}" targets module "{target_id}", which does not '
                        "expose a resolvable http-service endpoint (its contract spec lacks host/port); "
                        "route generation refuses to guess a backend address."
                    ),
                    path=f"{path_prefix}.target",
                ))
                continue

            # The backend address must be a name that actually resolves on
            # the rendered Compose network. Providers whose compose service
            # name differs from the instance id (multi-service modules such
            # as dagster) advertise the serving service via `serviceName`;
            # map it through the renderer's service-naming rule so it
            # matches the name Compose creates.
            advertised_service = spec.get("serviceName")
            if isinstance(advertised_service, str) and advertised_service:
                backend_host = _compose_service_name(str(target_module_id), advertised_service)
            else:
                backend_host = advertised_host

            rendered_services = module_service_names.get(str(target_module_id), [])
            if backend_host not in rendered_services:
                diagnostics.append(Diagnostic(
                    level="error",
                    code="E136",
                    message=(
                        f'Reverse-proxy route host "{host}" resolves to backend address '
                        f'"{backend_host}", which is not a rendered compose service of module '
                        f'"{target_module_id}" (rendered services: '
                        f'{", ".join(rendered_services) or "none"}); route generation refuses to '
                        "emit an address nothing answers. Multi-service providers must advertise "
                        "the serving service via a serviceName field in their http-service "
                        "provide entry."
                    ),
                    path=f"{path_prefix}.target",
                ))
                continue

            tls_enabled = route.get("tls", True)
            certificate = route.get("certificate")
            if certificate is not None and certificate not in certificates:
                diagnostics.append(Diagnostic(
                    level="error",
                    code="E136",
                    message=(
                        f'Reverse-proxy route host "{host}" references certificate alias "{certificate}", '
                        "which is not declared in spec.certificates.values."
                    ),
                    path=f"{path_prefix}.certificate",
                ))
                continue
            # The alias is validated regardless of the router's TLS flag, but
            # only wired for TLS routers: a plaintext router never presents a
            # certificate, so its alias must not contribute to the document's
            # tls.certificates section.
            if certificate is not None and tls_enabled:
                tls_certificates[certificate] = {
                    "certFile": f"/etc/traefik/certs/{certificate}.crt",
                    "keyFile": f"/etc/traefik/certs/{certificate}.key",
                }

            router_name = f"cds-route-{sorted_index}-{host or 'default'}"
            service_name = f"cds-service-{target_id}"
            route_entry_points = route.get("entryPoints") or ["websecure"]
            if known_entry_points is not None and any(
                ep not in known_entry_points for ep in route_entry_points
            ):
                diagnostics.append(Diagnostic(
                    level="error",
                    code="E136",
                    message=(
                        f'Reverse-proxy route host "{host}" names entrypoint(s) '
                        f'"{", ".join(ep for ep in route_entry_points if ep not in known_entry_points)}" '
                        f'not declared by proxy "{proxy_id}" (declared: '
                        f'{", ".join(sorted(known_entry_points))}); a router no '
                        "entrypoint serves would drop its traffic silently."
                    ),
                    path=f"{path_prefix}.entryPoints",
                ))
                continue
            router: dict[str, Any] = {
                "entryPoints": route_entry_points,
                "rule": f"Host(`{host}`)",
                "service": service_name,
            }
            if tls_enabled:
                router["tls"] = {}
            routers[router_name] = router
            # The backend URL is intentionally plain HTTP: Traefik
            # terminates TLS at the edge entrypoint (websecure, TLS 1.2+
            # per the module's policy) and proxies to target services over
            # the profile's private Docker network, which outside clients
            # cannot reach (docs/threat-model.md Boundary B; per-service
            # transport encryption is tracked as T10 / #205). S5332
            # therefore reports a false positive here.
            services[service_name] = {
                "loadBalancer": {"servers": [{"url": f"http://{backend_host}:{backend_port}"}]},  # NOSONAR
            }

        if routers:
            document: dict[str, Any] = {
                "http": {"routers": routers, "services": services},
            }
            if tls_certificates:
                document["tls"] = {
                    "certificates": [tls_certificates[a] for a in sorted(tls_certificates)]
                }
            documents[str(proxy_id)] = document

    return documents, diagnostics


def _write_reverse_proxy_route_documents(documents: dict[str, dict[str, Any]], compose_dir: Path) -> None:
    """
    Write one file-provider document per proxy next to the rendered
    compose file, and remove stale CDS-generated route files whose proxy
    no longer defines routes (or no longer exists in the plan). The file
    provider watches this directory, so a leftover file would keep
    serving deleted routes after a re-render.
    """
    written: set[str] = set()
    for proxy_id, document in documents.items():
        target = compose_dir / _REVERSE_PROXY_ROUTES_FILE.format(module=proxy_id)
        target.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write(target, yaml.safe_dump(document, sort_keys=True, default_flow_style=False))
        written.add(target.name)

    dynamic_dir = compose_dir / "traefik" / "dynamic"
    if dynamic_dir.is_dir():
        for stale in dynamic_dir.glob("cds-routes-*.yml"):
            if stale.name not in written:
                stale.unlink()


_UNRESOLVED_EXPRESSION_PATTERN = re.compile(
    r"\$\{((?:config|bindings|service)\.[^}]*|ifNonempty:[^}]*)\}"
)


def _check_unresolved_expressions(rendered_yaml: str) -> list[Diagnostic]:
    """
    Detect leftover ${config.*}/${bindings.*}/${service.*}/${ifNonempty:*} template expressions
    that survived rendering unresolved (e.g. an optional consumed contract that
    was never bound, but is unconditionally referenced by the module's
    template). These are always a rendering bug -- unlike ${CDS_*}/${VAR}
    placeholders, CDS's own template vocabulary is meant to be fully resolved
    by render time, so leaving one in place would silently ship a broken
    Compose file instead of failing loudly.
    """
    unresolved = sorted(set(_UNRESOLVED_EXPRESSION_PATTERN.findall(rendered_yaml)))
    return [
        Diagnostic(
            level="error",
            code="E071",
            message=(
                f'Unresolved template expression "${{{expr}}}" remains in the rendered '
                "output. This usually means an optional contract binding referenced by "
                "a module's template was never satisfied by the profile."
            ),
            path=f"rendered.{expr}",
        )
        for expr in unresolved
    ]


# ---------------------------------------------------------------------------
# Internal rendering helpers
# ---------------------------------------------------------------------------

def _is_production_plan(plan: dict[str, Any]) -> bool:
    """Whether the rendered profile is a production profile
    (metadata.environment == "production")."""
    return (plan.get("metadata") or {}).get("environment") == "production"


def _deep_merge(base: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(base)
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = deepcopy(value)
    return merged


def apply_production_gates(entries: dict[str, Any], is_production: bool) -> dict[str, Any]:
    """
    The production / learning dividing line for module templates.

    Two forms of the same tag, distinguished by type:

    - `production: true|false` on a service, volume, or volume-mount entry
      drops it when the profile environment does not match: `true` keeps
      the entry only for production profiles, `false` only for
      learning/development profiles.
    - `production: { ... }` (mapping form) marks the enclosed fields as
      production-only pieces of a service (a service is a single YAML key,
      so production and learning variants cannot be two services). In a
      production profile the mapping is merged over the service's base
      fields -- mappings such as `environment` merge recursively, anything
      else (e.g. `image`, `command`, `volumes`) replaces the base value; in
      other profiles it is ignored entirely.

    The base service therefore stays the easy-to-install learning variant
    (pullable stock image, no local build), and production profiles gain
    the production-grade pieces (hardened local builds, backup wiring).
    """
    kept: dict[str, Any] = {}
    for name, entry in entries.items():
        if not isinstance(entry, dict):
            kept[name] = entry
            continue
        marker = entry.get("production")
        if isinstance(marker, bool):
            if marker != is_production:
                continue
            entry = {k: v for k, v in entry.items() if k != "production"}
        elif isinstance(marker, dict):
            base = {k: v for k, v in entry.items() if k != "production"}
            entry = _deep_merge(base, marker) if is_production else base
        kept[name] = entry
    return kept


def _render_services(
    module: dict[str, Any],
    services: dict[str, Any],
    secrets: dict[str, str],
    profile_dir: Path | None,
    project_root: Path | None,
    compose_dir: Path,
    network_name: str | None = None,
    certificates: dict[str, dict[str, str]] | None = None,
    is_production: bool = False,
    external_volume_names: set[str] | None = None,
) -> tuple[dict[str, Any], list[Diagnostic]]:
    rendered: dict[str, Any] = {}
    diagnostics: list[Diagnostic] = []
    context = _build_context(module, secrets, certificates)

    for service_name, service_def in services.items():
        if not isinstance(service_def, dict):
            continue

        # Top-level enabledFrom guard
        if _is_disabled(service_def.get("enabledFrom"), context):
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
                if _is_disabled(cond, context):
                    service_copy.pop("healthcheck", None)
                else:
                    service_copy["healthcheck"] = _substitute_values(hc_copy, context)

        # Conditional individual bind-mount/volume entries: a service's
        # volumes list can mix mounts that only apply to one of several
        # mutually-exclusive consumed contracts (e.g. a module that
        # supports either a sql-database or a file-database target).
        # Entries carrying enabledFrom are dropped up front so an unused
        # mount never resolves an unwired binding into a broken/literal
        # path in the rendered compose file. Entries carrying the
        # production gate follow the same rules as service-level gates.
        volumes = service_copy.get("volumes")
        if isinstance(volumes, list):
            kept_volumes = []
            for volume_item in volumes:
                if isinstance(volume_item, dict) and "enabledFrom" in volume_item:
                    volume_item = deepcopy(volume_item)
                    item_enabled_from = volume_item.pop("enabledFrom", None)
                    if _is_disabled(item_enabled_from, context):
                        continue
                if isinstance(volume_item, dict) and isinstance(volume_item.get("production"), bool):
                    volume_item = deepcopy(volume_item)
                    item_production = volume_item.pop("production", None)
                    if item_production != is_production:
                        continue
                kept_volumes.append(volume_item)
            service_copy["volumes"] = kept_volumes

        diagnostics.extend(
            _check_unsafe_field_type_substitutions(service_copy, module.get("id"), service_name, context)
        )
        service_copy = _substitute_values(service_copy, context)
        service_copy = _rewrite_service_volumes(
            service_copy,
            module,
            profile_dir=profile_dir,
            project_root=project_root,
            compose_dir=compose_dir,
            external_volume_names=external_volume_names,
        )
        service_copy = _rewrite_depends_on(service_copy, module)
        service_copy = _rewrite_build_context(
            service_copy,
            module,
            profile_dir=profile_dir,
            project_root=project_root,
            compose_dir=compose_dir,
        )
        service_copy = _apply_image_source(service_copy, module)

        # Attach to the network if network_name is provided
        if network_name:
            service_copy["networks"] = [network_name]
        
        rendered[service_name] = service_copy

    return rendered, diagnostics


def _render_volumes(
    module: dict[str, Any],
    volumes: dict[str, Any],
    secrets: dict[str, str],
    certificates: dict[str, dict[str, str]] | None = None,
) -> dict[str, Any]:
    rendered: dict[str, Any] = {}
    context = _build_context(module, secrets, certificates)

    for volume_name, volume_def in volumes.items():
        if isinstance(volume_def, dict):
            if _is_disabled(volume_def.get("enabledFrom"), context):
                continue
            volume_copy = deepcopy(volume_def)
            volume_copy.pop("enabledFrom", None)
            rendered[volume_name] = _substitute_values(volume_copy, context)
        else:
            rendered[volume_name] = volume_def

    return rendered


def _merge_init_db_env(
    services: dict[str, Any],
    module: dict[str, Any],
    secrets: dict[str, str],
    certificates: dict[str, dict[str, str]] | None = None,
) -> None:
    """
    Merge initDbEnv into postgres service environment variables.

    If a module has config.initDbEnv, merge those environment variables
    into any service named "postgres" in the rendered services.
    """
    init_db_env = module.get("config", {}).get("initDbEnv")
    if not init_db_env or not isinstance(init_db_env, dict):
        return

    # Find postgres service and merge env vars
    postgres_service = services.get("postgres")
    if postgres_service and isinstance(postgres_service, dict):
        env = postgres_service.setdefault("environment", {})
        if not isinstance(env, dict):
            return
        
        # Substitute values in init_db_env and merge
        context = _build_context(module, secrets, certificates)
        for key, value in init_db_env.items():
            if isinstance(value, str):
                # Resolve references like "secrets.xyz"
                env[key] = _resolve_expr(value, context)
            else:
                env[key] = value


def _build_context(
    module: dict[str, Any],
    secrets: dict[str, str] | None = None,
    certificates: dict[str, dict[str, str]] | None = None,
) -> dict[str, Any]:
    secrets = secrets or {}
    certificates = certificates or {}
    bindings: dict[str, Any] = {}

    for consume_name, consume_value in module.get("consumes", {}).items():
        contract = consume_value.get("contract", {})
        if isinstance(contract, dict):
            spec = contract.get("spec", {})
            bindings[consume_name] = spec if isinstance(spec, dict) else {}

    implementation = module.get("implementation") or {}
    compose_impl = implementation.get("compose") if isinstance(implementation, dict) else None
    services = compose_impl.get("services") if isinstance(compose_impl, dict) else None
    module_id = str(module.get("id"))
    module_name = module_name_from_source(module.get("source"))

    return {
        "config": module.get("config", {}),
        "bindings": bindings,
        "service": {
            "host": module_id,
            "dns": module_dns_host(module_id, services, module_name),
            "services": module_service_dns_names(module_id, services),
        },
        "secrets": secrets,
        "certificates": certificates,
    }


def _substitute_values(obj: Any, context: dict[str, Any], _depth: int = 0) -> Any:
    """Recursively substitute interpolation expressions in obj."""
    if _depth > MAX_NESTING_DEPTH:
        raise MaxNestingDepthExceeded(
            f"Service/volume template nesting exceeds the maximum supported depth ({MAX_NESTING_DEPTH})."
        )
    if isinstance(obj, dict):
        return {k: _substitute_values(v, context, _depth + 1) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_substitute_values(v, context, _depth + 1) for v in obj]
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


# Compose service fields where a module template's pure `${config.*}` /
# `${bindings.*}` substitution resolving to a non-scalar type (dict/list) is
# always attacker-relevant: they control what docker-compose executes, sets
# as environment, or bind-mounts on the host, regardless of the module
# author's intent. See GHSA-gmc4-jw3j-mqcf.
_UNSAFE_TYPED_SUBSTITUTION_FIELDS = frozenset({
    "command",
    "entrypoint",
    "environment",
    "volumes",
    "cap_add",
    "cap_drop",
    "devices",
    "security_opt",
    "privileged",
    "network_mode",
    "pid",
    "ports",
    "labels",
    "tmpfs",
    "dns",
    "extra_hosts",
    "logging",
    "expose",
    "depends_on",
    "build",
})

# Fields where the field itself is already scalar (bool/str), so a dict/list
# resolution is not the only attacker-relevant outcome: a profile-controlled
# scalar value can *also* grant host-level escalation without ever changing
# the field's expected shape (e.g. `privileged: "${config.priv}"` resolving
# to `True`, or `network_mode`/`pid` resolving to `"host"`). These are
# checked against specific dangerous scalar values, in addition to the
# dict/list check above.
_UNSAFE_SCALAR_SUBSTITUTION_VALUES: dict[str, frozenset[Any]] = {
    "privileged": frozenset({True}),
    "network_mode": frozenset({"host"}),
    "pid": frozenset({"host"}),
}


def _check_unsafe_field_type_substitutions(
    service_def: dict[str, Any],
    module_id: str | None,
    service_name: str,
    context: dict[str, Any],
) -> list[Diagnostic]:
    """
    Detect a module template that uses a *pure* `${config.*}`/`${bindings.*}`
    substitution (i.e. the entire field value is a single expression, not
    string-concatenated) in a compose field position where docker-compose
    expects a fixed shape (list/dict/scalar). Pure substitution preserves the
    resolved value's original Python type, so a profile can inject arbitrary
    lists/dicts (e.g. a `command` list, `environment` map, or `volumes` bind
    mounts) through module config, bypassing the field's intended shape.

    This does not affect mixed substitution (e.g. "db://${bindings.db.host}"),
    which is always string-concatenated back into a str.

    Beyond the dict/list check, a small set of fields (`privileged`,
    `network_mode`, `pid`) are already scalar by design, so a resolved value
    is also flagged if it matches a known dangerous scalar (e.g.
    `privileged` resolving to `True`, or `network_mode`/`pid` resolving to
    `"host"`), since a profile can drive host-level escalation through
    module config without ever changing the field's shape.
    """
    diagnostics: list[Diagnostic] = []
    pure_pattern = re.compile(r"^\$\{([^}]+)\}$")

    for field in _UNSAFE_TYPED_SUBSTITUTION_FIELDS:
        raw_value = service_def.get(field)
        if not isinstance(raw_value, str):
            continue
        match = pure_pattern.match(raw_value)
        if not match:
            continue

        resolved = _resolve_expr(match.group(1), context)
        unsafe_scalars = _UNSAFE_SCALAR_SUBSTITUTION_VALUES.get(field, frozenset())
        if isinstance(resolved, (dict, list)):
            diagnostics.append(Diagnostic(
                level="error",
                code="E072",
                message=(
                    f'Module "{module_id}" service "{service_name}" field "{field}" resolves '
                    f'"${{{match.group(1)}}}" to a {type(resolved).__name__}, but pure '
                    "substitution in this field position is not permitted: a profile-supplied "
                    "config value would be spliced verbatim into the rendered Compose file, "
                    "letting an untrusted profile inject arbitrary command/environment/volumes "
                    "entries. Restructure the module template to interpolate scalar leaf values "
                    "individually instead of substituting the whole field."
                ),
                path=f"module:{module_id}.implementation.compose.services.{service_name}.{field}",
            ))
        elif resolved in unsafe_scalars:
            diagnostics.append(Diagnostic(
                level="error",
                code="E072",
                message=(
                    f'Module "{module_id}" service "{service_name}" field "{field}" resolves '
                    f'"${{{match.group(1)}}}" to {resolved!r}, a host-escalating value for this '
                    "field. A profile-supplied config value would grant host-level privileges "
                    "(privileged mode, or host network/PID namespace sharing) through what "
                    "should be module-controlled configuration. Do not let profile-controlled "
                    "config drive this field."
                ),
                path=f"module:{module_id}.implementation.compose.services.{service_name}.{field}",
            ))

    return diagnostics


def _is_disabled(expr: str | None, context: dict[str, Any]) -> bool:
    """
    Shared enabledFrom/conditionallyEnabledFrom/per-item enabledFrom guard:
    true only when expr is set and resolves to exactly False (or, for an
    equality gate, to a non-match). An unset expr, or one that resolves to
    None/True/any other value, means "enabled" -- callers only skip
    rendering the guarded service/healthcheck/volume when this returns True.
    """
    return bool(expr) and _resolve_expr(expr, context) is False


def _resolve_expr(expr: str, context: dict[str, Any]) -> Any:
    """
    Resolve a dot-notation expression against context.

    Secrets are emitted as Docker Compose runtime placeholders (${VAR}).
    Raw secret values are never returned.

    Returns None if the path is not found.
    """
    if "==" in expr:
        # Equality gate, e.g. "config.warehouseType==duckdb" -- used by
        # enabledFrom guards that need to pick one of several mutually
        # exclusive options (unlike enabledFrom's plain boolean-flag use,
        # which only checks a path resolves to literal False). Dotted paths
        # never contain "==", so this check is unambiguous. Both sides are
        # stripped so incidental whitespace around "==" (e.g. a template
        # author writing "config.warehouseType == duckdb") compares the
        # intended values instead of silently failing to match and
        # dropping the guarded entry with no error. Only plain string
        # equality is supported -- the expected side is always compared as
        # a literal string, never coerced to bool/int/etc, so e.g.
        # "config.threads==4" never matches an integer 4.
        path, _, expected = expr.partition("==")
        return _resolve_expr(path.strip(), context) == expected.strip()

    if expr.startswith("ifNonempty:"):
        args = expr[len("ifNonempty:"):].split(",", 2)
        if len(args) != 3:
            return None
        path, prefix, suffix = args
        value = _resolve_expr(path, context)
        if value is None or (isinstance(value, str) and not value.strip()):
            return ""
        return f"{prefix}{value}{suffix}"

    if expr.startswith("secrets."):
        alias = expr.split(".", 1)[1]
        secret_map = context.get("secrets", {})
        env_name = secret_map.get(alias, alias)
        return f"${{{env_name}}}"

    # Direct certificates.<alias>.<cert|key> reference (issue #578): emits
    # the runtime env placeholder for that half of the pair; never material.
    # Malformed refs and unknown aliases resolve to None (stay visible as
    # unresolved expressions) instead of falling through to the generic
    # dotted-path walk, which would expose the raw env-name map.
    if expr.startswith("certificates."):
        return _certificate_placeholder(expr, context)

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

    # Walked config value that is itself a certificates.<alias>.<part> ref
    # (e.g. ${config.tls.certRef} where the config field holds the ref string).
    if isinstance(current, str) and current.startswith("certificates."):
        placeholder = _certificate_placeholder(current, context)
        if placeholder is not None:
            return placeholder

    return current


def _certificate_placeholder(ref: str, context: dict[str, Any]) -> str | None:
    """
    Map a `certificates.<alias>.<cert|key>` reference to its `${ENV_NAME}`
    runtime placeholder. Returns None for malformed refs or unknown aliases so
    they stay visible as unresolved expressions instead of inventing a name;
    validation (E129) has already flagged them by the time rendering runs.
    """
    parts = ref.split(".")
    if len(parts) != 3 or parts[2] not in ("cert", "key"):
        return None
    alias, part = parts[1], parts[2]
    entry = context.get("certificates", {}).get(alias)
    if not isinstance(entry, dict):
        return None
    env_name = entry.get(part)
    if not isinstance(env_name, str) or not env_name:
        return None
    return f"${{{env_name}}}"


def _rewrite_service_volumes(
    service_def: dict[str, Any],
    module: dict[str, Any],
    profile_dir: Path | None,
    project_root: Path | None,
    compose_dir: Path,
    external_volume_names: set[str] | None = None,
) -> dict[str, Any]:
    external_volume_names = external_volume_names or set()
    volumes = service_def.get("volumes")
    if not isinstance(volumes, list):
        return service_def

    rewritten: list[Any] = []
    for item in volumes:
        if isinstance(item, str):
            parts = item.split(":", 1)
            if len(parts) == 2 and _is_named_volume(parts[0]):
                if parts[0] in external_volume_names:
                    # Cross-module reference (issue #668): the source is
                    # already a fully rendered "<module-id>-<volume>" name
                    # owned by another module; do not re-namespace it.
                    rewritten.append(item)
                    continue
                item = f"{module['id']}-{parts[0]}:{parts[1]}"
            elif len(parts) >= 2:
                source = parts[0]
                rewritten_source = _rewrite_local_path(
                    source,
                    module=module,
                    profile_dir=profile_dir,
                    project_root=project_root,
                    compose_dir=compose_dir,
                )
                # Compose's short volume syntax only treats a source as a
                # bind mount when it starts with ".", "/", or "~"; a bare
                # relative path like "traefik/dynamic" (what relative_to()
                # produces) is otherwise parsed as a named volume reference,
                # which fails for any bind source that isn't already a
                # single path segment. Re-add the "./" prefix so a rewritten
                # multi-segment relative path stays an unambiguous bind.
                if (
                    rewritten_source != source
                    and not rewritten_source.startswith((".", "/", "~"))
                ):
                    rewritten_source = f"./{rewritten_source}"
                if rewritten_source != source:
                    item = f"{rewritten_source}:{parts[1]}"
        elif isinstance(item, dict):
            item_copy = deepcopy(item)
            if item_copy.get("type") == "bind" and isinstance(item_copy.get("source"), str):
                item_copy["source"] = _rewrite_local_path(
                    item_copy["source"],
                    module=module,
                    profile_dir=profile_dir,
                    project_root=project_root,
                    compose_dir=compose_dir,
                )
            elif item_copy.get("type") == "volume" and isinstance(item_copy.get("source"), str):
                source = item_copy["source"]
                if source in external_volume_names:
                    # Cross-module reference (issue #668): the source is
                    # already a fully rendered "<module-id>-<volume>" name
                    # owned by another module; do not re-namespace it.
                    pass
                elif _is_named_volume(source):
                    item_copy["source"] = f"{module['id']}-{source}"
            item = item_copy
        rewritten.append(item)

    return {**service_def, "volumes": rewritten}


def _rewrite_local_path(
    path_value: str,
    module: dict[str, Any],
    profile_dir: Path | None,
    project_root: Path | None,
    compose_dir: Path,
) -> str:
    if Path(path_value).is_absolute() or _looks_remote_context(path_value) or "${" in path_value:
        return path_value

    candidates: list[Path] = []
    for base in _local_path_bases(module, profile_dir, project_root, compose_dir):
        candidate = (base / path_value).resolve()
        if candidate not in candidates:
            candidates.append(candidate)

    if not candidates:
        return path_value

    chosen = _choose_best_local_path_candidate(candidates)
    if project_root is not None and not _path_is_within_root(compose_dir, project_root):
        try:
            return Path(chosen).relative_to(project_root).as_posix()
        except ValueError:
            pass
    try:
        rel = Path(chosen).relative_to(compose_dir)
    except ValueError:
        # relative_to() only works for descendants; relpath preserves ../ segments.
        try:
            rel = Path(os.path.relpath(chosen, compose_dir))
        except ValueError:
            # On Windows, relpath raises when chosen and compose_dir are on
            # different drives (e.g. C:\ vs D:\), no relative path can
            # express that. Fall back to the absolute path, same as the
            # is_absolute() short-circuit above.
            return Path(chosen).as_posix()
    return rel.as_posix()


def _local_path_bases(
    module: dict[str, Any],
    profile_dir: Path | None,
    project_root: Path | None,
    compose_dir: Path,
) -> list[Path]:
    bases: list[Path] = []

    if project_root is not None:
        bases.append(project_root)

    if profile_dir is not None:
        bases.append(profile_dir)

    module_dir = _resolve_module_dir(module, profile_dir)
    if module_dir is not None:
        bases.append(module_dir)

    bases.append(compose_dir)

    if project_root is not None:
        bases.append((project_root / "build").resolve())

    return bases


def _choose_best_local_path_candidate(candidates: list[Path]) -> Path:
    for candidate in candidates:
        if candidate.exists():
            return candidate

    return candidates[0]


def _rewrite_depends_on(
    service_def: dict[str, Any],
    module: dict[str, Any],
) -> dict[str, Any]:
    depends_on = service_def.get("depends_on")
    if depends_on is None:
        return service_def

    if isinstance(depends_on, list):
        rewritten = {
            _compose_service_name(module["id"], dep): {"condition": "service_started"}
            for dep in depends_on
        }
    elif isinstance(depends_on, dict):
        rewritten = {
            _compose_service_name(module["id"], dep): val
            for dep, val in depends_on.items()
        }
    else:
        return service_def

    return {**service_def, "depends_on": rewritten}


_REGISTRY_IMAGE_TEMPLATES = {
    "dockerhub": "docker.io/ronaldsoeverein/{module_id}:{tag}",
    "ghcr": "ghcr.io/ronaldhensbergen/cds-{module_id}:{tag}",
}


def _apply_image_source(
    service_def: dict[str, Any],
    module: dict[str, Any],
) -> dict[str, Any]:
    """
    Swap a rendered service's build: block for a registry image: reference
    when the module's config.image.source is "registry".

    When config.image.source is "registry", any "build" key is dropped and
    "image" is rewritten to a reference on the registry named by
    config.image.registry ("dockerhub", the default, or "ghcr"):
    "docker.io/ronaldsoeverein/<module id>:<config.image.tag>" or
    "ghcr.io/ronaldhensbergen/cds-<module id>:<config.image.tag>"
    respectively -- the same two registries .github/workflows/publish-images.yml
    publishes to (see docs/image-signing.md for the tag scheme, which
    differs slightly between the two: GHCR has no version-derived tag,
    only sha-<sha>/latest, optionally variant/alpine-prefixed). Emitting
    a registry reference without a tag would silently produce an untagged
    image, so the service is left unchanged (with the build: block intact)
    if no tag is configured; validator enforcement of a required tag is
    tracked separately (issue #533). Likewise, an unrecognized
    config.image.registry value (unreachable via a schema-validated
    profile, since the schema enums this field, but this function also
    renders hand-built Plans that bypass that validation) leaves the
    service unchanged rather than silently defaulting to Docker Hub.
    """
    image_config = module.get("config", {}).get("image", {})
    if not isinstance(image_config, dict) or image_config.get("source") != "registry":
        return service_def

    tag = image_config.get("tag")
    if not tag:
        return service_def

    registry = image_config.get("registry", "dockerhub")
    template = _REGISTRY_IMAGE_TEMPLATES.get(registry)
    if template is None:
        return service_def

    service_copy = dict(service_def)
    service_copy.pop("build", None)
    service_copy["image"] = template.format(module_id=module.get("id"), tag=tag)
    return service_copy


def _rewrite_build_context(
    service_def: dict[str, Any],
    module: dict[str, Any],
    profile_dir: Path | None,
    project_root: Path | None,
    compose_dir: Path,
) -> dict[str, Any]:
    build = service_def.get("build")
    if build is None:
        return service_def

    if isinstance(build, str):
        rewritten = _resolve_context_path(
            context=build,
            dockerfile=None,
            module=module,
            profile_dir=profile_dir,
            project_root=project_root,
            compose_dir=compose_dir,
        )
        return {**service_def, "build": rewritten}

    if isinstance(build, dict):
        context = build.get("context")
        if not isinstance(context, str):
            return service_def

        dockerfile = build.get("dockerfile")
        rewritten = _resolve_context_path(
            context=context,
            dockerfile=dockerfile if isinstance(dockerfile, str) else None,
            module=module,
            profile_dir=profile_dir,
            project_root=project_root,
            compose_dir=compose_dir,
        )
        return {**service_def, "build": {**build, "context": rewritten}}

    return service_def


def _resolve_context_path(
    context: str,
    dockerfile: str | None,
    module: dict[str, Any],
    profile_dir: Path | None,
    project_root: Path | None,
    compose_dir: Path,
) -> str:
    # Keep absolute paths and remote contexts unchanged.
    if Path(context).is_absolute() or _looks_remote_context(context) or "${" in context:
        return context

    candidates: list[Path] = []
    for base in _context_bases(module, profile_dir, project_root, compose_dir):
        candidate = (base / context).resolve()
        if candidate not in candidates:
            candidates.append(candidate)

    if not candidates:
        return context

    chosen = _choose_best_context_candidate(candidates, dockerfile)
    if project_root is not None and not _path_is_within_root(compose_dir, project_root):
        try:
            return Path(chosen).relative_to(project_root).as_posix()
        except ValueError:
            pass
    try:
        rel = Path(chosen).relative_to(compose_dir)
    except ValueError:
        # relative_to() only works for descendants; relpath preserves ../ segments.
        try:
            rel = Path(os.path.relpath(chosen, compose_dir))
        except ValueError:
            # On Windows, relpath raises when chosen and compose_dir are on
            # different drives (e.g. C:\ vs D:\), no relative path can
            # express that. Fall back to the absolute path, same as the
            # is_absolute() short-circuit above.
            return Path(chosen).as_posix()
    return Path(rel).as_posix()


def _context_bases(
    module: dict[str, Any],
    profile_dir: Path | None,
    project_root: Path | None,
    compose_dir: Path,
) -> list[Path]:
    bases: list[Path] = []

    if project_root is not None:
        bases.append(project_root)

    bases.append(compose_dir)

    module_dir = _resolve_module_dir(module, profile_dir)
    if module_dir is not None:
        bases.append(module_dir)

    if project_root is not None:
        # Legacy compose path in this repo used to be project_root/build.
        bases.append((project_root / "build").resolve())

    return bases


def _choose_best_context_candidate(candidates: list[Path], dockerfile: str | None) -> Path:
    if dockerfile:
        for candidate in candidates:
            if candidate.exists() and (candidate / dockerfile).exists():
                return candidate

    for candidate in candidates:
        if candidate.exists():
            return candidate

    return candidates[0]


def _looks_remote_context(value: str) -> bool:
    return "://" in value or value.startswith("git@")


def _path_is_within_root(path_value: Path, root: Path) -> bool:
    try:
        path_value.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def _resolve_profile_dir(plan: dict[str, Any]) -> Path | None:
    source_profile = plan.get("sourceProfile")
    if not isinstance(source_profile, str):
        return None
    return Path(source_profile).resolve().parent


def _resolve_project_root(profile_dir: Path | None) -> Path | None:
    if profile_dir is None:
        return None

    for directory in [profile_dir, *profile_dir.parents]:
        if (directory / "pyproject.toml").exists() or (directory / ".git").exists():
            return directory

    return None


def _resolve_module_dir(module: dict[str, Any], profile_dir: Path | None) -> Path | None:
    source = module.get("source")
    if not isinstance(source, str):
        return None

    module_root = os.getenv("CDS_MODULE_PATH")
    module_root_path = Path(module_root) if module_root else None

    return resolve_module_dir(source, profile_dir, module_root=module_root_path)


def _is_named_volume(value: str) -> bool:
    if value.startswith((".", "/", "~")):
        return False
    return "/" not in value and "\\" not in value


def _add_cross_module_dependencies(
    compose: dict[str, Any],
    plan: dict[str, Any],
    module_service_names: dict[str, list[str]],
) -> None:
    """
    Add explicit cross-module dependencies to docker-compose services.
    
    For each module that has dependsOn declarations, add depends_on entries
    to all its services, referencing all services from the dependent modules.
    """
    modules = plan.get("modules", [])
    services = compose.get("services", {})
    
    # For each module, add depends_on for its dependencies
    for module in modules:
        module_id = module.get("id")
        depends_on = module.get("dependsOn", [])
        
        if not depends_on or not module_id:
            continue
        
        # Find all services belonging to this module
        current_module_service_names = module_service_names.get(module_id, [])
        
        # For each service in this module, add depends_on entries
        for service_name in current_module_service_names:
            service_def = services.get(service_name)
            if not service_def:
                continue
            
            # Collect all services from dependent modules
            for dep_module_id in depends_on:
                dep_services = module_service_names.get(dep_module_id, [])

                for dep_service_name in dep_services:
                    # A cross-module dependency is awaited via the
                    # `service_healthy` condition, so only services that can
                    # actually become healthy qualify: one-shot containers
                    # (healthcheck disabled or absent, e.g. init or restore
                    # services) and profile-gated services that may never
                    # start would leave consumers waiting forever. Skipped
                    # services keep their module-level ordering guarantees
                    # through the module's own depends_on graph instead.
                    dep_def = services.get(dep_service_name)
                    if not isinstance(dep_def, dict):
                        continue
                    dep_healthcheck = dep_def.get("healthcheck")
                    if not isinstance(dep_healthcheck, dict) or dep_healthcheck.get("disable") is True or "test" not in dep_healthcheck:
                        continue
                    if dep_def.get("profiles"):
                        continue

                    # Initialize depends_on if not present
                    if "depends_on" not in service_def:
                        service_def["depends_on"] = {}
                    
                    # Add the dependency with a started condition
                    if isinstance(service_def["depends_on"], dict):
                        service_def["depends_on"][dep_service_name] = {
                            "condition": "service_healthy"
                        }
                    elif isinstance(service_def["depends_on"], list):
                        if dep_service_name not in service_def["depends_on"]:
                            service_def["depends_on"].append(dep_service_name)
