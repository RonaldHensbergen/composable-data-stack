# Local Postgres + OPA + Superset (governed warehouse)

Local development stack demonstrating policy-as-code at the data layer
(issue #1284). Postgres declares its `sql-database` contracts **governed**
(`config.governed: true`), the experimental `opa` module serves the
version-controlled Rego policies from `./policies` through the
provider-neutral `data-access-policy` contract, and `superset` binds that
policy for both of its database consume entries via `policyFrom`.
`cds validate` / `cds test` accept this wiring and fail closed with E167
when a governed contract's consumer binds no policy.

## Purpose

Reference profile for the `data-access-policy` contract
(`shared/contracts/data-access-policy.yaml`) and its reference
implementation module (`modules-experimental/security/opa`). See
[docs/data-access-policy.md](../../docs/data-access-policy.md).

## Prerequisites

- Python 3.14+ with the CDS CLI installed, see [docs/installation.md](../../docs/installation.md)
- Docker Engine with the Compose v2 plugin
- The `init-db.sh` script in this directory (bind-mounted into postgres on
  first boot) and the `policies/` directory (bind-mounted read-only into
  the policy engine)

## Required secrets

| Secret alias | Environment variable |
| --- | --- |
| `postgres_superuser_password` | `CDS_POSTGRES_SUPERUSER_PASSWORD` |
| `analytics_db_password` | `CDS_ANALYTICS_DB_PASSWORD` |
| `superset_db_password` | `CDS_SUPERSET_DB_PASSWORD` |
| `superset_secret_key` | `CDS_SUPERSET_SECRET_KEY` |
| `superset_admin_password` | `CDS_SUPERSET_ADMIN_PASSWORD` |

Non-secret config values also come from env: `CDS_ANALYTICS_DB_NAME`,
`CDS_ANALYTICS_DB_USER`, `CDS_SUPERSET_DB_NAME`, `CDS_SUPERSET_DB_USER`.
Run `cds init local-postgres-opa-superset` to generate the `.env` file.

## How the governance check works

1. `postgres` sets `governed: true`, so every `sql-database` provide entry
   of that instance advertises `governed: true` in its contract spec.
2. `superset`'s `metadata-database` and `analytics-database` consume entries
   declare `policyFrom: spec.config.metadataDataPolicy` /
   `spec.config.analyticsDataPolicy`, bound here to
   `policy.data-access-policy`.
3. `cds validate` fails closed with E167 if a consumer of a governed
   contract declares no `policyFrom` or binds a non-`data-access-policy`
   contract. Remove the `analyticsDataPolicy` binding from this profile and
   run `cds validate local-postgres-opa-superset` to see it.
4. At render time the bound policy endpoint appears in the superset service
   as `CDS_ANALYTICS_DB_POLICY_DECISION_URI` /
   `CDS_METADATA_DB_POLICY_DECISION_URI` (e.g.
   `http://policy-opa:8181/v1/data`).
