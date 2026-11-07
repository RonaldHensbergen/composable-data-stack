"""Contract coverage for the local-postgres-grafana profile.

Names `profiles/local-postgres-grafana` (see docs/contract-coverage.md,
issue #1079) and exercises its contract behavior: the profile validates,
plans, and renders to Docker Compose without errors, with the grafana
module's `http-service` contract resolved and the warehouse datasource
provisioning mount present (the `sql-database` binding wired via
`warehouseDatabase.contractRef`, issue #369).
"""
import tempfile
import unittest
from pathlib import Path

import yaml

from cli.planner import build_plan
from cli.renderer import render_compose
from cli.validator import validate_profile

REPO_ROOT = Path(__file__).resolve().parent.parent
PROFILE_PATH = REPO_ROOT / "profiles" / "local-postgres-grafana" / "profile.yaml"

ENV_VALUES = {
    "CDS_ANALYTICS_DB_NAME": "analytics",
    "CDS_ANALYTICS_DB_USER": "analytics",
    "CDS_ANALYTICS_DB_PASSWORD": "sentinel-analytics-password",
    "CDS_POSTGRES_SUPERUSER_PASSWORD": "sentinel-postgres-password",
    "CDS_GRAFANA_ADMIN_PASSWORD": "sentinel-grafana-admin-password",
}


class LocalPostgresGrafanaProfileTest(unittest.TestCase):
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

    def test_profile_plans_and_renders_compose_with_warehouse_datasource(self) -> None:
        plan, plan_diags = build_plan(str(PROFILE_PATH), env_file=str(self.env_file))
        self.assertIsNotNone(plan)
        errors = [d for d in plan_diags if d.level == "error"]
        self.assertEqual(errors, [])

        module_ids = {m["id"] for m in plan["modules"]}
        self.assertEqual(module_ids, {"postgres", "grafana"})
        grafana = next(m for m in plan["modules"] if m["id"] == "grafana")
        provided = grafana["provides"]["http-service"]["spec"]
        self.assertEqual(provided["host"], "grafana")
        self.assertEqual(provided["port"], 3000)
        self.assertEqual(provided["healthPath"], "/api/health")

        output, render_diags = render_compose(plan, env_file=str(self.env_file))
        errors = [d for d in render_diags if d.level == "error"]
        self.assertEqual(errors, [])
        compose = yaml.safe_load(output)

        service = compose["services"]["grafana"]
        self.assertEqual(service["ports"], ["127.0.0.1:3000:3000"])
        bind_targets = {
            v["target"]
            for v in service["volumes"]
            if isinstance(v, dict) and v.get("type") == "bind"
        }
        self.assertIn(
            "/etc/grafana/provisioning/datasources/warehouse.yaml", bind_targets
        )
        env = service["environment"]
        self.assertEqual(env["CDS_GF_DS_WAREHOUSE_HOST"], "postgres")
        # The profile feeds database from ${CDS_ANALYTICS_DB_NAME}, which
        # stays a runtime placeholder in rendered compose.
        self.assertEqual(env["CDS_GF_DS_WAREHOUSE_DATABASE"], "${CDS_ANALYTICS_DB_NAME}")
        self.assertEqual(env["GF_SECURITY_ADMIN_PASSWORD"], "${CDS_GRAFANA_ADMIN_PASSWORD}")


if __name__ == "__main__":
    unittest.main()
