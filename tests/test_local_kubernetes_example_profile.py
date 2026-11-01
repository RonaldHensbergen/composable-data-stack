"""Contract coverage for the local-kubernetes-example profile.

Names `profiles/local-kubernetes-example` (see docs/contract-coverage.md,
issue #1079) and exercises its contract behavior: the profile (which
composes local-dagster-postgres-superset via `extends`) validates, plans,
and renders to a Helm chart without errors.
"""
import tempfile
import unittest
from pathlib import Path

import yaml

from cli.k8s_renderer import render_helm
from cli.planner import build_plan
from cli.validator import validate_profile

REPO_ROOT = Path(__file__).resolve().parent.parent
PROFILE_PATH = REPO_ROOT / "profiles" / "local-kubernetes-example" / "profile.yaml"

# Secret/config values inherited from the extended base profile (mirrors
# tests/test_k8s_renderer.py).
ENV_VALUES = {
    "CDS_ANALYTICS_DB_NAME": "analytics",
    "CDS_ANALYTICS_DB_PASSWORD": "sentinel-analytics-password",
    "CDS_ANALYTICS_DB_USER": "analytics",
    "CDS_DAGSTER_DB_NAME": "dagster",
    "CDS_DAGSTER_DB_PASSWORD": "sentinel-dagster-password",
    "CDS_DAGSTER_DB_USER": "dagster",
    "CDS_POSTGRES_SUPERUSER_PASSWORD": "sentinel-postgres-password",
    "CDS_SUPERSET_ADMIN_PASSWORD": "sentinel-admin-password",
    "CDS_SUPERSET_DB_NAME": "superset",
    "CDS_SUPERSET_DB_PASSWORD": "sentinel-superset-password",
    "CDS_SUPERSET_DB_USER": "superset",
    "CDS_SUPERSET_SECRET_KEY": "sentinel-superset-secret-key",
}


class LocalKubernetesExampleProfileTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tempdir = tempfile.TemporaryDirectory()
        cls.env_file = Path(cls._tempdir.name) / ".env"
        cls.env_file.write_text(
            "\n".join(f"{key}={value}" for key, value in ENV_VALUES.items()) + "\n",
            encoding="utf-8",
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tempdir.cleanup()

    def test_profile_validates_without_errors(self) -> None:
        self.assertTrue(PROFILE_PATH.exists(), f"profile not found at {PROFILE_PATH}")
        diagnostics = validate_profile(str(PROFILE_PATH))
        errors = [d for d in diagnostics if d.level == "error"]
        self.assertEqual(errors, [])

    def test_profile_plans_and_renders_helm_chart(self) -> None:
        plan, plan_diags = build_plan(str(PROFILE_PATH), env_file=str(self.env_file))
        self.assertIsNotNone(plan)
        errors = [d for d in plan_diags if d.level == "error"]
        self.assertEqual(errors, [])

        # The extends chain resolved to the base profile's modules.
        module_ids = {m["id"] for m in plan["modules"]}
        for expected in ("postgres", "dagster", "keydb", "superset"):
            self.assertIn(expected, module_ids)

        files, render_diags = render_helm(plan, env_file=str(self.env_file))
        errors = [d for d in render_diags if d.level == "error"]
        self.assertEqual(errors, [])

        chart = yaml.safe_load(files["Chart.yaml"])
        self.assertEqual(chart["name"], "local-kubernetes-example")
        templates = [p for p in files if p.startswith("templates/")]
        self.assertGreater(len(templates), 0)


if __name__ == "__main__":
    unittest.main()
