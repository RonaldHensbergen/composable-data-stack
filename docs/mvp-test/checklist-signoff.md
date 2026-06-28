# MVP Proof Checklist — `local-dagster-postgres-superset(-env)`

Use this checklist to prove the MVP profile is ready for release.

## Suggested test path

Run tests in this order:

1. **T1 Bootstrap proof**
2. **T2 Dagster proof**
3. **T3 Postgres proof**
4. **T4 End-to-end DAG execution proof**
5. **T5 Persistence proof**
6. **T6 Superset proof**
7. **T7 Restart and recovery proof**
8. **T8 Failure-path proof**
9. **T9 CI proof**

---

## T1 Bootstrap proof

- [X] **T1.1** Fresh bootstrap  
  Clone repo, copy env/config, render profile, and start stack from docs only.

- [ ] **T1.2** Preflight/doctor  
  Verify Docker, Compose, ports, env vars, disk, and memory before startup.

- [ ] **T1.3** Container health  
  Confirm Dagster, Postgres, and Superset all become healthy within timeout.

---

## T2 Dagster proof

- [ ] **T2.1** Dagster UI load  
  Confirm Dagster UI is reachable.

- [ ] **T2.2** Code location loads  
  Confirm repository/code location is visible with no import or config errors.

- [ ] **T2.3** Demo job exists  
  Confirm `load_demo_sales` or equivalent job is present.

---

## T3 Postgres proof

- [ ] **T3.1** Postgres connect  
  Connect using profile credentials successfully.

- [ ] **T3.2** Schema bootstrap  
  Confirm required database/schema exists.

- [ ] **T3.3** Dagster storage backing  
  Confirm Dagster uses Postgres-backed state if that is part of the profile.

---

## T4 End-to-end DAG execution proof

- [ ] **T4.1** Run demo Dagster job  
  Trigger `load_demo_sales` and confirm successful completion.

- [ ] **T4.2** Verify output table  
  Confirm output table exists in Postgres.

- [ ] **T4.3** Verify row count  
  Confirm row count matches expected fixture.

- [ ] **T4.4** Verify rerun behavior  
  Re-run the job and confirm overwrite/append/upsert behavior matches documentation.

- [ ] **T4.5** Verify logs available  
  Confirm Dagster run logs are visible and useful.

---

## T5 Persistence proof

- [ ] **T5.1** Restart stack  
  Stop and start services and confirm healthy recovery.

- [ ] **T5.2** Persisted Dagster run history  
  Confirm previous successful runs remain visible after restart.

- [ ] **T5.3** Persisted Postgres data  
  Confirm written data remains present after restart.

- [ ] **T5.4** Persisted Superset metadata  
  Confirm saved connection/dataset/chart persists if the profile promises it.

---

## T6 Superset proof

- [ ] **T6.1** Superset UI load  
  Confirm Superset is reachable.

- [ ] **T6.2** Admin login  
  Confirm login succeeds with documented credentials.

- [ ] **T6.3** Datasource connectivity  
  Confirm Superset can access the produced Postgres table.

- [ ] **T6.4** Dataset creation  
  Confirm dataset can be created or is pre-seeded.

- [ ] **T6.5** Visualization proof  
  Confirm at least one chart/dashboard successfully queries the output table.

---

## T7 Restart and recovery proof

- [ ] **T7.1** Clean restart recovery  
  Restart the full stack after a successful run and confirm services reconnect properly.

- [ ] **T7.2** Post-restart rerun  
  Run the demo job again after restart and confirm success.

- [ ] **T7.3** Duplicate/replay behavior  
  Confirm resulting data state matches documented semantics.

---

## T8 Failure-path proof

- [ ] **T8.1** Postgres unavailable  
  Stop Postgres and trigger the job; confirm failure is clear and actionable.

- [ ] **T8.2** Missing env/config  
  Start with broken or missing config and confirm error is actionable.

- [ ] **T8.3** Port conflict  
  Simulate an occupied port and confirm preflight catches it.

- [ ] **T8.4** Service readiness race  
  Force dependent services to start early and confirm healthchecks/retries handle it.

- [ ] **T8.5** Failure logs/artifacts  
  Confirm logs clearly identify root cause.

---

## T9 CI proof

- [ ] **T9.1** Validate profile/module config  
  Confirm schema and structure validation passes on a clean runner.

- [ ] **T9.2** Render runtime artifacts  
  Confirm rendered output is generated successfully and deterministically.

- [ ] **T9.3** Boot on clean CI runner  
  Confirm the profile boots successfully in CI.

- [ ] **T9.4** Full happy-path E2E  
  Run the job, verify DB output, and verify Superset reachability.

- [ ] **T9.5** Restart verification in CI  
  Restart and confirm persistence checks pass.

- [ ] **T9.6** Collect diagnostics on failure  
  Confirm logs and artifacts are uploaded on failure.

---

## MVP release gate

Minimum required before calling the profile proven:

- [ ] **T1.1–T1.3**
- [ ] **T2.1–T2.3**
- [ ] **T3.1–T3.3**
- [ ] **T4.1–T4.5**
- [ ] **T5.1–T5.3**
- [ ] **T6.1**
- [ ] **T6.3**
- [ ] **T7.1–T7.3**
- [ ] **T8.2**
- [ ] **T8.3**
- [ ] **T8.5**
- [ ] **T9.1–T9.6**
