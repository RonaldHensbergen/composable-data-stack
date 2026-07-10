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
                "CDS_ANALYTICS_POSTGRES_PASSWORD=analytics_testpass\n"
                "CDS_DAGSTER_POSTGRES_PASSWORD=dagster_testpass\n"
                "CDS_SUPERSET_POSTGRES_PASSWORD=superset_testpass\n"
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
                    print(f"  Summary: {diag.summary}")
                    if hasattr(diag, 'detail'):
                        print(f"  Detail: {diag.detail}")
                    if hasattr(diag, 'location'):
                        print(f"  Location: {diag.location}")
                print("="*60 + "\n")
            
            self.assertEqual(len(error_diags), 0)

            output, render_diags = render_compose(plan, env_file=str(env_file))
            self.assertEqual(len([d for d in render_diags if d.level == "error"]), 0)

            compose = yaml.safe_load(output)
            self.assertIsInstance(compose, dict)
            self.assertIn("services", compose)
            self.assertGreater(len(compose["services"]), 0)
            self.assertIn("name", compose)
            self.assertIn("dagster-user-code", compose["services"])
            self.assertEqual(
                compose["services"]["dagster-user-code"]["build"]["dockerfile"],
                "images/dagster/Dockerfile",
            )
            self.assertEqual(
                compose["services"]["dagster-dagster-webserver"]["depends_on"]["dagster-user-code"]["condition"],
                "service_healthy",
            )
            self.assertEqual(
                compose["services"]["dagster-dagster-daemon"]["healthcheck"]["test"],
                [
                    "CMD",
                    "dagster",
                    "api",
                    "grpc-health-check",
                    "-h",
                    "dagster-user-code",
                    "-p",
                    "4000",
                ],
            )

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
                "CDS_ANALYTICS_POSTGRES_PASSWORD=analytics_testpass\n"
                "CDS_DAGSTER_POSTGRES_PASSWORD=dagster_testpass\n"
                "CDS_SUPERSET_POSTGRES_PASSWORD=superset_testpass\n"
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
                    except:
                        pass
                print("="*60 + "\n")
            
            self.assertEqual(len(error_diags), 0)

            output, render_diags = render_compose(plan, env_file=str(env_file))
            self.assertEqual(len([d for d in render_diags if d.level == "error"]), 0)

            compose = yaml.safe_load(output)
            self.assertIsInstance(compose, dict)
            self.assertIn("services", compose)
            self.assertIn("vault-vault", compose["services"])
            self.assertGreater(len(compose["services"]), 0)


if __name__ == "__main__":
    unittest.main()
