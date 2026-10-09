"""Regression tests for the vault profile's init-db.sh bootstrap script.

Guards the defects fixed under #836 without needing Docker: psql
`:'var'` interpolation does not apply inside `DO $do$` dollar-quoted
bodies, every referenced psql variable must be passed with `-v`, and all
roles must come from configuration (no hardcoded role names).
"""

import re
import tempfile
import unittest
from pathlib import Path

import yaml

from cli.planner import build_plan
from cli.renderer import render_compose
from cli.validator import validate_profile

REPO_ROOT = Path(__file__).resolve().parent.parent
VAULT_PROFILE = REPO_ROOT / "profiles" / "local-dagster-postgres-superset-vault" / "profile.yaml"
SCRIPT = REPO_ROOT / "profiles" / "local-dagster-postgres-superset-vault" / "init-db.sh"

_EXPECTED_INIT_ENV = {
    "ANALYTICS_DB_NAME": "${CDS_ANALYTICS_DB_NAME}",
    "ANALYTICS_DB_USER": "${CDS_ANALYTICS_DB_USER}",
    "ANALYTICS_DB_PASSWORD": "${CDS_ANALYTICS_DB_PASSWORD}",
    "DAGSTER_DB_NAME": "${CDS_DAGSTER_DB_NAME}",
    "DAGSTER_DB_USER": "${CDS_DAGSTER_DB_USER}",
    "DAGSTER_DB_PASSWORD": "${CDS_DAGSTER_DB_PASSWORD}",
    "SUPERSET_DB_NAME": "${CDS_SUPERSET_DB_NAME}",
    "SUPERSET_DB_USER": "${CDS_SUPERSET_DB_USER}",
    "SUPERSET_DB_PASSWORD": "${CDS_SUPERSET_DB_PASSWORD}",
}


def _read_script() -> str:
    return SCRIPT.read_text(encoding="utf-8")


class VaultInitDbTest(unittest.TestCase):
    def test_no_dollar_quoted_do_block(self) -> None:
        text = _read_script()
        self.assertNotIn("DO $do$", text)
        self.assertNotIn("$do$;", text)

    def test_every_psql_variable_is_passed_via_dash_v(self) -> None:
        text = _read_script()
        header, _, body = text.partition("<<'SQL'")
        defined = set(re.findall(r"^\s*-v\s+([A-Za-z_][A-Za-z0-9_]*)\s*=", header, re.M))
        referenced = set(re.findall(r":'([A-Za-z_][A-Za-z0-9_]*)'", body))
        referenced |= set(re.findall(r"(?<![':]):([A-Za-z_][A-Za-z0-9_]*)", body))
        self.assertTrue(referenced, "expected psql variable references in init-db.sh")
        self.assertEqual(
            referenced - defined,
            set(),
            f"psql variables used but never passed with -v: {sorted(referenced - defined)}",
        )

    def test_no_hardcoded_role_names(self) -> None:
        text = _read_script()
        self.assertNotRegex(text, r"CREATE USER [A-Za-z_]")
        self.assertNotRegex(text, r"rolname = '[A-Za-z_]")


class VaultInitDbWiringTest(unittest.TestCase):
    """Proves, without Docker, that the rendered vault profile actually
    delivers the fixed script and its variables to the postgres container:
    init-db.sh is mounted into /docker-entrypoint-initdb.d/ and every
    variable the script reads is present in the service environment
    (passwords as ${CDS_*} placeholders, never resolved values)."""

    def test_rendered_postgres_service_mounts_script_and_env(self) -> None:
        self.assertTrue(VAULT_PROFILE.exists(), f"Vault profile not found at {VAULT_PROFILE}")

        diagnostics = validate_profile(str(VAULT_PROFILE))
        self.assertEqual(
            [d for d in diagnostics if d.level == "error"], [],
            msg=f"unexpected validation errors: {diagnostics}",
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            env_file = Path(tmpdir) / ".env"
            env_file.write_text(
                "CDS_VAULT_TOKEN=test-vault-token\n"
                "CDS_POSTGRES_SUPERUSER_PASSWORD=superuser_testpass\n"
                "CDS_ANALYTICS_DB_NAME=analytics\n"
                "CDS_ANALYTICS_DB_USER=analytics\n"
                "CDS_ANALYTICS_DB_PASSWORD=analytics_testpass\n"
                "CDS_DAGSTER_DB_NAME=dagster\n"
                "CDS_DAGSTER_DB_USER=dagster\n"
                "CDS_DAGSTER_DB_PASSWORD=dagster_testpass\n"
                "CDS_SUPERSET_DB_NAME=superset\n"
                "CDS_SUPERSET_DB_USER=superset\n"
                "CDS_SUPERSET_DB_PASSWORD=superset_testpass\n"
                "CDS_SUPERSET_SECRET_KEY=sekret\n"
                "CDS_SUPERSET_ADMIN_PASSWORD=adminpass\n",
                encoding="utf-8",
            )

            plan, plan_diags = build_plan(str(VAULT_PROFILE), env_file=str(env_file))
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
            postgres = compose["services"]["postgres"]

            mounts = postgres.get("volumes", [])
            self.assertTrue(
                any(
                    (isinstance(v, dict) and v.get("target") == "/docker-entrypoint-initdb.d/init-db.sh")
                    or (isinstance(v, str) and v.endswith("init-db.sh:/docker-entrypoint-initdb.d/init-db.sh"))
                    for v in mounts
                ),
                f"init-db.sh not mounted into postgres: {mounts}",
            )

            env = postgres.get("environment", {})
            for key, expected in _EXPECTED_INIT_ENV.items():
                self.assertIn(key, env)
                self.assertEqual(env[key], expected)


if __name__ == "__main__":
    unittest.main()
