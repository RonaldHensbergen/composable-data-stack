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
  - #678 Fix `generate-profile` accepting multi-segment names `cds list profiles` can't discover
  - #686 Wrap malformed compose YAML parse errors in `ScaffoldError`
  - #506 Fix `cds get --ref` being silently ignored when combined with `--local`
  - #507 Improve `cds get` error message on unexpected GitHub tarball layout
  - #559 Add renderer-level test coverage for `ifNonempty:` interpolation
- **`help wanted`** — scoped, but needs a design decision or more context read
  first; ask on the issue before starting:
  - #357 Implement or remove CDS-SEC-032 (secret file permission checks)
  - #356 Implement or remove CDS-SEC-006/030/071 (secret scanning of generated artifacts and CLI output)
  - #142 Add a runnable Airflow profile example
- **`area:docs`** — low risk, no test suite to satisfy:
  - #77 Publish Kubernetes docs, migration guide, and example profiles
  - #72 Document and test the production hardening checklist
- **`priority:high` / `priority:medium`** — higher-impact work for
  contributors who want to go deeper; check for an in-progress PR first:
  - #211 Define Docker host and daemon hardening baseline
  - #210 Add backup, restore, and disaster-recovery contracts
  - #207 Add runtime confinement and rootless deployment policies
  - #206 Enforce database and cache authentication policies
  - #205 Add TLS and certificate-management contracts
  - #625 Lock pip dependencies to verified hashes across all Dockerfiles
  - #557 Fix `ifNonempty:` expression crashing instead of producing a diagnostic

New capability modules and contracts (S3-compatible object storage, log-sink,
Keycloak realm/config, Grafana, DuckDB profile wiring, etc.) are tracked with
`enhancement` + `area:configuration`; browse those labels for open module
requests that don't yet have a `help wanted` tag.

If you plan to start on any issue above, comment on it first so effort isn't
duplicated — none are currently assigned.

---

## Stable Components

These are considered production-ready in the current release (v0.9.0):

