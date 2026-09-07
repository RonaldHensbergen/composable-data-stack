"""
Committed dlt -> postgres binding test.

Proves, via the real plan/render pipeline (not an uncommitted scratch
profile), that the experimental dlt module's `destinationDatabase.contractRef`
binding to a postgres module's `sql-database` contract resolves all the way
through to the rendered `dlt-run` service's `DESTINATION__POSTGRES__CREDENTIALS`
environment variable -- i.e. the consumed `connectionUri` is not left as an
unresolved `${bindings...}` expression, and the password stays an unresolved
`${CDS_*}` placeholder for Docker Compose to fill in at runtime (per repo
convention: secrets never resolve into plans or rendered Compose).
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


class DltPostgresWiringTest(unittest.TestCase):
    """Wires the real dlt and postgres modules together and renders them."""

    def _write_profile(self, profile_dir: Path) -> Path:
        profile = {
            "apiVersion": "cds/v1alpha1",
            "kind": "Profile",
            "metadata": {"name": "dlt-postgres-wiring", "environment": "local"},
            "spec": {
                "runtime": {"type": "docker-compose"},
                "modules": [
                    {
                        "id": "postgres",
                        "source": "modules/warehouse/postgres",
                        "version": "0.1.0",
                        "enabled": True,
                        "config": {
                            "database": "analytics",
                            "username": "analytics",
                            "passwordFrom": "secrets.analytics_db_password",
                            "superuserPasswordFrom": "secrets.postgres_superuser_password",
                            "port": 5432,
                        },
                    },
                    {
                        "id": "dlt",
                        "source": "modules-experimental/ingestion/dlt",
                        "version": "0.1.0",
                        "enabled": True,
                        "dependsOn": ["postgres"],
                        "config": {
                            "destinationDatabase": {
                                "contractRef": "postgres.sql-database",
                            },
                        },
                    },
                ],
                "secrets": {
                    "provider": {"type": "env"},
                    "values": {
                        "analytics_db_password": {
                            "env": "CDS_ANALYTICS_DB_PASSWORD",
                            "required": True,
                        },
                        "postgres_superuser_password": {
                            "env": "CDS_POSTGRES_SUPERUSER_PASSWORD",
                            "required": True,
                        },
                    },
                },
            },
        }

        profile_file = profile_dir / "profile.yaml"
        profile_file.write_text(yaml.safe_dump(profile), encoding="utf-8")
        return profile_file

    def test_destination_postgres_credentials_env_var_resolves(self):
        with tempfile.TemporaryDirectory() as root:
            profile_dir = Path(root)
            profile_file = self._write_profile(profile_dir)

            env_file = profile_dir / ".env"
            env_file.write_text(
                "CDS_ANALYTICS_DB_PASSWORD=analytics_testpass\n"
                "CDS_POSTGRES_SUPERUSER_PASSWORD=superuser_testpass\n",
                encoding="utf-8",
            )

            with mock.patch.dict(
                "os.environ", {"CDS_MODULE_PATH": str(_REPO_ROOT)}, clear=False
            ):
                diagnostics = validate_profile(str(profile_file))
                self.assertEqual(
                    [d for d in diagnostics if d.level == "error"], [],
                    msg=f"unexpected validation errors: {diagnostics}",
                )

                plan, plan_diags = build_plan(str(profile_file), env_file=str(env_file))
                self.assertIsNotNone(plan)
                self.assertEqual(
                    [d for d in plan_diags if d.level == "error"], [],
                    msg=f"unexpected plan errors: {plan_diags}",
                )

                dlt_entry = next(m for m in plan["modules"] if m["id"] == "dlt")
                bound_uri = dlt_entry["consumes"]["destination-database"]["contract"]["spec"][
                    "connectionUri"
                ]
                # The binding must be fully resolved to the producer's
                # rendered connection string (host/user/db from config,
                # password still a runtime placeholder) -- not left as the
                # raw "${bindings.destination-database.connectionUri}"
                # expression.
                self.assertNotIn("${bindings.", bound_uri)
                self.assertEqual(
                    bound_uri,
                    "postgresql://analytics:${CDS_ANALYTICS_DB_PASSWORD}@postgres:5432/analytics",
                )

                output, render_diags = render_compose(plan, env_file=str(env_file))
                self.assertEqual(
                    [d for d in render_diags if d.level == "error"], [],
                    msg=f"unexpected render errors: {render_diags}",
                )

                compose = yaml.safe_load(output)
                dlt_service = compose["services"]["dlt-run"]
                credentials = dlt_service["environment"]["DESTINATION__POSTGRES__CREDENTIALS"]

                self.assertNotIn("${bindings.", credentials)
                self.assertEqual(
                    credentials,
                    "postgresql://analytics:${CDS_ANALYTICS_DB_PASSWORD}@postgres:5432/analytics",
                )


if __name__ == "__main__":
    unittest.main()
