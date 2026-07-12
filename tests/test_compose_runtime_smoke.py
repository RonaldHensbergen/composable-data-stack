import os
import shutil
import subprocess
import sys
import time
import unittest
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
        env.setdefault("CDS_ANALYTICS_POSTGRES_PASSWORD", "analytics_testpass")
        env.setdefault("CDS_DAGSTER_POSTGRES_PASSWORD", "dagster_testpass")
        env.setdefault("CDS_SUPERSET_POSTGRES_PASSWORD", "superset_testpass")
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
            # e.g. "dagster-daemon|dagster daemon status;postgres|pg_isready -U analytics".
            available_services = self._available_services_from_compose()
            for service, command in self._module_exec_checks():
                if service not in available_services:
                    continue
                self._run_exec_with_retry(service, command, env)
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
            ("dagster-daemon", ["dagster", "daemon", "status"]),
        ]


if __name__ == "__main__":
    unittest.main()
