import http.cookiejar
import json
import os
import re
import shutil
import subprocess
import sys
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import yaml


class ComposeRuntimeSmokeTest(unittest.TestCase):
    _LONG_RUNNING_SERVICES = {
        "postgres",
        "dagster-user-code",
        "dagster-webserver",
        "dagster-daemon",
        "keydb",
        "superset",
    }
    _RUN_ID_PATTERN = re.compile(
        r" - ([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}) "
        r"- \d+ - RUN_SUCCESS"
    )

    @classmethod
    def setUpClass(cls):
        cls.repo_root = Path(__file__).resolve().parent.parent
        cls.compose_file = cls.repo_root / "docker-compose.yml"

        if os.getenv("CDS_RUN_DOCKER_SMOKE") != "1":
            raise unittest.SkipTest("Set CDS_RUN_DOCKER_SMOKE=1 to run Docker Compose smoke tests")

        if shutil.which("docker") is None:
            raise unittest.SkipTest("Docker CLI not available")

        docker_info = subprocess.run(
            ["docker", "info"],
            cwd=cls.repo_root,
            capture_output=True,
            text=True,
        )
        if docker_info.returncode != 0:
            raise unittest.SkipTest("Docker daemon is not available")

    def test_render_then_build_then_up(self):
        env = os.environ.copy()
        env.setdefault("CDS_POSTGRES_SUPERUSER_PASSWORD", "postgres_testpass")
        env.setdefault("CDS_ANALYTICS_DB_NAME", "analytics")
        env.setdefault("CDS_ANALYTICS_DB_USER", "analytics")
        env.setdefault("CDS_ANALYTICS_DB_PASSWORD", "analytics_testpass")
        env.setdefault("CDS_DAGSTER_DB_NAME", "dagster")
        env.setdefault("CDS_DAGSTER_DB_USER", "dagster")
        env.setdefault("CDS_DAGSTER_DB_PASSWORD", "dagster_testpass")
        env.setdefault("CDS_SUPERSET_DB_NAME", "superset")
        env.setdefault("CDS_SUPERSET_DB_USER", "superset")
        env.setdefault("CDS_SUPERSET_DB_PASSWORD", "superset_testpass")
        env.setdefault("CDS_SUPERSET_SECRET_KEY", "sekret")
        env.setdefault("CDS_SUPERSET_ADMIN_PASSWORD", "adminpass")

        try:
            if self.compose_file.exists():
                self._run(
                    [
                        "docker",
                        "compose",
                        "-f",
                        str(self.compose_file),
                        "down",
                        "-v",
                        "--remove-orphans",
                    ],
                    env,
                )
            self._run(
                [
                    sys.executable,
                    "-m",
                    "cli.main",
                    "render",
                    "local-dagster-postgres-superset",
                ],
                env,
            )
            self.assertTrue(self.compose_file.exists(), "docker-compose.yml was not generated")

            self._run(["docker", "compose", "-f", str(self.compose_file), "build"], env)
            self._verify_forced_readiness_recovery(env)

            available_services = self._available_services_from_compose()
            for service, command in self._module_exec_checks():
                if service not in available_services:
                    continue
                self._run_exec_with_retry(service, command, env)

            first_run_id = self._execute_offers_fixture_job(env)
            first_snapshot = self._offers_snapshot(env)
            superset_state = self._verify_superset_consumption(env)

            self._run(
                [
                    "docker",
                    "compose",
                    "-f",
                    str(self.compose_file),
                    "restart",
                    *sorted(self._LONG_RUNNING_SERVICES),
                ],
                env,
            )
            self._wait_for_stack_ready(env)

            self.assertEqual(self._dagster_run_status(first_run_id, env), "SUCCESS")
            self.assertEqual(self._offers_snapshot(env), first_snapshot)
            self.assertEqual(
                self._verify_superset_consumption(env, create_missing=False),
                superset_state,
            )

            second_run_id = self._execute_offers_fixture_job(env)
            self.assertNotEqual(second_run_id, first_run_id)
            self.assertEqual(self._offers_snapshot(env), first_snapshot)
            self._verify_postgres_outage_recovery(env, first_snapshot)
        finally:
            if os.getenv("CDS_KEEP_DOCKER_STACK") != "1":
                subprocess.run(
                    [
                        "docker",
                        "compose",
                        "-f",
                        str(self.compose_file),
                        "down",
                        "-v",
                        "--remove-orphans",
                    ],
                    cwd=self.repo_root,
                    env=env,
                    capture_output=True,
                    text=True,
                )

    def _run(
        self,
        command: list[str],
        env: dict[str, str],
        timeout: int = 1200,
    ) -> subprocess.CompletedProcess:
        result = subprocess.run(
            command,
            cwd=self.repo_root,
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

    def _run_exec_with_retry(
        self,
        service: str,
        command: list[str],
        env: dict[str, str],
        attempts: int = 12,
        delay_seconds: int = 5,
    ) -> None:
        for attempt in range(1, attempts + 1):
            result = subprocess.run(
                [
                    "docker",
                    "compose",
                    "-f",
                    str(self.compose_file),
                    "exec",
                    "-T",
                    service,
                    *command,
                ],
                cwd=self.repo_root,
                env=env,
                capture_output=True,
                text=True,
                timeout=300,
            )
            if result.returncode == 0:
                return
            if attempt < attempts:
                time.sleep(delay_seconds)
                continue
            self.fail(
                "Exec check failed after retries: service={service} cmd={cmd}\nstdout:\n{stdout}\nstderr:\n{stderr}".format(
                    service=service,
                    cmd=" ".join(command),
                    stdout=result.stdout,
                    stderr=result.stderr,
                )
            )

    def _available_services_from_compose(self) -> set[str]:
        compose = yaml.safe_load(self.compose_file.read_text(encoding="utf-8")) or {}
        services = compose.get("services", {})
        if not isinstance(services, dict):
            return set()
        return {name for name in services.keys() if isinstance(name, str)}

    def _module_exec_checks(self) -> list[tuple[str, list[str]]]:
        checks_from_env = os.getenv("CDS_DOCKER_EXEC_CHECKS", "").strip()
        if checks_from_env:
            parsed: list[tuple[str, list[str]]] = []
            for raw in checks_from_env.split(";"):
                entry = raw.strip()
                if not entry:
                    continue
                if "|" not in entry:
                    raise ValueError(
                        "Invalid CDS_DOCKER_EXEC_CHECKS entry. Expected 'service|command'. Got: {entry}".format(
                            entry=entry
                        )
                    )
                service, command_text = entry.split("|", 1)
                command = command_text.strip().split()
                if not service.strip() or not command:
                    raise ValueError(
                        "Invalid CDS_DOCKER_EXEC_CHECKS entry. Expected non-empty service and command. Got: {entry}".format(
                            entry=entry
                        )
                    )
                parsed.append((service.strip(), command))
            return parsed

        return [
            (
                "dagster-daemon",
                [
                    "sh",
                    "-lc",
                    "tr '\\0' ' ' </proc/1/cmdline | grep -q 'dagster-daemon run'",
                ],
            ),
            (
                "dagster-user-code",
                [
                    "dagster",
                    "job",
                    "list",
                    "-f",
                    "/app/workdirs/dagster/definitions.py",
                ],
            ),
            (
                "postgres",
                [
                    "pg_isready",
                    "-U",
                    "postgres",
                ],
            ),
        ]

    def _assert_stack_ready(self, env: dict[str, str]) -> None:
        result = self._run(
            [
                "docker",
                "compose",
                "-f",
                str(self.compose_file),
                "ps",
                "-a",
                "--format",
                "json",
            ],
            env,
        )
        rows = self._parse_compose_ps(result.stdout)
        by_service = {row.get("Service"): row for row in rows}

        self.assertEqual(
            set(by_service),
            self._LONG_RUNNING_SERVICES | {"superset-init"},
            "Rendered profile did not start exactly the expected services",
        )
        for service in self._LONG_RUNNING_SERVICES:
            row = by_service[service]
            self.assertEqual(row.get("State"), "running", f"{service} is not running")
            self.assertEqual(row.get("Health"), "healthy", f"{service} is not healthy")

        init = by_service["superset-init"]
        self.assertEqual(init.get("State"), "exited")
        self.assertEqual(int(init.get("ExitCode", -1)), 0)

        self._wait_for_http("http://127.0.0.1:3000/server_info")
        self._wait_for_http("http://127.0.0.1:8088/health")

    def _verify_forced_readiness_recovery(self, env: dict[str, str]) -> None:
        early_services = ["dagster-daemon", "dagster-webserver", "superset"]
        self._run(
            [
                "docker",
                "compose",
                "-f",
                str(self.compose_file),
                "up",
                "-d",
                "--no-deps",
                *early_services,
            ],
            env,
        )
        result = self._run(
            [
                "docker",
                "compose",
                "-f",
                str(self.compose_file),
                "ps",
                "-a",
                "--services",
            ],
            env,
        )
        started_services = set(result.stdout.split())
        self.assertTrue(set(early_services).issubset(started_services))
        self.assertNotIn("postgres", started_services)
        self.assertNotIn("dagster-user-code", started_services)
        self.assertNotIn("keydb", started_services)

        self._run(
            [
                "docker",
                "compose",
                "-f",
                str(self.compose_file),
                "up",
                "-d",
                "--wait",
                "--wait-timeout",
                "300",
            ],
            env,
        )
        self._assert_stack_ready(env)

    def _wait_for_stack_ready(self, env: dict[str, str]) -> None:
        deadline = time.monotonic() + 300
        last_error: AssertionError | None = None
        while time.monotonic() < deadline:
            try:
                self._assert_stack_ready(env)
                return
            except AssertionError as exc:
                last_error = exc
                time.sleep(5)
        self.fail(f"Stack did not recover within 300 seconds: {last_error}")

    @staticmethod
    def _parse_compose_ps(output: str) -> list[dict]:
        stripped = output.strip()
        if not stripped:
            return []
        if stripped.startswith("["):
            parsed = json.loads(stripped)
            return parsed if isinstance(parsed, list) else [parsed]
        return [json.loads(line) for line in stripped.splitlines() if line.strip()]

    def _wait_for_http(self, url: str) -> None:
        deadline = time.monotonic() + 120
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen(url, timeout=10) as response:
                    self.assertEqual(response.status, 200)
                    return
            except (OSError, urllib.error.URLError) as exc:
                last_error = exc
                time.sleep(5)
        self.fail(f"Endpoint did not become healthy: {url}: {last_error}")

    def _execute_offers_fixture_job(self, env: dict[str, str]) -> str:
        result = self._run(
            [
                "docker",
                "compose",
                "-f",
                str(self.compose_file),
                "exec",
                "-T",
                "dagster-user-code",
                "dagster",
                "job",
                "execute",
                "-f",
                "/app/workdirs/dagster/definitions.py",
                "-j",
                "load_offers_1000",
            ],
            env,
        )
        output = result.stdout + result.stderr
        match = self._RUN_ID_PATTERN.search(output)
        self.assertIsNotNone(match, "Dagster output did not contain a successful run ID")
        run_id = match.group(1)
        self.assertEqual(self._dagster_run_status(run_id, env), "SUCCESS")
        return run_id

    def _dagster_run_status(self, run_id: str, env: dict[str, str]) -> str:
        result = self._run(
            [
                "docker",
                "compose",
                "-f",
                str(self.compose_file),
                "exec",
                "-T",
                "-e",
                f"TEST_RUN_ID={run_id}",
                "dagster-user-code",
                "python",
                "-c",
                (
                    "import os; from dagster import DagsterInstance; "
                    "run = DagsterInstance.get().get_run_by_id(os.environ['TEST_RUN_ID']); "
                    "print(run.status.value if run else 'NOT_FOUND')"
                ),
            ],
            env,
        )
        return result.stdout.strip()

    def _offers_snapshot(self, env: dict[str, str]) -> tuple[str, str]:
        columns = self._postgres_query(
            "ANALYTICS",
            (
                "SELECT string_agg(column_name, ',' ORDER BY ordinal_position) "
                "FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = 'offers_1000';"
            ),
            env,
        )
        aggregates = self._postgres_query(
            "ANALYTICS",
            (
                "SELECT count(*) || '|' || sum(index::bigint) || '|' || "
                "sum(stock::bigint) || '|' || sum(price::bigint) "
                "FROM offers_1000 WHERE source_file = 'offers-1000.csv';"
            ),
            env,
        )
        self.assertEqual(columns, "source_file,ingested_at,index,ean,stock,price")
        self.assertEqual(aggregates, "1000|500500|500277|503552")
        return columns, aggregates

    def _verify_superset_consumption(
        self,
        env: dict[str, str],
        *,
        create_missing: bool = True,
    ) -> tuple[int, int, int]:
        opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
        )
        login = self._superset_request(
            opener,
            "/api/v1/security/login",
            payload={
                "username": "admin",
                "password": env["CDS_SUPERSET_ADMIN_PASSWORD"],
                "provider": "db",
                "refresh": True,
            },
        )
        token = login.get("access_token")
        self.assertIsInstance(token, str)
        self.assertTrue(token)
        csrf = self._superset_request(
            opener,
            "/api/v1/security/csrf_token/",
            token=token,
        ).get("result")
        self.assertIsInstance(csrf, str)
        self.assertTrue(csrf)

        database_name = "MVP Analytics"
        databases = self._superset_request(
            opener,
            "/api/v1/database/?q=(page_size:100)",
            token=token,
        ).get("result", [])
        database = next(
            (item for item in databases if item.get("database_name") == database_name),
            None,
        )
        if database is None:
            self.assertTrue(
                create_missing,
                "Superset analytics database connection did not persist",
            )
            username = urllib.parse.quote(env["CDS_ANALYTICS_DB_USER"], safe="")
            password = urllib.parse.quote(env["CDS_ANALYTICS_DB_PASSWORD"], safe="")
            database_value = urllib.parse.quote(env["CDS_ANALYTICS_DB_NAME"], safe="")
            created = self._superset_request(
                opener,
                "/api/v1/database/",
                token=token,
                csrf=csrf,
                payload={
                    "database_name": database_name,
                    "expose_in_sqllab": True,
                    "sqlalchemy_uri": (
                        f"postgresql+psycopg2://{username}:{password}"
                        f"@postgres:5432/{database_value}"
                    ),
                },
            )
            database_id = int(created["id"])
        else:
            database_id = int(database["id"])

        datasets = self._superset_request(
            opener,
            "/api/v1/dataset/?q=(page_size:100)",
            token=token,
        ).get("result", [])
        dataset = next(
            (
                item
                for item in datasets
                if item.get("table_name") == "offers_1000"
                and item.get("schema") == "public"
                and item.get("database", {}).get("id") == database_id
            ),
            None,
        )
        if dataset is None:
            self.assertTrue(
                create_missing,
                "Superset offers_1000 dataset did not persist",
            )
            created = self._superset_request(
                opener,
                "/api/v1/dataset/",
                token=token,
                csrf=csrf,
                payload={
                    "database": database_id,
                    "schema": "public",
                    "table_name": "offers_1000",
                },
            )
            dataset_id = int(created["id"])
        else:
            dataset_id = int(dataset["id"])

        query_context = {
            "datasource": {"id": dataset_id, "type": "table"},
            "force": True,
            "queries": [
                {
                    "columns": [],
                    "filters": [],
                    "metrics": ["count"],
                    "row_limit": 10,
                }
            ],
            "result_format": "json",
            "result_type": "full",
        }
        query_result = self._superset_request(
            opener,
            "/api/v1/chart/data",
            token=token,
            csrf=csrf,
            payload=query_context,
        )["result"][0]
        self.assertEqual(query_result["status"], "success")
        self.assertEqual(query_result["data"], [{"count": 1000}])

        chart_name = "MVP Offers Row Count"
        charts = self._superset_request(
            opener,
            "/api/v1/chart/?q=(page_size:100)",
            token=token,
        ).get("result", [])
        chart = next(
            (item for item in charts if item.get("slice_name") == chart_name),
            None,
        )
        if chart is None:
            self.assertTrue(
                create_missing,
                "Superset offers row-count chart did not persist",
            )
            created = self._superset_request(
                opener,
                "/api/v1/chart/",
                token=token,
                csrf=csrf,
                payload={
                    "datasource_id": dataset_id,
                    "datasource_type": "table",
                    "params": json.dumps(
                        {
                            "datasource": f"{dataset_id}__table",
                            "metric": "count",
                            "viz_type": "big_number_total",
                        }
                    ),
                    "query_context": json.dumps(query_context),
                    "slice_name": chart_name,
                    "viz_type": "big_number_total",
                },
            )
            chart_id = int(created["id"])
        else:
            chart_id = int(chart["id"])

        return database_id, dataset_id, chart_id

    def _superset_request(
        self,
        opener,
        path: str,
        *,
        token: str | None = None,
        csrf: str | None = None,
        payload: dict | None = None,
    ) -> dict:
        headers = {"Content-Type": "application/json"}
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        if csrf is not None:
            headers["X-CSRFToken"] = csrf
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = urllib.request.Request(
            f"http://127.0.0.1:8088{path}",
            data=data,
            headers=headers,
        )
        with opener.open(request, timeout=30) as response:
            self.assertIn(response.status, (200, 201))
            parsed = json.load(response)
        self.assertIsInstance(parsed, dict)
        return parsed

    def _verify_postgres_outage_recovery(
        self,
        env: dict[str, str],
        expected_snapshot: tuple[str, str],
    ) -> None:
        self._run(
            [
                "docker",
                "compose",
                "-f",
                str(self.compose_file),
                "stop",
                "postgres",
            ],
            env,
        )
        try:
            result = subprocess.run(
                [
                    "docker",
                    "compose",
                    "-f",
                    str(self.compose_file),
                    "exec",
                    "-T",
                    "dagster-user-code",
                    "dagster",
                    "job",
                    "execute",
                    "-f",
                    "/app/workdirs/dagster/definitions.py",
                    "-j",
                    "load_offers_1000",
                ],
                cwd=self.repo_root,
                env=env,
                capture_output=True,
                text=True,
                timeout=300,
            )
            output = result.stdout + result.stderr
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn("RUN_SUCCESS", output)
            self.assertRegex(
                output.lower(),
                r"(connection refused|connection to server.*failed|"
                r"could not connect|operationalerror)",
            )
            for key, secret in env.items():
                if secret and any(
                    marker in key.upper()
                    for marker in ("PASSWORD", "SECRET", "TOKEN")
                ):
                    self.assertNotIn(secret, output)
        finally:
            subprocess.run(
                [
                    "docker",
                    "compose",
                    "-f",
                    str(self.compose_file),
                    "start",
                    "postgres",
                ],
                cwd=self.repo_root,
                env=env,
                capture_output=True,
                text=True,
                timeout=300,
            )

        self._wait_for_stack_ready(env)
        recovery_run_id = self._execute_offers_fixture_job(env)
        self.assertEqual(self._dagster_run_status(recovery_run_id, env), "SUCCESS")
        self.assertEqual(self._offers_snapshot(env), expected_snapshot)

    def _postgres_query(
        self,
        prefix: str,
        query: str,
        env: dict[str, str],
    ) -> str:
        result = self._run(
            [
                "docker",
                "compose",
                "-f",
                str(self.compose_file),
                "exec",
                "-T",
                "postgres",
                "sh",
                "-c",
                (
                    f'PGPASSWORD="${{{prefix}_DB_PASSWORD}}" '
                    f'exec psql -U "${{{prefix}_DB_USER}}" '
                    f'-d "${{{prefix}_DB_NAME}}" -At -c "$1"'
                ),
                "sh",
                query,
            ],
            env,
        )
        return result.stdout.strip()


if __name__ == "__main__":
    unittest.main()
