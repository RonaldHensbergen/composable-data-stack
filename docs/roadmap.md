# CDS Roadmap

This document tracks near-term priorities for Composable Data Stack (CDS). Milestones follow the weekly release train documented in [docs/release-strategy.md](release-strategy.md). Items are marked stable or experimental to set contributor and user expectations.

A Kubernetes/Helm rendering target now exists (see Experimental Components below). For the longer-term plan to close the gap to a full Haven+-style platform (observability, security, backup, GitOps modules) and a profile maturity model, see [docs/haven-parity-plan.md](haven-parity-plan.md).

---

## Ready For Contributors

Every issue below is open and unassigned. Filter the
[issue tracker](https://github.com/RonaldHensbergen/composable-data-stack/issues)
by label to find work matching your available time and familiarity with the
codebase; see [docs/good-first-issues.md](good-first-issues.md) for the
step-by-step contribution flow. Note: this repo intentionally does not use a
`good first issue` label — it drew a high volume of low-quality, AI-generated
PRs. Use the groupings below and the issue's own Acceptance Criteria to judge
scope instead.

- **Self-contained, low-risk** — small, well-scoped, no deep
  planner/renderer/contract knowledge required:
  - #535 Document `image.source` (build\|registry) in `docs/modules.md` and README (docs-only)
- **`help wanted`** — scoped, but needs a design decision or more context read
  first; ask on the issue before starting:
  - #142 Add a runnable Airflow profile example

New capability modules and contracts (S3-compatible object storage, log-sink,
Keycloak realm/config, Grafana, etc.) are tracked with
`enhancement` + `area:configuration`; browse those labels for open module
requests that don't yet have a `help wanted` tag.

If you plan to start on any issue above, comment on it first so effort isn't
duplicated — none are currently assigned.

---

## Stable Components

These are considered production-ready in the current release (v0.15.1):

- `cds validate` — module and contract validation
- `cds plan` — dependency resolution and execution planning
- `cds render` — Docker Compose configuration generation (`--target compose`, the default)
- `cds security` — configuration security checks
- `cds up` / `cds down` / `cds state` — Docker Compose lifecycle management
- `cds test` — runs validate → security → plan → render in sequence; exercised in CI
- `cds get` — fetch a profile and its module/runtime assets from a GitHub repository (`--remote`, `--ref`, `--local`), with tracking metadata under `.cds/`
- `cds generate-profile` — persist a runtime/programmatically composed profile so it can be validated and planned like any hand-authored one
- `cds compose-profile` — merge a new module instance into an existing profile, resolving unambiguous contract bindings/dependencies automatically and reporting ambiguous or missing ones (#807)
- `cds list profiles` / `cds list modules` / `cds list images` — discover what's available locally or in a remote repository (`--remote`, `--ref`, `--local`)
- `cds config` (`get`/`set`/`unset`/`list`) and `cds use` — persisted project-level defaults (`profile`, `environment`, `security.strict`)
- `cds diff` — show effective configuration differences between two environment overlays of a profile (`--from`/`--to`)
- Module: Dagster (`modules/orchestration/dagster/`)
- Module: Postgres (`modules/warehouse/postgres/`)
- Module: Superset (`modules/bi/superset/`)
- Module: KeyDB (`modules/cache/keydb/`)
- Module: Vault (`modules/secrets/vault/`)
- Module: dbt (`modules/transformation/dbt/`) — promoted from experimental with production-suitable hardening (#594)
- Module: Istio service mesh (`modules/security/istio/`) — production-suitable Helm-only provider of the `service-mesh` contract, enrolled by the production and local Kubernetes example profiles (#1171)
- Module: DuckDB (`modules/warehouse/duckdb/`) — promoted from experimental (#593); embedded/file-based warehouse via the `file-database` contract, consumed by dlt and dbt
- Module: Traefik (`modules/integration/traefik/`) — TLS-enforcing reverse proxy; not yet wired into a stable profile
- Profile: `local-dagster-postgres-superset`
- Profile: `local-dagster-postgres-superset-dbt`
- Profile: `local-duckdb-dlt-dbt` — local-analytics demo with DuckDB as the warehouse, loaded by dlt and transformed by dbt (#593)
- Profile: `production-kubernetes-example` — production-style Helm stack with STRICT Istio mTLS enrollment for Postgres, Dagster, and Superset (#1171)
- Profile: `local-kubernetes-example` — k3d Helm example with STRICT Istio mTLS enrollment, exercised by Kubernetes E2E (#1171)
- Image supply chain: signed, SBOM- and provenance-attested image publishing to both Docker Hub and GHCR, with `--hardened` variant selection and `image.source: build|registry` per module

---

## Experimental Components

These work but may have breaking changes in upcoming releases:

- **Kubernetes/Helm rendering target** (`--target helm` on `render`/`validate`/`security`/`up`/`down`/`state`/`test`) — `cli/k8s_renderer.py`, `cli/k8s_security.py`, and `cli/k8s_runner.py` render a Helm chart and manage its lifecycle via `helm upgrade --install`; includes a k3d-based local-dev harness and a TenderNed demo profile (#608). The platform baseline once listed here as missing — GitOps (#767), observability (#777), backup (#210), network policies (#75), self-contained `cds up --target helm` (#690), and Kubernetes docs/example profiles (#77) — has shipped (see Completed below); remaining gaps to a full Haven+-style platform are tracked in [docs/haven-parity-plan.md](haven-parity-plan.md).
- Module: Airflow (`modules-experimental/orchestration/airflow/`) — not yet integrated into a stable profile
- Module: dlt (`modules-experimental/ingestion/dlt/`) — one-shot pipeline job, not yet wired into a stable profile
- Module: Keycloak (`modules/identity/keycloak/`) — identity/SSO provider with production web serving by default (`config.webServer.mode`, #216/#974; `development`/`start-dev` is an explicit local-only opt-in rejected in production profiles by E140), backed by a consumed `sql-database` contract and providing `oidc-provider`; wired into the `local-haven-reference` profile (#772), remains experimental pending its `--target helm` implementation (#1075)
- Module: Marquez (`modules-experimental/observability/marquez/`) — OpenLineage-compatible lineage backend, reference provider for the `lineage-sink` contract; demoed by `local-dagster-postgres-marquez` (#779)
- Module: Loki (`modules-experimental/observability/loki/`) — experimental `log-sink` provider over Loki's native OTLP/HTTP endpoint (`/otlp/v1/logs`, with `connectionUri` since #1076) plus a host-loopback Vector JSON gateway, and Loki retention tiers (#662)
- Module: Grafana (`modules-experimental/observability/grafana/`) — experimental dashboarding UI providing `http-service`, with an optional `sql-database` binding provisioned as a Postgres datasource and an optional `metrics-sink` binding (protocol `prometheus`) provisioned as a Prometheus datasource; demoed by `local-postgres-grafana` (#369)
- Module: FluxCD (`modules-experimental/gitops/fluxcd/`) — experimental reference provider of the new vendor-neutral `gitops-target` contract (`shared/contracts/gitops-target.yaml`); renders Flux `GitRepository`/`Kustomization`/`HelmRelease` bootstrap manifests over the `--target helm` chart output via the new `implementation.kubernetes.manifests` block (#767, see the "GitOps handoff" section of [docs/kubernetes.md](kubernetes.md))
- Module: postgres-operator (`modules-experimental/warehouse/postgres-operator/`) — experimental CloudNativePG-backed warehouse providing the same `sql-database` contract as `modules/warehouse/postgres`, Kubernetes-only (`--target helm`); renders a `Cluster` CR plus the app-credentials Secret and declares the operator-created `<cluster>-rw` Service via the new `implementation.kubernetes.services` block (#768, demoed by the `local-kubernetes-postgres-operator` profile below, see [docs/kubernetes.md](kubernetes.md#operator-managed-modules))
- Profile: `local-dagster-postgres-superset-vault` — not tested thoroughly yet
- Profile: `local-haven-reference` — Common Ground/Haven-aligned reference stack (identity, TLS ingress, database), experimental pending the Keycloak/Traefik `--target helm` implementations (#772, #1075, see [docs/common-ground-alignment.md](common-ground-alignment.md))
- Profile: `local-kubernetes-postgres-operator` — warehouse on the experimental CloudNativePG operator module with Superset/KeyDB binding via the unchanged `sql-database` contract, rendered with `--target helm` (#768)
- Profile: `local-postgres-grafana` — minimal Postgres + experimental Grafana demo wiring the `sql-database` contract into a provisioned Grafana datasource (#369)
- `scripts/compose_to_module.py` — scaffolds a starter `module.yaml` from an existing `docker-compose.yml`
- `scripts/ai_profile_review.py` — optional AI-assisted guardrail/simplification review for profiles

---

## Near-Term (Next 1–3 Releases)

- 📋 **Stabilize Vault-backed profile** — validate and harden `local-dagster-postgres-superset-vault` for regular use

---

## Cyber Resilience Act Readiness

Tracked under the **Cyber Resilience Act readiness** milestone: proactive
engineering records for CDS's own product-security posture, not a claim of
CRA scope or conformity (see [docs/cra-scope-decision.md](cra-scope-decision.md)):

- ✅ **Scope decision, incident runbook, SBOM/release inventory, support
  policy, compliance report** — #728, #729, #730, #731, #734, #735 (see
  [docs/cra-incident-runbook.md](cra-incident-runbook.md),
  [docs/security-support-policy.md](security-support-policy.md) and
  [docs/compliance-report.md](compliance-report.md))
- ✅ **Risk assessment and technical documentation** — product-lifecycle risk
  register, Annex I traceability matrix and Annex VII index, with a CI check
  (#732, see [docs/cra-risk-assessment.md](cra-risk-assessment.md) and
  [docs/cra-technical-documentation.md](cra-technical-documentation.md))
- ✅ **CRA release evidence gate** — release-evidence manifest and fail-closed
  conformity gate, disabled by default, with draft Annex II/V/VI templates
  (#733, see [docs/cra-release-evidence-gate.md](cra-release-evidence-gate.md))
- 📋 **CLI self-update notification** — notify users when a newer CDS CLI
  release is available (#841)
- ✅ **`cds get` source trust** — opt-in trusted-source allowlist and commit
  SHA pinning for fetched bundles (#849)

---

## Dutch Public-Sector Alignment (FDS · Common Ground · NIS2)

Tracked under the **Dutch Public-Sector Data & Security Alignment** milestone,
distinct from the CRA product-security milestone above (see
[Cyber Resilience Act Readiness](#cyber-resilience-act-readiness)) — these address the
Federatief Datastelsel/IBDS data-sharing framework, Common Ground/Haven
platform conventions, and Cyberbeveiligingswet/NIS2 operator obligations:

- ✅ **FDS data-provider alignment** — map CDS's `provides`/`consumes` contract
  model to the Federatief Datastelsel Afsprakenstelsel's data-provider
  basisafspraken; identify any missing shared contract (#773, see
  [docs/fds-alignment.md](fds-alignment.md)). Gap closed by a new shared
  contract expressing FDS-required dataset provider metadata (#825)
- ✅ **Common Ground / Haven reference profile** — mapping doc and a
  reference profile combining existing modules (identity, TLS, database) in
  a Haven-aligned shape (#772, see [docs/common-ground-alignment.md](common-ground-alignment.md)
  and [profiles/local-haven-reference](../profiles/local-haven-reference))
- ✅ **Cyberbeveiligingswet/NIS2 scope** — record CDS's role as a software
  supplier vs. the operator-level obligations of Cyberbeveiligingswet/NIS2
  Article 21, cross-referenced against the CRA milestone so the two are not
  conflated (#771, see [docs/nis2-cyberbeveiligingswet-scope.md](nis2-cyberbeveiligingswet-scope.md))
- ✅ **NIS2-aligned `cds security` reporting** — extend the rule-set with
  operator-readiness checks per Article 21 measure category, gated on the
  scoping issue above (#774)
- ✅ **Stale pinned-digest warning** — warn when a profile's pinned image
  digest is behind the latest published, patched digest; cited by the NIS2
  scoping issue's supply-chain-security gap (#736, #758)
- ✅ **Local audit trail** — retain a local audit trail of rendered/applied
  stacks for user incident evidence, supporting NIS2 incident-reporting
  timelines (#737, see [docs/audit-log.md](audit-log.md))
- ✅ **OpenLineage data-lineage for Dagster** — add a `lineage-sink` contract
  and wire Dagster's built-in OpenLineage integration to it, in support of
  IBDS/FDS "verantwoord datagebruik" provenance expectations; coordinate
  with the FDS mapping above (#779, #831; FDS follow-up #843)
- ✅ **FDS data-provider contract** — add a shared `open-data-provider`
  contract expressing FDS-required dataset provider metadata (description,
  classification, access conditions), the gap identified by the FDS mapping
  above; the `postgres` module demonstrates providing it via an opt-in
  `config.dataProvider` block (#825)

---

## Completed

Selected items shipped since v0.4.0 (see [CHANGELOG.md](../CHANGELOG.md) for the full history):

- ✅ **Docker host and daemon hardening baseline** — `HOST-001..006` advisory checks in `cds preflight` and the operator baseline in [docker-host-hardening.md](docker-host-hardening.md) (#211)
- ✅ **Runtime confinement and rootless deployment policies** — production runtime policy (`CDS-SEC-090`/`091`), waivers with documented reasons, and [runtime-confinement.md](runtime-confinement.md) (#207)
- ✅ **Database and cache authentication policies** — trust authentication rejected in production profiles (`E139`), with per-service credentials provisioned through secrets (#206)
- ✅ **TLS and certificate-management contracts** — shared reverse-proxy contract with TLS termination at the edge and certificate-reference validation (#205)
- ✅ **Hash-locked pip installs** — the shipped Dockerfiles install Python dependencies from sha256-pinned requirement files (#625)
- ✅ **Compose/Helm lifecycle parity** — `cds up`/`down`/`state` behave consistently across the Compose and Helm targets (#689)
- ✅ **Kubernetes/Helm rendering target** — new `--target helm` runtime alongside Compose, with a k3d local-dev harness and CI proof (#608)
- ✅ **Strengthened contract compatibility validation** — bundled compatibility registry flags module pairings explicitly recorded as unsupported for a contract (`E043`), beyond plain contract `kind` matching (#350)
- ✅ **`cds get`** — fetch profiles/modules from a GitHub repository, with symlink-safe extraction and tracking manifest (#493, #495, #474)
- ✅ **`cds generate-profile`** / **`cds compose-profile`** — persist a runtime-composed profile, and merge a new module instance into an existing profile with automatic contract-binding/dependency resolution (#349, #807)
- ✅ **dbt promoted to stable** (`modules/transformation/dbt/`) with production-suitable hardening (#594)
- ✅ **`cds config` / `cds use`** — persisted project-level defaults for `profile`, `environment`, `security.strict` (#383, #537)
- ✅ **`cds list --remote/--ref/--local`** — discover profiles/modules/images in a remote repository before fetching (#500)
- ✅ **`--hardened` flag and `image.source: build|registry`** — select the hardened image variant or pull a published registry image without editing profile YAML (#373, #532, #536)
- ✅ **Signed, attested image publishing** — SBOM and SLSA provenance attestation for both Docker Hub and GHCR image pushes (#275, #539)
- ✅ **Compose-injection hardening** — `${config.*}`/`${bindings.*}` substitution can no longer splice a profile-supplied dict/list into compose-dangerous fields (new `E072` diagnostic)
- ✅ **`scripts/compose_to_module.py`** — scaffold a starter module from an existing `docker-compose.yml` (#670)
- ✅ **Schema-backed validation** — profile/module/contract shape enforced via bundled JSON schemas (#413)
- ✅ **`cds update`** — refresh profiles, modules, contracts, and runtime assets previously fetched with `cds get`, reporting and preserving locally modified files (#348)
- ✅ **Self-contained `cds up --target helm`** — one-command Helm lifecycle: cluster reachability check (with k3d cluster creation), local image builds, and image import before `helm upgrade --install` (#690)
- ✅ **Kubernetes production resources** — default-deny NetworkPolicy and ServiceAccount generation on the Helm target, with allow rules derived from the plan's contract bindings (#75)
- ✅ **Kubernetes docs and example profiles** — "Migrating from Docker Compose" guide plus the `local-kubernetes-example` and `production-kubernetes-example` profiles (#77)
- ✅ **Production hardening checklist and verification procedure** — every hardening control mapped to CDS enforcement or operator responsibility, with a reproducible `cds validate` → `cds up` command sequence (#72)
- ✅ **Backup, restore, and disaster recovery** — backup/restore contracts with an automated restore-verification runtime proof (#210)
- ✅ **GitOps rendering** — vendor-neutral `gitops-target` contract and FluxCD reference module rendering Flux bootstrap manifests over the Helm chart output (#767)
- ✅ **Kubernetes-native database operator pattern** — CloudNativePG-backed `postgres-operator` module providing the `sql-database` contract on `--target helm` (#768)
- ✅ **`cds maturity`** — read-only profile maturity report across the Basic/Automated/Observed/Secured/Resilient tiers (#769, #1059)
- ✅ **Service mesh with mutual TLS** — `service-mesh` contract plus the Istio reference module promoted to production-suitable (`modules/security/istio/`) (#776, #1171)
- ✅ **Zero-Trust Architecture framing** — consolidated maturity tier over the existing TLS/identity/mesh/network-policy work, mapped to NIST SP 800-207 (#778)
- ✅ **`trace-sink`/`metrics-sink` contracts and OpenTelemetry Collector** — vendor-neutral sink contracts plus the experimental `otel-collector` module providing `log-sink`/`metrics-sink`/`trace-sink` over OTLP/HTTP (#777)

Items shipped in v0.1.1 through v0.4.0:

- ✅ **Docker runtime smoke test CI** — CI workflow for Docker runtime smoke test and MVP proof (#26)
- ✅ **Windows and macOS CI** — expanded CI coverage to include Windows and macOS host jobs (#58)
- ✅ **Windows setup instructions** — Windows setup guide added to README and CONTRIBUTING (#56)
- ✅ **PowerShell task runner** — PowerShell parity for core Makefile targets (#55)
- ✅ **Pre-commit hooks** — markdownlint, yamllint, and flake8 checks enforced locally before push (#31)
- ✅ **Release automation** — automated GitHub release creation on version tags (#32)
- ✅ **Publish CLI to PyPI** — enable `pipx install composable-data-stack` and `pip install composable-data-stack` (#52)

---

## Profile Test Plan

The [profile test plan](profile-testing/test-plan.md) defines reusable
regression coverage for `local-dagster-postgres-superset`:

- Use the profile test plan as the execution order for this profile.
- Use the roadmap and release docs to decide which blockers found during testing should be fixed immediately versus deferred.
- Pull roadmap items forward only when they unblock a failing profile test or
  reduce release risk for that profile.

---

## Contributing

See [CONTRIBUTING.md](../CONTRIBUTING.md) and [docs/good-first-issues.md](good-first-issues.md) for how to get started.
