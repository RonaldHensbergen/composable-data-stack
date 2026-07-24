import unittest
from pathlib import Path

import yaml


class MVPProofWorkflowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.repo_root = Path(__file__).resolve().parent.parent
        workflow_path = cls.repo_root / ".github" / "workflows" / "mvp-proof.yml"
        cls.workflow = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))

    def test_separates_compile_and_runtime_proof(self) -> None:
        jobs = self.workflow["jobs"]

        self.assertEqual(set(jobs), {"validate-render", "runtime-proof"})
        self.assertEqual(jobs["runtime-proof"]["needs"], "validate-render")
        self.assertEqual(jobs["runtime-proof"]["timeout-minutes"], 45)

    def test_compile_job_validates_plans_and_compares_renders(self) -> None:
        steps = self.workflow["jobs"]["validate-render"]["steps"]
        commands = "\n".join(step.get("run", "") for step in steps)

        self.assertIn("cds validate local-dagster-postgres-superset", commands)
        self.assertIn("cds plan local-dagster-postgres-superset", commands)
        self.assertEqual(commands.count("cds render local-dagster-postgres-superset"), 2)
        self.assertIn("cmp /tmp/mvp-compile-proof/render-1.yml", commands)
        self.assertIn("config --quiet", commands)

    def test_runtime_job_runs_stateful_proof_and_always_cleans_up(self) -> None:
        job = self.workflow["jobs"]["runtime-proof"]
        steps = {step["name"]: step for step in job["steps"]}

        self.assertEqual(job["env"]["CDS_RUN_DOCKER_SMOKE"], "1")
        self.assertEqual(job["env"]["CDS_KEEP_DOCKER_STACK"], "1")
        self.assertIn(
            "tests.test_compose_runtime_smoke",
            steps["Run runtime proof"]["run"],
        )
        self.assertEqual(
            steps["Clean up Compose resources"]["if"],
            "always()",
        )

    def test_failure_diagnostics_are_redacted_and_uploaded(self) -> None:
        steps = {
            step["name"]: step
            for step in self.workflow["jobs"]["runtime-proof"]["steps"]
        }

        self.assertEqual(steps["Collect failure diagnostics"]["if"], "failure()")
        self.assertNotIn(
            ".Config.Env",
            steps["Collect failure diagnostics"]["run"],
        )
        self.assertEqual(steps["Redact diagnostic artifacts"]["if"], "failure()")
        self.assertEqual(steps["Upload failure diagnostics"]["if"], "failure()")
        self.assertEqual(
            steps["Upload failure diagnostics"]["uses"],
            "actions/upload-artifact@v7",
        )


if __name__ == "__main__":
    unittest.main()
