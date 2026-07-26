# Profile test plan for `local-dagster-postgres-superset(-env)`

> Goal: verify that the profile is usable, repeatable, and suitable for a
> production-like demonstration.

## Test scope

Each complete profile test verifies these five areas:

| Area | Must prove |
| ---- | ---------- |
| **Boot** | Fresh clone can start reliably |
| **Control** | Dagster, Postgres, and Superset are reachable and healthy |
| **Persistence** | State survives restart where expected |
| **Data flow** | A real Dagster pipeline can write data into Postgres |
| **Consumption** | Superset can read and visualize that data |

## Test sequence

### 1. Environment and bootstrap

Run on a clean machine or clean CI runner from a fresh clone.

Test:

1. clone repo
1. copy env/example config
1. validate profile
1. run preflight checks
1. plan profile
1. render profile
1. boot profile
1. confirm all containers healthy

Pass criteria:

- no manual edits beyond documented setup
- startup finishes within expected time
- healthchecks pass
- ports and credentials match docs

### 2. Dagster

Verify the service behavior as well as UI availability.

Test:

- Dagster web UI loads
- Dagster daemon is running
- code location loads successfully
- at least one example job/asset appears
- schedules/sensors are either visible or intentionally disabled and documented

Pass criteria:

- no import/config errors
- repository loads automatically
- at least one runnable pipeline/job exists

### 3. Postgres

Test real persistence, not just container health.

Test:

- Postgres accepts connections
- expected database(s) exist
- Dagster can use Postgres-backed storage if that is part of the profile
- test table/data survives container restart if persistence is promised

Pass criteria:

- connection succeeds from host and/or service containers
- writes succeed
- restart does not lose persisted data unexpectedly

### 4. End-to-end DAG execution

This is the key end-to-end test.

Create one simple but real example pipeline/job:

#### Recommended example DAG/job

A `load_offers_1000` Dagster job that:

1. reads `workdirs/shared-data/incoming/offers-1000.csv`
1. normalizes the CSV column names
1. writes the `offers_1000` table into Postgres
1. optionally records run metadata/logs
1. exits successfully

Expected table:

- `offers_1000`
- exactly 1,000 rows
- columns `index`, `ean`, `stock`, and `price`

Pass criteria:

- run can be triggered from UI and CLI
- run completes successfully
- output table exists in Postgres
- row count matches expectation
- rerunning replaces rows from `offers-1000.csv`, leaving exactly 1,000 rows
- logs are accessible

### 5. Persistence

| **Persistence item** | **What to verify** |
| ---- | ------ |
| Dagster run history | Previous runs remain visible after restart |
| Dagster configuration/state | Instance storage persists if promised |
| Postgres data | Written tables remain after restart |
| Superset metadata | Saved datasource/dashboard survives restart if persistence is promised |

Minimum pass criteria:

- restart stack
- Dagster still shows prior run history
- Postgres still contains produced table/data
- Superset still has configured connection/metadata if expected

### 6. Superset

Test:

- Superset UI loads
- admin login works
- Postgres datasource can be added or is preconfigured
- produced table is visible
- simple chart or dataset can be created

Recommended consumption test:

- create one saved dataset from the table produced by Dagster
- create one simple chart
- optionally create one dashboard tile

Pass criteria:

- Superset can query the table
- query returns expected rows
- at least one saved visualization exists if the profile promises seeded content

### 7. Restart and recovery

Test resilience of the happy path.

Test:

- stop stack
- start stack again
- rerun the Dagster job
- verify no broken dependencies
- verify duplicate behavior is expected and documented

Pass criteria:

- stack restarts cleanly
- no manual repair needed
- rerun does not corrupt state

### 8. Failure paths

The profile should provide understandable failure behavior.

Test at least these cases:

- Postgres unavailable when Dagster job runs
- bad env var / missing credential
- port conflict
- Superset starts before DB ready

Pass criteria:

- failures are visible
- logs point to cause
- doctor/preflight or healthchecks catch common misconfigurations

### 9. CI

Everything above should reduce to automated checks.

#### Minimum CI stages

| **Stage** | **What it does** |
| ---- | ------ |
| Lint/validate | profile/module/schema validation |
| Render | generate final runtime artifacts |
| Boot | start profile on clean runner |
| Smoke | hit health endpoints / check services |
| E2E | trigger Dagster job and verify Postgres output |
| Persistence | optional restart and verify retained state |

## Required test fixtures

Keep these reusable fixtures in the repository:

- sample Dagster job/assets
- sample source data
- verification script to query Postgres row count
- Superset setup instructions or seed script
- e2e test script
- fresh-machine quickstart

## Full-suite acceptance criteria

The profile passes the full suite when all of these are true:

- one documented example Dagster job runs successfully
- the job writes usable data into Postgres
- the data is still there after restart
- Dagster run history persists after restart
- Superset can query that produced data
- a fresh machine can reproduce the result
- CI automates at least the happy-path proof
- known limitations are documented
