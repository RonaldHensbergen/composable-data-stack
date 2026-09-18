"""
Committed keycloak -> postgres binding test.

Proves, via the real plan/render pipeline (not an uncommitted scratch
profile), that keycloak's `metadataDatabase.contractRef` binding to a
postgres module's `sql-database` contract resolves all five
`KC_DB_URL_*`/`KC_DB_USERNAME`/`KC_DB_PASSWORD` environment variables through
to the rendered `keycloak` service -- i.e. none of them are left as an
unresolved `${bindings...}` expression -- while `KC_BOOTSTRAP_ADMIN_PASSWORD`
(sourced from `config.adminUser.passwordFrom`, not a consumed binding) stays
an unresolved `${CDS_*}` placeholder for Docker Compose to fill in at
runtime, per repo convention: secrets never resolve into plans or rendered
Compose. Same bar as the dlt<->postgres wiring test
(test_dlt_postgres_wiring.py).
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


class KeycloakPostgresWiringTest(unittest.TestCase):
    """Wires the real keycloak and postgres modules together and renders them."""

    def _write_profile(self, profile_dir: Path) -> Path:
        profile = {
            "apiVersion": "cds/v1alpha1",
            "kind": "Profile",
            "metadata": {"name": "keycloak-postgres-wiring", "environment": "local"},
            "spec": {
                "runtime": {"type": "docker-compose"},
                "modules": [
                    {
                        "id": "postgres",
                        "source": "modules/warehouse/postgres",
                        "version": "0.1.0",
                        "enabled": True,
                        "config": {
                            "database": "identity",
                            "username": "identity",
                            "passwordFrom": "secrets.identity_db_password",
                            "superuserPasswordFrom": "secrets.postgres_superuser_password",
                            "port": 5432,
                        },
                    },
                    {
                        "id": "keycloak",
                        "source": "modules/identity/keycloak",
                        "version": "0.1.0",
                        "enabled": True,
                        "dependsOn": ["postgres"],
                        "config": {
                            "metadataDatabase": {
                                "contractRef": "postgres.sql-database",
                            },
                            "adminUser": {
                                "username": "admin",
                                "passwordFrom": "secrets.keycloak_admin_password",
                            },
                        },
                    },
                ],
                "secrets": {
                    "provider": {"type": "env"},
                    "values": {
                        "identity_db_password": {
                            "env": "CDS_IDENTITY_DB_PASSWORD",
                            "required": True,
                        },
                        "postgres_superuser_password": {
                            "env": "CDS_POSTGRES_SUPERUSER_PASSWORD",
                            "required": True,
                        },
                        "keycloak_admin_password": {
                            "env": "CDS_KEYCLOAK_ADMIN_PASSWORD",
                            "required": True,
                        },
                    },
                },
            },
        }

        profile_file = profile_dir / "profile.yaml"
        profile_file.write_text(yaml.safe_dump(profile), encoding="utf-8")
        return profile_file

    def test_all_five_kc_db_vars_resolve_and_admin_password_stays_a_placeholder(self):
        with tempfile.TemporaryDirectory() as root:
            profile_dir = Path(root)
            profile_file = self._write_profile(profile_dir)

            env_file = profile_dir / ".env"
            env_file.write_text(
                "CDS_IDENTITY_DB_PASSWORD=identity_testpass\n"
                "CDS_POSTGRES_SUPERUSER_PASSWORD=superuser_testpass\n"
                "CDS_KEYCLOAK_ADMIN_PASSWORD=admin_testpass\n",
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

                output, render_diags = render_compose(plan, env_file=str(env_file))
                self.assertEqual(
                    [d for d in render_diags if d.level == "error"], [],
                    msg=f"unexpected render errors: {render_diags}",
                )

                compose = yaml.safe_load(output)
                keycloak_env = compose["services"]["keycloak"]["environment"]

                # All five consumed sql-database binding fields must be
                # fully resolved to postgres's rendered values -- not left
                # as raw "${bindings.metadata-database.*}" expressions.
                for key in (
                    "KC_DB_URL_HOST",
                    "KC_DB_URL_PORT",
                    "KC_DB_URL_DATABASE",
                    "KC_DB_USERNAME",
                    "KC_DB_PASSWORD",
                ):
                    self.assertIn(key, keycloak_env)
                    self.assertNotIn("${bindings.", str(keycloak_env[key]))

                self.assertEqual(keycloak_env["KC_DB_URL_HOST"], "postgres")
                self.assertEqual(keycloak_env["KC_DB_URL_PORT"], 5432)
                self.assertEqual(keycloak_env["KC_DB_URL_DATABASE"], "identity")
                self.assertEqual(keycloak_env["KC_DB_USERNAME"], "identity")
                self.assertEqual(keycloak_env["KC_DB_PASSWORD"], "${CDS_IDENTITY_DB_PASSWORD}")

                # KC_DB is derived from config.metadataDatabase.driver (not
                # hardcoded) and defaults to "postgres" when the profile
                # does not set it explicitly.
                self.assertEqual(keycloak_env["KC_DB"], "postgres")

                # The admin bootstrap password is not a consumed binding --
                # it comes straight from config.adminUser.passwordFrom -- but
                # must still stay an unresolved runtime placeholder, never a
                # plaintext secret, per repo-wide secrets convention.
                self.assertEqual(
                    keycloak_env["KC_BOOTSTRAP_ADMIN_PASSWORD"],
                    "${CDS_KEYCLOAK_ADMIN_PASSWORD}",
                )
                self.assertEqual(keycloak_env["KC_BOOTSTRAP_ADMIN_USERNAME"], "admin")


if __name__ == "__main__":
    unittest.main()