- `cds validate` — module and contract validation
- `cds plan` — dependency resolution and execution planning
- `cds render` — Docker Compose configuration generation (`--target compose`, the default)
- `cds security` — configuration security checks
- `cds up` / `cds down` / `cds state` — Docker Compose lifecycle management
- `cds test` — runs validate → security → plan → render in sequence; exercised in CI
- `cds get` — fetch a profile and its module/runtime assets from a GitHub repository (`--remote`, `--ref`, `--local`), with tracking metadata under `.cds/`
- `cds generate-profile` — persist a runtime/programmatically composed profile so it can be validated and planned like any hand-authored one
- `cds list profiles` / `cds list modules` / `cds list images` — discover what's available locally or in a remote repository (`--remote`, `--ref`, `--local`)
- `cds config` (`get`/`set`/`unset`/`list`) and `cds use` — persisted project-level defaults (`profile`, `environment`, `security.strict`)
- `cds diff` — show effective configuration differences between two environment overlays of a profile (`--from`/`--to`)
- Module: Dagster (`modules/orchestration/dagster/`)
- Module: Postgres (`modules/warehouse/postgres/`)
- Module: Superset (`modules/bi/superset/`)
- Module: KeyDB (`modules/cache/keydb/`)
- Module: Vault (`modules/secrets/vault/`)
- Module: dbt (`modules/transformation/dbt/`) — promoted from experimental with production-suitable hardening (#594)
- Module: Traefik (`modules/integration/traefik/`) — TLS-enforcing reverse proxy; not yet wired into a stable profile
- Profile: `local-dagster-postgres-superset`
- Profile: `local-dagster-postgres-superset-dbt`
- Image supply chain: signed, SBOM- and provenance-attested image publishing to both Docker Hub and GHCR, with `--hardened` variant selection and `image.source: build|registry` per module

---

## Experimental Components

These work but may have breaking changes in upcoming releases:

- **Kubernetes/Helm rendering target** (`--target helm` on `render`/`validate`/`security`/`up`/`down`/`state`/`test`) — `cli/k8s_renderer.py`, `cli/k8s_security.py`, and `cli/k8s_runner.py` render a Helm chart and manage its lifecycle via `helm upgrade --install`; includes a k3d-based local-dev harness and a TenderNed demo profile (#608). Still missing the platform baseline (GitOps, observability, backup, network policy) tracked in [docs/haven-parity-plan.md](haven-parity-plan.md) and issues #689/#690/#75/#77.
- Module: Airflow (`modules-experimental/orchestration/airflow/`) — not yet integrated into a stable profile
- Module: dlt (`modules-experimental/ingestion/dlt/`) — one-shot pipeline job, not yet wired into a stable profile
- Module: DuckDB (`modules-experimental/warehouse/duckdb/`) — embedded/file-based warehouse via the `file-database` contract; wired into dbt (#599), not yet wired into dlt or a demo profile (#593)
- Module: Keycloak (`modules/identity/keycloak/`) — identity/SSO provider running in development mode (`start-dev`), backed by a consumed `sql-database` contract; not yet wired into a stable profile, no realm configuration (#680) or `oidc-provider` contract for other modules to consume (#681)
- Profile: `local-dagster-postgres-superset-vault` — not tested thoroughly yet
- `cds test` — implemented; not yet exercised in CI or real contributor usage
- `scripts/compose_to_module.py` — scaffolds a starter `module.yaml` from an existing `docker-compose.yml`
- `scripts/ai_profile_review.py` — optional AI-assisted guardrail/simplification review for profiles

---

## Near-Term (Next 1–3 Releases)

- 📋 **Stabilize Vault-backed profile** — validate and harden `local-dagster-postgres-superset-vault` for regular use
- 📋 **`cds update`** — refresh profiles, modules, and contracts previously fetched with `cds get` (#348)
- 📋 **Dynamic Composition follow-up**
  - allow runtime-generated profiles for planning and composition beyond `cds generate-profile` (#349)
  - strengthen compatibility validation beyond plain contract `kind` matching (#350)
- 📋 **Kubernetes platform baseline** — close the gap to the full plan in [docs/haven-parity-plan.md](haven-parity-plan.md): Helm target lifecycle parity with Compose (#689), self-contained `cds up --target helm` (#690), K8s secrets/configmaps/network policies (#75), Kubernetes docs/migration guide (#77)

---

## Completed

Selected items shipped since v0.4.0 (see [CHANGELOG.md](../CHANGELOG.md) for the full history):

- ✅ **Kubernetes/Helm rendering target** — new `--target helm` runtime alongside Compose, with a k3d local-dev harness and CI proof (#608)
- ✅ **`cds get`** — fetch profiles/modules from a GitHub repository, with symlink-safe extraction and tracking manifest (#493, #495, #474)
- ✅ **`cds generate-profile`** — persist and validate a runtime-composed profile (#349 groundwork)
- ✅ **dbt promoted to stable** (`modules/transformation/dbt/`) with production-suitable hardening (#594)
- ✅ **`cds config` / `cds use`** — persisted project-level defaults for `profile`, `environment`, `security.strict` (#383, #537)
- ✅ **`cds list --remote/--ref/--local`** — discover profiles/modules/images in a remote repository before fetching (#500)
- ✅ **`--hardened` flag and `image.source: build|registry`** — select the hardened image variant or pull a published registry image without editing profile YAML (#373, #532, #536)
- ✅ **Signed, attested image publishing** — SBOM and SLSA provenance attestation for both Docker Hub and GHCR image pushes (#275, #539)
- ✅ **Compose-injection hardening** — `${config.*}`/`${bindings.*}` substitution can no longer splice a profile-supplied dict/list into compose-dangerous fields (new `E072` diagnostic)
- ✅ **`scripts/compose_to_module.py`** — scaffold a starter module from an existing `docker-compose.yml` (#670)
- ✅ **Schema-backed validation** — profile/module/contract shape enforced via bundled JSON schemas (#413)

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
