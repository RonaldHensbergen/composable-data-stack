"""Docker runtime proof for the vault profile's init-db.sh (issue #836).

Boots ONLY the postgres service from the rendered vault profile with a
fresh volume and non-default database/role names, then asserts the
bootstrap really happened: the three configured roles and databases
exist, no stray hardcoded roles exist, each database ACL carries an
explicit grant for its configured role (PUBLIC already gets CONNECT by
default, so bare connectivity proves nothing), and each user's password
actually works.

Runs only when CDS_RUN_DOCKER_SMOKE=1 with a working daemon (the
docker-smoke-test workflow); everywhere else it skips in milliseconds,
so the normal unit-test suite stays Docker-free. Uses its own compose
project name, so cleanup can never touch other stacks' volumes; the
GitHub-hosted runner itself is ephemeral either way.
"""

import os
import re
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from cli.planner import build_plan
from cli.renderer import render_compose
from cli.validator import validate_profile

REPO_ROOT = Path(__file__).resolve().parent.parent
VAULT_PROFILE = REPO_ROOT / "profiles" / "local-dagster-postgres-superset-vault" / "profile.yaml"

PROJECT = "cds-vault-initdb-proof"
SERVICE = "postgres"

# Non-default names: proves nothing is hardcoded (a literal 'analytics'
# role or default-named database would fail these assertions).
DBS = {
    "analytics": ("analytics_t1", "analytics_t1", "analytics_testpass"),
    "dagster": ("dagster_t1", "dagster_t1", "dagster_testpass"),
    "superset": ("superset_t1", "superset_t1", "superset_testpass"),
}

# Transient container-registry error substrings seen in CI (mirrors
# test_compose_runtime_smoke.py): retry only these, fail fast otherwise.
_TRANSIENT_REGISTRY_ERROR_MARKERS = (
    "Client.Timeout exceeded while awaiting headers",
    "TLS handshake timeout",
    "connection reset by peer",
    "i/o timeout",
    "net/http: request canceled",
    "toomanyrequests",
    "unexpected EOF",
)


class VaultInitDbRuntimeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if os.getenv("CDS_RUN_DOCKER_SMOKE") != "1":
            raise unittest.SkipTest("Set CDS_RUN_DOCKER_SMOKE=1 to run Docker runtime proof tests")

        if shutil.which("docker") is None:
            raise unittest.SkipTest("Docker CLI not available")

        docker_info = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            text=True,
        )
        if docker_info.returncode != 0:
            raise unittest.SkipTest("Docker daemon is not available")

    @staticmethod
    def _redact(value: str, env: dict[str, str]) -> str:
        redacted = value
        for key, secret in env.items():
            if secret and any(marker in key.upper() for marker in ("PASSWORD", "SECRET", "TOKEN")):
                redacted = redacted.replace(secret, "<redacted>")
        return re.sub(
            r"(postgresql(?:\+[a-z0-9]+)?://[^:/\s]*:)[^@/\s]+",
            r"\1<redacted>",
            redacted,
            flags=re.IGNORECASE,
        )

    def _run(self, command: list[str], env: dict[str, str], timeout: int = 600) -> subprocess.CompletedProcess:
        result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=timeout)
        if result.returncode != 0:
            self.fail(
                "Command failed: {cmd}\nstdout:\n{stdout}\nstderr:\n{stderr}".format(
                    cmd=" ".join(command),
                    stdout=self._redact(result.stdout, env),
                    stderr=self._redact(result.stderr, env),
                )
            )
        return result

    def _compose(self, compose_file: Path) -> list[str]:
        return ["docker", "compose", "-f", str(compose_file), "-p", PROJECT]

    def _up_postgres(self, compose_file: Path, env: dict[str, str]) -> None:
        last_output = ""
        for attempt in range(1, 4):
            result = subprocess.run(
                [*self._compose(compose_file), "up", "-d", SERVICE],
                env=env,
                capture_output=True,
                text=True,
                timeout=1200,
            )
            if result.returncode == 0:
                return
            last_output = result.stdout + result.stderr
            if not any(m in last_output for m in _TRANSIENT_REGISTRY_ERROR_MARKERS) or attempt == 3:
                break
            time.sleep(15)
        self.fail(
            "Could not start postgres after 3 attempt(s), container logs follow.\n"
            f"up output:\n{self._redact(last_output, env)}\n"
            f"logs:\n{self._redact(self._logs(compose_file, env), env)}"
        )

    def _logs(self, compose_file: Path, env: dict[str, str]) -> str:
        result = subprocess.run(
            [*self._compose(compose_file), "logs", "--tail", "150", SERVICE],
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )
        return result.stdout + result.stderr

    def _wait_ready(self, compose_file: Path, env: dict[str, str], deadline_seconds: int = 300) -> None:
        started = time.monotonic()
        last_error = ""
        while time.monotonic() - started < deadline_seconds:
            result = subprocess.run(
                [*self._compose(compose_file), "exec", "-T", SERVICE, "pg_isready", "-U", "postgres"],
                env=env,
                capture_output=True,
                text=True,
                timeout=60,
            )
            if result.returncode == 0:
                return
            last_error = result.stdout + result.stderr
            time.sleep(5)
        self.fail(
            f"postgres never became ready within {deadline_seconds}s, container logs follow.\n"
            f"last pg_isready error:\n{self._redact(last_error, env)}\n"
            f"logs:\n{self._redact(self._logs(compose_file, env), env)}"
        )

    def _psql(self, compose_file: Path, env: dict[str, str], sql: str,
              user: str = "postgres", dbname: str = "postgres", password: str | None = None) -> str:
        command = [*self._compose(compose_file), "exec", "-T"]
        run_env = dict(env)
        if password is not None:
            command += ["-e", f"PGPASSWORD={password}"]
        command += [SERVICE, "psql", "-U", user, "-d", dbname, "-tAc", sql]
        result = subprocess.run(command, env=run_env, capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            self.fail(
                f"psql failed: {sql}\n"
                f"stdout:\n{self._redact(result.stdout, env)}\n"
                f"stderr:\n{self._redact(result.stderr, env)}\n"
                f"logs:\n{self._redact(self._logs(compose_file, env), env)}"
            )
        return result.stdout.strip()

    def test_vault_init_db_bootstraps_configured_roles_and_grants(self) -> None:
        diagnostics = validate_profile(str(VAULT_PROFILE))
        self.assertEqual(
            [d for d in diagnostics if d.level == "error"], [],
            msg=f"unexpected validation errors: {diagnostics}",
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            env_file = Path(tmpdir) / ".env"
            lines = [
                "CDS_VAULT_TOKEN=test-vault-token",
                "CDS_POSTGRES_SUPERUSER_PASSWORD=superuser_testpass",
                "CDS_SUPERSET_SECRET_KEY=sekret",
                "CDS_SUPERSET_ADMIN_PASSWORD=adminpass",
            ]
            secret_env: dict[str, str] = {}
            for prefix, (dbname, username, password) in DBS.items():
                upper = prefix.upper()
                lines += [
                    f"CDS_{upper}_DB_NAME={dbname}",
                    f"CDS_{upper}_DB_USER={username}",
                    f"CDS_{upper}_DB_PASSWORD={password}",
                ]
                secret_env[f"CDS_{upper}_DB_PASSWORD"] = password
            env_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

            plan, plan_diags = build_plan(str(VAULT_PROFILE), env_file=str(env_file))
            self.assertIsNotNone(plan)
            self.assertEqual(
                [d for d in plan_diags if d.level == "error"], [],
                msg=f"unexpected plan errors: {plan_diags}",
            )

            output, render_diags = render_compose(plan, env_file=str(env_file))
            self.assertEqual(
                [d for d in render_diags if d.level == "error"], [],
                msg=f"unexpected render errors: {render_diags}",
            )
            compose_file = Path(tmpdir) / "proof-compose.yml"
            compose_file.write_text(output, encoding="utf-8")

            env = os.environ.copy()

            def cleanup() -> None:
                subprocess.run(
                    [*self._compose(compose_file), "down", "-v", "--remove-orphans"],
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=300,
                )

            try:
                # Fresh volume: the postgres entrypoint only runs init-db.sh
                # when the data directory is empty.
                cleanup()
                self._up_postgres(compose_file, env)
                self._wait_ready(compose_file, env)

                names = sorted(dbname for dbname, _, _ in DBS.values())
                users = sorted(username for _, username, _ in DBS.values())

                roles = self._psql(
                    compose_file, env,
                    "SELECT rolname FROM pg_roles WHERE rolname IN ({}) ORDER BY 1;".format(  # noqa: S608 - test-only query over hardcoded DBS names
                        ",".join(f"'{u}'" for u in users)
                    ),
                ).splitlines()
                self.assertEqual(roles, users)

                stray = self._psql(
                    compose_file, env,
                    "SELECT rolname FROM pg_roles WHERE rolname IN ('analytics', 'dagster', 'superset');",
                )
                self.assertEqual(stray, "", f"stray hardcoded roles exist: {stray!r}")

                databases = self._psql(
                    compose_file, env,
                    "SELECT datname FROM pg_database WHERE datname IN ({}) ORDER BY 1;".format(  # noqa: S608 - test-only query over hardcoded DBS names
                        ",".join(f"'{d}'" for d in names)
                    ),
                ).splitlines()
                self.assertEqual(databases, names)

                # Explicit per-role grants: PUBLIC already holds CONNECT by
                # default, so only datacl entries prove the GRANTs ran.
                for _prefix, (dbname, username, _password) in DBS.items():
                    datacl = self._psql(
                        compose_file, env,
                        f"SELECT datacl::text FROM pg_database WHERE datname = '{dbname}';",  # noqa: S608 - test-only query over hardcoded DBS names
                    )
                    self.assertIn(
                        f"{username}=CTc",
                        datacl,
                        f"no explicit grant for {username} on {dbname}: {datacl!r}",
                    )

                # Each user's password actually works against its own database.
                for _prefix, (dbname, username, password) in DBS.items():
                    self.assertEqual(
                        self._psql(compose_file, env, "SELECT 1;", user=username, dbname=dbname, password=password),
                        "1",
                    )

                # Schema-level grants: each user can create objects in its
                # own database's public schema (USAGE + CREATE).
                for _prefix, (dbname, username, password) in DBS.items():
                    self.assertEqual(
                        self._psql(
                            compose_file, env,
                            f"SELECT has_schema_privilege('{username}', 'public', 'CREATE');",  # noqa: S608 - test-only query over hardcoded DBS names
                            user=username, dbname=dbname, password=password,
                        ),
                        "t",
                        f"{username} lacks CREATE on {dbname}.public",
                    )
            finally:
                cleanup()


if __name__ == "__main__":
    unittest.main()
