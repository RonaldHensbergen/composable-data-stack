from __future__ import annotations

from typing import Any

_CAPABILITIES = (
    ("database", "Database", ("sql-database",)),
    ("cache", "Cache", ("cache-service",)),
    ("identity", "Identity", ("oidc-provider", "identity-broker")),
    ("tls", "TLS / ingress", ("reverse-proxy",)),
    ("logging", "Logging", ("log-sink",)),
    ("metrics", "Metrics", ("metrics-sink",)),
    ("tracing", "Tracing", ("trace-sink",)),
    ("backup", "Backup", ("backup-target",)),
    ("gitops", "GitOps", ("gitops-target",)),
    ("service-mesh", "Service mesh", ("service-mesh",)),
)


def build_maturity_report(plan: dict[str, Any]) -> dict[str, Any]:
    """Summarize recognized platform contracts from a resolved Plan."""
    modules = plan.get("modules")
    if not isinstance(modules, list):
        raise ValueError("Maturity reporting requires a resolved Plan with a modules list.")

    providers_by_kind: dict[str, set[str]] = {}
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
        and observability_achieved
        and bound_by_id["backup"]
    )
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
                "observability",
                "backup",
            ],
        },
    ]
    highest_tier = (
        "Automated"
        if automated_achieved
        else "Basic"
        if basic_achieved
        else None
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

    labels = {capability["id"]: capability["name"] for capability in report["capabilities"]}
    lines.extend(["", "Tier requirements:"])
    for tier in report["tiers"]:
        requirements = [
            labels.get(item, item)
            for item in tier["requirements"]
        ]
        status = "met" if tier["achieved"] else "not met"
        lines.append(f"  {tier['name']}: {status} ({', '.join(requirements)})")
    lines.append(
        "Note: GitOps and service-mesh capabilities are reported but do not "
        "affect the Basic or Automated tiers."
    )
    return "\n".join(lines)
