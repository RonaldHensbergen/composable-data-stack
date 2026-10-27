"""Docker runtime proof for restore into a clean environment (issue #210).

Executes the restore-verification procedure of docs/backup-restore.md
section 5 against the shipped production overlay of the
local-dagster-postgres-superset profile and the warehouse/postgres module's
built-in pgBackRest backup capability (#665), so the acceptance criterion
"a reproducible restore test validates recovery into a clean environment"
is exercised end to end rather than only declared as policy:

1. validate -> plan -> render the production overlay, which enables the
   encrypted backup capability and the spec.backup policies (E130-E135).
2. Boot postgres plus the backup sidecar in a dedicated compose project,
   wait for the scheduler's initial full backup, and write marker rows.
3. Archive WAL, take an incremental backup, and verify repository
   integrity with pgbackrest check.
4. Tear the source project down (keeping its volumes) and copy only the
   encrypted repository volume into a *clean environment*: a second
   compose project with a fresh, empty data volume and no other state.
5. Prove the artifacts are encrypted: restoring with the wrong key must
   fail, and the marker text must not appear in plaintext in the
   repository.
6. Restore the newest restore point with the correct key, start postgres
   in the clean environment, and verify the marker rows survived
   byte-for-byte (count and checksum).
7. Compare the measured restore time and restore-point age with the
   profile's rtoMinutes/rpoMinutes backup policy.

Runs only with CDS_RUN_DOCKER_SMOKE=1 and a working daemon; skips
otherwise, like tests/test_vault_init_db_runtime.py.
"""

import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

import yaml

from cli.planner import build_plan
from cli.renderer import render_compose
from cli.validator import validate_profile

REPO_ROOT = Path(__file__).resolve().parent.parent
PROFILE = REPO_ROOT / "profiles" / "local-dagster-postgres-superset" / "profile.yaml"
PROD_OVERLAY = REPO_ROOT / "profiles" / "local-dagster-postgres-superset" / "environments" / "prod.yaml"
COMPOSE_FILE = REPO_ROOT / "docker-compose.restore-proof.yml"

SOURCE_PROJECT = "cds-restore-proof-src"
CLEAN_PROJECT = "cds-restore-proof-clean"
REPO_VOLUME = "postgres-postgres-backup-repo"
BACKUP_SERVICE = "postgres-backup"
PG_SERVICE = "postgres"
RESTORE_SERVICE = "postgres-restore"

MARKER_TOKEN = "cds-restore-proof-7c1e04b9"  # noqa: S105 - test marker, not a credential
MARKER_TABLE = "cds_restore_proof"
MARKER_ROWS = 250

# Retry only known-transient registry flakes (mirrors the other runtime proofs).
_TRANSIENT_REGISTRY_ERROR_MARKERS = (
    "Client.Timeout exceeded while awaiting headers",
    "TLS handshake timeout",
    "connection reset by peer",
    "i/o timeout",
    "net/http: request canceled",
    "toomanyrequests",
    "unexpected EOF",
)

# pgBackRest needs pg1-path and the socket directory, which the sidecar's
# entrypoint exports but a plain `docker compose exec` does not; mirror the
# cds-backup-scheduler/cds-restore wrappers.
_PGBACKREST_EXPORTS = (
    'export HOME=/tmp '
    'PGBACKREST_PG1_PATH="${PGDATA:-/var/lib/postgresql/18/docker}" '
    'PGBACKREST_PG1_SOCKET_PATH=/var/lib/postgresql; '
)


class PostgresBackupRestoreRuntimeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if os.getenv("CDS_RUN_DOCKER_SMOKE") != "1":
            raise unittest.SkipTest("Set CDS_RUN_DOCKER_SMOKE=1 to run Docker runtime proof tests")

        if shutil.which("docker") is None:
            raise unittest.SkipTest("Docker CLI not available")

        docker_info = subprocess.run(["docker", "info"], capture_output=True, text=True, errors="replace")
        if docker_info.returncode != 0:
            raise unittest.SkipTest("Docker daemon is not available")

    @staticmethod
    def _redact(value: str, env: dict[str, str]) -> str:
        redacted = value
        for key, secret in env.items():
            if secret and any(marker in key.upper() for marker in ("PASSWORD", "SECRET", "TOKEN")):
                redacted = redacted.replace(secret, "<redacted>")
        return re.sub(
            r"(postgresql(?:\+[a-z0-9]+)?://[^:/\s]*:)[^@\s]+",
            r"\1<redacted>",
            redacted,
            flags=re.IGNORECASE,
        )

    def _compose(self, project: str, env_file: Path) -> list[str]:
        # Bind sources and build contexts resolve relative to the compose
        # file's directory, so it lives at the repo root like production.
        return [
            "docker", "compose",
            "--env-file", str(env_file),
            "-f", str(COMPOSE_FILE),
            "-p", project,
        ]

    def _run(self, command: list[str], env: dict[str, str], timeout: int = 1200,
             allow_failure: bool = False) -> subprocess.CompletedProcess:
        result = subprocess.run(command, env=env, capture_output=True, text=True, errors="replace", timeout=timeout)
        if result.returncode != 0 and not allow_failure:
            self.fail(
                "Command failed: {cmd}\nstdout:\n{stdout}\nstderr:\n{stderr}".format(
                    cmd=" ".join(command),
                    stdout=self._redact(result.stdout, env),
                    stderr=self._redact(result.stderr, env),
                )
            )
        return result

    def _up_services(self, project: str, env_file: Path, env: dict[str, str],
                     services: list[str]) -> None:
        last_output = ""
        for attempt in range(1, 4):
            result = subprocess.run(
                [*self._compose(project, env_file), "up", "-d", *services],
                env=env, capture_output=True, text=True, errors="replace", timeout=1200,
            )
            if result.returncode == 0:
                return
            last_output = result.stdout + result.stderr
            if not any(m in last_output for m in _TRANSIENT_REGISTRY_ERROR_MARKERS) or attempt == 3:
                break
            time.sleep(15)
        self.fail(
            f"Could not start {services} after 3 attempt(s), container logs follow.\n"
            f"up output:\n{self._redact(last_output, env)}\n"
            f"logs:\n{self._redact(self._logs(project, env_file, env), env)}"
        )

    def _logs(self, project: str, env_file: Path, env: dict[str, str]) -> str:
        result = subprocess.run(
            [*self._compose(project, env_file), "logs", "--tail", "150", PG_SERVICE, BACKUP_SERVICE],
            env=env, capture_output=True, text=True, errors="replace", timeout=120,
        )
        return result.stdout + result.stderr

    def _wait_pg_ready(self, project: str, env_file: Path, env: dict[str, str],
                       deadline_seconds: int = 300) -> None:
        started = time.monotonic()
        last_error = ""
        while time.monotonic() - started < deadline_seconds:
            result = subprocess.run(
                [*self._compose(project, env_file), "exec", "-T", PG_SERVICE,
                 "pg_isready", "-U", "postgres"],
                env=env, capture_output=True, text=True, errors="replace", timeout=60,
            )
            if result.returncode == 0:
                return
            last_error = result.stdout + result.stderr
            time.sleep(5)
        self.fail(
            f"postgres never became ready within {deadline_seconds}s (project {project}), "
            f"container logs follow.\n"
            f"last pg_isready error:\n{self._redact(last_error, env)}\n"
            f"logs:\n{self._redact(self._logs(project, env_file, env), env)}"
        )

    @staticmethod
    def _env_value(env_file: Path, name: str) -> str:
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if line.startswith(name + "="):
                return line.split("=", 1)[1]
        raise AssertionError(f"{name} missing from proof env file")

    def _psql(self, project: str, env_file: Path, env: dict[str, str], sql: str,
              dbname: str = "postgres") -> str:
        command = [
            *self._compose(project, env_file), "exec", "-T",
            "-e", "PGPASSWORD=" + self._env_value(env_file, "CDS_POSTGRES_SUPERUSER_PASSWORD"),
            PG_SERVICE, "psql", "-U", "postgres", "-d", dbname, "-tAc", sql,
        ]
        result = subprocess.run(command, env=env, capture_output=True, text=True, errors="replace", timeout=120)
        if result.returncode != 0:
            self.fail(
                f"psql failed: {sql}\n"
                f"stdout:\n{self._redact(result.stdout, env)}\n"
                f"stderr:\n{self._redact(result.stderr, env)}\n"
                f"logs:\n{self._redact(self._logs(project, env_file, env), env)}"
            )
        return result.stdout.strip()

    def _pgbackrest(self, project: str, env_file: Path, env: dict[str, str],
                    args: list[str], timeout: int = 600) -> subprocess.CompletedProcess:
        command = [
            *self._compose(project, env_file), "exec", "-T", BACKUP_SERVICE,
            "bash", "-c", _PGBACKREST_EXPORTS + " ".join(["pgbackrest", *args]),
        ]
        result = subprocess.run(command, env=env, capture_output=True, text=True, errors="replace", timeout=timeout)
        if result.returncode != 0:
            self.fail(
                f"pgbackrest {' '.join(args)} failed (project {project})\n"
                f"stdout:\n{self._redact(result.stdout, env)}\n"
                f"stderr:\n{self._redact(result.stderr, env)}\n"
                f"logs:\n{self._redact(self._logs(project, env_file, env), env)}"
            )
        return result

    def _pgbackrest_info(self, project: str, env_file: Path,
                          env: dict[str, str]) -> dict | None:
        result = self._pgbackrest(project, env_file, env, ["--output=json", "info"])
        stanzas = json.loads(result.stdout or "[]")
        for stanza in stanzas:
            # pgBackRest keys the stanza as "name" in info output.
            if stanza.get("name") == "cds":
                return stanza
        return None

    def _wait_for_backup(self, project: str, env_file: Path, env: dict[str, str],
                         backup_type: str, deadline_seconds: int = 600) -> dict:
        """Wait until the repository holds a backup of the given type and
        return its info entry (timestamp.stop is the restore point)."""
        started = time.monotonic()
        while time.monotonic() - started < deadline_seconds:
            stanza = self._pgbackrest_info(project, env_file, env)
            for backup in (stanza or {}).get("backup", []):
                if backup.get("type") == backup_type:
                    return backup
            time.sleep(10)
        self.fail(
            f"No {backup_type} backup appeared within {deadline_seconds}s (project {project})"
        )

    @staticmethod
    def _volume_name(project: str, volume: str) -> str:
        return f"{project}_{volume}"

    def _write_env_file(self, tmpdir: str) -> Path:
        # The env file lives inside a temporary directory under the repo root:
        # `docker compose --env-file` is executed by the Docker CLI, which on
        # dev machines may not share the test process's system temp dir.
        env_file = Path(tmpdir) / ".env"
        lines = [
            "CDS_POSTGRES_SUPERUSER_PASSWORD=superuser_testpass",
            "CDS_ANALYTICS_DB_NAME=analytics",
            "CDS_ANALYTICS_DB_USER=analytics",
            "CDS_ANALYTICS_DB_PASSWORD=analytics_testpass",
            "CDS_DAGSTER_DB_NAME=dagster",
            "CDS_DAGSTER_DB_USER=dagster",
            "CDS_DAGSTER_DB_PASSWORD=dagster_testpass",
            "CDS_SUPERSET_DB_NAME=superset",
            "CDS_SUPERSET_DB_USER=superset",
            "CDS_SUPERSET_DB_PASSWORD=superset_testpass",
            "CDS_SUPERSET_SECRET_KEY=sekret",
            "CDS_SUPERSET_ADMIN_PASSWORD=adminpass",
            "CDS_BACKUP_ENCRYPTION_KEY=restore-proof-test-key-1c3f",
            "CDS_CACHE_PASSWORD=cache_testpass",
        ]
        env_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return env_file

    def _backup_policy(self) -> dict:
        overlay = yaml.safe_load(PROD_OVERLAY.read_text(encoding="utf-8"))
        for policy in overlay["spec"]["backup"]["policies"]:
            if policy["workloadClass"] == "warehouse":
                return policy
        raise AssertionError("warehouse backup policy missing from the prod overlay")

    def test_backup_restores_into_clean_environment(self) -> None:
        policy = self._backup_policy()

        # Compile path: the shipped production overlay must validate, plan,
        # and render with the backup capability enabled.
        diagnostics = validate_profile(str(PROFILE), environment="prod")
        self.assertEqual(
            [d for d in diagnostics if d.level == "error"], [],
            msg=f"unexpected validation errors: {diagnostics}",
        )

        with tempfile.TemporaryDirectory(prefix="cds-restore-proof-", dir=REPO_ROOT) as tmpdir:
            env_file = self._write_env_file(tmpdir)

            plan, plan_diags = build_plan(str(PROFILE), env_file=str(env_file), environment="prod")
            self.assertIsNotNone(plan)
            self.assertEqual(
                [d for d in plan_diags if d.level == "error"], [],
                msg=f"unexpected plan errors: {plan_diags}",
            )

            output, render_diags = render_compose(
                plan, output_path=str(COMPOSE_FILE), env_file=str(env_file),
            )
            self.assertEqual(
                [d for d in render_diags if d.level == "error"], [],
                msg=f"unexpected render errors: {render_diags}",
            )
            COMPOSE_FILE.write_text(output, encoding="utf-8")
            self.assertIn(RESTORE_SERVICE, yaml.safe_load(output)["services"])

            # Scrub ambient CDS_* so the shell can never override proof values.
            env = {k: v for k, v in os.environ.items() if not k.startswith("CDS_")}

            def teardown() -> None:
                for project in (SOURCE_PROJECT, CLEAN_PROJECT):
                    subprocess.run(
                        [*self._compose(project, env_file), "--profile", "cds-restore",
                         "down", "-v", "--remove-orphans"],
                        env=env, capture_output=True, text=True, errors="replace", timeout=300,
                    )
                # The clean repository volume is pre-created before compose
                # ever sees it, so remove it explicitly.
                subprocess.run(
                    ["docker", "volume", "rm", "--force",
                     self._volume_name(CLEAN_PROJECT, REPO_VOLUME)],
                    env=env, capture_output=True, text=True, errors="replace", timeout=60,
                )
                COMPOSE_FILE.unlink(missing_ok=True)

            try:
                self._run(
                    [*self._compose(SOURCE_PROJECT, env_file), "build", PG_SERVICE], env,
                    timeout=1800,
                )

                # Source environment: postgres + backup sidecar + marker data.
                self._up_services(SOURCE_PROJECT, env_file, env, [PG_SERVICE, BACKUP_SERVICE])
                self._wait_pg_ready(SOURCE_PROJECT, env_file, env)

                self._wait_for_backup(SOURCE_PROJECT, env_file, env, "full")

                self._psql(
                    SOURCE_PROJECT, env_file, env,
                    f"CREATE TABLE {MARKER_TABLE} (id integer primary key, marker text);",
                )
                self._psql(
                    SOURCE_PROJECT, env_file, env,
                    f"INSERT INTO {MARKER_TABLE} "  # noqa: S608 - test-only query over hardcoded constants
                    f"SELECT g, '{MARKER_TOKEN}' FROM generate_series(1, {MARKER_ROWS}) g;",
                )
                expected_checksum = self._psql(
                    SOURCE_PROJECT, env_file, env,
                    f"SELECT count(*) || ':' || md5(string_agg(marker, '' ORDER BY id)) "  # noqa: S608 - test-only query over hardcoded constants
                    f"FROM {MARKER_TABLE};",
                )
                self.assertTrue(
                    expected_checksum.startswith(f"{MARKER_ROWS}:"),
                    msg=f"marker rows vanished after insert: checksum {expected_checksum!r}",
                )

                # Make the marker data durable in the repository: switch WAL
                # (exercises continuous archiving) and take an incremental
                # backup, then verify repository integrity.
                self._psql(SOURCE_PROJECT, env_file, env, "SELECT pg_switch_wal();")
                self._pgbackrest(
                    SOURCE_PROJECT, env_file, env, ["--stanza=cds", "backup", "--type=incr"],
                )
                incremental = self._wait_for_backup(SOURCE_PROJECT, env_file, env, "incr")
                self._pgbackrest(SOURCE_PROJECT, env_file, env, ["--stanza=cds", "check"])
                restore_point_epoch = incremental["timestamp"]["stop"]

                # The source project's containers stop here; its volumes stay
                # until the final teardown so failures keep evidence.
                self._run(
                    [*self._compose(SOURCE_PROJECT, env_file), "down", "--remove-orphans"], env,
                )

                # Clean environment: a second compose project with a fresh,
                # empty data volume. Only the encrypted repository volume is
                # seeded from the source environment.
                clean_repo_volume = self._volume_name(CLEAN_PROJECT, REPO_VOLUME)
                source_repo_volume = self._volume_name(SOURCE_PROJECT, REPO_VOLUME)
                self._run(["docker", "volume", "create", clean_repo_volume], env)
                copy_output = subprocess.run(
                    ["docker", "run", "--rm", "--user", "0:0",
                     "--entrypoint", "/bin/sh",
                     "-v", f"{source_repo_volume}:/from:ro",
                     "-v", f"{clean_repo_volume}:/to",
                     "local/postgres:custom",
                     "-c", "cp -a /from/. /to/ && chown 999:999 /to && chmod 700 /to"],
                    env=env, capture_output=True, text=True, errors="replace", timeout=300,
                )
                self.assertEqual(
                    copy_output.returncode, 0,
                    msg="repository volume copy failed:\n" +
                    self._redact(copy_output.stdout + copy_output.stderr, env),
                )

                # Encryption proof 1: the marker text must not appear in
                # plaintext in the repository at rest.
                grep = subprocess.run(
                    ["docker", "run", "--rm", "--entrypoint", "/bin/sh",
                     "-v", f"{clean_repo_volume}:/repo:ro",
                     "local/postgres:custom",
                     "-c", f"grep -rasq {MARKER_TOKEN!r} /repo; if [ $? -eq 0 ]; then "
                           "echo 'marker found in plaintext'; exit 1; fi; exit 0"],
                    env=env, capture_output=True, text=True, errors="replace", timeout=300,
                )
                self.assertEqual(
                    grep.returncode, 0,
                    msg="backup repository exposes the marker in plaintext:\n" +
                    self._redact(grep.stdout + grep.stderr, env),
                )

                # Encryption proof 2: restoring with the wrong key must fail.
                wrong_key = self._run(
                    [*self._compose(CLEAN_PROJECT, env_file), "--profile", "cds-restore",
                     "run", "--rm", "-e", "PGBACKREST_REPO1_CIPHER_PASS=wrong-key-restore-proof",
                     RESTORE_SERVICE],
                    env, allow_failure=True,
                )
                self.assertNotEqual(
                    wrong_key.returncode, 0,
                    msg="restore with a wrong encryption key succeeded; artifacts are not "
                        "encrypted or the key is ignored:\n" +
                    self._redact(wrong_key.stdout + wrong_key.stderr, env),
                )

                # Restore the newest restore point with the correct key and
                # measure the recovery time (policy rtoMinutes).
                restore_started = time.monotonic()
                self._run(
                    [*self._compose(CLEAN_PROJECT, env_file), "--profile", "cds-restore",
                     "run", "--rm", RESTORE_SERVICE],
                    env,
                )
                elapsed_restore_seconds = time.monotonic() - restore_started

                self._up_services(CLEAN_PROJECT, env_file, env, [PG_SERVICE])
                self._wait_pg_ready(CLEAN_PROJECT, env_file, env)

                # The restored cluster replays archived WAL up to the newest
                # segment; poll until the marker rows are queryable and equal.
                restored_checksum = ""
                deadline = time.monotonic() + 300
                while time.monotonic() < deadline:
                    result = subprocess.run(
                        [*self._compose(CLEAN_PROJECT, env_file), "exec", "-T",
                         "-e", "PGPASSWORD=" + self._env_value(
                             env_file, "CDS_POSTGRES_SUPERUSER_PASSWORD"),
                         PG_SERVICE, "psql", "-U", "postgres", "-d", "postgres", "-tAc",
                         f"SELECT count(*) || ':' || md5(string_agg(marker, '' ORDER BY id)) "  # noqa: S608 - test-only query over hardcoded constants
                         f"FROM {MARKER_TABLE};"],
                        env=env, capture_output=True, text=True, errors="replace", timeout=120,
                    )
                    if result.returncode == 0 and result.stdout.strip() == expected_checksum:
                        restored_checksum = result.stdout.strip()
                        break
                    time.sleep(5)
                self.assertEqual(
                    restored_checksum, expected_checksum,
                    msg=f"marker data did not survive the clean-environment restore; "
                        f"expected {expected_checksum!r}, last query returned "
                        f"{restored_checksum!r}",
                )

                # Recovery expectations: the measured restore time must stay
                # inside the profile's rtoMinutes, and the restore point used
                # must be within rpoMinutes of the marker write (the
                # incremental backup that captured it).
                restore_point_age_seconds = time.time() - restore_point_epoch
                print(
                    f"\nrestore verification measurements: restore time "
                    f"{elapsed_restore_seconds:.0f}s (policy rtoMinutes="
                    f"{policy['rtoMinutes']}), restore point age "
                    f"{restore_point_age_seconds:.0f}s (policy rpoMinutes="
                    f"{policy['rpoMinutes']}), data loss: none "
                    f"(checksum {expected_checksum.split(':', 1)[1]})",
                    flush=True,
                )
                self.assertLess(
                    elapsed_restore_seconds, policy["rtoMinutes"] * 60,
                    msg=f"measured restore time {elapsed_restore_seconds:.0f}s exceeds the "
                        f"profile's rtoMinutes={policy['rtoMinutes']}",
                )
                self.assertLess(
                    restore_point_age_seconds, policy["rpoMinutes"] * 60,
                    msg=f"restore point age {restore_point_age_seconds:.0f}s exceeds the "
                        f"profile's rpoMinutes={policy['rpoMinutes']}",
                )
            finally:
                if os.getenv("CDS_KEEP_DOCKER_STACK") != "1":
                    teardown()
