"""Docker lifecycle smoke over the shipped compose profiles (issue #1208).

Runs only with CDS_RUN_DOCKER_SMOKE=1 and a working daemon; skips otherwise.
For each profile in CDS_SMOKE_LIFECYCLE_PROFILES (default: the superset and
vault variants named in #1208) it exercises the full CLI lifecycle:

    cds render -> docker compose build -> docker compose up -d --wait
               -> cds state -> cds down

Runtime stays bounded: no fixture jobs or API calls (those live in
tests/test_compose_runtime_smoke.py for the base profile), and image builds
are cache hits after the first profile. Each profile runs as its own test
method so CI reports failures per profile.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import time
import unittest
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
COMPOSE_FILE = REPO_ROOT / "docker-compose.yml"

DEFAULT_PROFILES = (
    "local-dagster-postgres-superset",
    "local-dagster-postgres-superset-vault",
)

# CI-only sentinel values for every env var the shipped profiles reference
# (mirrors scripts/check_profile_matrix.py); docker compose interpolates
# them into the rendered compose file.
_ENV_DEFAULTS = {
    "CDS_ANALYTICS_DB_NAME": "analytics",
    "CDS_ANALYTICS_DB_USER": "analytics",
    "CDS_ANALYTICS_DB_PASSWORD": "smoke_analytics_testpass",
    "CDS_BACKUP_ENCRYPTION_KEY": "smoke-backup-encryption-key",
    "CDS_CACHE_PASSWORD": "smoke_cache_testpass",
    "CDS_DAGSTER_DB_NAME": "dagster",
    "CDS_DAGSTER_DB_USER": "dagster",
    "CDS_DAGSTER_DB_PASSWORD": "smoke_dagster_testpass",
    "CDS_GRAFANA_ADMIN_PASSWORD": "smoke_grafana_testpass",
    "CDS_IDENTITY_DB_NAME": "identity",
    "CDS_IDENTITY_DB_USER": "identity",
    "CDS_IDENTITY_DB_PASSWORD": "smoke_identity_testpass",
    "CDS_KEYCLOAK_ADMIN_PASSWORD": "smoke_keycloak_testpass",
    "CDS_MARQUEZ_DB_NAME": "marquez",
    "CDS_MARQUEZ_DB_USER": "marquez",
    "CDS_MARQUEZ_DB_PASSWORD": "smoke_marquez_testpass",
    "CDS_POSTGRES_SUPERUSER_PASSWORD": "smoke_postgres_testpass",
    "CDS_SUPERSET_ADMIN_PASSWORD": "smoke_admin_testpass",
    "CDS_SUPERSET_DB_NAME": "superset",
    "CDS_SUPERSET_DB_PASSWORD": "smoke_superset_testpass",
    "CDS_SUPERSET_DB_USER": "superset",
    "CDS_SUPERSET_SECRET_KEY": "smoke-superset-secret-key",
    "CDS_VAULT_TOKEN": "smoke-vault-root-token",
}

# Transient container-registry error substrings seen in CI (e.g. Docker Hub
# pull rate limits/timeouts), used to retry only infrastructure flakiness
# (mirrors test_compose_runtime_smoke.py).
_TRANSIENT_REGISTRY_ERROR_MARKERS = (
    "Client.Timeout exceeded while awaiting headers",
    "TLS handshake timeout",
    "connection reset by peer",
    "i/o timeout",
    "net/http: request canceled",
    "toomanyrequests",
    "unexpected EOF",
)

# docker compose up --wait bound per profile; the deep functional smoke in
# test_compose_runtime_smoke.py owns the longer assertions.
_WAIT_TIMEOUT_SECONDS = "600"

# Bucket labels cds state may legitimately show after a healthy `up --wait`
# (cli/state.py uppercases them: HEALTHY/RUNNING for live services,
# HEALTHY EXIT for completed one-shot init containers).
_OK_STATE_BUCKETS = {"HEALTHY:", "RUNNING:", "HEALTHY EXIT:"}


def _profiles() -> list[str]:
    raw = os.getenv("CDS_SMOKE_LIFECYCLE_PROFILES", "").strip()
    if not raw:
        return list(DEFAULT_PROFILES)
    return [p.strip() for p in raw.split(",") if p.strip()]


class ComposeProfileLifecycleSmokeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if os.getenv("CDS_RUN_DOCKER_SMOKE") != "1":
            raise unittest.SkipTest(
                "Set CDS_RUN_DOCKER_SMOKE=1 to run Docker Compose smoke tests"
            )
        if shutil.which("docker") is None:
            raise unittest.SkipTest("Docker CLI not available")
        docker_info = subprocess.run(
            ["docker", "info"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )
        if docker_info.returncode != 0:
            raise unittest.SkipTest("Docker daemon is not available")

    def _lifecycle(self, profile: str) -> None:
        env = os.environ.copy()
        for key, value in _ENV_DEFAULTS.items():
            env.setdefault(key, value)
        try:
            self._compose_down(env)

            # cds render (writes <repo-root>/docker-compose.yml, the path
            # `cds state` / `cds down` read).
            self._run([sys.executable, "-m", "cli.main", "render", profile], env)
            self.assertTrue(COMPOSE_FILE.exists(), "docker-compose.yml was not generated")
            doc = yaml.safe_load(COMPOSE_FILE.read_text(encoding="utf-8")) or {}
            services = {name for name in doc.get("services", {}) if isinstance(name, str)}
            self.assertTrue(services, "rendered compose file has no services")

            # build -> up --wait
            self._run_with_registry_retry(
                ["docker", "compose", "-f", str(COMPOSE_FILE), "build"], env
            )
            self._run_with_registry_retry(
                [
                    "docker",
                    "compose",
                    "-f",
                    str(COMPOSE_FILE),
                    "up",
                    "-d",
                    "--wait",
                    "--wait-timeout",
                    _WAIT_TIMEOUT_SECONDS,
                ],
                env,
                timeout=900,
            )

            # cds state: every rendered service shows up in an OK bucket.
            state = self._run(
                [sys.executable, "-m", "cli.main", "state", profile, "--no-color"], env
            )
            buckets = {
                line.strip()
                for line in state.stdout.splitlines()
                if line.strip().endswith(":") and not line.startswith(" ")
            }
            self.assertTrue(buckets, "cds state printed no health buckets")
            self.assertLessEqual(
                buckets,
                _OK_STATE_BUCKETS,
                f"cds state reported non-OK buckets: {sorted(buckets - _OK_STATE_BUCKETS)}",
            )
            for service in services:
                self.assertIn(service, state.stdout, f"cds state did not list {service}")

            # docker compose ps agrees: running services healthy, one-shots exited 0.
            for row in self._compose_ps(env):
                row_state = row.get("State")
                if row_state == "running":
                    self.assertIn(
                        row.get("Health"),
                        (None, "", "healthy"),
                        f"{row.get('Service')} is running but not healthy",
                    )
                elif row_state == "exited":
                    self.assertEqual(
                        int(row.get("ExitCode", -1)),
                        0,
                        f"{row.get('Service')} exited non-zero",
                    )
                else:
                    self.fail(f"{row.get('Service')} in unexpected state {row_state}")

            # cds down tears the stack down.
            self._run([sys.executable, "-m", "cli.main", "down", profile], env)
            remaining = [
                row for row in self._compose_ps(env) if row.get("State") == "running"
            ]
            self.assertEqual(
                remaining, [], f"services still running after cds down: {remaining}"
            )
        except BaseException:
            self._dump_stack_diagnostics(env)
            raise
        finally:
            if os.getenv("CDS_KEEP_DOCKER_STACK") != "1":
                self._compose_down(env)

    def _run(
        self,
        command: list[str],
        env: dict[str, str],
        timeout: int = 1200,
    ) -> subprocess.CompletedProcess:
        result = subprocess.run(
            command,
            cwd=REPO_ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if result.returncode != 0:
            self.fail(
                "Command failed: {cmd}\nstdout:\n{stdout}\nstderr:\n{stderr}".format(
                    cmd=" ".join(command),
                    stdout=self._redact(result.stdout, env),
                    stderr=self._redact(result.stderr, env),
                )
            )
        return result

    def _run_with_registry_retry(
        self,
        command: list[str],
        env: dict[str, str],
        timeout: int = 1200,
        attempts: int = 3,
        delay_seconds: int = 15,
    ) -> subprocess.CompletedProcess:
        """Like _run, but retries the whole command on known-transient
        container-registry errors instead of failing on infrastructure
        flakiness unrelated to the code under test (mirrors
        test_compose_runtime_smoke.py)."""
        last_result: subprocess.CompletedProcess | None = None
        for attempt in range(1, attempts + 1):
            result = subprocess.run(
                command,
                cwd=REPO_ROOT,
                env=env,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            if result.returncode == 0:
                return result
            last_result = result
            combined = result.stdout + result.stderr
            is_transient = any(
                marker in combined for marker in _TRANSIENT_REGISTRY_ERROR_MARKERS
            )
            if not is_transient or attempt == attempts:
                break
            time.sleep(delay_seconds)
        self.fail(
            "Command failed after {attempts} attempt(s): {cmd}\nstdout:\n{stdout}\nstderr:\n{stderr}".format(
                attempts=attempts,
                cmd=" ".join(command),
                stdout=self._redact(last_result.stdout, env),
                stderr=self._redact(last_result.stderr, env),
            )
        )

    def _compose_down(self, env: dict[str, str]) -> None:
        if not COMPOSE_FILE.exists():
            return
        subprocess.run(
            [
                "docker",
                "compose",
                "-f",
                str(COMPOSE_FILE),
                "down",
                "-v",
                "--remove-orphans",
            ],
            cwd=REPO_ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=300,
        )

    def _compose_ps(self, env: dict[str, str]) -> list[dict]:
        result = self._run(
            ["docker", "compose", "-f", str(COMPOSE_FILE), "ps", "-a", "--format", "json"],
            env,
        )
        stripped = result.stdout.strip()
        if not stripped:
            return []
        if stripped.startswith("["):
            parsed = json.loads(stripped)
            return parsed if isinstance(parsed, list) else [parsed]
        return [json.loads(line) for line in stripped.splitlines() if line.strip()]

    def _dump_stack_diagnostics(self, env: dict[str, str]) -> None:
        """Print container state and logs so CI failures are debuggable.
        Best effort: never raises."""
        if not COMPOSE_FILE.exists():
            return
        sections = []
        for title, args in (
            ("docker compose ps -a", ["ps", "-a"]),
            ("docker compose logs", ["logs", "--no-color", "--tail", "300"]),
        ):
            try:
                result = subprocess.run(
                    ["docker", "compose", "-f", str(COMPOSE_FILE), *args],
                    cwd=REPO_ROOT,
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=120,
                )
                output = result.stdout + result.stderr
            except (OSError, subprocess.SubprocessError) as exc:
                output = f"could not collect: {exc}"
            sections.append(f"===== {title} =====\n{self._redact(output, env)}")
        report = "\n".join(sections)
        print(report, file=sys.stderr)
        try:
            log_dir = REPO_ROOT / "smoke-logs"
            log_dir.mkdir(exist_ok=True)
            (log_dir / "compose-lifecycle.log").write_text(report, encoding="utf-8")
        except OSError:
            pass

    @staticmethod
    def _redact(value: str, env: dict[str, str]) -> str:
        redacted = value
        for key, secret in env.items():
            if secret and any(
                marker in key.upper() for marker in ("PASSWORD", "SECRET", "TOKEN")
            ):
                redacted = redacted.replace(secret, "<redacted>")
        return re.sub(
            r"(postgresql(?:\+[a-z0-9]+)?://[^:/\s]*:)[^@/\s]+",
            r"\1<redacted>",
            redacted,
            flags=re.IGNORECASE,
        )


def _make_lifecycle_test(profile: str):
    def test(self: ComposeProfileLifecycleSmokeTest) -> None:
        self._lifecycle(profile)

    test.__name__ = f"test_lifecycle_{profile}"
    test.__doc__ = f"render -> build -> up --wait -> state -> down for {profile} (#1208)"
    return test


for _profile in _profiles():
    setattr(
        ComposeProfileLifecycleSmokeTest,
        f"test_lifecycle_{_profile}",
        _make_lifecycle_test(_profile),
    )


if __name__ == "__main__":
    unittest.main()
