from __future__ import annotations

from typing import Any

_CAPABILITIES = (
    ("database", "Database", ("sql-database",)),
    ("cache", "Cache", ("cache-service",)),
    ("identity", "Identity", ("oidc-provider", "identity-broker")),
    ("identity-broker", "Identity broker", ("identity-broker",)),
    ("tls", "TLS / ingress", ("reverse-proxy",)),
    ("tls-cert", "TLS certificate", ("tls-cert",)),
    ("logging", "Logging", ("log-sink",)),
    ("metrics", "Metrics", ("metrics-sink",)),
    ("tracing", "Tracing", ("trace-sink",)),
    ("backup", "Backup", ("backup-target",)),
    ("gitops", "GitOps", ("gitops-target",)),
    ("service-mesh", "Service mesh", ("service-mesh",)),
)

_STATEFUL_CONTRACT_KINDS = {"sql-database", "file-database"}


def build_maturity_report(plan: dict[str, Any]) -> dict[str, Any]:
    """Summarize recognized platform contracts from a resolved Plan."""
    modules = plan.get("modules")
    if not isinstance(modules, list):
        raise ValueError("Maturity reporting requires a resolved Plan with a modules list.")

    providers_by_kind: dict[str, set[str]] = {}
    stateful_instances: set[str] = set()
    for module in modules:
        if not isinstance(module, dict):
            raise ValueError("Resolved Plan contains a malformed module entry.")
        module_id = module.get("id")
        provides = module.get("provides", {})
        if not isinstance(module_id, str) or not isinstance(provides, dict):
            raise ValueError("Resolved Plan contains a module without a valid id or provides map.")
        for contract in provides.values():
            if not isinstance(contract, dict):
                continue
            contract_kind = contract.get("kind")
            if isinstance(contract_kind, str):
                providers_by_kind.setdefault(contract_kind, set()).add(module_id)
                if contract_kind in _STATEFUL_CONTRACT_KINDS:
                    stateful_instances.add(module_id)

    capabilities = []
    bound_by_id: dict[str, bool] = {}
    for capability_id, label, contract_kinds in _CAPABILITIES:
        provider_instances = sorted(
            {
                module_id
                for contract_kind in contract_kinds
                for module_id in providers_by_kind.get(contract_kind, set())
            }
        )
        is_bound = bool(provider_instances)
        bound_by_id[capability_id] = is_bound
        capabilities.append(
            {
                "id": capability_id,
                "name": label,
                "contractKinds": list(contract_kinds),
                "bound": is_bound,
                "providerInstances": provider_instances,
            }
        )

    basic_achieved = bound_by_id["database"] and bound_by_id["cache"]
    observability_achieved = any(
        bound_by_id[capability_id] for capability_id in ("logging", "metrics", "tracing")
    )
    automated_achieved = (
        basic_achieved
        and bound_by_id["identity"]
        and bound_by_id["tls"]
        and bound_by_id["backup"]
    )
    observed_achieved = automated_achieved and observability_achieved
    # An identity broker also satisfies the identity capability, so it only
    # counts as an extra security control when a different module provides
    # the OIDC identity it fronts.
    broker_is_security_control = bool(
        providers_by_kind.get("identity-broker")
    ) and bool(
        providers_by_kind.get("oidc-provider", set())
        - providers_by_kind["identity-broker"]
    )
    secured_achieved = observed_achieved and (
        bool(providers_by_kind.get("tls-cert"))
        or bool(providers_by_kind.get("service-mesh"))
        or broker_is_security_control
    )
    backed_up_instances = _backed_up_stateful_instances(
        modules, stateful_instances
    )
    missing_stateful_backups = sorted(stateful_instances - backed_up_instances)
    resilient_achieved = secured_achieved and not missing_stateful_backups
    tiers = [
        {
            "name": "Basic",
            "achieved": basic_achieved,
            "requirements": ["database", "cache"],
        },
        {
            "name": "Automated",
            "achieved": automated_achieved,
            "requirements": [
                "Basic",
                "identity",
                "tls",
                "backup",
            ],
        },
        {
            "name": "Observed",
            "achieved": observed_achieved,
            "requirements": ["Automated", "observability"],
        },
        {
            "name": "Secured",
            "achieved": secured_achieved,
            "requirements": ["Observed", "security-control"],
        },
        {
            "name": "Resilient",
            "achieved": resilient_achieved,
            "requirements": ["Secured", "stateful-backups"],
        },
    ]
    highest_tier = next(
        (tier["name"] for tier in reversed(tiers) if tier["achieved"]),
        None,
    )
    metadata = plan.get("metadata", {})
    if not isinstance(metadata, dict):
        metadata = {}

    return {
        "apiVersion": "cds/v1alpha1",
        "kind": "MaturityReport",
        "profile": {
            "name": metadata.get("name"),
            "environment": plan.get("environment") or metadata.get("environment"),
            "sourceProfile": plan.get("sourceProfile"),
        },
        "highestTier": highest_tier,
        "tiers": tiers,
        "capabilities": capabilities,
    }


