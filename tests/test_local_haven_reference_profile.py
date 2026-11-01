"""Contract coverage for the local-haven-reference profile.

Names `profiles/local-haven-reference` (see docs/contract-coverage.md,
issue #1079) and exercises its contract behavior: the profile validates,
plans, and renders to docker-compose without errors, publishing the
identity provider (OIDC), edge (reverse proxy), and database output
contracts of the Haven-aligned reference shape.
"""
import tempfile
import unittest
from pathlib import Path

import yaml

from cli.planner import build_plan
from cli.renderer import render_compose
from cli.validator import validate_profile

REPO_ROOT = Path(__file__).resolve().parent.parent
PROFILE_PATH = REPO_ROOT / "profiles" / "local-haven-reference" / "profile.yaml"

# Non-secret config values and required secrets the profile resolves from
# the environment (see the profile README).
ENV_VALUES = {
    "CDS_IDENTITY_DB_NAME": "identity",
    "CDS_IDENTITY_DB_PASSWORD": "sentinel-identity-password",
    "CDS_IDENTITY_DB_USER": "identity",
    "CDS_KEYCLOAK_ADMIN_PASSWORD": "sentinel-keycloak-password",
    "CDS_POSTGRES_SUPERUSER_PASSWORD": "sentinel-postgres-password",
}


class LocalHavenReferenceProfileTest(unittest.TestCase):
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

    def test_profile_plans_and_renders_compose(self) -> None:
        plan, plan_diags = build_plan(str(PROFILE_PATH), env_file=str(self.env_file))
        self.assertIsNotNone(plan)
        errors = [d for d in plan_diags if d.level == "error"]
        self.assertEqual(errors, [])

        # Haven-aligned output contracts: identity database, identity API,
        # OIDC provider, and edge reverse proxy.
        outputs = plan.get("outputs", {}).get("contracts", {})
        for expected in ("identityDatabase", "identityApi", "identityProvider", "edge"):
            self.assertIn(expected, outputs)

        output, render_diags = render_compose(plan, env_file=str(self.env_file))
        errors = [d for d in render_diags if d.level == "error"]
        self.assertEqual(errors, [])

        compose = yaml.safe_load(output)
        services = compose["services"]
        for expected in ("postgres", "keycloak", "traefik"):
            self.assertIn(expected, services)
        # Keycloak consumes the generic sql-database contract from postgres.
        keycloak_env = services["keycloak"]["environment"]
        self.assertEqual(keycloak_env["KC_DB"], "postgres")
        self.assertEqual(keycloak_env["KC_DB_URL_HOST"], "postgres")
        # Compare stringified: the contract is the port value, not the YAML
        # scalar type the renderer happens to emit.
        self.assertEqual(str(keycloak_env["KC_DB_URL_PORT"]), "5432")


if __name__ == "__main__":
    unittest.main()
