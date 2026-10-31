# Zero Trust Architecture mapping

This document maps Composable Data Stack (CDS) capabilities and tracked work
to the core principles of [NIST SP 800-207, Zero Trust
Architecture](https://csrc.nist.gov/pubs/sp/800/207/final). It consolidates
existing platform work; it does not claim that CDS or a profile is NIST
conformant.

Zero Trust is an operational architecture, not a set of contracts whose
presence proves enforcement. CDS can validate declared configuration and
render platform resources, but operators remain responsible for their
identity providers, cluster, runtime, network, and policies. In particular,
`cds maturity` reports declared contract presence, not effective runtime
controls or a security certification.

## Mapping to the core principles

| Principle | CDS capabilities and tracking | Limits |
| --- | --- | --- |
| **Verify explicitly** — authenticate and authorize access using relevant identity and context rather than network location alone. | Database and cache authentication policies reject insecure trust modes in production profiles ([#206](https://github.com/RonaldHensbergen/composable-data-stack/issues/206)). Shared TLS requirements and reverse-proxy metadata let consumers declare and validate transport expectations ([#205](https://github.com/RonaldHensbergen/composable-data-stack/issues/205), [#577](https://github.com/RonaldHensbergen/composable-data-stack/issues/577), [#579](https://github.com/RonaldHensbergen/composable-data-stack/issues/579), [#580](https://github.com/RonaldHensbergen/composable-data-stack/issues/580)). The identity module work began under [#370](https://github.com/RonaldHensbergen/composable-data-stack/issues/370); its shared OIDC contract and consumer wiring remain tracked by [#681](https://github.com/RonaldHensbergen/composable-data-stack/issues/681) and [#1055](https://github.com/RonaldHensbergen/composable-data-stack/issues/1055). | Declared TLS does not prove that every connection is encrypted or that certificates are trusted. OIDC provider wiring is not yet a general per-service authorization policy, and a network boundary does not authenticate a workload. |
| **Use least-privilege access** — grant only the access needed to a resource. | The Helm renderer emits a per-module default-deny ingress/egress NetworkPolicy, then permits contract-derived traffic, same-module traffic, and DNS; custom allow rules are explicit. This work was delivered under [#75](https://github.com/RonaldHensbergen/composable-data-stack/issues/75). Kubernetes pod hardening and opt-in RBAC are covered by [PR #608](https://github.com/RonaldHensbergen/composable-data-stack/pull/608). The optional service-mesh capability for mTLS and traffic policy is tracked by [#776](https://github.com/RonaldHensbergen/composable-data-stack/issues/776). | NetworkPolicy and pod hardening apply to the Helm target, not Docker Compose. The default-deny policy limits network reachability; it does not itself establish peer identity or application authorization. A service mesh is not yet implemented. |
| **Assume breach** — minimize blast radius, continuously assess access, and retain evidence for detection and response. | Per-module network boundaries and hardened Kubernetes workloads reduce some lateral movement. The local audit trail records rendered/applied command outcomes and module/image versions ([#737](https://github.com/RonaldHensbergen/composable-data-stack/issues/737)). | The audit trail is local command history, not centralized or continuous runtime monitoring. It does not collect workload activity or provide incident response. |

These are partial, target-dependent controls. The mapping is intended to make
the evidence and its limits reviewable, not to imply that a profile satisfying
one row satisfies the whole NIST architecture.

## How `cds maturity` uses the mapping

`cds maturity` uses the following profile-facing tiers, based on recognized
contracts in the resolved plan:

| Tier | Contract evidence required |
| --- | --- |
| **Basic** | A database and cache provider |
| **Automated** | Basic, plus identity, a reverse proxy, and a backup provider |
| **Observed** | Automated, plus at least one logging, metrics, or tracing provider |
| **Secured** | Observed, plus a TLS-certificate provider, a service-mesh provider, or an identity broker backed by a separate OIDC provider |
| **Resilient** | Secured, plus backup coverage for every stateful module instance |

The **Secured** threshold therefore indicates additional security-control
contract evidence on top of an Observed profile. It is not a score for all
three NIST principles, and does not attest that a provider is correctly
configured or that a control is effective. The tier names and contract
requirements are defined by [`cli/maturity.py`](../cli/maturity.py); the CLI
also states this limitation in its text report.

## Remaining gaps and follow-ups

- **Workload identity and attestation:** There is no provider-neutral contract
  for cryptographically attested workload identity or trust-domain evidence.
  This is distinct from user-facing OIDC identity ([#681](https://github.com/RonaldHensbergen/composable-data-stack/issues/681))
  and from network-level mTLS. A focused follow-up, [#1068](https://github.com/RonaldHensbergen/composable-data-stack/issues/1068),
  tracks the contract and an initial Kubernetes reference.
- **OIDC authorization wiring:** The Keycloak `oidc-provider` contract and
  its first consumer remain open in [#681](https://github.com/RonaldHensbergen/composable-data-stack/issues/681)
  and [#1055](https://github.com/RonaldHensbergen/composable-data-stack/issues/1055).
  Even when implemented, provider binding alone will not represent every
  per-resource authorization policy.
- **Service-mesh enforcement:** mTLS and traffic-policy support remain open
  under [#776](https://github.com/RonaldHensbergen/composable-data-stack/issues/776).
- **Target coverage:** The default-deny NetworkPolicy is generated for Helm;
  Docker Compose has no equivalent CDS-generated per-module network policy.
  Do not infer cross-target segmentation from the maturity report.
- **Continuous verification:** The local audit log in [docs/audit-log.md](audit-log.md)
  is not a runtime event stream. Continuous workload monitoring and response
  remain operator responsibilities.

The status and background for this work are also tracked in
[docs/haven-parity-plan.md](haven-parity-plan.md), workstreams 3.4 and 3.7,
and in [docs/roadmap.md](roadmap.md).