def _backed_up_stateful_instances(
    modules: list[dict[str, Any]],
    stateful_instances: set[str],
) -> set[str]:
    backup_target_providers: set[str] = set()
    backup_source_bindings: list[tuple[str, str]] = []
    for module in modules:
        if not isinstance(module, dict):
            continue
        module_id = module.get("id")
        if not isinstance(module_id, str):
            continue
        provides = module.get("provides", {})
        if not isinstance(provides, dict):
            continue
        provided_kinds = {
            contract.get("kind")
            for contract in provides.values()
            if isinstance(contract, dict)
            and isinstance(contract.get("kind"), str)
        }
        if "backup-target" in provided_kinds:
            backup_target_providers.add(module_id)
        config = module.get("config", {})
        backup = config.get("backup") if isinstance(config, dict) else None
        if (
            provided_kinds & _STATEFUL_CONTRACT_KINDS
            and isinstance(backup, dict)
            and backup.get("enabled") is True
        ):
            backup_source_bindings.append((module_id, module_id))

        consumes = module.get("consumes", {})
        if not isinstance(consumes, dict):
            continue
        for consumed in consumes.values():
            if not isinstance(consumed, dict):
                continue
            contract = consumed.get("contract", {})
            if not isinstance(contract, dict):
                continue
            if contract.get("kind") == "backup-target":
                contract_ref = consumed.get("contractRef")
                if isinstance(contract_ref, str):
                    backup_source_bindings.append(
                        (contract_ref.split(".", 1)[0], module_id)
                    )
            elif contract.get("kind") == "backup-source":
                contract_ref = consumed.get("contractRef")
                if isinstance(contract_ref, str):
                    backup_source_bindings.append(
                        (module_id, contract_ref.split(".", 1)[0])
                    )

    backed_up: set[str] = set()
    for backup_module_id, stateful_module_id in backup_source_bindings:
        if (
            backup_module_id in backup_target_providers
            and stateful_module_id in stateful_instances
        ):
            backed_up.add(stateful_module_id)
    return backed_up


def format_maturity_report(report: dict[str, Any]) -> str:
    """Format a maturity report for terminal output."""
    profile = report["profile"]
    lines = [
        "== CDS Profile Maturity ==",
        f"Profile: {profile.get('name') or profile.get('sourceProfile') or '(unknown)'}",
    ]
    if profile.get("environment"):
        lines.append(f"Environment: {profile['environment']}")
    lines.extend(
        [
            f"Highest tier: {report['highestTier'] or 'Not yet Basic'}",
            "",
            "Capability | Contract kind(s) | Bound | Provider module instance(s)",
            "-----------|-----------------|-------|---------------------------",
        ]
    )
    for capability in report["capabilities"]:
        providers = ", ".join(capability["providerInstances"]) or "-"
        contract_kinds = ", ".join(capability["contractKinds"])
        bound = "Yes" if capability["bound"] else "No"
        lines.append(
            f"{capability['name']} | {contract_kinds} | {bound} | {providers}"
        )

    labels = {
        capability["id"]: capability["name"]
        for capability in report["capabilities"]
    }
    labels.update(
        {
            "observability": "at least one of logging, metrics, or tracing",
            "security-control": (
                "a TLS certificate, service mesh, or identity broker fronting a "
                "separate OIDC provider"
            ),
            "stateful-backups": "a backup target for every stateful module",
        }
    )
    lines.extend(["", "Tier requirements:"])
    for tier in report["tiers"]:
        requirements = [
            labels.get(item, item)
            for item in tier["requirements"]
        ]
        status = "met" if tier["achieved"] else "not met"
        lines.append(f"  {tier['name']}: {status} ({', '.join(requirements)})")
    lines.append(
        "Note: GitOps is reported but does not affect the maturity tiers."
    )
    lines.append(
        "Note: Tier results reflect declared contract presence, not effective "
        "runtime enforcement or NIST SP 800-207 conformity."
    )
    return "\n".join(lines)
