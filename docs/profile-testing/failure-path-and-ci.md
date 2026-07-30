# Profile failure-path and CI tests

This document expands phases 8 and 9 of the
[profile test plan](test-plan.md).

> These phases test **runtime** correctness (booting the rendered stack,
> service health, recovery from failure, and CI enforcement of all of the
> above). They extend, and are complementary to, the README's **compile-time**
> pipeline (`cds validate → cds security → cds plan → cds render`, see
> [Internal Flow](../../README.md#internal-flow)), which only proves a
> profile compiles — not that it runs correctly.

## 8. Failure-path tests

### 8.1 Reusable failure-test harness

Extend the Docker runtime test infrastructure or add
`tests/test_profile_failure_paths.py`.

The harness must:

1. Render the selected profile.
2. Start a clean stack and wait for health.
3. Execute each failure scenario independently.
4. Capture the exit code, standard output, standard error, service health, and
   container logs.
5. Restore the stack after each scenario.
6. Always run:

   ```bash
   docker compose down -v --remove-orphans
   ```

Each scenario must be isolated so one expected failure cannot invalidate later
tests.

### 8.2 Postgres or analytics database unavailable

Dagster metadata and analytics data use the same Postgres service. Stopping
Postgres before launching a run may prevent Dagster from recording the run at
all. Split this proof into two tests.

#### Stack dependency outage

1. Boot the healthy stack.
2. Stop Postgres.
3. Verify Dagster and Superset become unhealthy or report database connection
   failures.
4. Capture `docker compose ps -a` and relevant logs.
5. Restart Postgres.
6. Wait for health.
7. Confirm the stack recovers without recreating volumes.

#### Pipeline analytics connection failure

1. Leave Dagster metadata storage operational.
2. Override only the demo job's analytics connection with an invalid host,
   port, or credential.
3. Execute `load_offers_1000` non-interactively:

   ```bash
   docker compose exec -T dagster-user-code \
     dagster job execute \
     -f /app/workdirs/dagster/definitions.py \
     -j load_offers_1000
   ```

4. Assert the command returns a non-zero exit code.
5. Assert Dagster records the run as failed.
6. Assert the error identifies the analytics database connection without
   printing its password.
7. Restore the connection and prove the next run succeeds.

Pass when the failure is deterministic and actionable, no secret is exposed,
and recovery succeeds.

### 8.3 Missing or invalid configuration

Test these required variables individually:

- `CDS_POSTGRES_SUPERUSER_PASSWORD`
- `CDS_ANALYTICS_DB_PASSWORD`
- `CDS_DAGSTER_DB_PASSWORD`
- `CDS_SUPERSET_DB_PASSWORD`
- `CDS_SUPERSET_SECRET_KEY`
- `CDS_SUPERSET_ADMIN_PASSWORD`

For each case:

1. Create a temporary environment containing every valid value except one.
2. Run `cds validate`, `cds plan`, `cds render`, and
   `docker compose config`.
3. Require failure before containers start.
4. Assert the diagnostic names the missing variable and affected capability.
5. Assert no generated artifact contains a substituted empty password.

Profile schema validation alone does not guarantee that referenced environment
values are populated. Add environment or preflight validation if the current
commands do not reject a missing value.

### 8.4 Port conflicts

Cover ports 3000, 5432, 8088, and, if exposed by the profile, 6379.

1. Bind a temporary listener to the target port.
2. Run preflight or attempt stack startup.
3. Assert the check fails quickly.
4. Assert the message names the occupied port and associated capability.
5. Release the listener.
6. Start the stack successfully afterward.

Prefer provider-neutral endpoint availability validation over checks tied to
specific module names.

### 8.5 Service-readiness race

Test a genuine cold start rather than only starting `dagster-webserver`.
Compose automatically starts declared dependencies.

1. Remove containers and volumes.
2. Start the complete stack simultaneously.
3. Record container start and health transitions.
4. Assert Dagster waits for healthy Postgres and user code.
5. Assert Superset initialization waits for Postgres and KeyDB.
6. Repeat the cold boot several times to expose intermittent races.
7. Fail if a service permanently restarts, exits unexpectedly, or requires
   manual intervention.

Use bounded health timeouts. The complete stack should become ready within five
minutes on the target CI runner.

### 8.6 Failure diagnostics and artifacts

Capture the following for every failure:

```bash
docker compose ps -a
docker compose logs --no-color --timestamps
docker inspect <container>
```

Also capture the failed Dagster run status and event logs when available. Do
not collect `.env` files or unredacted container environments.

### Phase 8 acceptance criteria

Phase 8 is complete when:

- all four failure classes are automated;
- expected product failures are distinguishable from test infrastructure
  failures;
- diagnostics identify the cause;
- credentials never appear in output;
- every scenario proves recovery afterward.

## 9. CI tests

### 9.1 Separate fast and runtime checks

Retain `.github/workflows/ci.yml` for:

- linting;
- unit tests;
- profile validation;
- plan generation; and
- deterministic rendering.

Use `.github/workflows/docker-smoke-test.yml`, or a dedicated profile workflow,
for expensive Docker tests.

### 9.2 Correct validation and rendering stages

Run the profile operations directly:

```bash
cds validate local-dagster-postgres-superset
cds plan local-dagster-postgres-superset --output /tmp/plan.json
cds render local-dagster-postgres-superset --output docker-compose.yml
docker compose config --quiet
```

Render twice and compare the results to prove determinism. Supply synthetic CI
credentials through workflow environment variables. Do not use production or
repository secrets for local test passwords.

### 9.3 Boot and health stage

1. Build the images.
2. Start the stack:

   ```bash
   docker compose up -d --wait --wait-timeout 300
   ```

3. Assert every required long-running service is healthy.
4. Assert `superset-init` exits successfully.
5. Check:
   - the Dagster health endpoint;
   - the Superset `/health` endpoint;
   - `pg_isready`;
   - Dagster daemon status; and
   - Dagster code-location availability.
6. Fail on unexpected exited or restarting containers.

### 9.4 Happy-path end-to-end stage

1. Execute `load_offers_1000` through the Dagster CLI or GraphQL API.
2. Assert the command and recorded Dagster run succeed.
3. Query Postgres from inside its container.
4. Assert:
   - `offers_1000` exists;
   - its schema matches expectations;
   - the exact fixture row count is present; and
   - selected aggregate values match known results.
5. Run the job again and verify its documented idempotency.
6. Query the dataset through the Superset API if Superset consumption is part
   of the required CI gate.

Avoid UI automation for the minimum gate. Prefer stable CLI and API assertions.

### 9.5 Persistence stage

1. Record the successful Dagster run ID and database checksum or row count.
2. Restart all long-running services without removing volumes.
3. Wait for health.
4. Verify:
   - the prior Dagster run remains queryable;
   - `offers_1000` still contains exactly 1,000 fixture rows;
   - Superset metadata still exists; and
   - another Dagster run succeeds.
5. Confirm rerun semantics remain correct.

### 9.6 CI failure artifacts

Use an `if: failure()` or `if: always()` diagnostic step that writes:

- rendered Compose configuration;
- `docker compose ps -a`;
- redacted Compose logs;
- container health inspection;
- end-to-end command output; and
- SQL assertion output.

Upload these files with `actions/upload-artifact`. Always run cleanup
afterward, but do not allow cleanup failure to hide the original test result.

### 9.7 Workflow policy

Run the profile workflow for:

- pull requests changing profiles, modules, images, workdirs, renderer code, or
  runtime tests;
- pushes to `main` that change runtime-relevant paths;
- manual dispatch; and
- an optional scheduled weekly cold-start test.

Configure:

- `timeout-minutes: 45`;
- workflow concurrency with stale-run cancellation;
- pinned action versions;
- least-privilege `contents: read`; and
- one Linux runner for Docker tests.

### Phase 9 acceptance criteria

Phase 9 is complete when a clean runner renders and boots the profile, executes
the real pipeline, verifies Postgres and Superset consumption, proves
persistence after restart, and retains actionable diagnostics when an
assertion fails.
