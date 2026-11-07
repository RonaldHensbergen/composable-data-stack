"""
Committed dlt -> duckdb binding test.

Proves, via the real plan/render pipeline (not an uncommitted scratch
profile), that the experimental dlt module's `destinationWarehouseFile.contractRef`
binding to a duckdb module's `file-database` contract resolves all the way
through to the rendered `dlt-run` service: the shared host directory is
bind-mounted at /usr/app/duckdb_data and the consumed `filename` lands in
`DESTINATION__DUCKDB__CREDENTIALS` (the variable dlt's duckdb destination
reads itself), while the postgres credentials variable renders empty --
i.e. no `${bindings...}`/`${ifNonempty:...}` expression is left unresolved.
Also covers the shipped `local-duckdb-dlt-dbt` demo profile through
validate -> plan -> render, and the compile-time guards (E041) that force
config.destinationType to match the binding that is actually set.
"""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import yaml

from cli.planner import build_plan
from cli.renderer import render_compose
from cli.validator import validate_profile

_REPO_ROOT = Path(__file__).resolve().parent.parent
_DEMO_PROFILE = _REPO_ROOT / "profiles" / "local-duckdb-dlt-dbt" / "profile.yaml"


def _wiring_profile(destination_type: str | None, with_warehouse_file: bool) -> dict:
    dlt_config: dict = {}
    if destination_type is not None:
        dlt_config["destinationType"] = destination_type
    if with_warehouse_file:
        dlt_config["destinationWarehouseFile"] = {"contractRef": "duckdb.file-database"}
    return {
        "apiVersion": "cds/v1alpha1",
        "kind": "Profile",
        "metadata": {"name": "dlt-duckdb-wiring", "environment": "local"},
        "spec": {
            "runtime": {"type": "docker-compose"},
            "modules": [
                {
                    "id": "duckdb",
                    "source": "modules/warehouse/duckdb",
                    "version": "0.1.0",
                    "enabled": True,
                    "config": {"hostDirectory": "./data/duckdb"},
                },
                {
                    "id": "dlt",
                    "source": "modules-experimental/ingestion/dlt",
                    "version": "0.1.0",
                    "enabled": True,
                    "dependsOn": ["duckdb"],
                    "config": dlt_config,
                },
            ],
        },
    }


class DltDuckdbWiringTest(unittest.TestCase):
    """Wires the real dlt and duckdb modules together and renders them."""

    def _compile(self, profile: dict):
        with tempfile.TemporaryDirectory() as root:
            profile_file = Path(root) / "profile.yaml"
            profile_file.write_text(yaml.safe_dump(profile), encoding="utf-8")
            with mock.patch.dict(
                "os.environ", {"CDS_MODULE_PATH": str(_REPO_ROOT)}, clear=False
            ):
                diagnostics = validate_profile(str(profile_file))
                plan, plan_diags = build_plan(str(profile_file))
                output = rendered = None
                render_diags: list = []
                if plan is not None:
                    output, render_diags = render_compose(plan)
                    rendered = yaml.safe_load(output) if output else None
                return diagnostics, plan, plan_diags, render_diags, rendered

    def test_file_database_binding_resolves_through_plan_and_render(self):
        diagnostics, plan, plan_diags, render_diags, compose = self._compile(
            _wiring_profile("duckdb", with_warehouse_file=True)
        )
        self.assertEqual(
            [d for d in diagnostics if d.level == "error"],
            [],
            msg=f"unexpected validation errors: {diagnostics}",
        )
        self.assertIsNotNone(plan)
        self.assertEqual(
            [d for d in plan_diags if d.level == "error"],
            [],
            msg=f"unexpected plan errors: {plan_diags}",
        )
        self.assertEqual(
            [d for d in render_diags if d.level == "error"],
            [],
            msg=f"unexpected render errors: {render_diags}",
        )

        dlt_entry = next(m for m in plan["modules"] if m["id"] == "dlt")
        bound = dlt_entry["consumes"]["destination-warehouse-file"]["contract"]["spec"]
        # The binding must be fully resolved to the producer's contract
        # fields -- not left as a raw "${bindings...}" expression.
        for key, expected in {
            "hostDirectory": "./data/duckdb",
            "filename": "warehouse.duckdb",
            "path": "./data/duckdb/warehouse.duckdb",
            "readOnly": False,
        }.items():
            self.assertNotIn("${bindings.", str(bound[key]))
            self.assertEqual(bound[key], expected)
        # The inactive sql-database consume must be skipped, not unresolved.
        self.assertNotIn("destination-database", dlt_entry["consumes"])

        self.assertIn("duckdb-init", compose["services"])
        dlt_service = compose["services"]["dlt-run"]

        env = dlt_service["environment"]
        self.assertEqual(env["CDS_DLT_DESTINATION"], "duckdb")
        self.assertEqual(
            env["DESTINATION__DUCKDB__CREDENTIALS"],
            "/usr/app/duckdb_data/warehouse.duckdb",
        )
        # Postgres mode is inactive: the variable renders empty, not as an
        # unresolved expression.
        self.assertEqual(env["DESTINATION__POSTGRES__CREDENTIALS"], "")
        self.assertEqual(env["CDS_DB_DESTINATION_CONNECTION_URI"], "")
        for key, value in env.items():
            self.assertNotIn("${bindings.", str(value), msg=f"{key} left unresolved")
            self.assertNotIn("${ifNonempty:", str(value), msg=f"{key} left unresolved")

        duckdb_mount = next(
            v
            for v in dlt_service["volumes"]
            if isinstance(v, dict) and v.get("target") == "/usr/app/duckdb_data"
        )
        self.assertEqual(duckdb_mount["type"], "bind")
        # The renderer rewrites bind sources relative to the rendered
        # compose file; the throwaway profile lives in a temp dir, so only
        # the trailing path segment is stable here (the shipped-profile
        # test below asserts the exact repo-relative path).
        self.assertTrue(duckdb_mount["source"].endswith("data/duckdb"))
        self.assertNotIn("${", duckdb_mount["source"])

    def test_duckdb_mode_without_warehouse_file_binding_fails_e041(self):
        diagnostics, plan, plan_diags, _, _ = self._compile(
            _wiring_profile("duckdb", with_warehouse_file=False)
        )
        errors = [d for d in diagnostics if d.level == "error"] + [
            d for d in plan_diags if d.level == "error"
        ]
        self.assertTrue(
            any(d.code == "E041" for d in errors),
            msg=f"expected E041 for missing destinationWarehouseFile, got: {errors}",
        )

    def test_default_postgres_mode_still_requires_database_binding(self):
        profile = _wiring_profile(None, with_warehouse_file=False)
        diagnostics, plan, plan_diags, _, _ = self._compile(profile)
        errors = [d for d in diagnostics if d.level == "error"] + [
            d for d in plan_diags if d.level == "error"
        ]
        self.assertTrue(
            any(d.code == "E041" for d in errors),
            msg=f"expected E041 for missing destinationDatabase in default postgres mode, got: {errors}",
        )


