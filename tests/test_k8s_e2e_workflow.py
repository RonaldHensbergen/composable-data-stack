import unittest
from pathlib import Path

import yaml


class K8sE2eWorkflowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        repo_root = Path(__file__).resolve().parent.parent
        path = repo_root / ".github" / "workflows" / "k8s-e2e.yml"
        cls.workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
        cls.job = cls.workflow["jobs"]["k3d-e2e"]

    def test_runs_on_pull_requests_and_pushes_with_same_path_filter(self) -> None:
        triggers = self.workflow.get("on", self.workflow.get(True))

        self.assertEqual(
            triggers["push"]["paths"], triggers["pull_request"]["paths"]
        )
        self.assertIn("scripts/k8s/**", triggers["pull_request"]["paths"])

    def test_runs_documented_harness_and_always_deletes_cluster(self) -> None:
        steps = {step["name"]: step for step in self.job["steps"]}
        commands = "\n".join(step.get("run", "") for step in self.job["steps"])

        self.assertIn("make k3d-build", commands)
        self.assertIn("make k3d-e2e", commands)
        self.assertEqual(steps["Delete cluster"]["if"], "always()")
        self.assertEqual(steps["Delete cluster"]["run"], "make k3d-down")

    def test_k3d_binary_is_pinned_and_checksum_verified(self) -> None:
        env = self.workflow["env"]
        run = next(
            step["run"]
            for step in self.job["steps"]
            if step["name"].startswith("Install k3d")
        )

        self.assertRegex(env["K3D_VERSION"], r"^v\d+\.\d+\.\d+$")
        self.assertRegex(env["K3D_LINUX_AMD64_SHA256"], r"^[0-9a-f]{64}$")
        self.assertIn("sha256sum --check", run)

    def test_job_supplies_every_secret_the_profile_requires(self) -> None:
        env = self.workflow["env"]

        for key in (
            "CDS_POSTGRES_SUPERUSER_PASSWORD",
            "CDS_SUPERSET_SECRET_KEY",
            "CDS_DAGSTER_DB_PASSWORD",
            "CDS_SUPERSET_DB_PASSWORD",
        ):
            self.assertIn(key, env)
