# Profile test cases

Use these cases to verify `local-dagster-postgres-superset(-env)`. Record
execution results outside this document so it remains reusable.

## T1 Bootstrap

| ID | Test | Expected result |
| -- | ---- | --------------- |
| T1.1 | Fresh bootstrap | A clean clone starts using only documented setup. |
| T1.2 | Preflight | Runtime, Compose, environment, and port checks pass. |
| T1.3 | Container health | Dagster, Postgres, and Superset become healthy. |

## T2 Dagster

| ID | Test | Expected result |
| -- | ---- | --------------- |
| T2.1 | UI load | The Dagster UI is reachable. |
| T2.2 | Code location | The code location loads without import or configuration errors. |
| T2.3 | Demo job | `load_offers_1000` is available. |

## T3 Postgres

| ID | Test | Expected result |
| -- | ---- | --------------- |
| T3.1 | Connection | Profile credentials connect successfully. |
| T3.2 | Schema bootstrap | Required databases and schemas exist. |
| T3.3 | Dagster storage | Dagster state uses Postgres-backed storage. |

## T4 End-to-end execution

| ID | Test | Expected result |
| -- | ---- | --------------- |
| T4.1 | Run demo job | `load_offers_1000` completes successfully. |
| T4.2 | Output table | `offers_1000` exists in Postgres. |
| T4.3 | Row count | The table contains exactly 1,000 fixture rows. |
| T4.4 | Rerun | Rerunning replaces fixture rows without duplicates. |
| T4.5 | Logs | Dagster run logs are available and actionable. |

## T5 Persistence

| ID | Test | Expected result |
| -- | ---- | --------------- |
| T5.1 | Restart | Services recover and become healthy. |
| T5.2 | Dagster history | Previous successful runs remain available. |
| T5.3 | Postgres data | Produced data remains present. |
| T5.4 | Superset metadata | Promised connections and content remain present. |

## T6 Superset

| ID | Test | Expected result |
| -- | ---- | --------------- |
| T6.1 | UI load | Superset is reachable. |
| T6.2 | Admin login | Login succeeds with documented credentials. |
| T6.3 | Datasource | Superset can access the produced Postgres table. |
| T6.4 | Dataset | A dataset can be created or is pre-seeded. |
| T6.5 | Visualization | A chart or dashboard queries the output table. |

## T7 Recovery

| ID | Test | Expected result |
| -- | ---- | --------------- |
| T7.1 | Clean restart | The full stack reconnects without manual repair. |
| T7.2 | Post-restart run | The demo job succeeds after restart. |
| T7.3 | Replay behavior | Resulting data matches documented rerun semantics. |

## T8 Failure paths

| ID | Test | Expected result |
| -- | ---- | --------------- |
| T8.1 | Postgres unavailable | Failure is clear, safe, and recoverable. |
| T8.2 | Missing configuration | Validation identifies the missing value. |
| T8.3 | Port conflict | Preflight identifies the occupied port. |
| T8.4 | Readiness race | Health checks and retries handle cold startup. |
| T8.5 | Diagnostics | Logs and artifacts identify the root cause. |

## T9 CI

| ID | Test | Expected result |
| -- | ---- | --------------- |
| T9.1 | Validate | Profile and module validation passes on a clean runner. |
| T9.2 | Render | Runtime output is valid and deterministic. |
| T9.3 | Boot | The profile becomes healthy in CI. |
| T9.4 | End-to-end | The job and database assertions pass in CI. |
| T9.5 | Restart | Persistence assertions pass after restart. |
| T9.6 | Failure artifacts | Redacted diagnostics are uploaded on failure. |

## Full-suite acceptance

The full suite passes when:

- documented setup works from a clean clone;
- the example Dagster job writes expected data to Postgres;
- state and data persist across restart;
- Superset can consume the produced data;
- reruns have documented, deterministic behavior;
- common failures produce actionable diagnostics; and
- CI automates validation, rendering, runtime, and persistence checks.
