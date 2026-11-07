"""Target-specific security checks for Kubernetes workload declarations."""

from __future__ import annotations

import ipaddress
from typing import Any


def scan_k8s_security(plan: dict[str, Any]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for module in plan.get("modules", []):
        module_id = module.get("id", "<unknown>")
        implementation = module.get("implementation", {})
        compose_services = (implementation.get("compose") or {}).get("services", {})
        kubernetes = implementation.get("kubernetes") or {}
        for workload_name, workload in (kubernetes.get("workloads") or {}).items():
            path = f"module:{module_id}.implementation.kubernetes.workloads.{workload_name}"
            pod_context = workload.get("podSecurityContext") or {}
            resources = workload.get("resources") or {}
            container_names = [
                *(workload.get("initContainers") or []),
                *(workload.get("containers") or []),
            ]
            overrides = workload.get("containerOverrides") or {}
            for container_name in container_names:
                service = compose_services.get(container_name) or {}
                override_context = (overrides.get(container_name) or {}).get(
                    "securityContext"
                ) or {}
                if not _runs_as_non_root(pod_context, override_context):
                    findings.append(
                        _finding(
                            "CDS-K8S-001",
                            "high",
                            f'Container "{module_id}/{workload_name}/{container_name}" is not constrained to a non-root user.',
                            f"{path}.containers.{container_name}.securityContext",
                            module_id,
                            [
                                "Set an effective runAsNonRoot: true and a non-zero "
                                "runAsUser in podSecurityContext or the container override."
                            ],
                        )
                    )
                if not _read_only_root(service, override_context):
                    findings.append(
                        _finding(
                            "CDS-K8S-002",
                            "high",
                            f'Container "{module_id}/{workload_name}/{container_name}" has a writable root filesystem.',
                            f"{path}.containers.{container_name}.securityContext",
                            module_id,
                            [
                                "Set compose read_only: true or Kubernetes readOnlyRootFilesystem: true."
                            ],
                        )
                    )
                if not _drops_all_capabilities(service, override_context):
                    findings.append(
                        _finding(
                            "CDS-K8S-003",
                            "high",
                            f'Container "{module_id}/{workload_name}/{container_name}" does not drop all Linux capabilities.',
                            f"{path}.containers.{container_name}.securityContext",
                            module_id,
                            [
                                "Set compose cap_drop: [ALL] or Kubernetes capabilities.drop: [ALL]."
                            ],
                        )
                    )
                if not _disallows_privilege_escalation(service, override_context):
                    findings.append(
                        _finding(
                            "CDS-K8S-004",
                            "high",
                            f'Container "{module_id}/{workload_name}/{container_name}" allows privilege escalation.',
                            f"{path}.containers.{container_name}.securityContext",
                            module_id,
                            [
                                "Set compose no-new-privileges:true or Kubernetes "
                                "allowPrivilegeEscalation: false."
                            ],
                        )
                    )
                declared = resources.get(container_name) or {}
                if not declared.get("requests") or not declared.get("limits"):
                    findings.append(
                        _finding(
                            "CDS-K8S-005",
                            "medium",
                            f'Container "{module_id}/{workload_name}/{container_name}" lacks resource requests or limits.',
                            f"{path}.resources.{container_name}",
                            module_id,
                            ["Declare both requests and limits for the container."],
                            category="configuration-management",
                        )
                    )
            _scan_declared_external_egress(
                findings,
                module_id,
                workload.get("egress"),
                f"{path}.egress",
            )
            service_type = (workload.get("service") or {}).get("type")
            if service_type in ("NodePort", "LoadBalancer"):
                findings.append(
                    _finding(
                        "CDS-K8S-007",
                        "low",
                        (
                            f'Workload "{module_id}/{workload_name}" exposes a '
                            f"{service_type} service; the generated NetworkPolicy must "
                            "allow ingress from all sources on its ports."
                        ),
                        f"{path}.service.type",
                        module_id,
                        [
                            "Confirm the workload is meant to be reachable cluster-wide "
                            "or externally; prefer ClusterIP with an explicit ingress "
                            "controller plus the cross-namespace ingress rules that "
                            "controller needs (implementation.kubernetes.networkPolicy."
                            "ingress), since the generated NetworkPolicy denies it by "
                            "default."
                        ],
                    )
                )
        service_account = kubernetes.get("serviceAccount") or {}
        declared_rbac = (
            service_account.get("rbac") if isinstance(service_account, dict) else None
        )
        for index, rule in enumerate(declared_rbac or []):
            if not isinstance(rule, dict):
                continue
            wildcard_fields = [
                field
                for field in ("apiGroups", "resources", "verbs")
                if isinstance(rule.get(field), list) and "*" in rule[field]
            ]
            if wildcard_fields:
                findings.append(
                    _finding(
                        "CDS-K8S-006",
                        "medium",
                        (
                            f'RBAC rule {index} of module "{module_id}" grants wildcard '
                            f"{' and '.join(wildcard_fields)}; least-privilege rules name "
                            "every API group, resource, and verb explicitly."
                        ),
                        f"module:{module_id}.implementation.kubernetes.serviceAccount."
                        f"rbac.{index}",
                        module_id,
                        [
                            "Replace the wildcard with the explicit API groups, "
                            "resources, and verbs the workload needs."
                        ],
                        category="configuration-management",
                    )
                )
        network_policy = kubernetes.get("networkPolicy") or {}
        if isinstance(network_policy, dict):
            for index, rule in enumerate(network_policy.get("egress") or []):
                if not isinstance(rule, dict):
                    continue
                # An egress rule without a "to" restriction (or with an
                # ipBlock peer) opens external egress that the default-deny
                # baseline would otherwise block. Selector-based peers stay
                # cluster-internal and are not flagged.
                destinations = rule.get("to")
                opens_external = True
                if isinstance(destinations, list):
                    opens_external = any(
                        isinstance(peer, dict) and "ipBlock" in peer
                        for peer in destinations
                    )
                if opens_external:
                    findings.append(
                        _finding(
                            "CDS-K8S-008",
                            "low",
                            (
                                f'Custom egress rule {index} of module "{module_id}" opens '
                                "external egress (no destination restriction or an "
                                "ipBlock peer), which the default-deny NetworkPolicy "
                                "baseline would otherwise block."
                            ),
                            f"module:{module_id}.implementation.kubernetes.networkPolicy."
                            f"egress.{index}",
                            module_id,
                            [
                                "Confirm the external destination is intended; scope the "
                                "rule to the minimum: the smallest ipBlock cidr (with "
                                "except where possible) or a selector-based peer, and a "
                                "TCP port list."
                            ],
                            category="configuration-management",
                        )
                    )
            for index, rule in enumerate(network_policy.get("ingress") or []):
                if not isinstance(rule, dict):
                    continue
                # An ingress rule without a "from" restriction (or with an
                # ipBlock peer) admits external sources on its ports --
                # equal or worse exposure than the NodePort/LoadBalancer
                # service CDS-K8S-007 flags. Selector-based peers stay
                # cluster-internal and are not flagged.
                sources = rule.get("from")
                if isinstance(sources, list) and sources:
                    opens_external = any(
                        isinstance(peer, dict) and "ipBlock" in peer for peer in sources
                    )
                else:
                    opens_external = True
                if opens_external:
                    findings.append(
                        _finding(
                            "CDS-K8S-010",
                            "low",
                            (
                                f'Custom ingress rule {index} of module "{module_id}" '
                                "opens external ingress (no from restriction or an "
                                "ipBlock peer), which the default-deny "
                                "NetworkPolicy baseline would otherwise block."
                            ),
                            f"module:{module_id}.implementation.kubernetes.networkPolicy."
                            f"ingress.{index}",
                            module_id,
                            [
                                "Confirm the source is intended; scope the rule to the "
                                "minimum: the smallest ipBlock cidr (with except where "
                                "possible) or a selector-based peer, and a TCP port list."
                            ],
                            category="configuration-management",
                        )
                    )
        _scan_declared_external_egress(
            findings,
            module_id,
            kubernetes.get("egress"),
            f"module:{module_id}.implementation.kubernetes.egress",
        )
    return findings


def _scan_declared_external_egress(
    findings: list[dict[str, Any]],
    module_id: str,
    declared: Any,
    path: str,
) -> None:
    """
    Record every declared external egress entry (issue #1023) as a
    CDS-K8S-009 finding whose severity scales with breadth. A declaration
    opens a hole in the default-deny egress baseline by definition, so the
    scan is the audit record: 0.0.0.0/0 (or ::/0) on all ports disables the
    baseline for the affected pods (high), an unscoped destination or a
    missing port list is medium, and a destination+port-scoped entry is low,
    mirroring how CDS-K8S-008 records raw ipBlock passthrough rules.
    """
    if not isinstance(declared, list):
        return
    for index, entry in enumerate(declared):
        if not isinstance(entry, dict):
            # Shape errors are the renderer's to fail closed on (E153).
            continue
        cidr_text = entry.get("cidr")
        if cidr_text is None and entry.get("host") is not None:
            host = str(entry["host"])
            cidr_text = f"{host}{'/128' if ':' in host else '/32'}"
        try:
            network = ipaddress.ip_network(str(cidr_text), strict=False)
        except ValueError:
            # Unparseable (e.g. an unresolved ${config.*} expression or a DNS
            # name): the renderer fails closed with E153 on the resolved value.
            continue
        all_destinations = network.prefixlen == 0
        ports = entry.get("ports")
        if ports is None and entry.get("port") is not None:
            ports = [entry["port"]]
        # The scan sees the plan before ${config.*} substitution, so a ports
        # value supplied through a config expression arrives as a string:
        # ports are declared (breadth unknown until render), so it must not
        # be misread as a missing port list.
        port_scoped = (isinstance(ports, list) and bool(ports)) or (
            isinstance(ports, str) and bool(ports.strip())
        )
        if all_destinations and not port_scoped:
            severity = "high"
            message = (
                f'Declared external egress {index} of module "{module_id}" opens '
                f"egress to every address ({network}) on all ports, which disables "
                "the default-deny egress baseline for the affected pods."
            )
            recommendation = [
                "Scope the declaration to the smallest cidr (with except where "
                "possible) and an explicit port list, or drop it: external egress "
                "stays denied unless declared (issue #1023).",
            ]
        elif all_destinations:
            severity = "medium"
            message = (
                f'Declared external egress {index} of module "{module_id}" opens '
                f"egress to every address ({network}) on the declared ports only; "
                "the destination itself is unscoped."
            )
            recommendation = [
                "Replace the 0.0.0.0/0 (or ::/0) cidr with the smallest range that "
                "covers the intended destination, with except entries where possible.",
            ]
        elif not port_scoped:
            severity = "medium"
            message = (
                f'Declared external egress {index} of module "{module_id}" opens '
                f"egress to {network} on all ports; the declaration carries no port "
                "list, so the rule cannot be port-scoped."
            )
            recommendation = [
                "Declare port/ports (and protocol) so the rule allows exactly the "
                "ports the destination needs.",
            ]
        else:
            severity = "low"
            message = (
                f'Declared external egress {index} of module "{module_id}" opens '
                f"egress to {network} on {len(ports)} declared port(s); recorded as "
                "the audit trail of an intentional external opening."
            )
            recommendation = [
                "Keep the cidr as small as possible (with except where applicable) "
                "and the port list minimal; remove the entry when the destination is "
                "no longer needed.",
            ]
        findings.append(
            _finding(
                "CDS-K8S-009",
                severity,
                message,
                f"{path}.{index}",
                module_id,
                recommendation,
                category="configuration-management",
            )
        )


def _runs_as_non_root(pod_context: dict[str, Any], override: dict[str, Any]) -> bool:
    run_as_non_root = override.get("runAsNonRoot", pod_context.get("runAsNonRoot"))
    run_as_user = override.get("runAsUser", pod_context.get("runAsUser"))
    return run_as_non_root is True and run_as_user != 0


def _read_only_root(service: dict[str, Any], override: dict[str, Any]) -> bool:
    return (
        override.get("readOnlyRootFilesystem", service.get("read_only", False)) is True
    )


def _drops_all_capabilities(service: dict[str, Any], override: dict[str, Any]) -> bool:
    dropped = (override.get("capabilities") or {}).get(
        "drop", service.get("cap_drop", [])
    )
    return "ALL" in (dropped or [])


def _disallows_privilege_escalation(
    service: dict[str, Any], override: dict[str, Any]
) -> bool:
    if override.get("allowPrivilegeEscalation") is False:
        return True
    options = {
        str(option).replace(" ", "") for option in service.get("security_opt", []) or []
    }
    return bool(options & {"no-new-privileges:true", "no-new-privileges=true"})


def _finding(
    rule_id: str,
    severity: str,
    message: str,
    path: str,
    module: str,
    recommendation: list[str],
    *,
    category: str = "system-hardening",
) -> dict[str, Any]:
    return {
        "rule_id": rule_id,
        "severity": severity,
        # These findings aren't declared in cli/resources/rule-set.json (the
        # declarative rule-set engine has no scope for the plan's
        # Kubernetes-specific implementation), so their compliance category
        # can't be looked up the way cli.security.run_security_validation()
        # does for rule-set findings. CDS-K8S-001-004 and CDS-K8S-007 (the
        # NodePort/LoadBalancer ingress exposure note) are container/pod
        # runtime-hardening posture checks, so they default to the matching
        # cli.security_common.COMPLIANCE_CATEGORIES value; CDS-K8S-005
        # (resource requests/limits), CDS-K8S-006 (wildcard RBAC rules),
        # CDS-K8S-008 (unrestricted custom egress), CDS-K8S-009 (declared
        # external egress breadth), and CDS-K8S-010 (source-unrestricted
        # custom ingress) override this below since they're capacity-,
        # privilege-, and network-governance concerns rather than
        # runtime-hardening posture.
        "category": category,
        "message": message,
        "path": path,
        "module": module,
        "value": None,
        "recommendation": recommendation,
    }
