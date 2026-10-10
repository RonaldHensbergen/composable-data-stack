"""Docker runtime proof for the vault profile's init-db.sh (#836).

Runs only with CDS_RUN_DOCKER_SMOKE=1 and a working daemon; skips
otherwise. Boots only postgres, on its own compose project, with a fresh
volume and non-default names, then asserts roles, databases, explicit
datacl grants, passwords, and schema privileges.
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

# Non-default names catch hardcoded roles/databases.
DBS = {
    "analytics": ("analytics_t1", "analytics_t1", "analytics_testpass"),
    "dagster": ("dagster_t1", "dagster_t1", "dagster_testpass"),
    "superset": ("superset_t1", "superset_t1", "superset_testpass"),
}

# Retry only known-transient registry flakes (mirrors test_compose_runtime_smoke.py).
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

    def _compose(self, compose_file: Path, env_file: Path) -> list[str]:
        # Bind sources resolve relative to the compose file's directory, so
        # it lives at the repo root like production (deleted afterwards).
        return ["docker", "compose", "--env-file", str(env_file), "-f", str(compose_file), "-p", PROJECT]

    def _up_postgres(self, compose_file: Path, env_file: Path, env: dict[str, str]) -> None:
        last_output = ""
        for attempt in range(1, 4):
            result = subprocess.run(
                [*self._compose(compose_file, env_file), "up", "-d", SERVICE],
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
            f"logs:\n{self._redact(self._logs(compose_file, env_file, env), env)}"
        )

    def _logs(self, compose_file: Path, env_file: Path, env: dict[str, str]) -> str:
        result = subprocess.run(
            [*self._compose(compose_file, env_file), "logs", "--tail", "150", SERVICE],
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )
        return result.stdout + result.stderr

    def _wait_ready(self, compose_file: Path, env_file: Path, env: dict[str, str], deadline_seconds: int = 300) -> None:
        started = time.monotonic()
        last_error = ""
        while time.monotonic() - started < deadline_seconds:
            result = subprocess.run(
                [*self._compose(compose_file, env_file), "exec", "-T", SERVICE, "pg_isready", "-U", "postgres"],
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
            f"logs:\n{self._redact(self._logs(compose_file, env_file, env), env)}"
        )

    @staticmethod
    def _env_value(env_file: Path, name: str) -> str:
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if line.startswith(name + "="):
                return line.split("=", 1)[1]
        raise AssertionError(f"{name} missing from proof env file")

    def _psql(self, compose_file: Path, env_file: Path, env: dict[str, str], sql: str,
              user: str = "postgres", dbname: str = "postgres", password: str | None = None) -> str:
        command = [*self._compose(compose_file, env_file), "exec", "-T"]
        run_env = dict(env)
        resolved = password
        if resolved is None and user == "postgres":
            resolved = self._env_value(env_file, "CDS_POSTGRES_SUPERUSER_PASSWORD")
        if resolved is not None:
            command += ["-e", f"PGPASSWORD={resolved}"]
        command += [SERVICE, "psql", "-U", user, "-d", dbname, "-tAc", sql]
        result = subprocess.run(command, env=run_env, capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            self.fail(
                f"psql failed: {sql}\n"
                f"stdout:\n{self._redact(result.stdout, env)}\n"
                f"stderr:\n{self._redact(result.stderr, env)}\n"
                f"logs:\n{self._redact(self._logs(compose_file, env_file, env), env)}"
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
            for prefix, (dbname, username, password) in DBS.items():
                upper = prefix.upper()
                lines += [
                    f"CDS_{upper}_DB_NAME={dbname}",
                    f"CDS_{upper}_DB_USER={username}",
                    f"CDS_{upper}_DB_PASSWORD={password}",
                ]
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
            compose_file = REPO_ROOT / "docker-compose.vault-proof.yml"
            compose_file.write_text(output, encoding="utf-8")

            # Scrub ambient CDS_* so the shell can never override proof values.
            env = {k: v for k, v in os.environ.items() if not k.startswith("CDS_")}

            def cleanup() -> None:
                subprocess.run(
                    [*self._compose(compose_file, env_file), "down", "-v", "--remove-orphans"],
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=300,
                )

            try:
                # Fresh volume: postgres entrypoint runs init-db.sh only on empty data dir.
                cleanup()
                self._up_postgres(compose_file, env_file, env)
                self._wait_ready(compose_file, env_file, env)

                names = sorted(dbname for dbname, _, _ in DBS.values())
                users = sorted(username for _, username, _ in DBS.values())

                roles = self._psql(
                    compose_file, env_file, env,
                    "SELECT rolname FROM pg_roles WHERE rolname IN ({}) ORDER BY 1;".format(  # noqa: S608 - test-only query over hardcoded DBS names
                        ",".join(f"'{u}'" for u in users)
                    ),
                ).splitlines()
                self.assertEqual(roles, users)

                stray = self._psql(
                    compose_file, env_file, env,
                    "SELECT rolname FROM pg_roles WHERE rolname IN ('analytics', 'dagster', 'superset');",
                )
                self.assertEqual(stray, "", f"stray hardcoded roles exist: {stray!r}")

                databases = self._psql(
                    compose_file, env_file, env,
                    "SELECT datname FROM pg_database WHERE datname IN ({}) ORDER BY 1;".format(  # noqa: S608 - test-only query over hardcoded DBS names
                        ",".join(f"'{d}'" for d in names)
                    ),
                ).splitlines()
                self.assertEqual(databases, names)

                # Explicit per-role grants: PUBLIC already holds CONNECT by
                # default, so only datacl entries prove the GRANTs ran.
                for _prefix, (dbname, username, _password) in DBS.items():
                    datacl = self._psql(
                        compose_file, env_file, env,
                        f"SELECT datacl::text FROM pg_database WHERE datname = '{dbname}';",  # noqa: S608 - test-only query over hardcoded DBS names
                    )
                    self.assertIn(
                        f"{username}=CTc",
                        datacl,
                        f"no explicit grant for {username} on {dbname}: {datacl!r}",
                    )

                for _prefix, (dbname, username, password) in DBS.items():
                    self.assertEqual(
                        self._psql(
                            compose_file, env_file, env, "SELECT 1;",
                            user=username, dbname=dbname, password=password,
                        ),
                        "1",
                    )

                for _prefix, (dbname, username, password) in DBS.items():
                    self.assertEqual(
                        self._psql(
                            compose_file, env_file, env,
                            f"SELECT has_schema_privilege('{username}', 'public', 'CREATE');",  # noqa: S608 - test-only query over hardcoded DBS names
                            user=username, dbname=dbname, password=password,
                        ),
                        "t",
                        f"{username} lacks CREATE on {dbname}.public",
                    )
            finally:
                try:
                    cleanup()
                finally:
                    compose_file.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
