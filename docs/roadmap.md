# CDS Roadmap

This document tracks near-term priorities for Composable Data Stack (CDS). Milestones follow the weekly release train documented in [docs/release-strategy.md](release-strategy.md). Items are marked stable or experimental to set contributor and user expectations.

---

## Stable Components

These are considered production-ready in the current release (v0.4.0):

- `cds validate` — module and contract validation
- `cds plan` — dependency resolution and execution planning
- `cds render` — Docker Compose configuration generation
- `cds security` — configuration security checks
- `cds up` — Spinning up the Docker Compose configuration
- Module: Dagster (`modules/orchestration/dagster/`)
- Module: Postgres (`modules/warehouse/postgres/`)
- Module: Superset (`modules/bi/superset/`)
- Module: KeyDB (`modules/cache/keydb/`)
- Module: Vault (`modules/secrets/vault/`)
- Profile: `local-dagster-postgres-superset`

---

## Experimental Components

These work but may have breaking changes in upcoming releases:

- Module: Airflow (`modules-experimental/orchestration/airflow/`) — not yet integrated into a stable profile
- Module: DuckDB (`modules-experimental/warehouse/duckdb/`) — embedded/file-based warehouse via the new `file-database` contract; dbt can now target it (see below), dlt wiring and a demo profile are still pending (#593)
- Module: dbt (`modules-experimental/transformation/dbt/`) — can now optionally target DuckDB via `file-database`, in addition to Postgres via `sql-database`, selected by the `warehouseType` config field (#593)
- `cds test` — implemented; not yet exercised in CI or real contributor usage

---

## Near-Term (Next 1–3 Releases)

- 📋 **Publish CLI to PyPI** — enable `pipx install composable-data-stack` and `pip install composable-data-stack` (#52)
- Profile: `local-dagster-postgres-superset-vault` - not tested thoroughly yet
- 📋 **Stabilize Vault-backed profile** — validate and harden `local-dagster-postgres-superset-vault` for regular use
- 📋 **Profile Retrieval & Update milestone**
  - add tracking metadata for fetched CDS configuration assets
  - add `cds get` to fetch a profile and its dependencies from a repository
  - add `cds update` to refresh tracked profiles, modules, and contracts
- 📋 **Dynamic Composition follow-up**
  - allow runtime-generated profiles for planning and composition
  - strengthen compatibility validation beyond plain contract `kind` matching

---

## Completed

Items shipped in v0.1.1 through v0.4.0:

- ✅ **Docker runtime smoke test CI** — CI workflow for Docker runtime smoke test and MVP proof (#26)
- ✅ **Windows and macOS CI** — expanded CI coverage to include Windows and macOS host jobs (#58)
- ✅ **Windows setup instructions** — Windows setup guide added to README and CONTRIBUTING (#56)
- ✅ **PowerShell task runner** — PowerShell parity for core Makefile targets (#55)
- ✅ **Pre-commit hooks** — markdownlint, yamllint, and flake8 checks enforced locally before push (#31)
- ✅ **Release automation** — automated GitHub release creation on version tags (#32)

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