class ShippedDuckdbDemoProfileTest(unittest.TestCase):
    """The shipped local-duckdb-dlt-dbt profile must validate, plan, render."""

    def test_demo_profile_compiles_through_render(self):
        self.assertTrue(
            _DEMO_PROFILE.is_file(), msg=f"missing demo profile: {_DEMO_PROFILE}"
        )
        diagnostics = validate_profile(str(_DEMO_PROFILE))
        self.assertEqual(
            [d for d in diagnostics if d.level == "error"],
            [],
            msg=f"unexpected validation errors: {diagnostics}",
        )

        plan, plan_diags = build_plan(str(_DEMO_PROFILE))
        self.assertIsNotNone(plan)
        self.assertEqual(
            [d for d in plan_diags if d.level == "error"],
            [],
            msg=f"unexpected plan errors: {plan_diags}",
        )

        output, render_diags = render_compose(plan)
        self.assertEqual(
            [d for d in render_diags if d.level == "error"],
            [],
            msg=f"unexpected render errors: {render_diags}",
        )
        compose = yaml.safe_load(output)
        services = compose["services"]
        self.assertIn("duckdb-init", services)
        self.assertIn("dlt-run", services)
        self.assertIn("dbt-run", services)
        self.assertIn("dbt-docs", services)

        dlt_env = services["dlt-run"]["environment"]
        self.assertEqual(dlt_env["CDS_DLT_DESTINATION"], "duckdb")
        self.assertEqual(
            dlt_env["DESTINATION__DUCKDB__CREDENTIALS"],
            "/usr/app/duckdb_data/warehouse.duckdb",
        )
        dlt_mount = next(
            v
            for v in services["dlt-run"]["volumes"]
            if isinstance(v, dict) and v.get("target") == "/usr/app/duckdb_data"
        )
        self.assertEqual(dlt_mount["source"], "workdirs/duckdb")

        dbt_env = services["dbt-run"]["environment"]
        self.assertEqual(dbt_env["DBT_WAREHOUSE_TYPE"], "duckdb")
        self.assertEqual(
            dbt_env["DBT_DUCKDB_PATH"], "/usr/app/dbt_duckdb/warehouse.duckdb"
        )
        dbt_mount = next(
            v
            for v in services["dbt-run"]["volumes"]
            if isinstance(v, dict) and v.get("target") == "/usr/app/dbt_duckdb"
        )
        self.assertEqual(dbt_mount["source"], "workdirs/duckdb")


if __name__ == "__main__":
    unittest.main()
