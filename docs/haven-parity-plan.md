# Reaching Haven Parity: A Plan for CDS

Version: 0.3
Status: Draft — planning document. Section 4 tracks work already in flight
against the workstreams below; section 7 recommends a concrete module per
open gap (updated 2026-09-11).
Related: [docs/roadmap.md](roadmap.md), [docs/architecture.md](architecture.md),
[docs/observability.md](observability.md), [docs/threat-model.md](threat-model.md)

## 1. What "Haven" is, and why it is the reference point

[Haven](https://haven.commonground.nl/) is VNG Realisatie's standard for
platform-independent cloud hosting for Dutch municipalities. It is not a
product; it is a **reference architecture plus a certified reference
implementation** ("Haven+") for running workloads on Kubernetes in a portable,
secure-by-default, and auditable way. Its three defining traits are:

1. **A Kubernetes target with a fixed set of platform capabilities.**
   Haven+ automates deployment of GitOps, observability, security, database
   operators, networking, and backup — not as optional add-ons bolted onto a
   workload, but as the baseline every compliant cluster provides.
2. **A maturity model.** Compliance is not binary. Clusters/organizations are
   assessed against staged criteria (roughly: manual → automated/GitOps →
   full platform capability set with policy enforcement), so adoption can be
   incremental and still be measured.
3. **Vendor neutrality with a concrete reference implementation.** Haven
   defines the standard, and Haven+ is the open-source reference
   implementation that shows a compliant setup end-to-end (FluxCD/ArgoCD,
   Grafana/Loki/Mimir/Tempo/Alloy, cert-manager, Istio, Keycloak, Sealed
   Secrets/External Secrets, CloudNativePG, Velero).

CDS already shares Haven's philosophy (contracts over hardcoded integrations,
vendor neutrality, incremental adoption) but today only targets Docker
Compose and only models application-layer modules (orchestration, warehouse,
BI, cache, secrets, transformation). To reach a comparable "level," CDS needs
a **platform layer**, a **second rendering target**, and a **maturity model**
that measures a profile/deployment against that platform layer — the same
three traits above, translated into CDS's contract/module/profile vocabulary.

This document scopes that work. It intentionally does not implement
anything; it is the plan the roadmap items below should be pulled from.

## 2. Gap summary

