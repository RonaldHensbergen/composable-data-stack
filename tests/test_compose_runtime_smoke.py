import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path


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
        env.setdefault("CDS_POSTGRES_PASSWORD", "testpass")
        env.setdefault("CDS_SUPERSET_SECRET_KEY", "sekret")
        env.setdefault("CDS_SUPERSET_ADMIN_PASSWORD", "adminpass")

        try:
            self._run([sys.executable, "-m", "cli.main", "render", "local-dagster-postgres-superset"], env)
            self.assertTrue(self.compose_file.exists(), "docker-compose.yml was not generated")

            self._run(["docker", "compose", "-f", str(self.compose_file), "build"], env)
            self._run(["docker", "compose", "-f", str(self.compose_file), "up", "-d"], env)
            self._run(["docker", "compose", "-f", str(self.compose_file), "ps"], env)
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


if __name__ == "__main__":
    unittest.main()
