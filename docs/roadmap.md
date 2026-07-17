# CDS Roadmap

This document tracks near-term priorities for Composable Data Stack (CDS). Milestones follow the weekly release train documented in [docs/release-strategy.md](release-strategy.md). Items are marked stable or experimental to set contributor and user expectations.

---

## Stable Components

These are considered production-ready in the current release (v0.1.1):

- `cds validate` — module and contract validation
- `cds plan` — dependency resolution and execution planning
- `cds render` — Docker Compose configuration generation
- `cds security` — configuration security checks
- Module: Dagster (`modules/orchestration/dagster/`)
- Module: Postgres (`modules/warehouse/postgres/`)
- Module: Superset (`modules/bi/superset/`)
- Module: Vault (`modules/secrets/vault/`)
- Profile: `local-dagster-postgres-superset`

---

## Experimental Components

These work but may have breaking changes in upcoming releases:

- Module: Airflow (`modules-experimental/orchestration/airflow/`) — not yet integrated into a stable profile
- `cds up` — implemented; not yet validated against a real Docker Compose run
- `cds test` — implemented; not yet exercised in real contributor/CI usage

---

## Near-Term (Next 1–3 Releases)

- 📋 **Docker runtime smoke test CI** — add CI workflow for Docker runtime smoke test (#26)
- 📋 **Publish CLI to PyPI** — enable `pipx install composable-data-stack` and `pip install composable-data-stack` (#52)
- 📋 **Windows and macOS CI** — expand CI coverage to include Windows and macOS host jobs (#58)
- 📋 **Windows setup instructions** — add Windows setup guide to README and CONTRIBUTING (#56)
- 📋 **PowerShell task runner** — PowerShell parity for all Makefile targets (#55)
- 📋 **Pre-commit hooks** — enforce markdown and Python quality checks locally before push (#31)
- 📋 **Release automation** — automate GitHub release creation on version tags (#32)

---

## MVP Test Plan

The [MVP test plan](mvp-test/mvp-test-plan.md) defines the execution order and release gate for the `local-dagster-postgres-superset` profile:

- Use the MVP test plan as the execution order and release gate for this profile.
- Use the roadmap and release docs to decide which blockers found during testing should be fixed immediately versus deferred.
- Pull roadmap items forward only when they unblock a failing MVP test or reduce release risk for that exact profile.

---

## Contributing

See [CONTRIBUTING.md](../CONTRIBUTING.md) and [docs/good-first-issues.md](good-first-issues.md) for how to get started.
