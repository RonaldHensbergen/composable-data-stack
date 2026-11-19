# MVP — Minimum Viable Product, Feature Inventory and Acceptance Criteria

Version: 0.1
Status: Draft — deliverable of action-plan item 2 ("Define the MVP and
acceptance criteria", 17–27 November 2026)
Related: [docs/haven-parity-plan.md](docs/haven-parity-plan.md),
[docs/plan/k8s-haven-mvp.md](docs/plan/k8s-haven-mvp.md),
[docs/okdp-stackable-uwv-checkin.md](docs/okdp-stackable-uwv-checkin.md),
[docs/roadmap.md](docs/roadmap.md), [README.md](README.md)

## 1. Purpose and method

This document is the action-plan item 2 deliverable: an `MVP.md`, a feature
inventory, a testable acceptance checklist, and a concise Stackable/OKDP
comparison — in sections 2–5 below.

The method is borrowed from the [Haven parity plan](docs/haven-parity-plan.md):
**the MVP is defined from capabilities already implemented, not from an
aspirational feature list.** The Haven+ platform baseline (GitOps,
observability, cert management, service mesh, database operators, backup)
informs the roadmap — it is *not* MVP scope. What *is* MVP scope is the
minimum deployable use case that can be demonstrated, reviewed and supported
today, on both render targets CDS already ships.

## 2. The MVP: minimum deployable use case

> A new user on a fresh machine with only Docker installed can install the
> `cds` CLI from PyPI, fetch a published reference profile without cloning the
> repository, and stand up a working, contract-wired local data stack —
> Postgres warehouse, Dagster orchestration, Superset BI, KeyDB cache —
> through the full lifecycle (`init` → `test` → `up` → `state` → `down`),
> with fail-closed validation and security checks at every static gate. The
> same profile renders and runs on a local Kubernetes cluster via
> `--target helm`. The Haven-shaped reference profile additionally shows
> identity (Keycloak) and TLS ingress (Traefik) — the sovereignty headline.

Reference profiles that define the MVP surface:

| Profile | Proves |
| --- | --- |
| `profiles/local-dagster-postgres-superset` | the core stack on Compose (the default target) |
| `profiles/team-dagster-postgres-superset` (+ `prod` overlay) | the same stack with the production security policy, fail-closed gates (`E139`/`E140`/`E142`/`E143`, backup policies) |
| `profiles/local-haven-reference` | Haven-shaped reference: Keycloak `oidc-provider` behind Traefik TLS ingress, on both Compose and `--target helm` (#1132, #1140) |
| `profiles/local-kubernetes-example` / `profiles/production-kubernetes-example` | the core stack on the Helm target with k3d lifecycle (#608) |

What "deployable" means concretely: `cds get <profile> --remote ... --into <dir>` fetches everything (with a `.cds/get-manifest.json` provenance record),
`cds init` generates the secrets file, `cds test` runs validate → security →
plan → render green, `cds up` starts the stack, `cds state` reports health,
`cds down` tears it down. All of this from public documentation alone — no
source checkout required.

## 3. Feature inventory (implemented as of v0.15.1)

### 3.1 Compile pipeline and CLI

- Pipeline `load → validate → plan → render` (`cli/validator.py`,
  `cli/planner.py`, `cli/renderer.py`), with structured `E###`/`W###`
  diagnostics and fail-closed staging.
- Commands: `validate`, `plan`, `render`, `up`, `down`, `state`, `test`,
  `security`, `preflight`, `maturity`, `report`, `init`, `get`, `update`,
  `list` (profiles/modules/images), `config`, `use`, `diff`,
  `generate-profile`, `compose-profile` — see
  [docs/cli-reference.md](docs/cli-reference.md).

### 3.2 Render targets

- **Docker Compose** (default, stable): full lifecycle via the real
  `docker compose` executable.
- **Kubernetes/Helm** (`--target helm`): `spec.implementation.kubernetes`
  rendered independently from the same plan (ADR 0001), Helm chart output,
  NetworkPolicies, pod/container hardening (`cli/k8s_security.py`), k3d E2E
  harness (#608, #75).

### 3.3 Modules and contracts

- Production modules (12): `orchestration/dagster`, `warehouse/postgres`,
  `warehouse/duckdb`, `bi/superset`, `cache/keydb`, `secrets/vault`,
  `transformation/dbt`, `identity/keycloak`, `integration/traefik`,
  `security/istio`, `backup/restic`, `storage/garage`.
- Experimental modules (10): fluxcd, loki, grafana, otel-collector, marquez,
  opa, dlt, airflow, postgres-operator, object-storage under
  `modules-experimental/`.
- 18 vendor-neutral contract kinds under `shared/contracts/` (`sql-database`,
  `http-service`, `oidc-provider`, `service-mesh`, `log-sink`,
  `metrics-sink`, `trace-sink`, `backup-target`, `gitops-target`, …) — see
  [docs/contract-coverage.md](docs/contract-coverage.md).

### 3.4 Security, compliance and evidence

- Rule-based `cds security` checks (CDS-SEC-
  *), host `cds preflight` advisories, Kubernetes `CDS-K8S-*` rules;
  secrets never resolved into plans or rendered output (only `${CDS_*}`
  placeholders); secret-leak scan in `cds report`.
- `cds maturity` tier report (Basic/Automated/Observed/Secured/Resilient)
  over resolved platform contracts (#769, #1059).
- CRA readiness evidence pack, SBOM + signed published images, audit log,
  weekly release train ([docs/release-strategy.md](docs/release-strategy.md)).

### 3.5 Example profiles

12 profiles under `profiles/`, including the four reference profiles in
section 2; CI validates/renders the whole matrix.

## 4. Acceptance checklist

Every criterion has an objective test and an owner. "Green" means exit code 0
with the documented expected output; any deviation is filed as an issue with
severity, not waived silently (the rule from
[docs/plan/k8s-haven-mvp.md](docs/plan/k8s-haven-mvp.md) §11).

| ID | Category | Criterion | Objective test | Owner |
| --- | --- | --- | --- | --- |
| F1 | functional | CLI installs from PyPI and runs without a source checkout | fresh dir: `pipx install composable-data-stack && cds --version` | CI install smoke |
| F2 | functional | Profile fetch without cloning, with provenance | `cds get local-dagster-postgres-superset --remote RonaldHensbergen/composable-data-stack --into <dir>`; `.cds/get-manifest.json` records commit SHA + hashes | maintainer |
| F3 | functional | Secrets bootstrap is complete and safe | `cds init <profile>` writes `.env` mode 0600 with every required `CDS_*` key | maintainer |
| F4 | functional | Full static pipeline passes on the reference stack | `cds test local-dagster-postgres-superset` → validate/security/plan/render all `[PASS]` | CI (profile matrix) |
| F5 | functional | Compose lifecycle works end to end | `cds up` → `cds state` all healthy → Dagster/Superset UIs answer on their published ports → `cds down` clean | `tests/test_compose_runtime_smoke` (docker smoke CI) |
| F6 | functional | Helm lifecycle works on a local cluster | `cds test/up/state/down local-kubernetes-example --target helm` on k3d; `helm lint`/`template` clean; boundary checks of `scripts/k8s/e2e.sh` pass | `make k3d-e2e` |
| F7 | functional | Haven-shaped reference renders on both targets | `cds render local-haven-reference [--target helm]` green; Keycloak reachable behind Traefik TLS ingress | `tests/test_local_haven_reference_profile.py` |
| S1 | security | Security stage is fail-closed on weak config | `cds security` rejects placeholder/reused secrets (CDS-SEC-012/013) and reports only the documented snapshot on reference profiles | CI |
| S2 | security | No secret values in plans or rendered output | rendered Compose/chart contain only `${CDS_*}`/`$(CDS_*)` placeholders; `cds report --hardened` secret-leak check `status=pass` | `tests/test_secret_output_scanning.py` |
| S3 | security | Production overlay enforces its gates | `cds test team-dagster-postgres-superset --environment prod` fails closed on E139/E140/E142/E143 violations; passes when compliant | CI (profile matrix) |
| S4 | security | Maturity report reflects declared contracts | `cds maturity <profile> --format json` reports the expected tier with justifying contracts | unit tests `tests/test_maturity*.py` |
| P1 | deployment | Fresh-machine run reproduces from docs alone | Path A runbook of [docs/plan/k8s-haven-mvp.md](docs/plan/k8s-haven-mvp.md) Gate 0–1 executed from README/docs only, evidence recorded | maintainer |
| P2 | deployment | Images resolvable on both targets | `build` (local import) and `registry` (published signed images) source paths both render and start | CI (image publish + smoke) |
| P3 | deployment | Teardown is clean | `cds down` leaves no workloads; PVCs retained by default (`--delete-pvcs` opt-in) | e2e tests |
| DOC1 | documentation | Quickstart works as written | README Quickstart copy-paste green on a clean machine | maintainer |
| DOC2 | documentation | Error codes are documented | every `E###`/`W###` in README troubleshooting table maps to a cause + fix; codes collision-free with `main` | CI (`scripts/check_*` + lint) |
| DOC3 | documentation | CLI reference matches implementation | `cds --help` per command matches [docs/cli-reference.md](docs/cli-reference.md) | CI |

## 5. Landscape comparison: Stackable and OKDP (concise)

Full check-in with sources and twelve lessons:
[docs/okdp-stackable-uwv-checkin.md](docs/okdp-stackable-uwv-checkin.md)
(follow-ups #1281–#1285). Summary:

| Dimension | CDS (MVP) | Stackable | OKDP |
| --- | --- | --- | --- |
| Runtime | Docker Compose **and** Kubernetes/Helm — local-first, no cluster required | Kubernetes only (incl. OpenShift) | Kubernetes only |
| Packaging model | Profiles compile to one Compose file or one Helm chart; contracts wire modules | One operator per product, product CRDs, `stackablectl` | Standalone Helm chart per component, optional control plane/ArgoCD |
| Composition | Declarative `profile.yaml` + vendor-neutral contracts (`shared/contracts/`); component swap without rewrite | Curated product set, uniform CR shape, daily interoperability testing | Modular charts combinable via GitOps |
| Governance | Single-maintainer OSS project (this repo) | Company-backed (Stackable GmbH) + community | Community under TOSIT association |
| Security model | Fail-closed static gates (`cds security`/`preflight`), maturity tiers, CRA evidence pack | Uniform authn/authz (LDAP/OIDC/OPA) per product | Left to the deployer/GitOps tooling |
| Maturity signal | `cds maturity` reports profile tier from resolved contracts | Release-train versioning, signed images | Public roadmap |

**Architectural overlap:** all three are Kubernetes-shaped, contract/CR-driven
and vendor-neutral in intent. **CDS differentiators:** the dual render target
(Compose for local/dev without a cluster), the compiler model
(validate → plan → render with structured diagnostics) instead of an operator
platform, and the profile-facing maturity model. **Maturity gaps vs both:**
operator-managed products, daily cross-version interoperability testing,
published per-release tested module matrix (#1283), and a control-plane UI.
**Reuse/collaboration opportunities:** adopt OKDP-style standalone-chart
packaging for rendered output (#1282) and learn from Stackable's uniform
authn/authz configuration across products when designing the OIDC/OPA
consumption pattern.

## 6. Out of scope for the MVP

- **Automated contract generation** (AI-assisted `module.yaml`/contract
  scaffolding beyond the existing `scripts/compose_to_module.py`).
- **Additional deployment backends**: Podman/Quadlet (#1303), Docker Swarm
  (#1304), Ansible (#1305), Nix/NixOS (#1306), Incus — assessed in the
  action-plan item 4 infrastructure review, committed only with evidence and
  capacity.
- **`CDSProfile` CRD/operator** — explicitly deferred in
  [ADR 0001](docs/adr/0001-kubernetes-render-target.md).
- **Certified Haven+ compliance** — Haven parity means "baseline capabilities
  present or tracked as issues", per the non-goals of
  [docs/haven-parity-plan.md](docs/haven-parity-plan.md); the full
  `production-haven` platform profile (#1242) is the parity target, not the
  MVP.
- Multi-node, multi-cloud or bare-metal clusters; one local k3d cluster is
  the MVP Kubernetes target.

## 7. Mapping to the Haven parity plan

Loose correspondence between [docs/haven-parity-plan.md](docs/haven-parity-plan.md)
workstreams and the MVP:

| Parity workstream | In MVP? | Where |
| --- | --- | --- |
| 3.1 Kubernetes render target | yes | section 3.2; acceptance F6 |
| 3.7 Maturity model | yes (reporting only) | section 3.4; acceptance S4 |
| 3.4 Identity + TLS ingress (Keycloak/Traefik) | yes, reference shape | `local-haven-reference`; acceptance F7 |
| 3.2 GitOps, 3.3 observability providers, 3.5 backup provider, 3.6 operator warehouse | no — post-MVP roadmap | tracked by #767, #369/#662/#777, #210/#665/#668/#669, #768; full profile = #1242 |
| CI cluster validation | no — follow-up | #76 |
