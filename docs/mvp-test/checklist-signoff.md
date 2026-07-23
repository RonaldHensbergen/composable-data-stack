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

- [x] **T1.1** Fresh bootstrap  
  Clone repo, copy env/config, render profile, and start stack from docs only.

- [x] **T1.2** Preflight/doctor  
  Verify Docker, Compose, ports, env vars, disk, and memory before startup.

- [x] **T1.3** Container health  
  Confirm Dagster, Postgres, and Superset all become healthy within timeout.

---

## T2 Dagster proof

- [x] **T2.1** Dagster UI load  
  Confirm Dagster UI is reachable.

- [x] **T2.2** Code location loads  
  Confirm repository/code location is visible with no import or config errors.

- [x] **T2.3** Demo job exists  
  Confirm `load_demo_sales` or equivalent job is present.

---

## T3 Postgres proof

- [x] **T3.1** Postgres connect  
  Connect using profile credentials successfully.

- [x] **T3.2** Schema bootstrap  
  Confirm required database/schema exists.

- [x] **T3.3** Dagster storage backing  
  Confirm Dagster uses Postgres-backed state if that is part of the profile.

---

## T4 End-to-end DAG execution proof

- [x] **T4.1** Run demo Dagster job  
  Trigger `load_demo_sales` and confirm successful completion.
  ```bash
  Dagster UI -> Jobs -> load_demo_sales -> Launch run
  ```

- [x] **T4.2** Verify output table  
  Confirm output table exists in Postgres.
  ```bash
  psql -h localhost -U analytics -d analytics_db -c "\dt demo_sales"
  ```

- [x] **T4.3** Verify row count  
  Confirm row count matches expected fixture.
  ```bash
  psql -h localhost -U analytics -d analytics_db -c "select count(*) from demo_sales;"
  ```

- [x] **T4.4** Verify rerun behavior  
  Re-run the job and confirm overwrite/append/upsert behavior matches documentation.
  ```bash
  Dagster UI -> Jobs -> load_demo_sales -> Launch run again
  ```

- [x] **T4.5** Verify logs available  
  Confirm Dagster run logs are visible and useful.
  ```bash
  Dagster UI -> Runs -> open latest run logs
  ```

---

## T5 Persistence proof

- [x] **T5.1** Restart stack  
  ```bash
  docker compose restart dagster-user-code dagster-webserver dagster-daemon postgres superset
  ```
  State: all services come back healthy and the stack is reachable again.

- [x] **T5.2** Persisted Dagster run history  
  ```bash
  Dagster UI -> Runs -> confirm prior successful runs still appear
  ```
  State: earlier successful runs are still listed after the restart.

- [x] **T5.3** Persisted Postgres data  
  ```bash
  psql -h localhost -U analytics -d analytics_db -c "select count(*) from demo_sales;"
  ```
  State: the row count is unchanged after restart.

- [x] **T5.4** Persisted Superset metadata  
  ```bash
  Superset UI -> Data -> Datasets/Charts -> confirm saved objects still exist
  ```
  State: saved datasets/charts/connections are still present.

---

## T6 Superset proof

- [x] **T6.1** Superset UI load  
  ```bash
  curl -I http://localhost:8088/
  ```
  State: Superset returns a healthy HTTP response.

- [x] **T6.2** Admin login  
  ```bash
  Superset UI -> log in with documented admin credentials
  ```
  State: login succeeds and the home page loads.

- [x] **T6.3** Datasource connectivity  
  ```bash
  Superset UI -> Data -> Datasets -> verify the Postgres table is selectable
  ```
  State: the produced Postgres table is visible as a selectable dataset source.

- [x] **T6.4** Dataset creation  
  ```bash
  Superset UI -> Data -> Datasets -> + Dataset
  ```
  State: a dataset can be created or is already pre-seeded.

- [x] **T6.5** Visualization proof  
  ```bash
  Superset UI -> Charts -> create a chart from the output table
  ```
  State: at least one chart renders data from the output table.

---

## T7 Restart and recovery proof

- [x] **T7.1** Clean restart recovery  
  ```bash
  docker compose restart && docker compose ps
  ```
  State: all services reconnect and report healthy/ready status.

- [x] **T7.2** Post-restart rerun  
  ```bash
  Dagster UI -> Jobs -> load_demo_sales -> Launch run again
  ```
  State: the job succeeds after the restart.

- [x] **T7.3** Duplicate/replay behavior  
  ```bash
  psql -h localhost -U analytics -d analytics_db -c "select count(*) from demo_sales;"
  ```
  State: the data state matches the documented rerun semantics.

---

## T8 Failure-path proof

- [x] **T8.1** Postgres unavailable  
  ```bash
  docker compose stop postgres && Dagster UI -> Jobs -> load_demo_sales -> Launch run
  ```
  State: the job fails clearly and points to the missing database dependency.

- [x] **T8.2** Missing env/config  
  ```bash
  docker compose --env-file .env.missing up
  ```
  State: startup fails with an actionable configuration error.

- [x] **T8.3** Port conflict  
  ```bash
  python3 -m http.server 8088
  ```
  State: preflight or startup detects the occupied port.

- [ ] **T8.4** Service readiness race  
  ```bash
  docker compose up dagster-webserver
  ```
  State: healthchecks/retries handle the dependency startup order.

- [ ] **T8.5** Failure logs/artifacts  
  ```bash
  Dagster UI -> Runs -> open failed run logs and artifacts
  ```
  State: logs and artifacts identify the root cause.

---

## T9 CI proof

- [ ] **T9.1** Validate profile/module config  
  ```bash
  python3 -m json.tool renovate.json > /dev/null
  ```
  State: config/schema validation passes on a clean runner.

- [ ] **T9.2** Render runtime artifacts  
  ```bash
  python3 images/dagster/generate_config.py
  ```
  State: rendered runtime config is generated deterministically.

- [ ] **T9.3** Boot on clean CI runner  
  ```bash
  docker compose up -d
  ```
  State: the profile boots successfully in CI.

- [ ] **T9.4** Full happy-path E2E  
  ```bash
  Dagster UI -> Jobs -> load_demo_sales -> Launch run; psql -h localhost -U analytics -d analytics_db -c "select count(*) from demo_sales;"
  ```
  State: the job succeeds, DB output is present, and the service is reachable.

- [ ] **T9.5** Restart verification in CI  
  ```bash
  docker compose restart && docker compose ps
  ```
  State: restart succeeds and persistence checks still pass.

- [ ] **T9.6** Collect diagnostics on failure  
  ```bash
  Dagster UI -> Runs -> open failed run logs and artifacts
  ```
  State: logs and uploaded artifacts are available for troubleshooting.

---

## MVP release gate

Minimum required before calling the profile proven:

- [x] **T1.1–T1.3**
- [x] **T2.1–T2.3**
- [x] **T3.1–T3.3**
- [x] **T4.1–T4.5**
- [x] **T5.1–T5.3**
- [x] **T6.1**
- [x] **T6.3**
- [x] **T7.1–T7.3**
- [ ] **T8.2**
- [ ] **T8.3**
- [ ] **T8.5**
- [ ] **T9.1–T9.6**
