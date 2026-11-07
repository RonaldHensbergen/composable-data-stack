"""Keycloak OIDC client provisioning from profile config (issue #1074).

A profile can declare `config.oidcClients` on a keycloak module instance;
the Compose renderer builds the realm-import JSON from `config.realm` +
`config.oidcClients` and injects it as the `CDS_OIDC_REALM_IMPORT`
environment variable, which the keycloak entrypoint writes to
/opt/keycloak/data/import on startup. Client secrets stay `secrets.*`
aliases rendered as `${CDS_*}` runtime placeholders: the rendered Compose
output never contains a resolved secret value (CDS-SEC-030/E078 bar), and
malformed client entries fail closed with E160.
"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import yaml

from cli.planner import build_plan
from cli.renderer import _oidc_realm_import_document, render_compose
from cli.validator import validate_profile

_REPO_ROOT = Path(__file__).resolve().parent.parent

_CLIENT_SECRET_VALUE = "superset-client-secret-testvalue"  # noqa: S105 - test fixture, not a credential


class KeycloakOidcClientsTest(unittest.TestCase):
    """End-to-end plan/render tests for config.oidcClients on the keycloak module."""

    def _write_profile(self, profile_dir: Path, keycloak_config: dict) -> Path:
        profile = {
            "apiVersion": "cds/v1alpha1",
            "kind": "Profile",
            "metadata": {
                "name": "keycloak-oidc-clients",
                "environment": "local",
            },
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
                        "config": keycloak_config,
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
                        "superset_client_secret": {
                            "env": "CDS_SUPERSET_CLIENT_SECRET",
                            "required": True,
                        },
                    },
                },
            },
        }
        profile_file = profile_dir / "profile.yaml"
        profile_file.write_text(yaml.safe_dump(profile), encoding="utf-8")
        return profile_file

    def _base_keycloak_config(self) -> dict:
        return {
            "realm": "app",
            "metadataDatabase": {"contractRef": "postgres.sql-database"},
            "adminUser": {
                "username": "admin",
                "passwordFrom": "secrets.keycloak_admin_password",
            },
        }

    def _render(self, profile_file: Path, env_file: Path):
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
            return render_compose(plan, env_file=str(env_file))

    def _write_env(self, profile_dir: Path) -> Path:
        env_file = profile_dir / ".env"
        env_file.write_text(
            "CDS_IDENTITY_DB_PASSWORD=identity_testpass\n"
            "CDS_POSTGRES_SUPERUSER_PASSWORD=superuser_testpass\n"
            "CDS_KEYCLOAK_ADMIN_PASSWORD=admin_testpass\n"
            f"CDS_SUPERSET_CLIENT_SECRET={_CLIENT_SECRET_VALUE}\n",
            encoding="utf-8",
        )
        return env_file

    def test_clients_render_into_realm_import_with_secret_placeholder(self):
        with tempfile.TemporaryDirectory() as root:
            profile_dir = Path(root)
            config = self._base_keycloak_config()
            config["oidcClients"] = [
                {
                    "clientId": "superset",
                    "redirectUris": [
                        "https://superset.example.org/oauth-authorized/keycloak"
                    ],
                    "webOrigins": ["https://superset.example.org"],
                    "standardFlowEnabled": True,
                    "roles": ["viewer", "editor"],
                    "secretFrom": "secrets.superset_client_secret",
                },
                {
                    "clientId": "public-app",
                    "redirectUris": ["https://app.example.org/callback"],
                },
            ]
            profile_file = self._write_profile(profile_dir, config)
            env_file = self._write_env(profile_dir)

            output, render_diags = self._render(profile_file, env_file)
            self.assertEqual(
                [d for d in render_diags if d.level == "error"],
                [],
                msg=f"unexpected render errors: {render_diags}",
            )

            compose = yaml.safe_load(output)
            keycloak_env = compose["services"]["keycloak"]["environment"]
            self.assertIn("CDS_OIDC_REALM_IMPORT", keycloak_env)

            document = json.loads(keycloak_env["CDS_OIDC_REALM_IMPORT"])
            self.assertEqual(document["realm"], "app")
            self.assertTrue(document["enabled"])
            self.assertEqual(len(document["clients"]), 2)

            superset = document["clients"][0]
            self.assertEqual(superset["clientId"], "superset")
            self.assertEqual(superset["protocol"], "openid-connect")
            self.assertTrue(superset["standardFlowEnabled"])
            self.assertEqual(
                superset["redirectUris"],
                ["https://superset.example.org/oauth-authorized/keycloak"],
            )
            self.assertEqual(superset["webOrigins"], ["https://superset.example.org"])
            self.assertFalse(superset["publicClient"])
            # The secret stays a runtime placeholder in the rendered output.
            self.assertEqual(superset["secret"], "${CDS_SUPERSET_CLIENT_SECRET}")
            self.assertEqual(superset["defaultRoles"], ["viewer", "editor"])
            self.assertEqual(
                document["roles"]["realm"], [{"name": "viewer"}, {"name": "editor"}]
            )

            public = document["clients"][1]
            self.assertEqual(public["clientId"], "public-app")
            self.assertTrue(public["publicClient"])
            self.assertNotIn("secret", public)
            self.assertNotIn("defaultRoles", public)

            # The entrypoint consumes the injected variable.
            entrypoint = "\n".join(compose["services"]["keycloak"]["entrypoint"])
            self.assertIn("CDS_OIDC_REALM_IMPORT", entrypoint)

            # CDS-SEC-030 bar: the resolved secret value never lands in the
            # rendered Compose output.
            self.assertNotIn(_CLIENT_SECRET_VALUE, output)
            self.assertIn("${CDS_SUPERSET_CLIENT_SECRET}", output)
            leak = [d for d in render_diags if d.level == "error"]
            self.assertEqual(leak, [])

    def test_without_clients_the_realm_import_has_an_empty_client_list(self):
        with tempfile.TemporaryDirectory() as root:
            profile_dir = Path(root)
            profile_file = self._write_profile(
                profile_dir, self._base_keycloak_config()
            )
            env_file = self._write_env(profile_dir)

            output, render_diags = self._render(profile_file, env_file)
            self.assertEqual(
                [d for d in render_diags if d.level == "error"],
                [],
                msg=f"unexpected render errors: {render_diags}",
            )
            compose = yaml.safe_load(output)
            keycloak_env = compose["services"]["keycloak"]["environment"]
            document = json.loads(keycloak_env["CDS_OIDC_REALM_IMPORT"])
            self.assertEqual(document, {"realm": "app", "enabled": True, "clients": []})

    def test_undeclared_secret_alias_fails_validation(self):
        with tempfile.TemporaryDirectory() as root:
            profile_dir = Path(root)
            config = self._base_keycloak_config()
            config["oidcClients"] = [
                {
                    "clientId": "superset",
                    "redirectUris": ["https://superset.example.org/cb"],
                    "secretFrom": "secrets.not_declared",
                }
            ]
            profile_file = self._write_profile(profile_dir, config)
            with mock.patch.dict(
                "os.environ", {"CDS_MODULE_PATH": str(_REPO_ROOT)}, clear=False
            ):
                diagnostics = validate_profile(str(profile_file))
            errors = [d for d in diagnostics if d.level == "error"]
            self.assertTrue(errors, "expected an error for the undeclared alias")
            self.assertTrue(
                any("not_declared" in d.message for d in errors),
                msg=f"expected the alias named in the error: {errors}",
            )


class OidcRealmImportDocumentUnitTest(unittest.TestCase):
    """Unit tests for the E160 fail-closed checks on malformed client entries."""

    def test_malformed_entries_are_e160_errors(self):
        cases = [
            ("not-a-list", {"realm": "app", "oidcClients": {"clientId": "x"}}),
            ("entry-not-object", {"realm": "app", "oidcClients": ["x"]}),
            (
                "bad-client-id",
                {
                    "realm": "app",
                    "oidcClients": [
                        {"clientId": "bad id!", "redirectUris": ["https://x/cb"]}
                    ],
                },
            ),
            (
                "empty-redirect-uris",
                {
                    "realm": "app",
                    "oidcClients": [{"clientId": "ok", "redirectUris": []}],
                },
            ),
            (
                "bad-secret-ref",
                {
                    "realm": "app",
                    "oidcClients": [
                        {
                            "clientId": "ok",
                            "redirectUris": ["https://x/cb"],
                            "secretFrom": "not-a-secret-ref",
                        }
                    ],
                },
            ),
            (
                "unknown-secret-alias",
                {
                    "realm": "app",
                    "oidcClients": [
                        {
                            "clientId": "ok",
                            "redirectUris": ["https://x/cb"],
                            "secretFrom": "secrets.ghost",
                        }
                    ],
                },
            ),
        ]
        for name, config in cases:
            with self.subTest(name=name):
                document, diagnostics = _oidc_realm_import_document(
                    "keycloak", config, secrets={"declared": "CDS_DECLARED"}
                )
                self.assertIsNone(document)
                self.assertTrue(
                    any(d.code == "E160" and d.level == "error" for d in diagnostics),
                    msg=f"expected E160, got {diagnostics}",
                )

    def test_valid_document_shape(self):
        document, diagnostics = _oidc_realm_import_document(
            "keycloak",
            {
                "realm": "app",
                "oidcClients": [
                    {
                        "clientId": "superset",
                        "redirectUris": ["https://x/cb"],
                        "secretFrom": "secrets.declared",
                        "roles": ["viewer"],
                    }
                ],
            },
            secrets={"declared": "CDS_DECLARED"},
        )
        self.assertEqual(diagnostics, [])
        self.assertEqual(
            document,
            {
                "realm": "app",
                "enabled": True,
                "clients": [
                    {
                        "clientId": "superset",
                        "enabled": True,
                        "protocol": "openid-connect",
                        "standardFlowEnabled": True,
                        "redirectUris": ["https://x/cb"],
                        "webOrigins": [],
                        "defaultRoles": ["viewer"],
                        "publicClient": False,
                        "secret": "${CDS_DECLARED}",
                    }
                ],
                "roles": {"realm": [{"name": "viewer"}]},
            },
        )
        # The JSON encoding keeps profile-controlled values inert strings.
        encoded = json.dumps(document)
        self.assertIn('"${CDS_DECLARED}"', encoded)


if __name__ == "__main__":
    unittest.main()
