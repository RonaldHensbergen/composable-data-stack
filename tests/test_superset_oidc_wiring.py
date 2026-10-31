import runpy
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

import yaml

from cli.planner import build_plan
from cli.renderer import render_compose
from cli.validator import validate_profile

_REPO_ROOT = Path(__file__).resolve().parent.parent


class SupersetOidcWiringTest(unittest.TestCase):
    def _write_profile(self, profile_dir: Path, *, oidc: bool) -> Path:
        superset_config: dict = {
            "webPort": 8088,
            "secretKeyFrom": "secrets.superset_secret_key",
            "adminUser": {
                "username": "admin",
                "passwordFrom": "secrets.superset_admin_password",
                "email": "admin@example.local",
            },
            "metadataDatabase": {
                "contractRef": "postgres.superset-database",
            },
            "analyticsDatabase": {
                "contractRef": "postgres.sql-database",
            },
            "cacheService": {
                "contractRef": "keydb.cache-service",
            },
        }
        if oidc:
            superset_config["identityProvider"] = {
                "contractRef": "keycloak.oidc-provider",
                "clientId": "superset",
                "clientSecretFrom": "secrets.superset_oidc_credential",
            }

        modules = [
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
                    "supersetDatabase": {
                        "name": "superset",
                        "username": "superset",
                        "passwordFrom": "secrets.superset_db_password",
                    },
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
                    "realm": "analytics",
                    "publicUrl": "https://identity.example.test",
                },
            },
            {
                "id": "keydb",
                "source": "modules/cache/keydb",
                "version": "0.1.0",
                "enabled": True,
                "config": {"port": 6379},
            },
            {
                "id": "superset",
                "source": "modules/bi/superset",
                "version": "0.1.0",
                "enabled": True,
                "dependsOn": (
                    ["postgres", "keycloak", "keydb"]
                    if oidc
                    else ["postgres", "keydb"]
                ),
                "config": superset_config,
            },
        ]
        aliases = {
            "analytics_db_password": "CDS_ANALYTICS_DB_PASSWORD",
            "postgres_superuser_password": "CDS_POSTGRES_SUPERUSER_PASSWORD",
            "superset_db_password": "CDS_SUPERSET_DB_PASSWORD",
            "superset_secret_key": "CDS_SUPERSET_SECRET_KEY",
            "superset_admin_password": "CDS_SUPERSET_ADMIN_PASSWORD",
            "keycloak_admin_password": "CDS_KEYCLOAK_ADMIN_PASSWORD",
        }
        if oidc:
            aliases["superset_oidc_credential"] = "CDS_SUPERSET_OIDC_CREDENTIAL"
        profile = {
            "apiVersion": "cds/v1alpha1",
            "kind": "Profile",
            "metadata": {"name": "superset-oidc-wiring", "environment": "local"},
            "spec": {
                "runtime": {"type": "docker-compose"},
                "modules": modules,
                "secrets": {
                    "provider": {"type": "env"},
                    "values": {
                        name: {"env": env, "required": True}
                        for name, env in aliases.items()
                    },
                },
            },
        }
        profile_file = profile_dir / "profile.yaml"
        profile_file.write_text(yaml.safe_dump(profile), encoding="utf-8")
        return profile_file

    def _render_profile(self, profile_file: Path, profile_dir: Path) -> dict:
        env_file = profile_dir / ".env"
        env_file.write_text(
            "CDS_ANALYTICS_DB_PASSWORD=analytics-test\n"
            "CDS_POSTGRES_SUPERUSER_PASSWORD=postgres-test\n"
            "CDS_SUPERSET_DB_PASSWORD=superset-db-test\n"
            "CDS_SUPERSET_SECRET_KEY=superset-secret-test\n"
            "CDS_SUPERSET_ADMIN_PASSWORD=superset-admin-test\n"
            "CDS_KEYCLOAK_ADMIN_PASSWORD=keycloak-admin-test\n"
            "CDS_SUPERSET_OIDC_CREDENTIAL=oidc-client-test\n",
            encoding="utf-8",
        )
        with mock.patch.dict(
            "os.environ", {"CDS_MODULE_PATH": str(_REPO_ROOT)}, clear=False
        ):
            diagnostics = validate_profile(str(profile_file))
            self.assertEqual(
                [d for d in diagnostics if d.level == "error"],
                [],
                msg=f"unexpected validation errors: {diagnostics}",
            )
            plan, plan_diags = build_plan(str(profile_file), env_file=str(env_file))
            self.assertIsNotNone(plan)
            self.assertEqual(
                [d for d in plan_diags if d.level == "error"],
                [],
                msg=f"unexpected plan errors: {plan_diags}",
            )
            output, render_diags = render_compose(plan, env_file=str(env_file))
            self.assertEqual(
                [d for d in render_diags if d.level == "error"],
                [],
                msg=f"unexpected render errors: {render_diags}",
            )
        return yaml.safe_load(output)

    def test_optional_oidc_binding_resolves_issuer_and_keeps_secret_runtime_only(self):
        with tempfile.TemporaryDirectory() as root:
            profile_dir = Path(root)
            profile_file = self._write_profile(profile_dir, oidc=True)
            compose = self._render_profile(profile_file, profile_dir)
            environment = compose["services"]["superset"]["environment"]

            self.assertEqual(
                environment["CDS_OIDC_ISSUER_URL"],
                "https://identity.example.test/realms/analytics",
            )
            self.assertEqual(environment["CDS_OIDC_CLIENT_ID"], "superset")
            self.assertEqual(
                environment["CDS_OIDC_CLIENT_SECRET"],
                "${CDS_SUPERSET_OIDC_CREDENTIAL}",
            )
            self.assertNotIn("oidc-client-test", str(compose))
            keycloak = compose["services"]["keycloak"]
            self.assertEqual(keycloak["environment"]["CDS_KEYCLOAK_REALM"], "analytics")
            keycloak_script = "\n".join(keycloak["entrypoint"])
            self.assertIn("--import-realm", keycloak_script)
            self.assertIn("$$REALM-realm.json", keycloak_script)

    def test_superset_without_identity_provider_keeps_oidc_disabled(self):
        with tempfile.TemporaryDirectory() as root:
            profile_dir = Path(root)
            profile_file = self._write_profile(profile_dir, oidc=False)
            compose = self._render_profile(profile_file, profile_dir)
            environment = compose["services"]["superset"]["environment"]

            self.assertEqual(environment["CDS_OIDC_ISSUER_URL"], "")
            self.assertEqual(environment["CDS_OIDC_CLIENT_ID"], "")
            self.assertEqual(environment["CDS_OIDC_CLIENT_SECRET"], "")

    def test_superset_config_enables_oidc_only_with_complete_credentials(self):
        config_path = _REPO_ROOT / "images" / "superset" / "superset_config.py"
        with mock.patch.dict(
            "os.environ",
            {
                "CDS_OIDC_ISSUER_URL": "",
                "CDS_OIDC_CLIENT_ID": "",
                "CDS_OIDC_CLIENT_SECRET": "",
            },
            clear=False,
        ):
            config_without_oidc = runpy.run_path(str(config_path))
        self.assertNotIn("AUTH_TYPE", config_without_oidc)

        manager = types.ModuleType("flask_appbuilder.security.manager")
        manager.AUTH_OAUTH = 2
        security = types.ModuleType("flask_appbuilder.security")
        security.manager = manager
        appbuilder = types.ModuleType("flask_appbuilder")
        appbuilder.security = security

        with mock.patch.dict(
            sys.modules,
            {
                "flask_appbuilder": appbuilder,
                "flask_appbuilder.security": security,
                "flask_appbuilder.security.manager": manager,
            },
        ), mock.patch.dict(
            "os.environ",
            {
                "CDS_OIDC_ISSUER_URL": "https://identity.example.test/realms/analytics",
                "CDS_OIDC_CLIENT_ID": "superset",
                "CDS_OIDC_CLIENT_SECRET": "${CDS_SUPERSET_OIDC_CREDENTIAL}",
            },
            clear=False,
        ):
            config = runpy.run_path(str(config_path))

        self.assertEqual(config["AUTH_TYPE"], manager.AUTH_OAUTH)
        self.assertTrue(config["AUTH_USER_REGISTRATION"])
        self.assertEqual(config["AUTH_USER_REGISTRATION_ROLE"], "Gamma")
        remote_app = config["OAUTH_PROVIDERS"][0]["remote_app"]
        self.assertEqual(
            remote_app["server_metadata_url"],
            "https://identity.example.test/realms/analytics/"
            ".well-known/openid-configuration",
        )
        self.assertEqual(remote_app["client_secret"], "${CDS_SUPERSET_OIDC_CREDENTIAL}")

        with mock.patch.dict(
            "os.environ",
            {
                "CDS_OIDC_ISSUER_URL": "https://identity.example.test/realms/analytics",
                "CDS_OIDC_CLIENT_ID": "superset",
                "CDS_OIDC_CLIENT_SECRET": "",
            },
            clear=False,
        ):
            with self.assertRaisesRegex(RuntimeError, "requires issuer URL"):
                runpy.run_path(str(config_path))


if __name__ == "__main__":
    unittest.main()
