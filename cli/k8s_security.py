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
            if pod_context.get("runAsNonRoot") is not True or pod_context.get("runAsUser") == 0:
                findings.append(_finding(
                    "CDS-K8S-001",
                    "high",
                    f'Kubernetes workload "{module_id}/{workload_name}" is not constrained to a non-root user.',
                    f"{path}.podSecurityContext",
                    module_id,
                    ["Set runAsNonRoot: true and a non-zero runAsUser in podSecurityContext."],
                ))

            resources = workload.get("resources") or {}
            container_names = [
                *(workload.get("initContainers") or []),
                *(workload.get("containers") or []),
            ]
            overrides = workload.get("containerOverrides") or {}
            for container_name in container_names:
                service = compose_services.get(container_name) or {}
                override_context = (overrides.get(container_name) or {}).get("securityContext") or {}
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
                    ))
    return findings


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
) -> dict[str, Any]:
    return {
        "rule_id": rule_id,
        "severity": severity,
        "message": message,
        "path": path,
        "module": module,
        "value": None,
        "recommendation": recommendation,
    }
