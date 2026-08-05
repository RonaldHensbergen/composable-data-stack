import tempfile
import unittest
from pathlib import Path
from unittest import mock

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
                "images/dagster/base/Dockerfile",
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


class RenderGuardRegressionTest(unittest.TestCase):
    def _build_example_plan(self):
        repo_root = Path(__file__).resolve().parent.parent
        profile_path = repo_root / "profiles" / "local-dagster-postgres-superset" / "profile.yaml"

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
            plan, diagnostics = build_plan(str(profile_path), env_file=str(env_file))

        self.assertIsNotNone(plan)
        self.assertEqual([d for d in diagnostics if d.level == "error"], [])
        return plan

    def test_render_compose_skips_service_when_enabled_from_config_is_false(self):
        plan = self._build_example_plan()
        dagster = next(module for module in plan["modules"] if module["id"] == "dagster")
        dagster["config"]["daemon"]["enabled"] = False

        output, diagnostics = render_compose(plan)

        self.assertEqual([d for d in diagnostics if d.level == "error"], [])
        compose = yaml.safe_load(output)
        self.assertNotIn("dagster-daemon", compose["services"])

    def test_render_compose_skips_volume_when_enabled_from_config_is_false(self):
        plan = self._build_example_plan()
        postgres = next(module for module in plan["modules"] if module["id"] == "postgres")
        postgres["config"]["storage"]["enabled"] = False

        output, diagnostics = render_compose(plan)

        self.assertEqual([d for d in diagnostics if d.level == "error"], [])
        compose = yaml.safe_load(output)
        self.assertNotIn("postgres-postgres-data", compose.get("volumes", {}))

    def test_render_compose_drops_healthcheck_when_condition_is_false(self):
        plan = self._build_example_plan()
        postgres = next(module for module in plan["modules"] if module["id"] == "postgres")
        postgres["config"]["healthcheck"]["enabled"] = False

        output, diagnostics = render_compose(plan)

        self.assertEqual([d for d in diagnostics if d.level == "error"], [])
        compose = yaml.safe_load(output)
        self.assertNotIn("healthcheck", compose["services"]["postgres"])


class MaterializedDefaultsRenderTest(unittest.TestCase):
    def test_omitted_nested_config_renders_with_materialized_defaults(self):
        repo_root = Path(__file__).resolve().parent.parent
        modules_root = repo_root / "modules"

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "pyproject.toml").write_text("[project]\nname='tmp'\nversion='0.0.0'\n", encoding="utf-8")

            profile_dir = root / "profiles" / "local"
            profile_dir.mkdir(parents=True)
            profile_file = profile_dir / "profile.yaml"
            profile_file.write_text(
                f"""apiVersion: cds/v1alpha1
kind: Profile
metadata:
  name: materialized-defaults
  environment: local
spec:
  runtime:
    type: docker-compose
    namespace: cds-test
    networks:
      - name: cds-test
        driver: bridge

  modules:
    - id: postgres
      source: warehouse/postgres
      version: "0.1.0"
      enabled: true
      config:
        database: appdb
        username: app
        passwordFrom: secrets.analytics_db_password
        superuserPasswordFrom: secrets.postgres_superuser_password
        port: 5432
        dagsterDatabase:
          name: dagster
          username: dagster
          passwordFrom: secrets.dagster_db_password

    - id: dagster
      source: orchestration/dagster
      version: "0.1.0"
      enabled: true
      dependsOn:
        - postgres
      config:
        webPort: 3000
        homeDir: /opt/dagster/dagster_home
        analyticsDatabase:
          contractRef: postgres.sql-database
        storage:
          backend: postgres
          runStorage:
            contractRef: postgres.dagster-database
          eventLogStorage:
            contractRef: postgres.dagster-database
          scheduleStorage:
            contractRef: postgres.dagster-database

  secrets:
    provider:
      type: env
    values:
      postgres_superuser_password:
        env: CDS_POSTGRES_SUPERUSER_PASSWORD
        required: true
      db_password:
        env: CDS_ANALYTICS_DB_PASSWORD
        required: true
      dagster_db_password:
        env: CDS_DAGSTER_DB_PASSWORD
        required: true
      analytics_db_password:
        env: CDS_ANALYTICS_DB_PASSWORD
        required: true
""",
                encoding="utf-8",
            )

            env_file = root / ".env"
            env_file.write_text(
                "CDS_POSTGRES_SUPERUSER_PASSWORD=superuser_testpass\n"
                "CDS_ANALYTICS_DB_PASSWORD=analytics_testpass\n"
                "CDS_DAGSTER_DB_PASSWORD=dagster_testpass\n",
                encoding="utf-8",
            )

            with mock.patch.dict("os.environ", {"CDS_MODULE_PATH": str(modules_root)}, clear=False):
                plan, plan_diags = build_plan(str(profile_file), env_file=str(env_file))

            self.assertIsNotNone(plan)
            error_diags = [d for d in plan_diags if d.level == "error"]
            self.assertEqual(error_diags, [], f"plan errors: {error_diags}")

            dagster = next(module for module in plan["modules"] if module["id"] == "dagster")
            self.assertEqual(
                dagster["config"]["sharedData"],
                {"hostPath": "./workdirs/shared-data", "containerPath": "/app/data/cds"},
            )
            self.assertEqual(
                dagster["config"]["definitionsFile"],
                {
                    "hostPath": "./workdirs/dagster/definitions.py",
                    "containerPath": "/app/workdirs/dagster/definitions.py",
                },
            )
            self.assertEqual(dagster["config"]["daemon"], {"enabled": True})

            output, render_diags = render_compose(plan, env_file=str(env_file))

            render_errors = [d for d in render_diags if d.level == "error"]
            self.assertEqual(render_errors, [], f"render errors: {render_errors}")

            compose = yaml.safe_load(output)
            dagster_user_code = compose["services"]["dagster-user-code"]
            volumes = dagster_user_code["volumes"]
            self.assertIn("dagster-daemon", compose["services"])

            shared_data_mount = next(
                item
                for item in volumes
                if isinstance(item, dict)
                and item.get("type") == "bind"
                and str(item.get("target", "")).rstrip("/") == "/app/data/cds"
            )
            self.assertEqual(shared_data_mount["source"], "workdirs/shared-data")

            definitions_mount = next(
                item
                for item in volumes
                if isinstance(item, dict)
                and item.get("target") == "/app/workdirs/dagster/definitions.py"
            )
            self.assertEqual(definitions_mount["source"], "workdirs/dagster/definitions.py")
            self.assertTrue(definitions_mount["read_only"])


if __name__ == "__main__":
    unittest.main()
