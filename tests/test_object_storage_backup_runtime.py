"""Docker runtime proof for the object-storage snapshot provider (issue #669).

Executes the recovery procedure the object-storage-backup module documents
against real services, so the acceptance criterion "a backup is only
trustworthy once restored" is exercised end to end for bucket contents:

1. validate -> plan -> render a production profile with two Garage
   instances (source and destination) and the object-storage-backup
   module bound to the source's object-storage contract.
2. Boot both Garage instances, seed a marker object into the source
   bucket, and start the backup service.
3. Wait for the service's scheduled snapshot pass: the marker must
   appear under an immutable, UTC-timestamped prefix on the destination
   (<prefix>/<bucket>/<timestamp>/), and the pass must finish by writing
   its .cds-snapshot-complete marker -- only a completed prefix counts
   as a snapshot.
4. Overwrite the marker in the source bucket, then restore it by copying
   the timestamped prefix back with rclone -- the documented recovery
   procedure -- and verify the original content returned byte-for-byte.

Runs only with CDS_RUN_DOCKER_SMOKE=1 and a working daemon; skips
otherwise, like tests/test_restic_backup_restore_runtime.py.
"""

import os
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
COMPOSE_FILE = REPO_ROOT / "docker-compose.objstore-proof.yml"

PROJECT = "cds-objstore-proof"
SOURCE_SERVICE = "garage"
DESTINATION_SERVICE = "backup-garage-garage"
BACKUP_SERVICE = "object-storage-backup-rclone"
NETWORK = f"{PROJECT}_objstore-proof"

RCLONE_IMAGE = "rclone/rclone:1.74.0@sha256:d2e0e88359d0b2e67cfcd2c43d5405185eb8adfc207079df27c42da82c5207bc"
SNAPSHOT_PREFIX = "cds-object-storage-snapshots"
SOURCE_BUCKET = "cds-data"
DESTINATION_BUCKET = "cds-backups"
MARKER_KEY = "marker.txt"
COMPLETION_MARKER = ".cds-snapshot-complete"
MARKER_V1 = "cds-objstore-proof-marker-v1\n"
MARKER_V2 = "cds-objstore-proof-marker-v2-overwritten\n"

ENV_VALUES = {
    "CDS_SRC_ACCESS_KEY": "GK" + "1" * 32,
    "CDS_SRC_SECRET_KEY": "a" * 64,
    "CDS_SRC_RPC_SECRET": "b" * 64,
    "CDS_DST_ACCESS_KEY": "GK" + "2" * 32,
    "CDS_DST_SECRET_KEY": "c" * 64,
    "CDS_DST_RPC_SECRET": "d" * 64,
    "CDS_BACKUP_ENCRYPTION_KEY": "e" * 32,
}

# Retry only known-transient registry flakes (mirrors the other runtime proofs).
_TRANSIENT_REGISTRY_ERROR_MARKERS = (
    "Client.Timeout exceeded while awaiting headers",
    "connection reset by peer",
    "i/o timeout",
)


