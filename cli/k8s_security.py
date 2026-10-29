"""Target-specific security checks for Kubernetes workload declarations."""
from __future__ import annotations

from typing import Any


def scan_k8s_security(plan: dict[str, Any]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for module in plan.get("modules", []):
        module_id = module.get("id", "<unknown>")
        implementation = module.get("implementation", {})
        compose_services = (implementation.get("compose") or {}).get("services", {})
        kubernetes = implementation.get("kubernetes") or {}
        for workload_name, workload in (kubernetes.get("workloads") or {}).items():
            path = (
                f"module:{module_id}.implementation.kubernetes.workloads.{workload_name}"
            )
            pod_context = workload.get("podSecurityContext") or {}
            resources = workload.get("resources") or {}
            container_names = [
                *(workload.get("initContainers") or []),
                *(workload.get("containers") or []),
            ]
            overrides = workload.get("containerOverrides") or {}
            for container_name in container_names:
                service = compose_services.get(container_name) or {}
                override_context = (overrides.get(container_name) or {}).get("securityContext") or {}
                if not _runs_as_non_root(pod_context, override_context):
                    findings.append(_finding(
                        "CDS-K8S-001",
                        "high",
                        f'Container "{module_id}/{workload_name}/{container_name}" is not constrained to a non-root user.',
                        f"{path}.containers.{container_name}.securityContext",
                        module_id,
                        [
                            "Set an effective runAsNonRoot: true and a non-zero "
                            "runAsUser in podSecurityContext or the container override."
                        ],
                    ))
                if not _read_only_root(service, override_context):
                    findings.append(_finding(
                        "CDS-K8S-002",
                        "high",
                        f'Container "{module_id}/{workload_name}/{container_name}" has a writable root filesystem.',
                        f"{path}.containers.{container_name}.securityContext",
                        module_id,
                        ["Set compose read_only: true or Kubernetes readOnlyRootFilesystem: true."],
                    ))
                if not _drops_all_capabilities(service, override_context):
                    findings.append(_finding(
                        "CDS-K8S-003",
                        "high",
                        f'Container "{module_id}/{workload_name}/{container_name}" does not drop all Linux capabilities.',
                        f"{path}.containers.{container_name}.securityContext",
                        module_id,
                        ["Set compose cap_drop: [ALL] or Kubernetes capabilities.drop: [ALL]."],
                    ))
                if not _disallows_privilege_escalation(service, override_context):
                    findings.append(_finding(
                        "CDS-K8S-004",
                        "high",
                        f'Container "{module_id}/{workload_name}/{container_name}" allows privilege escalation.',
                        f"{path}.containers.{container_name}.securityContext",
                        module_id,
                        [
                            "Set compose no-new-privileges:true or Kubernetes "
                            "allowPrivilegeEscalation: false."
                        ],
                    ))
                declared = resources.get(container_name) or {}
                if not declared.get("requests") or not declared.get("limits"):
                    findings.append(_finding(
                        "CDS-K8S-005",
                        "medium",
                        f'Container "{module_id}/{workload_name}/{container_name}" lacks resource requests or limits.',
                        f"{path}.resources.{container_name}",
                        module_id,
                        ["Declare both requests and limits for the container."],
                        category="configuration-management",
                    ))
            service_type = (workload.get("service") or {}).get("type")
            if service_type in ("NodePort", "LoadBalancer"):
                findings.append(_finding(
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
                        "controller otherwise."
                    ],
                ))
        for index, rule in enumerate(
            (kubernetes.get("serviceAccount") or {}).get("rbac") or []
        ):
            if not isinstance(rule, dict):
                continue
            wildcard_fields = [
                field
                for field in ("apiGroups", "resources", "verbs")
                if isinstance(rule.get(field), list) and "*" in rule[field]
            ]
            if wildcard_fields:
                findings.append(_finding(
                    "CDS-K8S-006",
                    "medium",
                    (
                        f'RBAC rule {index} of module "{module_id}" grants wildcard '
                        f'{" and ".join(wildcard_fields)}; least-privilege rules name '
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
                ))
    return findings


def _runs_as_non_root(
    pod_context: dict[str, Any], override: dict[str, Any]
) -> bool:
    run_as_non_root = override.get(
        "runAsNonRoot", pod_context.get("runAsNonRoot")
    )
    run_as_user = override.get("runAsUser", pod_context.get("runAsUser"))
    return run_as_non_root is True and run_as_user != 0


def _read_only_root(service: dict[str, Any], override: dict[str, Any]) -> bool:
    return override.get("readOnlyRootFilesystem", service.get("read_only", False)) is True


def _drops_all_capabilities(service: dict[str, Any], override: dict[str, Any]) -> bool:
    dropped = (override.get("capabilities") or {}).get("drop", service.get("cap_drop", []))
    return "ALL" in (dropped or [])


def _disallows_privilege_escalation(service: dict[str, Any], override: dict[str, Any]) -> bool:
    if override.get("allowPrivilegeEscalation") is False:
        return True
    options = {str(option).replace(" ", "") for option in service.get("security_opt", []) or []}
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
        # (resource requests/limits) and CDS-K8S-006 (wildcard RBAC rules)
        # override this below since they're capacity-governance and
        # privilege-governance concerns rather than runtime-hardening posture.
        "category": category,
        "message": message,
        "path": path,
        "module": module,
        "value": None,
        "recommendation": recommendation,
    }
