"""Contract coverage for the local-dagster-postgres-superset-dbt profile.

Names `profiles/local-dagster-postgres-superset-dbt` (see
docs/contract-coverage.md, issue #1079) and exercises its contract
behavior: the profile validates, plans, and renders to docker-compose
without errors, and the rendered compose contains the dbt transformation
job bound to the postgres warehouse via the sql-database contract.
"""
import tempfile
import unittest
from pathlib import Path

import yaml

from cli.planner import build_plan
from cli.renderer import render_compose
from cli.validator import validate_profile

REPO_ROOT = Path(__file__).resolve().parent.parent
PROFILE_PATH = REPO_ROOT / "profiles" / "local-dagster-postgres-superset-dbt" / "profile.yaml"

# Non-secret config values and required secrets the profile resolves from
# the environment (mirrors tests/test_k8s_renderer.py).
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


class LocalDagsterPostgresSupersetDbtProfileTest(unittest.TestCase):
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

    def test_profile_plans_and_renders_compose_with_dbt_job(self) -> None:
        plan, plan_diags = build_plan(str(PROFILE_PATH), env_file=str(self.env_file))
        self.assertIsNotNone(plan)
        errors = [d for d in plan_diags if d.level == "error"]
        self.assertEqual(errors, [])

        # The profile publishes the warehouse and transformation docs as
        # output contracts.
        outputs = plan.get("outputs", {}).get("contracts", {})
        self.assertIn("warehouse", outputs)
        self.assertIn("transformationDocs", outputs)

        output, render_diags = render_compose(plan, env_file=str(self.env_file))
        errors = [d for d in render_diags if d.level == "error"]
        self.assertEqual(errors, [])

        compose = yaml.safe_load(output)
        services = compose["services"]
        for expected in ("postgres", "dagster-webserver", "keydb", "dbt-run", "dbt-docs", "superset"):
            self.assertIn(expected, services)
        # The dbt job targets the analytics warehouse through the
        # postgres.sql-database contract binding.
        dbt_env = services["dbt-run"]["environment"]
        self.assertEqual(dbt_env["DBT_WAREHOUSE_TYPE"], "postgres")
        self.assertEqual(dbt_env["DBT_HOST"], "postgres")
        self.assertEqual(
            services["dbt-run"]["depends_on"]["postgres"]["condition"],
            "service_healthy",
        )


if __name__ == "__main__":
    unittest.main()
