import tempfile
import unittest
from pathlib import Path

from cli.planner import build_plan
from cli.validator import validate_profile


class SmokeExampleProfileTest(unittest.TestCase):
    def test_example_profile_validates_and_builds_plan(self):
        repo_root = Path(__file__).resolve().parent.parent
        profiles_root = repo_root / "profiles"
        profile_path = profiles_root / "local-dagster-postgres-superset" / "profile.yaml"
        self.assertTrue(profile_path.exists(), f"Example profile not found at {profile_path}")

        diagnostics = validate_profile(str(profile_path))
        self.assertEqual(len([d for d in diagnostics if d.level == "error"]), 0, f"Validation failed: {[d.format() for d in diagnostics]}")

        with tempfile.TemporaryDirectory() as tmpdir:
            env_file = Path(tmpdir) / ".env"
            env_file.write_text(
                "CDS_POSTGRES_PASSWORD=testpass\n"
                "CDS_SUPERSET_SECRET_KEY=sekret\n"
                "CDS_SUPERSET_ADMIN_PASSWORD=adminpass\n",
                encoding="utf-8",
            )

            plan, plan_diags = build_plan(str(profile_path), env_file=str(env_file))
            self.assertIsNotNone(plan, "Plan generation returned None")
            self.assertEqual(len([d for d in plan_diags if d.level == "error"]), 0, f"Plan generation failed: {[d.format() for d in plan_diags]}")
            self.assertIn("modules", plan)
            self.assertGreater(len(plan["modules"]), 0)

            # Ensure secrets map to env variable names (no secret values embedded in plan)
            self.assertEqual(plan["secrets"].get("postgres_password"), "CDS_POSTGRES_PASSWORD")
            self.assertEqual(plan["secrets"].get("superset_secret_key"), "CDS_SUPERSET_SECRET_KEY")
            self.assertEqual(plan["secrets"].get("superset_admin_password"), "CDS_SUPERSET_ADMIN_PASSWORD")


if __name__ == "__main__":
    unittest.main()
