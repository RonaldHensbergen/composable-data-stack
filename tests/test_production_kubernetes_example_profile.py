"""Contract coverage for the production-kubernetes-example profile.

Names `profiles/production-kubernetes-example` (see
docs/contract-coverage.md, issue #1079) and exercises its contract
behavior: the profile (which composes local-dagster-postgres-superset via
`extends` and adds a required cache secret plus a `prod` environment
overlay) validates, plans, and renders to a Helm chart without errors in
both the base and the production environment.
"""
import tempfile
import unittest
from pathlib import Path

import yaml

from cli.k8s_renderer import render_helm
from cli.planner import build_plan
from cli.validator import validate_profile

REPO_ROOT = Path(__file__).resolve().parent.parent
PROFILE_PATH = REPO_ROOT / "profiles" / "production-kubernetes-example" / "profile.yaml"

# Base profile values plus the cache secret the profile itself declares
# and the backup encryption key the prod overlay requires.
ENV_VALUES = {
    "CDS_ANALYTICS_DB_NAME": "analytics",
    "CDS_ANALYTICS_DB_PASSWORD": "sentinel-analytics-password",
    "CDS_ANALYTICS_DB_USER": "analytics",
    "CDS_BACKUP_ENCRYPTION_KEY": "sentinel-backup-key",
    "CDS_CACHE_PASSWORD": "sentinel-cache-password",
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


class ProductionKubernetesExampleProfileTest(unittest.TestCase):
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

    def test_profile_validates_without_errors_in_base_and_prod(self) -> None:
        self.assertTrue(PROFILE_PATH.exists(), f"profile not found at {PROFILE_PATH}")
        for environment in (None, "prod"):
            with self.subTest(environment=environment or "base"):
                diagnostics = validate_profile(str(PROFILE_PATH), environment=environment)
                errors = [d for d in diagnostics if d.level == "error"]
                self.assertEqual(errors, [])

    def test_profile_plans_and_renders_helm_in_base_and_prod(self) -> None:
        for environment in (None, "prod"):
            with self.subTest(environment=environment or "base"):
                plan, plan_diags = build_plan(
                    str(PROFILE_PATH),
                    env_file=str(self.env_file),
                    environment=environment,
                )
                self.assertIsNotNone(plan)
                errors = [d for d in plan_diags if d.level == "error"]
                self.assertEqual(errors, [])

                files, render_diags = render_helm(plan, env_file=str(self.env_file))
                errors = [d for d in render_diags if d.level == "error"]
                self.assertEqual(errors, [])

                chart = yaml.safe_load(files["Chart.yaml"])
                self.assertEqual(chart["name"], "production-kubernetes-example")

    def test_prod_overlay_promotes_environment_and_requires_cache_secret(self) -> None:
        plan, _ = build_plan(
            str(PROFILE_PATH),
            env_file=str(self.env_file),
            environment="prod",
        )
        self.assertIsNotNone(plan)
        self.assertEqual(plan["metadata"]["environment"], "production")
        keydb = next(m for m in plan["modules"] if m["id"] == "keydb")
        self.assertEqual(keydb["config"]["passwordFrom"], "secrets.cache_password")


if __name__ == "__main__":
    unittest.main()
