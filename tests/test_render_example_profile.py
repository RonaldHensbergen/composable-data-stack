import tempfile
import unittest
from pathlib import Path

import yaml

from cli.planner import build_plan
from cli.renderer import render_compose
from cli.validator import validate_profile


class RenderExampleProfileTest(unittest.TestCase):
    def test_example_profile_validates_plans_and_renders_compose(self):
        repo_root = Path(__file__).resolve().parent.parent
        profile_path = repo_root / "profiles" / "local-dagster-postgres-superset" / "profile.yaml"
        self.assertTrue(profile_path.exists(), f"Example profile not found at {profile_path}")

        diagnostics = validate_profile(str(profile_path))
        self.assertEqual(len([d for d in diagnostics if d.level == "error"]), 0)

        with tempfile.TemporaryDirectory() as tmpdir:
            env_file = Path(tmpdir) / ".env"
            env_file.write_text(
                "CDS_POSTGRES_SUPERUSER_PASSWORD=superuser_testpass\n"
                "CDS_ANALYTICS_DB_PASSWORD=analytics_testpass\n"
                "CDS_DAGSTER_DB_PASSWORD=dagster_testpass\n"
                "CDS_SUPERSET_DB_PASSWORD=superset_testpass\n"
                "CDS_SUPERSET_SECRET_KEY=sekret\n"
                "CDS_SUPERSET_ADMIN_PASSWORD=adminpass\n",
                encoding="utf-8",
            )

            plan, plan_diags = build_plan(str(profile_path), env_file=str(env_file))
            self.assertIsNotNone(plan)
            
            error_diags = [d for d in plan_diags if d.level == "error"]
            # Add debugging output for errors
            if error_diags:
                print("\n" + "="*60)
                print(f"FOUND {len(error_diags)} VALIDATION ERRORS:")
                print("="*60)
                for i, diag in enumerate(error_diags, 1):
                    print(f"\n[Error {i}]")
                    print(f"  Diagnostic object: {diag}")
                    print(f"  Dir: {[attr for attr in dir(diag) if not attr.startswith('_')]}")
                    print(f"  Repr: {repr(diag)}")
                    try:
                        print(f"  Str: {str(diag)}")
                    except Exception:
                        pass
                print("="*60 + "\n")
            
            self.assertEqual(len(error_diags), 0)

            output, render_diags = render_compose(plan, env_file=str(env_file))
            self.assertEqual(len([d for d in render_diags if d.level == "error"]), 0)

            compose = yaml.safe_load(output)
            self.assertIsInstance(compose, dict)
            self.assertIn("services", compose)
            self.assertGreater(len(compose["services"]), 0)
            self.assertIn("dagster-dagster-io-manager-storage", compose.get("volumes", {}))
            self.assertIn("name", compose)
            self.assertIn("dagster-user-code", compose["services"])
            self.assertEqual(
                compose["services"]["dagster-user-code"]["build"]["dockerfile"],
                "images/dagster/Dockerfile",
            )
            self.assertEqual(
                compose["services"]["dagster-webserver"]["depends_on"]["dagster-user-code"]["condition"],
                "service_healthy",
            )
            self.assertEqual(
                compose["services"]["dagster-daemon"]["healthcheck"]["test"],
                [
                    "CMD",
                    "python",
                    "/app/images/dagster/healthcheck.py",
                    "--unix",
                    "/var/run/dagster/user-code.sock",
                ],
            )
            self.assertIn("dagster-dagster-grpc-socket", compose.get("volumes", {}))
            analytics_env = compose["services"]["dagster-user-code"]["environment"]
            self.assertIn("ANALYTICS_DB_NAME", analytics_env)
            self.assertIn("ANALYTICS_DB_CONNECTION_URI", analytics_env)
            self.assertIn("postgresql://", analytics_env["ANALYTICS_DB_CONNECTION_URI"])
            dagster_volumes = compose["services"]["dagster-user-code"].get("volumes", [])
            shared_data_mount = next(
                (
                    item
                    for item in dagster_volumes
                    if isinstance(item, dict)
                    and item.get("type") == "bind"
                    and str(item.get("target", "")).rstrip("/") == "/app/data/cds"
                ),
                None,
            )
            self.assertIsNotNone(shared_data_mount)
            self.assertEqual(shared_data_mount["source"], "workdirs/shared-data")
            definitions_mount = next(
                (
                    item
                    for item in dagster_volumes
                    if isinstance(item, dict)
                    and item.get("target") == "/app/workdirs/dagster/definitions.py"
                ),
                None,
            )
            self.assertIsNotNone(definitions_mount)
            self.assertEqual(definitions_mount["source"], "workdirs/dagster/definitions.py")
            self.assertTrue(definitions_mount["read_only"])

    def test_vault_profile_validates_plans_and_renders_vault_service(self):
        repo_root = Path(__file__).resolve().parent.parent
        profile_path = repo_root / "profiles" / "local-dagster-postgres-superset-vault" / "profile.yaml"
        self.assertTrue(profile_path.exists(), f"Vault profile not found at {profile_path}")

        diagnostics = validate_profile(str(profile_path))
        self.assertEqual(len([d for d in diagnostics if d.level == "error"]), 0)

        with tempfile.TemporaryDirectory() as tmpdir:
            env_file = Path(tmpdir) / ".env"
            env_file.write_text(
                "CDS_VAULT_TOKEN=test-vault-token\n"
                "CDS_POSTGRES_SUPERUSER_PASSWORD=superuser_testpass\n"
                "CDS_ANALYTICS_DB_PASSWORD=analytics_testpass\n"
                "CDS_DAGSTER_DB_PASSWORD=dagster_testpass\n"
                "CDS_SUPERSET_DB_PASSWORD=superset_testpass\n"
                "CDS_SUPERSET_SECRET_KEY=sekret\n"
                "CDS_SUPERSET_ADMIN_PASSWORD=adminpass\n",
                encoding="utf-8",
            )

            plan, plan_diags = build_plan(str(profile_path), env_file=str(env_file))
            self.assertIsNotNone(plan)
            
            error_diags = [d for d in plan_diags if d.level == "error"]
            # Add debugging output for errors
            if error_diags:
                print("\n" + "="*60)
                print(f"FOUND {len(error_diags)} VALIDATION ERRORS:")
                print("="*60)
                for i, diag in enumerate(error_diags, 1):
                    print(f"\n[Error {i}]")
                    print(f"  Diagnostic object: {diag}")
                    print(f"  Dir: {[attr for attr in dir(diag) if not attr.startswith('_')]}")
                    print(f"  Repr: {repr(diag)}")
                    try:
                        print(f"  Str: {str(diag)}")
                    except Exception:
                        pass
                print("="*60 + "\n")
            
            self.assertEqual(len(error_diags), 0)

            output, render_diags = render_compose(plan, env_file=str(env_file))
            self.assertEqual(len([d for d in render_diags if d.level == "error"]), 0)

            compose = yaml.safe_load(output)
            self.assertIsInstance(compose, dict)
            self.assertIn("services", compose)
            self.assertIn("vault", compose["services"])
            self.assertGreater(len(compose["services"]), 0)


if __name__ == "__main__":
    unittest.main()
