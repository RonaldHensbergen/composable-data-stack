"""Docker runtime proof for the generic file backup provider (issue #668).

Executes the restore-verification procedure of docs/backup-restore.md
section 5 against the restic backup module with BOTH backup-source kinds
bound at once — the file-based warehouse's bind-kind host directory and
the ingestion module's volume-kind state volume — so the acceptance
criterion "a backup is only trustworthy once restored" is exercised end
to end for file/volume data rather than only declared as policy, and the
multi-source restore path of issue #1014 is covered (a per-source
snapshot regression would silently restore only one slot):

1. validate -> plan -> render a production profile wiring the restic
   module to two backup-source contracts via its source slots.
2. Build the restic image, boot the backup sidecar in a dedicated compose
   project, and wait for the scheduler's initial backup pass of the
   seeded marker files (one restic snapshot per pass covering every
   bound source).
3. Update the bind-kind marker and take a second backup pass, so the
   newest restore point is measured, then verify repository integrity
   with restic check.
4. Tear the source project down and copy only the encrypted repository
   volume into a *clean environment*: a second compose project with a
   fresh, empty source directory and a fresh state volume.
5. Prove the artifacts are encrypted: restoring with the wrong key must
   fail, and the marker text must not appear in plaintext in the
   repository.
6. Restore the newest restore point with the correct key and verify BOTH
   markers survived byte-for-byte (sha256).
7. Compare the measured restore time and restore-point age with the
   profile's rtoMinutes/rpoMinutes backup policy.

Runs only with CDS_RUN_DOCKER_SMOKE=1 and a working daemon; skips
otherwise, like tests/test_postgres_backup_restore_runtime.py.
"""

import hashlib
import os
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import yaml

from cli.planner import build_plan
from cli.renderer import render_compose
from cli.validator import validate_profile

REPO_ROOT = Path(__file__).resolve().parent.parent
COMPOSE_FILE = REPO_ROOT / "docker-compose.restic-proof.yml"

SOURCE_PROJECT = "cds-restic-proof-src"
CLEAN_PROJECT = "cds-restic-proof-clean"
REPO_VOLUME = "restic-restic-repo"
DLT_STATE_VOLUME = "dlt-dlt-state"
BACKUP_SERVICE = "restic-backup"
RESTORE_SERVICE = "restic-restore"

MARKER_FILE = "marker.txt"
MARKER_TOKEN = "cds-restic-proof-4b7f2a91"  # noqa: S105 - test marker, not a credential

# Scratch container for volume seeding, ownership setup, and reads.
ALPINE_IMAGE = "alpine:3.20@sha256:d9e853e87e55526f6b2917df91a2115c36dd7c696a35be12163d44e6e2a4b6bc"

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


