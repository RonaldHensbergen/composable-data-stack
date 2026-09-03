import os
import subprocess
import tempfile
import unittest
from pathlib import Path


class K3dLocalExposureTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.repo_root = Path(__file__).resolve().parent.parent
        cls.script = cls.repo_root / "scripts" / "k8s" / "expose-local.sh"

    def _run(self, enabled: str) -> tuple[subprocess.CompletedProcess[str], str]:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            log_path = root / "kubectl.log"
            kubectl = root / "kubectl"
            kubectl.write_text(
                """#!/bin/sh
printf '%s\\n' \"$*\" >> \"$KUBECTL_LOG\"
case \"$*\" in
  *\"get service dagster-webserver\"*) printf 'NodePort:30300' ;;
  *\"get service superset\"*) printf 'NodePort:30808' ;;
esac
""",
                encoding="utf-8",
            )
            kubectl.chmod(0o755)
            env = os.environ.copy()
            env.update(
                {
                    "CDS_EXPOSE_LOCALHOST": enabled,
                    "KUBECTL_LOG": str(log_path),
                    "PATH": f"{root}:{env['PATH']}",
                }
            )
            result = subprocess.run(
                ["bash", str(self.script)],
                cwd=self.repo_root,
                env=env,
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            log = log_path.read_text(encoding="utf-8") if log_path.exists() else ""
            return result, log

    def test_exposes_both_ui_services_on_published_nodeports(self) -> None:
        result, log = self._run("1")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertRegex(result.stdout, r"Dagster: http://127\.0\.0\.1:\d+")
        self.assertRegex(result.stdout, r"Superset: http://127\.0\.0\.1:\d+")
        self.assertIn("patch service dagster-webserver", log)
        self.assertIn('"nodePort":30300', log)
        self.assertIn("patch service superset", log)
        self.assertIn('"nodePort":30808', log)
        self.assertEqual(log.count("get service"), 2)

    def test_exposure_can_be_disabled_for_parallel_releases(self) -> None:
        result, log = self._run("0")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("localhost exposure disabled", result.stdout)
        self.assertEqual(log, "")

    def test_invalid_exposure_setting_fails_closed(self) -> None:
        result, log = self._run("yes")

        self.assertEqual(result.returncode, 2)
        self.assertIn("must be 0 or 1", result.stderr)
        self.assertEqual(log, "")

    def test_environment_script_prints_the_current_branch_urls(self) -> None:
        result = subprocess.run(
            ["bash", str(self.repo_root / "scripts" / "k8s" / "k3d-env.sh")],
            cwd=self.repo_root,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertRegex(result.stdout, r"node ports  : \d+ \(Dagster\), \d+ \(Superset\)")
        self.assertIn("context     : k3d-cds-", result.stdout)

    def test_cluster_publishes_ui_nodeports_on_loopback_only(self) -> None:
        script = (self.repo_root / "scripts" / "k8s" / "k3d-up.sh").read_text(
            encoding="utf-8"
        )

        self.assertIn('127.0.0.1:${CDS_DAGSTER_PORT}:30300@server:0', script)
        self.assertIn('127.0.0.1:${CDS_SUPERSET_PORT}:30808@server:0', script)
        self.assertNotIn('--port "${CDS_DAGSTER_PORT}:30300', script)
        self.assertNotIn('--port "${CDS_SUPERSET_PORT}:30808', script)


if __name__ == "__main__":
    unittest.main()