def _profile() -> dict:
    return {
        "apiVersion": "cds/v1alpha1",
        "kind": "Profile",
        "metadata": {"name": "objstore-proof", "environment": "production"},
        "spec": {
            "runtime": {"type": "docker-compose"},
            "modules": [
                {
                    "id": "garage",
                    "source": "../../modules/storage/garage",
                    "version": "0.1.0",
                    "enabled": True,
                    "config": {
                        "bucket": SOURCE_BUCKET,
                        "accessKeyFrom": "secrets.src_access_key",
                        "secretKeyFrom": "secrets.src_secret_key",
                        "rpcSecretFrom": "secrets.src_rpc_secret",
                    },
                },
                {
                    # A second Garage instance is the S3-compatible
                    # destination: a different backend *host*, as the
                    # module README prescribes. Its own (unbound)
                    # object-storage contract is irrelevant here; the
                    # destination is explicit endpoint configuration.
                    "id": "backup-garage",
                    "source": "../../modules/storage/garage",
                    "version": "0.1.0",
                    "enabled": True,
                    "config": {
                        "bucket": DESTINATION_BUCKET,
                        "accessKeyFrom": "secrets.dst_access_key",
                        "secretKeyFrom": "secrets.dst_secret_key",
                        "rpcSecretFrom": "secrets.dst_rpc_secret",
                        "port": 3901,
                    },
                },
                {
                    "id": "object-storage-backup",
                    "source": "../../modules-experimental/backup/object-storage",
                    "version": "0.1.0",
                    "enabled": True,
                    "dependsOn": ["garage"],
                    "config": {
                        "sourceStorage": {"contractRef": "garage.object-storage"},
                        "destination": {
                            "endpoint": "http://backup-garage-garage:3900",
                            "bucket": DESTINATION_BUCKET,
                            "region": "garage",
                            "accessKeyFrom": "secrets.dst_access_key",
                            "secretKeyFrom": "secrets.dst_secret_key",
                        },
                    },
                },
            ],
            "secrets": {
                "provider": {"type": "env"},
                "values": {
                    alias: {"env": env, "required": True}
                    for alias, env in {
                        "src_access_key": "CDS_SRC_ACCESS_KEY",
                        "src_secret_key": "CDS_SRC_SECRET_KEY",
                        "src_rpc_secret": "CDS_SRC_RPC_SECRET",
                        "dst_access_key": "CDS_DST_ACCESS_KEY",
                        "dst_secret_key": "CDS_DST_SECRET_KEY",
                        "dst_rpc_secret": "CDS_DST_RPC_SECRET",
                        "backup_encryption_key": "CDS_BACKUP_ENCRYPTION_KEY",
                    }.items()
                },
            },
            "backup": {
                "encryption": {"keyFrom": "secrets.backup_encryption_key"},
                "policies": [
                    {
                        "workloadClass": "configuration",
                        "retentionDays": 90,
                        "rpoMinutes": 1440,
                        "rtoMinutes": 120,
                        "restoreVerification": {"frequencyDays": 90},
                    }
                ],
            },
        },
    }


def _source_env() -> list[str]:
    return [
        "-e", "RCLONE_CONFIG_SOURCE_TYPE=s3",
        "-e", "RCLONE_CONFIG_SOURCE_PROVIDER=Other",
        "-e", "RCLONE_CONFIG_SOURCE_ENV_AUTH=false",
        "-e", f"RCLONE_CONFIG_SOURCE_ACCESS_KEY_ID={ENV_VALUES['CDS_SRC_ACCESS_KEY']}",
        "-e", f"RCLONE_CONFIG_SOURCE_SECRET_ACCESS_KEY={ENV_VALUES['CDS_SRC_SECRET_KEY']}",
        "-e", f"RCLONE_CONFIG_SOURCE_ENDPOINT=http://{SOURCE_SERVICE}:3900",
        "-e", "RCLONE_CONFIG_SOURCE_REGION=garage",
        "-e", "RCLONE_CONFIG_SOURCE_FORCE_PATH_STYLE=true",
    ]


def _destination_env() -> list[str]:
    return [
        "-e", "RCLONE_CONFIG_DESTINATION_TYPE=s3",
        "-e", "RCLONE_CONFIG_DESTINATION_PROVIDER=Other",
        "-e", "RCLONE_CONFIG_DESTINATION_ENV_AUTH=false",
        "-e", f"RCLONE_CONFIG_DESTINATION_ACCESS_KEY_ID={ENV_VALUES['CDS_DST_ACCESS_KEY']}",
        "-e", f"RCLONE_CONFIG_DESTINATION_SECRET_ACCESS_KEY={ENV_VALUES['CDS_DST_SECRET_KEY']}",
        "-e", f"RCLONE_CONFIG_DESTINATION_ENDPOINT=http://{DESTINATION_SERVICE}:3900",
        "-e", "RCLONE_CONFIG_DESTINATION_REGION=garage",
        "-e", "RCLONE_CONFIG_DESTINATION_FORCE_PATH_STYLE=true",
    ]


class ObjectStorageBackupRuntimeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if os.getenv("CDS_RUN_DOCKER_SMOKE") != "1":
            raise unittest.SkipTest("Set CDS_RUN_DOCKER_SMOKE=1 to run Docker runtime proof tests")

        if shutil.which("docker") is None:
            raise unittest.SkipTest("Docker CLI not available")

        docker_info = subprocess.run(["docker", "info"], capture_output=True, text=True, errors="replace")
        if docker_info.returncode != 0:
            raise unittest.SkipTest("Docker daemon is not available")

    def _compose(self) -> list[str]:
        # Bind sources resolve relative to the compose file's directory, so
        # it lives at the repo root like production (the garage module's
        # garage.toml bind, notably).
        return [
            "docker", "compose",
            "--env-file", str(self._env_file),
            "-f", str(COMPOSE_FILE),
            "-p", PROJECT,
        ]

    def _run(self, command: list[str], timeout: int = 1200,
             allow_failure: bool = False) -> subprocess.CompletedProcess:
        result = subprocess.run(
            command, env=os.environ.copy(), capture_output=True, text=True,
            errors="replace", timeout=timeout,
        )
        if result.returncode != 0 and not allow_failure:
            self.fail(
                "Command failed: {cmd}\nstdout:\n{stdout}\nstderr:\n{stderr}".format(
                    cmd=" ".join(command),
                    stdout=result.stdout,
                    stderr=result.stderr,
                )
            )
        return result

    def _rclone(self, remote_env: list[str], args: list[str], timeout: int = 300,
                allow_failure: bool = False,
                volumes: list[str] | None = None) -> subprocess.CompletedProcess:
        return self._run(
            [
                "docker", "run", "--rm", "--network", NETWORK,
                *([vol for v in (volumes or []) for vol in ("-v", v)]),
                *remote_env, RCLONE_IMAGE, "--config", "/dev/null", *args,
            ],
            timeout=timeout,
            allow_failure=allow_failure,
        )

    def _seed(self, content: str) -> None:
        seed_dir = self._work / "seed"
        seed_dir.mkdir(parents=True, exist_ok=True)
        seed_dir.chmod(0o755)
        (seed_dir / MARKER_KEY).write_text(content, encoding="utf-8")
        self._rclone(
            _source_env(),
            ["copy", "/seed", f"source:{SOURCE_BUCKET}"],
            volumes=[f"{seed_dir}:/seed:ro"],
        )
        # Make the overwrite real: rclone copy skips identical files, so
        # wait until the bucket reports the new content before continuing.
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            cat = self._rclone(
                _source_env(), ["cat", f"source:{SOURCE_BUCKET}/{MARKER_KEY}"],
                allow_failure=True,
            )
            if cat.returncode == 0 and cat.stdout == content:
                return
            time.sleep(5)
        self.fail(f"seeding the source bucket with {content!r} did not converge")

    def _wait_healthy(self, service: str, deadline_seconds: int = 300) -> None:
        started = time.monotonic()
        while time.monotonic() - started < deadline_seconds:
            container = self._run(
                [*self._compose(), "ps", "-q", service], timeout=60, allow_failure=True,
            )
            container_id = container.stdout.strip()
            if container_id:
                status = subprocess.run(
                    ["docker", "inspect", "--format", "{{.State.Health.Status}}", container_id],
                    capture_output=True, text=True, errors="replace", timeout=60,
                )
                if status.stdout.strip() == "healthy":
                    return
            time.sleep(5)
        self.fail(f"{service} did not become healthy within {deadline_seconds}s")

    def _wait_for_snapshot(self, deadline_seconds: int = 300) -> str:
        """Returns the snapshot's UTC-timestamped directory id once the
        seeded marker and the pass's completion marker have both landed
        under it on the destination."""
        listing_base = f"destination:{DESTINATION_BUCKET}/{SNAPSHOT_PREFIX}/{SOURCE_BUCKET}"
        started = time.monotonic()
        while time.monotonic() - started < deadline_seconds:
            listing = self._rclone(
                _destination_env(), ["lsf", "--recursive", listing_base],
                allow_failure=True,
            )
            if listing.returncode == 0:
                entries: dict[str, set[str]] = {}
                for line in listing.stdout.splitlines():
                    parts = line.strip("/").split("/")
                    if len(parts) == 2:
                        entries.setdefault(parts[0], set()).add(parts[1])
                for snapshot_id in sorted(entries):
                    # Only a completed pass counts: the module writes the
                    # .cds-snapshot-complete marker last, so its presence
                    # proves the marker object made it too.
                    if entries[snapshot_id] >= {MARKER_KEY, COMPLETION_MARKER}:
                        return snapshot_id
            time.sleep(5)
        self.fail(
            f"no completed snapshot of {SOURCE_BUCKET} appeared on the destination within "
            f"{deadline_seconds}s; backup logs:\n"
            + self._run([*self._compose(), "logs", "--tail", "150", BACKUP_SERVICE]).stdout
        )

    def test_snapshot_recovers_overwritten_object(self) -> None:
        with tempfile.TemporaryDirectory(dir=REPO_ROOT / "tests") as tmpdir:
            self._work = Path(tmpdir)
            # The Docker daemon must be able to traverse the bind source and
            # the compose --env-file path; TemporaryDirectory defaults to
            # owner-only permissions.
            self._work.chmod(0o755)
            self._env_file = self._work / ".env"
            self._env_file.write_text(
                "\n".join(f"{name}={value}" for name, value in ENV_VALUES.items()) + "\n",
                encoding="utf-8",
            )
            profile_path = self._work / "profile.yaml"
            profile_path.write_text(yaml.safe_dump(_profile()), encoding="utf-8")
            with self.subTest("validate and render"):
                diagnostics = validate_profile(str(profile_path))
                self.assertEqual(
                    [d.format() for d in diagnostics if d.level == "error"], []
                )
                plan, _plan_diagnostics = build_plan(
                    str(profile_path), env_file=str(self._env_file)
                )
                assert plan is not None
                compose_text, render_diagnostics = render_compose(
                    plan, env_file=str(self._env_file)
                )
                self.assertEqual(
                    [d.format() for d in render_diagnostics if d.level == "error"], []
                )
                COMPOSE_FILE.write_text(compose_text, encoding="utf-8")
            self.addCleanup(lambda: COMPOSE_FILE.unlink(missing_ok=True))
            self.addCleanup(
                lambda: self._run(
                    [*self._compose(), "down", "-v", "--remove-orphans"],
                    timeout=300, allow_failure=True,
                )
            )

            with self.subTest("boot storage services and seed the source bucket"):
                self._run([*self._compose(), "up", "-d", SOURCE_SERVICE, DESTINATION_SERVICE])
                self._wait_healthy(SOURCE_SERVICE)
                self._wait_healthy(DESTINATION_SERVICE)
                self._seed(MARKER_V1)

            with self.subTest("scheduled snapshot reaches the destination"):
                self._run([*self._compose(), "up", "-d", BACKUP_SERVICE])
                snapshot_id = self._wait_for_snapshot()
                logs = self._run(
                    [*self._compose(), "logs", "--tail", "150", BACKUP_SERVICE]
                ).stdout
                self.assertIn(
                    f"Creating object-storage snapshot {SNAPSHOT_PREFIX}/{SOURCE_BUCKET}",
                    logs,
                )
                self.assertIn(f"Snapshot {snapshot_id} complete", logs)
                self.assertRegex(snapshot_id, r"^\d{8}T\d{6}Z$")

            with self.subTest("overwrite is recoverable from the snapshot"):
                self._seed(MARKER_V2)
                restored = self._rclone(
                    _source_env(),
                    ["cat", f"source:{SOURCE_BUCKET}/{MARKER_KEY}"],
                )
                self.assertEqual(restored.stdout, MARKER_V2)

                # The documented recovery procedure: copy the timestamped
                # prefix back into the source bucket.
                self._rclone(
                    [*_source_env(), *_destination_env()],
                    [
                        "copy",
                        f"destination:{DESTINATION_BUCKET}/{SNAPSHOT_PREFIX}/{SOURCE_BUCKET}/{snapshot_id}",
                        f"source:{SOURCE_BUCKET}",
                    ],
                )
                recovered = self._rclone(
                    _source_env(),
                    ["cat", f"source:{SOURCE_BUCKET}/{MARKER_KEY}"],
                )
                self.assertEqual(
                    recovered.stdout,
                    MARKER_V1,
                    "the snapshot restore did not bring back the original content",
                )


if __name__ == "__main__":
    unittest.main()
