import os
import shutil
import subprocess
import sys
import time
import unittest
import uuid
from pathlib import Path

import yaml


class ComposeRuntimeSmokeTest(unittest.TestCase):
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
            self._run([sys.executable, "-m", "cli.main", "render", "local-dagster-postgres-superset"], env)
            self.assertTrue(self.compose_file.exists(), "docker-compose.yml was not generated")

            self._run(["docker", "compose", "-f", str(self.compose_file), "build"], env)
            self._run(["docker", "compose", "-f", str(self.compose_file), "up", "-d"], env)
            self._run(["docker", "compose", "-f", str(self.compose_file), "ps"], env)

            # Run module-bounded runtime checks inside containers after startup.
            # Extend by setting CDS_DOCKER_EXEC_CHECKS with ';' separated commands,
            # e.g. "dagster-daemon|sh -lc tr '\\0' ' ' </proc/1/cmdline | grep -q 'dagster-daemon run';postgres|pg_isready -U analytics".
            available_services = self._available_services_from_compose()
            for service, command in self._module_exec_checks():
                if service not in available_services:
                    continue
                self._run_exec_with_retry(service, command, env)

            self._verify_incoming_csv_ingested(env)
        finally:
            # Always tear down stack resources created by this smoke test.
            subprocess.run(
                ["docker", "compose", "-f", str(self.compose_file), "down", "-v", "--remove-orphans"],
                cwd=self.repo_root,
                env=env,
                capture_output=True,
                text=True,
            )

    def _run(self, command: list[str], env: dict[str, str]) -> None:
        result = subprocess.run(
            command,
            cwd=self.repo_root,
            env=env,
            capture_output=True,
            text=True,
            timeout=1200,
        )
        if result.returncode != 0:
            self.fail(
                "Command failed: {cmd}\nstdout:\n{stdout}\nstderr:\n{stderr}".format(
                    cmd=" ".join(command),
                    stdout=result.stdout,
                    stderr=result.stderr,
                )
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
        ]

    def _verify_incoming_csv_ingested(self, env: dict[str, str]) -> None:
        incoming_dir = self.repo_root / "workdirs" / "shared-data" / "incoming"
        processed_dir = self.repo_root / "workdirs" / "shared-data" / "processed"
        incoming_dir.mkdir(parents=True, exist_ok=True)
        processed_dir.mkdir(parents=True, exist_ok=True)

        file_stem = f"smoke_{uuid.uuid4().hex[:8]}"
        file_name = f"{file_stem}.csv"
        table_name = f"incoming_{file_stem}"
        source_file = incoming_dir / file_name
        source_file.write_text("id,name\n1,alice\n2,bob\n", encoding="utf-8")

        analytics_user = env["CDS_ANALYTICS_DB_USER"]
        analytics_db = env["CDS_ANALYTICS_DB_NAME"]
        analytics_password = env["CDS_ANALYTICS_DB_PASSWORD"]
        query = f"SELECT COUNT(*) FROM {table_name} WHERE source_file = '{file_name}';"

        cmd = [
            "sh",
            "-lc",
            (
                f"PGPASSWORD='{analytics_password}' "
                f"psql -U '{analytics_user}' -d '{analytics_db}' -tAc \"{query}\""
            ),
        ]

        attempts = 30
        delay_seconds = 5
        for attempt in range(1, attempts + 1):
            result = subprocess.run(
                [
                    "docker",
                    "compose",
                    "-f",
                    str(self.compose_file),
                    "exec",
                    "-T",
                    "postgres",
                    *cmd,
                ],
                cwd=self.repo_root,
                env=env,
                capture_output=True,
                text=True,
                timeout=120,
            )
            output = result.stdout.strip()
            if result.returncode == 0 and output.isdigit() and int(output) >= 2:
                return
            if attempt < attempts:
                time.sleep(delay_seconds)
                continue
            self.fail(
                "Incoming ingestion check failed for file {file_name} into {table_name}.\n"
                "stdout:\n{stdout}\n\nstderr:\n{stderr}".format(
                    file_name=file_name,
                    table_name=table_name,
                    stdout=result.stdout,
                    stderr=result.stderr,
                )
            )


if __name__ == "__main__":
    unittest.main()
