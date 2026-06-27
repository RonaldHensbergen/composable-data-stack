# MVP proof plan for `local-dagster-postgres-superset(-env)

> Goal: prove the profile is not just “up,” but usable, repeatable, and demoable.

## Proof definition

Before calling the MVP profile proven, verify these 5 things:

| Area | Must prove |
| ---- | ---------- |
| **Boot** |	Fresh clone can start reliably |
| **Control** | plane	Dagster, Postgres, Superset are reachable and healthy |
| **Persistence**	| State survives restart where expected |
| **Data flow**	| A real Dagster pipeline can write data into Postgres |
| **Consumption**	| Superset can read and visualize that data |

## Test plan by phase

### 1. Environment and bootstrap proof

Run on a clean machine or clean CI runner. This means are tested on a fresh cloned repo.

Test:

1. clone repo
1. copy env/example config
1. validate profile
1. plan profile
1. render profile
1. boot profile
1. confirm all containers healthy

Pass criteria:

- no manual edits beyond documented setup
- startup finishes within expected time
- healthchecks pass
- ports and credentials match docs

### 2. Dagster proof

You want more than “UI opens.”

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

### 3. Postgres proof

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

### 4. End-to-end DAG execution proof

This is the key MVP proof.

Create one simple but real example pipeline/job:

#### Recommended example DAG/job

A Dagster job that:

1. reads a small CSV or inline dataset
1. transforms it slightly
1. writes a table into Postgres
1. optionally records run metadata/logs
1. exits successfully

Example table:

- demo_sales
- 20–100 rows
- columns like order_id, region, amount, created_at

Pass criteria:

- run can be triggered from UI and CLI
- run completes successfully
- output table exists in Postgres
- row count matches expectation
- rerun behavior is defined:
  - overwrite, append, or idempotent upsert
- logs are accessible

### 5. Persistence proof

You specifically mentioned “running a DAG that can be stored.”

That should mean testing:

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

### 6. Superset proof

Again, more than “login page loads.”

Test:

- Superset UI loads
- admin login works
- Postgres datasource can be added or is preconfigured
- produced table is visible
- simple chart or dataset can be created

Best MVP proof:

- create one saved dataset from the table produced by Dagster
- create one simple chart
- optionally create one dashboard tile

Pass criteria:

- Superset can query the table
- query returns expected rows
- at least one saved visualization exists if the profile promises seeded content

### 7. Restart and recovery proof

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

### 8. Failure-path proof

MVP should show understandable failure behavior.

Test at least these cases:

- Postgres unavailable when Dagster job runs
- bad env var / missing credential
- port conflict
- Superset starts before DB ready

Pass criteria:

- failures are visible
- logs point to cause
- doctor/preflight or healthchecks catch common misconfigurations

### 9. CI proof

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



## Recommended proof artifact set

Before MVP signoff, have these in repo:

- sample Dagster job/assets
- sample source data
- verification script to query Postgres row count
- Superset setup instructions or seed script
- e2e test script
- fresh-machine quickstart

## Concrete test cases

| **ID** | **Test** | **Expected result** |
| ---- | ------ | ------ |
| T1 | Fresh bootstrap | stack starts from docs only |
| T2 | Dagster UI load | reachable and healthy |
| T3 | Postgres connect | connection succeeds |
| T4 | Run demo Dagster job | successful run |
| T5 | Verify output table | table exists with expected rows |
| T6 | Restart stack | services recover cleanly |
| T7 | Verify persisted run history | previous Dagster run still visible |
| T8 | Verify persisted table | data still exists after restart |
| T9 | Superset query test | table visible and queryable |
| T10 | Saved chart/dashboard | at least one visualization works |
| T11 | Re-run job | behavior matches documented semantics |
| T12 | Common failure diagnostics | errors are actionable |

## MVP signoff checklist

You can call the profile proven when all of these are true:

- one documented example Dagster job runs successfully
- the job writes usable data into Postgres
- the data is still there after restart
- Dagster run history persists after restart
- Superset can query that produced data
- a fresh machine can reproduce the result
- CI automates at least the happy-path proof
- known limitations are documented

## Recommended implementation order

1.	add demo Dagster job writing to Postgres
2.	add verification SQL/check script
3.	add restart/persistence test
4.	add Superset datasource + query proof
5.	automate e2e in CI
6.	document exact expected outputs