def _profile(host_directory: str) -> dict:
    return {
        "apiVersion": "cds/v1alpha1",
        "kind": "Profile",
        "metadata": {
            "name": "restic-restore-proof",
            "environment": "production",
        },
        "spec": {
            "runtime": {"type": "docker-compose"},
            "modules": [
                {
                    # The dlt module's required destination-database consume
                    # needs a postgres producer in the profile; only the
                    # restic-backup service is ever started, so the postgres
                    # services are rendered but not run.
                    "id": "postgres",
                    "source": "modules/warehouse/postgres",
                    "version": "0.1.0",
                    "enabled": True,
                    "config": {
                        "database": "analytics",
                        "username": "analytics",
                        "passwordFrom": "secrets.analytics_db_password",
                        "superuserPasswordFrom": "secrets.postgres_superuser_password",
                        "port": 5432,
                        "authentication": {"hostAuth": "scram-sha-256"},
                    },
                },
                {
                    "id": "duckdb",
                    "source": "modules-experimental/warehouse/duckdb",
                    "version": "0.1.0",
                    "enabled": True,
                    "config": {"hostDirectory": host_directory},
                },
                {
                    "id": "dlt",
                    "source": "modules-experimental/ingestion/dlt",
                    "version": "0.1.0",
                    "enabled": True,
                    "dependsOn": ["postgres"],
                    "config": {
                        "destinationDatabase": {"contractRef": "postgres.sql-database"},
                    },
                },
                {
                    "id": "restic",
                    "source": "modules/backup/restic",
                    "version": "0.1.0",
                    "enabled": True,
                    "config": {
                        "sources": {
                            "a": {"contractRef": "duckdb.backup-source"},
                            "b": {"contractRef": "dlt.backup-source"},
                        },
                        "encryptionKeyFrom": "secrets.backup_encryption_key",
                    },
                },
            ],
            "secrets": {
                "provider": {"type": "env"},
                "values": {
                    "analytics_db_password": {
                        "env": "CDS_ANALYTICS_DB_PASSWORD",
                        "required": True,
                    },
                    "postgres_superuser_password": {
                        "env": "CDS_POSTGRES_SUPERUSER_PASSWORD",
                        "required": True,
                    },
                    "backup_encryption_key": {
                        "env": "CDS_BACKUP_ENCRYPTION_KEY",
                        "required": True,
                    },
                },
            },
            "backup": {
                "target": {"contractRef": "restic.backup-target"},
                "encryption": {"keyFrom": "secrets.backup_encryption_key"},
                "policies": [
                    {
                        "workloadClass": "warehouse",
                        "retentionDays": 30,
                        "rpoMinutes": 60,
                        "rtoMinutes": 240,
                        "restoreVerification": {"frequencyDays": 30},
                    },
                    {
                        "workloadClass": "configuration",
                        "retentionDays": 90,
                        "rpoMinutes": 1440,
                        "rtoMinutes": 120,
                        "restoreVerification": {"frequencyDays": 90},
                    },
                ],
            },
        },
    }


class ResticBackupRestoreRuntimeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if os.getenv("CDS_RUN_DOCKER_SMOKE") != "1":
            raise unittest.SkipTest("Set CDS_RUN_DOCKER_SMOKE=1 to run Docker runtime proof tests")

        if shutil.which("docker") is None:
            raise unittest.SkipTest("Docker CLI not available")

        docker_info = subprocess.run(["docker", "info"], capture_output=True, text=True, errors="replace")
        if docker_info.returncode != 0:
            raise unittest.SkipTest("Docker daemon is not available")

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
                    stdout=result.stdout,
                    stderr=result.stderr,
                )
            )
        return result

    def _logs(self, project: str, env_file: Path, env: dict[str, str]) -> str:
        result = subprocess.run(
            [*self._compose(project, env_file), "logs", "--tail", "150", BACKUP_SERVICE],
            env=env, capture_output=True, text=True, errors="replace", timeout=120,
        )
        return result.stdout + result.stderr

    def _wait_for_backup_pass(self, project: str, env_file: Path, env: dict[str, str],
                              deadline_seconds: int = 300) -> None:
        """Wait until the scheduler logs a completed backup pass of both
        source mounts (one restic snapshot per pass, issue #1014), and fail
        with the container logs otherwise."""
        started = time.monotonic()
        while time.monotonic() - started < deadline_seconds:
            if "backed up /backup/a /backup/b" in self._logs(project, env_file, env):
                return
            time.sleep(5)
        self.fail(
            f"No backup pass appeared within {deadline_seconds}s (project {project}), "
            f"container logs follow.\n{self._logs(project, env_file, env)}"
        )

    def _restic(self, project: str, env_file: Path, env: dict[str, str],
                args: list[str], timeout: int = 600) -> subprocess.CompletedProcess:
        # RESTIC_REPOSITORY is exported inside the scheduler's process, so a
        # plain `docker compose exec` does not see it; mirror the
        # cds-restic-env resolution the entrypoint performs.
        command = [
            *self._compose(project, env_file), "exec", "-T", BACKUP_SERVICE,
            "sh", "-c",
            ". /usr/local/bin/cds-restic-env && restic_env_setup && restic " + " ".join(args),
        ]
        result = subprocess.run(command, env=env, capture_output=True, text=True, errors="replace", timeout=timeout)
        if result.returncode != 0:
            self.fail(
                f"restic {' '.join(args)} failed (project {project})\n"
                f"stdout:\n{result.stdout}\n"
                f"stderr:\n{result.stderr}\n"
                f"logs:\n{self._logs(project, env_file, env)}"
            )
        return result

    def _alpine(self, binds: list[str], command: str, env: dict[str, str],
                user: str | None = None, timeout: int = 300) -> subprocess.CompletedProcess:
        """Runs a scratch Alpine container (digest-pinned) with the given
        volume binds; used for volume seeding, ownership setup, and reads."""
        return subprocess.run(
            ["docker", "run", "--rm",
             *(["--user", user] if user else []),
             "--entrypoint", "/bin/sh", *binds, ALPINE_IMAGE, "-c", command],
            env=env, capture_output=True, text=True, errors="replace", timeout=timeout,
        )

    @staticmethod
    def _volume_name(project: str, volume: str) -> str:
        return f"{project}_{volume}"

    @staticmethod
    def _marker_checksum(marker_text: str) -> str:
        return hashlib.sha256(marker_text.encode("utf-8")).hexdigest()

    def test_backup_restores_into_clean_environment(self) -> None:
        # The env file, host directory, and compose file all live inside a
        # temporary directory under the repo root: `docker compose
        # --env-file` is executed by the Docker CLI, which on dev machines
        # may not share the test process's system temp dir, and bind paths
        # in the rendered Compose resolve relative to the compose file.
        with tempfile.TemporaryDirectory(prefix="cds-restic-proof-", dir=REPO_ROOT) as tmpdir:
            work = Path(tmpdir)
            env_file = work / ".env"
            env_file.write_text(
                "CDS_ANALYTICS_DB_PASSWORD=analytics-restore-proof-pass-1a\n"
                "CDS_POSTGRES_SUPERUSER_PASSWORD=superuser-restore-proof-pass-2b\n"
                "CDS_BACKUP_ENCRYPTION_KEY=restic-restore-proof-test-key-9d2f\n",
                encoding="utf-8",
            )

            # The bind source lives under the same temporary directory and
            # is referenced relative to the repo-root compose file.
            host_dir = work / "duckdb"
            host_dir.mkdir()
            # The warehouse module's init service chmods the directory
            # 0777; mirror that here so the restore (which runs as the
            # non-root restic user) can write into it.
            host_dir.chmod(0o777)

            marker_path = host_dir / MARKER_FILE

            def seed_marker(text: str) -> None:
                marker_path.write_text(text, encoding="utf-8")
                marker_path.chmod(0o666)

            # Compile path: the production profile must validate, plan,
            # and render with the backup module bound to the warehouse's
            # backup-source contract.
            profile_file = work / "profile.yaml"
            relative_host_dir = f"./{host_dir.relative_to(REPO_ROOT).as_posix()}"
            profile_file.write_text(
                yaml.safe_dump(_profile(relative_host_dir)), encoding="utf-8"
            )

            with mock.patch.dict(
                "os.environ", {"CDS_MODULE_PATH": str(REPO_ROOT)}, clear=False
            ):
                diagnostics = validate_profile(str(profile_file))
                self.assertEqual(
                    [d for d in diagnostics if d.level == "error"], [],
                    msg=f"unexpected validation errors: {diagnostics}",
                )

                plan, plan_diags = build_plan(str(profile_file), env_file=str(env_file))
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

            policy = next(
                p
                for p in _profile(relative_host_dir)["spec"]["backup"]["policies"]
                if p["workloadClass"] == "warehouse"
            )

            # Scrub ambient CDS_* so the shell can never override proof values.
            env = {k: v for k, v in os.environ.items() if not k.startswith("CDS_")}

            def teardown() -> None:
                for project in (SOURCE_PROJECT, CLEAN_PROJECT):
                    subprocess.run(
                        [*self._compose(project, env_file), "--profile", "cds-restore",
                         "down", "-v", "--remove-orphans"],
                        env=env, capture_output=True, text=True, errors="replace", timeout=300,
                    )
                # Volumes pre-created before compose ever sees them carry no
                # compose labels, so `down -v` skips them; remove them
                # explicitly.
                for volume in (
                    self._volume_name(CLEAN_PROJECT, REPO_VOLUME),
                    self._volume_name(SOURCE_PROJECT, DLT_STATE_VOLUME),
                    self._volume_name(CLEAN_PROJECT, DLT_STATE_VOLUME),
                ):
                    subprocess.run(
                        ["docker", "volume", "rm", "--force", volume],
                        env=env, capture_output=True, text=True, errors="replace", timeout=60,
                    )
                COMPOSE_FILE.unlink(missing_ok=True)

            try:
                self._run(
                    [*self._compose(SOURCE_PROJECT, env_file), "build", BACKUP_SERVICE], env,
                    timeout=1800,
                )

                # Source environment: backup sidecar over seeded marker data.
                # Slot a (bind-kind) is the host directory; slot b
                # (volume-kind) is the dlt state volume, pre-created so the
                # marker is seeded before the scheduler's first pass reads
                # it (compose reuses the existing volume).
                marker_v1 = f"{MARKER_TOKEN}-v1\n"
                seed_marker(marker_v1)
                marker_b = f"{MARKER_TOKEN}-slotb\n"
                source_dlt_volume = self._volume_name(SOURCE_PROJECT, DLT_STATE_VOLUME)
                self._run(["docker", "volume", "create", source_dlt_volume], env)
                seed_b = self._alpine(
                    ["-v", f"{source_dlt_volume}:/data"],
                    f"echo '{MARKER_TOKEN}-slotb' > /data/{MARKER_FILE}",
                    env,
                )
                self.assertEqual(
                    seed_b.returncode, 0,
                    msg="seeding the dlt state volume failed:\n"
                        + seed_b.stdout + seed_b.stderr,
                )
                self._run(
                    [*self._compose(SOURCE_PROJECT, env_file), "up", "-d", BACKUP_SERVICE], env,
                )
                self._wait_for_backup_pass(SOURCE_PROJECT, env_file, env)

                # Update the bind-kind marker and take a second backup pass
                # of BOTH sources (mirroring the scheduler's one-snapshot
                # pass), so the newest restore point is measured, then
                # verify repository integrity.
                marker_v2 = f"{MARKER_TOKEN}-v2\n"
                seed_marker(marker_v2)
                self._restic(
                    SOURCE_PROJECT, env_file, env, ["backup", "/backup/a", "/backup/b"]
                )
                self._restic(SOURCE_PROJECT, env_file, env, ["check"])
                restore_point_epoch = time.time()
                expected_checksum = self._marker_checksum(marker_v2)
                expected_checksum_b = self._marker_checksum(marker_b)

                # The source project's containers stop here; its volumes stay
                # until the final teardown so failures keep evidence.
                self._run(
                    [*self._compose(SOURCE_PROJECT, env_file), "down", "--remove-orphans"], env,
                )

                # Clean environment: a second compose project with a fresh,
                # empty source directory and a fresh state volume. Only the
                # encrypted repository volume is seeded from the source
                # environment; the dlt state volume is pre-created and
                # chowned to the restore user (uid 10000) so the restore
                # service can write into it (pre-created volumes are
                # otherwise root-owned).
                clean_repo_volume = self._volume_name(CLEAN_PROJECT, REPO_VOLUME)
                clean_dlt_volume = self._volume_name(CLEAN_PROJECT, DLT_STATE_VOLUME)
                source_repo_volume = self._volume_name(SOURCE_PROJECT, REPO_VOLUME)
                self._run(["docker", "volume", "create", clean_repo_volume], env)
                self._run(["docker", "volume", "create", clean_dlt_volume], env)
                prepare_clean = self._alpine(
                    ["-v", f"{clean_dlt_volume}:/data"],
                    "chown 10000:10000 /data && chmod 700 /data",
                    env,
                )
                self.assertEqual(
                    prepare_clean.returncode, 0,
                    msg="preparing the clean dlt state volume failed:\n"
                        + prepare_clean.stdout + prepare_clean.stderr,
                )
                copy_output = self._alpine(
                    ["-v", f"{source_repo_volume}:/from:ro",
                     "-v", f"{clean_repo_volume}:/to"],
                    "cp -a /from/. /to/ && chown -R 10000:10000 /to && chmod 700 /to",
                    env, user="0:0",
                )
                self.assertEqual(
                    copy_output.returncode, 0,
                    msg="repository volume copy failed:\n" + copy_output.stdout + copy_output.stderr,
                )

                # Empty the source directory: the clean environment restores
                # into a fresh, empty warehouse directory.
                marker_path.unlink()
                self.assertFalse(marker_path.exists())

                # Encryption proof 1: the marker text must not appear in
                # plaintext in the repository at rest.
                grep = self._alpine(
                    ["-v", f"{clean_repo_volume}:/repo:ro"],
                    f"grep -rasq {MARKER_TOKEN!r} /repo; if [ $? -eq 0 ]; then "
                    "echo 'marker found in plaintext'; exit 1; fi; exit 0",
                    env,
                )
                self.assertEqual(
                    grep.returncode, 0,
                    msg="backup repository exposes the marker in plaintext:\n" + grep.stdout + grep.stderr,
                )

                # Encryption proof 2: restoring with the wrong key must fail.
                wrong_key = self._run(
                    [*self._compose(CLEAN_PROJECT, env_file), "--profile", "cds-restore",
                     "run", "--rm", "-e", "RESTIC_PASSWORD=wrong-key-restore-proof",
                     RESTORE_SERVICE],
                    env, allow_failure=True,
                )
                self.assertNotEqual(
                    wrong_key.returncode, 0,
                    msg="restore with a wrong encryption key succeeded; artifacts are not "
                        "encrypted or the key is ignored:\n" + wrong_key.stdout + wrong_key.stderr,
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

                # The bind-kind marker must be back byte-for-byte.
                self.assertTrue(marker_path.is_file(), "marker file missing after restore")
                restored = marker_path.read_text(encoding="utf-8")
                self.assertEqual(
                    self._marker_checksum(restored), expected_checksum,
                    msg=f"marker data did not survive the clean-environment restore; "
                        f"expected checksum {expected_checksum!r}, got "
                        f"{self._marker_checksum(restored)!r}",
                )

                # The volume-kind marker must be back byte-for-byte too
                # (issue #1014: a restore that covers only one bound source
                # fails here).
                read_b = self._alpine(
                    ["-v", f"{clean_dlt_volume}:/data:ro"],
                    f"cat /data/{MARKER_FILE}",
                    env, timeout=120,
                )
                self.assertEqual(
                    read_b.returncode, 0,
                    msg="reading the restored dlt state volume failed:\n"
                        + read_b.stdout + read_b.stderr,
                )
                self.assertEqual(
                    self._marker_checksum(read_b.stdout), expected_checksum_b,
                    msg=f"volume-kind marker data did not survive the "
                        f"clean-environment restore; expected checksum "
                        f"{expected_checksum_b!r}, got "
                        f"{self._marker_checksum(read_b.stdout)!r}",
                )

                # Recovery expectations: the measured restore time must stay
                # inside the profile's rtoMinutes, and the restore point used
                # must be within rpoMinutes of the marker write.
                restore_point_age_seconds = time.time() - restore_point_epoch
                print(
                    f"\nrestore verification measurements: restore time "
                    f"{elapsed_restore_seconds:.0f}s (policy rtoMinutes="
                    f"{policy['rtoMinutes']}), restore point age "
                    f"{restore_point_age_seconds:.0f}s (policy rpoMinutes="
                    f"{policy['rpoMinutes']}), data loss: none",
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


if __name__ == "__main__":
    unittest.main()