| Haven+ trait | CDS today | Gap |
| --- | --- | --- |
| Kubernetes-native rendering | `cli/renderer.py` (Compose) plus `cli/k8s_renderer.py` (Helm), landed via PR [#608](https://github.com/RonaldHensbergen/composable-data-stack/pull/608) | Largely closed for the core render/deploy path; NetworkPolicy generation and CI-based cluster validation remain (see 7.1) |
| GitOps | None | No module/contract for declarative continuous deployment; no tracked issue yet |
| Observability stack (metrics/logs/traces) | `log-sink` contract + structured-event schema shipped (issue [#174](https://github.com/RonaldHensbergen/composable-data-stack/issues/174), [docs/observability.md](observability.md)) | No reference module implementing `log-sink`/metrics/tracing yet — tracked by [#369](https://github.com/RonaldHensbergen/composable-data-stack/issues/369) and [#662](https://github.com/RonaldHensbergen/composable-data-stack/issues/662) |
| Security platform (cert lifecycle, service mesh, IAM, secrets sync) | `modules/secrets/vault` (secrets only); `cli/security.py` rule-based static checks; new `cli/k8s_security.py` (PR #608) adds pod/container hardening rules (non-root, read-only rootfs, capability drop, no privilege escalation, resource limits) | No cert-management, service-mesh, or IAM/identity-broker modules yet — tracked by [#205](https://github.com/RonaldHensbergen/composable-data-stack/issues/205), [#577](https://github.com/RonaldHensbergen/composable-data-stack/issues/577), [#579](https://github.com/RonaldHensbergen/composable-data-stack/issues/579), [#580](https://github.com/RonaldHensbergen/composable-data-stack/issues/580) (TLS), [#370](https://github.com/RonaldHensbergen/composable-data-stack/issues/370) (identity) |
| Database operators | `modules/warehouse/postgres` (plain container; StatefulSet+PVC on the new Kubernetes target, still not operator-managed) | No operator-managed/HA warehouse module; no tracked issue yet |
| Backup/restore | None | No backup contract or module — tracked by parent issue [#210](https://github.com/RonaldHensbergen/composable-data-stack/issues/210) and module-specific issues [#665](https://github.com/RonaldHensbergen/composable-data-stack/issues/665) (Postgres), [#668](https://github.com/RonaldHensbergen/composable-data-stack/issues/668) (generic file/volume), [#669](https://github.com/RonaldHensbergen/composable-data-stack/issues/669) (object storage) |
| Maturity model | None (roadmap uses "stable"/"experimental" tags only, which describe CDS's own component maturity, not a deployed profile's compliance level) | No profile-facing maturity model or scoring; no tracked issue yet |

## 3. Workstreams

### 3.1 Kubernetes rendering target — largely delivered by PR #608

PR [#608](https://github.com/RonaldHensbergen/composable-data-stack/pull/608)
implemented this workstream: `spec.implementation.kubernetes` as a sibling of
`compose` in `module.yaml` (not a Compose-to-Kubernetes converter — both
implementations are rendered independently from the same resolved plan, per
[ADR 0001](adr/0001-kubernetes-render-target.md)), a new `cli/k8s_renderer.py`
producing a Helm chart, `cds render/validate/security/up/down/state --target
helm`, and a local k3d harness with E2E coverage
([docs/kubernetes.md](kubernetes.md), [docs/plan/k8s-progress.md](plan/k8s-progress.md)).
Remaining gaps, not covered by that PR:

- **NetworkPolicy generation** — the translation table in
  [docs/kubernetes.md](kubernetes.md#translation-boundaries) explicitly notes
  "Network policy: Not emitted; the contract graph does not claim exhaustive
  traffic yet." This was originally scoped under issue
  [#75](https://github.com/RonaldHensbergen/composable-data-stack/issues/75)
  and remains open work.
- **CI-based cluster validation** — CI now runs `helm lint`/`helm template`
  and a deterministic double-render diff, but does not apply the chart to a
  real cluster (kind or k3d) in CI; the local k3d E2E suite
  (`make k3d-e2e`) is developer/PR-author-run only. Issue
  [#76](https://github.com/RonaldHensbergen/composable-data-stack/issues/76)
  scoped "kind-based CI validation" and is not yet fully satisfied.
- **A `CDSProfile` CRD / operator** was evaluated and explicitly deferred (not
  rejected) in ADR 0001, on the grounds that a chart renderer forecloses
  nothing and an operator can layer on top later. Relevant if CDS ever wants
  a reconciliation loop instead of CLI-driven `helm upgrade --install`.

Everything else originally scoped here (schema extension, core renderer,
CLI target switch, secrets/ConfigMap/PVC generation, docs/examples — issues
[#65](https://github.com/RonaldHensbergen/composable-data-stack/issues/65),
[#73](https://github.com/RonaldHensbergen/composable-data-stack/issues/73),
[#74](https://github.com/RonaldHensbergen/composable-data-stack/issues/74),
[#77](https://github.com/RonaldHensbergen/composable-data-stack/issues/77))
is implemented and verified. Once #608 merges, these issues should close and
the two remaining gaps above should be re-filed or reopened as focused
follow-ups.

### 3.2 GitOps

No issue currently tracks this workstream — it should be filed once 3.1's
remaining follow-ups (NetworkPolicy, CI cluster validation) are scoped.

- Define a `gitops-target` contract (repo URL, path, branch, sync interval)
  that a GitOps module (FluxCD- or ArgoCD-shaped, vendor-neutral contract
  fields only, per CDS's existing "contract, not vendor" convention) can
  provide.
- Add a reference module under `modules-experimental/platform/gitops/` (or a
  new `platform` category) with a `docker-compose` implementation for local
  testing and a `kubernetes` implementation for real use once 3.1 lands.
- `cds render` output becomes the GitOps source of truth artifact; document
  how a rendered Kubernetes manifest set is expected to be committed/synced.

### 3.3 Observability platform module

The contract and schema this needs already exist — issue
[#174](https://github.com/RonaldHensbergen/composable-data-stack/issues/174)
closed with the `log-sink` contract, structured-event schema, and profile
opt-in ([docs/observability.md](observability.md)). The reference module(s)
that provide it are still open, tracked by
[#369](https://github.com/RonaldHensbergen/composable-data-stack/issues/369)
(Grafana observability module) and
[#662](https://github.com/RonaldHensbergen/composable-data-stack/issues/662)
(log-sink provider module, e.g. Loki/Vector) — a prior duplicate,
[#664](https://github.com/RonaldHensbergen/composable-data-stack/issues/664)
(Prometheus/Grafana metrics module), was closed in favor of #369.

- Ship a reference module implementing the existing `log-sink` contract
  ([shared/contracts/log-sink.yaml](../shared/contracts/log-sink.yaml)) —
  e.g. `modules/observability/log-collector/` — so `docs/observability.md`'s
  design has a real provider instead of only reference pipelines in section 8.
- Add `metrics-sink` and `trace-sink` contracts following the same
  vendor-neutral shape as `log-sink` (host/port/protocol, no product-specific
  fields), and a reference module that can provide all three (e.g. a single
  module fronting Grafana-stack-shaped ingestion, without hardcoding Grafana
  in the CLI or other modules).
- Extend `spec.observability` in the profile schema (`cli/resources/profile.schema.json`)
  with `metricsShipping`/`tracing` blocks mirroring `logShipping`, and extend
  `validate_observability_config` in `cli/validator.py` accordingly.

### 3.4 Security platform modules

- `cert-issuer` contract + reference module (cert-manager-shaped): issues/renews
  TLS certs for modules that consume a `tls-cert` contract.
- `service-mesh` contract + reference module (Istio-shaped): mutual TLS and
  traffic policy between module instances, opt-in per profile.
- `identity-broker` contract + reference module (Keycloak-shaped): distinct
  from `modules/secrets/vault` (secret storage) — this is authentication/SSO,
  a different capability that Haven+ also treats as a separate component
  (Keycloak vs. Vault-equivalents). Tracked by issue
  [#370](https://github.com/RonaldHensbergen/composable-data-stack/issues/370).
- Extend `cli/resources/rule-schema.json` / `rule-set.json` with security
  rules that apply to the new contracts (e.g., "profile exposes a service
  externally without a `tls-cert` binding" the same way existing rules flag
  missing secrets scoping). A first slice of this — pod/container hardening,
  not TLS/mesh/IAM — already landed as `cli/k8s_security.py` in PR
  [#608](https://github.com/RonaldHensbergen/composable-data-stack/pull/608)
  (non-root, read-only rootfs, capability drop, no privilege escalation,
  resource requests/limits for Kubernetes workloads).
- TLS/certificate contract work is already tracked in four issues that this
  workstream should build on rather than duplicate:
  [#205](https://github.com/RonaldHensbergen/composable-data-stack/issues/205)
  (parent: TLS and certificate-management contracts, depends on the Traefik
  reverse-proxy module in #549),
  [#577](https://github.com/RonaldHensbergen/composable-data-stack/issues/577)
  (TLS requirement fields on HTTP/database/cache contracts),
  [#579](https://github.com/RonaldHensbergen/composable-data-stack/issues/579)
  (shared TLS-capable reverse-proxy contract), and
  [#580](https://github.com/RonaldHensbergen/composable-data-stack/issues/580)
  (validator/planner enforcement of TLS requirements).

### 3.5 Backup and restore

This workstream is already tracked in more granular form than this document
originally scoped it: parent issue
[#210](https://github.com/RonaldHensbergen/composable-data-stack/issues/210)
(backup/restore/DR contracts) plus module-specific issues
[#665](https://github.com/RonaldHensbergen/composable-data-stack/issues/665)
(Postgres warehouse backup/DR),
[#668](https://github.com/RonaldHensbergen/composable-data-stack/issues/668)
(generic file/volume backup for Dagster IO manager, DuckDB, dlt via restic),
and
[#669](https://github.com/RonaldHensbergen/composable-data-stack/issues/669)
(backup/replication for an object-storage module's own bucket contents,
building on #668). Define the shared `backup-target` contract at #210 first
so the module-specific issues bind to one vendor-neutral shape instead of
each inventing their own.

- Define a `backup-target` contract (destination, schedule, retention) that
  a backup module (Velero-shaped) can provide.
- Any module with persistent state (e.g. `modules/warehouse/postgres`) gains
  an optional `consumes` entry for `backup-target`, matching the existing
  optional-consumption pattern already used for `log-sink`.
- This is the workstream most likely to need genuine Kubernetes-native
  primitives (volume snapshots) — treat the Docker Compose implementation as
  best-effort (e.g. volume-mount tar/dump-based backup) and the Kubernetes
  implementation as the primary target.

### 3.6 Database operator patterns

- Add an operator-managed warehouse module variant (CloudNativePG-shaped) as
  a Kubernetes-only alternative to `modules/warehouse/postgres`'s plain
  container, once 3.1 exists. Keep the existing Compose-based Postgres module
  as-is for local/dev use — this is additive, not a replacement.

### 3.7 Maturity model

Define a **profile-facing maturity model**, distinct from the roadmap's
existing stable/experimental component-maturity tags (which describe *CDS's
own* component readiness, not a *deployed profile's* compliance level):

| Tier | Criteria (draft) |
| --- | --- |
| **Basic** | Profile validates and renders; no platform-layer contracts consumed |
| **Automated** | Profile renders to a Kubernetes target (3.1) and is deployed via a `gitops-target` binding (3.2) |
| **Observed** | Automated, plus profile binds `log-sink`/`metrics-sink`/`trace-sink` (3.3) |
| **Secured** | Observed, plus profile binds `tls-cert` and/or `service-mesh` and/or `identity-broker` (3.4) |
| **Resilient** | Secured, plus every stateful module instance binds `backup-target` (3.5) |

Implementation sketch:

- Add `cds maturity <profile>` (or fold into `cds validate`/`cds test` output)
  that inspects the resolved Plan's contract bindings and reports the highest
  tier satisfied, plus which bindings are missing for the next tier — mirrors
  how `cds security` already reports rule violations without blocking
  compile stages by default.
- This is purely additive: it reads the Plan, it does not change plan/render
  semantics, and a profile with zero platform-layer bindings still validates
  and renders exactly as it does today (tier: Basic).

## 4. Current status vs. tracked work (as of 2026-09-11)

Cross-referencing this plan against open GitHub issues and PR #608 shows the
gap is narrower than section 2 suggested when this document was drafted:

| Workstream | Status | Tracking |
| --- | --- | --- |
| 3.1 Kubernetes rendering target | **Done, pending merge** — implemented end-to-end with real k3d E2E verification | PR [#608](https://github.com/RonaldHensbergen/composable-data-stack/pull/608); originating issues [#65](https://github.com/RonaldHensbergen/composable-data-stack/issues/65), [#73](https://github.com/RonaldHensbergen/composable-data-stack/issues/73), [#74](https://github.com/RonaldHensbergen/composable-data-stack/issues/74), [#77](https://github.com/RonaldHensbergen/composable-data-stack/issues/77) should close on merge |
| 3.1 follow-up: NetworkPolicy generation | Open, not started | [#75](https://github.com/RonaldHensbergen/composable-data-stack/issues/75) (partially — also covered secrets/PVC, which #608 did deliver) |
| 3.1 follow-up: CI cluster validation (kind/k3d apply in CI, not just `helm lint`/`template`) | Open, not started | [#76](https://github.com/RonaldHensbergen/composable-data-stack/issues/76) |
| 3.2 GitOps | Not started, no issue filed | — |
| 3.3 Observability platform module | Contract/schema done; reference module open | Design closed via [#174](https://github.com/RonaldHensbergen/composable-data-stack/issues/174); module tracked by [#369](https://github.com/RonaldHensbergen/composable-data-stack/issues/369), [#662](https://github.com/RonaldHensbergen/composable-data-stack/issues/662) |
| 3.4 Security platform modules | Pod/container hardening rules done; TLS/mesh/IAM open | Hardening in PR #608 (`cli/k8s_security.py`); TLS tracked by [#205](https://github.com/RonaldHensbergen/composable-data-stack/issues/205), [#577](https://github.com/RonaldHensbergen/composable-data-stack/issues/577), [#579](https://github.com/RonaldHensbergen/composable-data-stack/issues/579), [#580](https://github.com/RonaldHensbergen/composable-data-stack/issues/580); identity tracked by [#370](https://github.com/RonaldHensbergen/composable-data-stack/issues/370) |
| 3.5 Backup and restore | Not started, but scoped into 4 issues already | [#210](https://github.com/RonaldHensbergen/composable-data-stack/issues/210) (contracts), [#665](https://github.com/RonaldHensbergen/composable-data-stack/issues/665) (Postgres), [#668](https://github.com/RonaldHensbergen/composable-data-stack/issues/668) (generic file/volume), [#669](https://github.com/RonaldHensbergen/composable-data-stack/issues/669) (object storage) |
| 3.6 Database operators | Not started, no issue filed | — |
| 3.7 Maturity model | Not started, no issue filed | — |

Notable duplicates closed in favor of the issues above:
[#664](https://github.com/RonaldHensbergen/composable-data-stack/issues/664)
(Prometheus/Grafana metrics module → closed in favor of #369) and
[#661](https://github.com/RonaldHensbergen/composable-data-stack/issues/661)
(Keycloak identity/RBAC module → closed in favor of #370).

Practical implication: once PR #608 merges, this plan's single biggest
prerequisite (3.1) is satisfied, and the highest-leverage next filings are
**3.2 (GitOps)**, **3.6 (database operators)**, and **3.7 (maturity model)**
— the three workstreams with no existing issue at all — plus following up on
the two 3.1 gaps (#75's NetworkPolicy scope, #76's CI cluster validation
scope) that PR #608 did not close.

## 5. Sequencing

The workstreams have real dependencies; suggested order, updated for the
current status in section 4:

1. **3.1 follow-ups (NetworkPolicy, CI cluster validation)** — close out the
   two gaps PR #608 left in an otherwise-complete workstream before building
   on top of it.
2. **3.3 Observability platform module** — no Kubernetes dependency, and
   closes an already-designed but unimplemented contract
   ([docs/observability.md](observability.md)); issues #369/#662 are ready
   to pick up now.
3. **3.7 Maturity model (Basic/Automated tiers only)** — can be scaffolded
   now that 3.1 has landed, even before 3.3–3.6 exist, since it is a
   read-only reporting tool over Plan bindings. No issue filed yet.
4. **3.4 Security platform modules** (issues #205/#370/#577/#579/#580) and
   **3.2 GitOps** (no issue yet) — parallelizable now that 3.1 has landed.
5. **3.5 Backup** (issues #210/#665/#668/#669) and **3.6 Database operators**
   (no issue yet) — lowest priority; highest Kubernetes-specific complexity,
   least reusable by Compose-only users.

## 6. Non-goals

- CDS is not adopting Haven's specific product choices (FluxCD vs. ArgoCD,
  Grafana stack vs. alternatives) as hard dependencies — every workstream
  above defines a **vendor-neutral contract first**, matching CDS's existing
  `sql-database`/`cache-service`/`log-sink` pattern. Reference modules are
  examples, not mandates.
- CDS is not dropping Docker Compose support. Kubernetes is an additional
  rendering target, not a replacement — profiles that only need local/dev
  Compose stacks keep working unchanged.
- This document does not commit to a timeline; it feeds
  [docs/roadmap.md](roadmap.md), which tracks actual release commitments.

## 7. Recommended module per gap

Concrete module/category recommendations for each open gap in section 4,
following CDS's existing `modules/<category>/<name>/module.yaml` convention
(new/unproven modules start under `modules-experimental/`, per
[docs/modules.md](modules.md)):

| Gap | Category | Module | Contract it provides/consumes | Notes |
| --- | --- | --- | --- | --- |
| 3.2 GitOps | `modules-experimental/gitops/` | `fluxcd` | new `gitops-target` contract | FluxCD over ArgoCD as the default reference: no separate UI/DB, lighter CRD surface, pull-based — matches Haven+'s lower-friction option. ArgoCD can be a documented alternative provider of the same contract, not a second mandatory module. |
| 3.3 Observability — logs | `modules-experimental/observability/` | `loki` (Vector or Fluent Bit as the shipping agent, Loki as the store) | existing `log-sink` contract ([shared/contracts/log-sink.yaml](../shared/contracts/log-sink.yaml)) | Closes #662 directly — the contract/schema already exist ([docs/observability.md](observability.md)); this module is the missing provider. |
| 3.3 Observability — metrics/traces | `modules-experimental/observability/` | `grafana` (bundling Grafana + Prometheus/Mimir; Tempo optional) | new `metrics-sink` (+ optional `trace-sink`) contracts | Closes #369. Keep as a second module rather than folding into `loki`, so profiles can opt into logs without pulling in the full metrics stack. |
| 3.4 Security — identity/SSO | `modules/identity/` | `keycloak` | new `identity-broker` contract | Already scaffolded on branch `feat/identity-keycloak-module` (draft, closes #370) — needs `consumes`/`provides` filled in and the hardcoded `postgres` service name replaced with a consumed `sql-database` binding before it is profile-ready. |
| 3.4 Security — TLS | `modules/integration/` or new `modules-experimental/security/` | `traefik` (already a dependency named in issue #549) | new `reverse-proxy` contract with TLS fields (#579) + `cert-issuer` contract | Do this before Istio — it is the prerequisite blocking #205, and has far lower operational cost than a service mesh. |
| 3.4 Security — service mesh | `modules-experimental/security/` | `istio` | `service-mesh` contract | Lowest priority in this group — highest complexity, Kubernetes-only, and TLS-at-the-proxy (above) covers most profiles' actual need without it. Defer until a profile explicitly needs mTLS between module instances. |
| 3.5 Backup — Postgres | `modules/warehouse/postgres/` (extend, don't fork) | add a sidecar/`backup-target` consumer to the existing module | consumes new `backup-target` contract | Closes #665. A `pgbackrest`- or `wal-g`-shaped sidecar, not a new warehouse module — keeps the existing stable Postgres module as the single source of truth. |
| 3.5 Backup — generic files | `modules-experimental/backup/` | `restic` | provides `backup-target` | Closes #668 (Dagster IO manager, DuckDB, dlt). Define the contract here first (#210); #665/#669 should bind to it rather than inventing their own. |
| 3.5 Backup — object storage | `modules-experimental/backup/` | extend `restic` module or add a `restic-s3` variant | consumes `backup-target` | Closes #669; depends on #668 landing first per that issue's own text. |
| 3.6 Database operators | `modules-experimental/warehouse/` | `postgres-operator` (CloudNativePG) | same `sql-database` contract as `modules/warehouse/postgres`, Kubernetes-only implementation | Additive, not a replacement — keep the Compose-based `postgres` module as-is for local/dev; this is the Kubernetes-native HA alternative, gated to the Helm render target. |
| 3.7 Maturity model | *(no module)* | `cds maturity` CLI feature reading Plan bindings | — | Not a module — a reporting layer over which of the above contracts a profile binds. Don't file it as a module issue. |

Suggested build order: `log-sink` (`loki`) and the Keycloak identity module
first — both have existing issues/scaffolding and no Kubernetes dependency
blocking them. TLS (`traefik`) next, since #205/#579/#580 are already
blocked on it. GitOps, metrics, backup, and the Postgres operator can follow
in parallel; Istio last.

## 8. Next steps

- File issues for the three untracked workstreams — **3.2 GitOps**, **3.6
  database operators**, and **3.7 maturity model** — using the module
  recommendations in section 7, and link them from
  [docs/roadmap.md](roadmap.md)'s Near-Term section.
- Re-file or reopen focused follow-ups for the two 3.1 gaps PR #608 left
  open: NetworkPolicy generation (building on #75) and CI-based cluster
  validation (building on #76).
- Track PR #608 to merge, then close issues #65/#73/#74/#77 as delivered.
