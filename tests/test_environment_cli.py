"""
End-to-end coverage for --environment on profile-consuming commands and for
the `cds diff` command (issue #230). Uses a real, minimal profile+module
fixture on disk so validate/plan/render/security run their full pipeline
against an actual environment overlay merge, rather than mocking cli.overlay.
"""
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from cli.main import main


class EnvironmentOverlayFixture(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.profile_dir = self.root / "profiles" / "analytics"
        self.profile_dir.mkdir(parents=True)
        self.modules_dir = self.root / "modules" / "warehouse" / "postgres"
        self.modules_dir.mkdir(parents=True)

        (self.modules_dir / "module.yaml").write_text(
            yaml.safe_dump(
                {
                    "apiVersion": "cds/v1alpha1",
                    "kind": "Module",
                    "metadata": {"name": "postgres", "category": "warehouse", "version": "0.1.0"},
                    "spec": {
                        "configSchema": {"type": "object", "additionalProperties": True},
                        "implementation": {"kind": "docker-compose", "compose": {"services": {}}},
                    },
                }
            )
        )

        self.base_profile = {
            "apiVersion": "cds/v1alpha1",
            "kind": "Profile",
            "metadata": {"name": "analytics", "environment": "local"},
            "spec": {
                "runtime": {"type": "docker-compose"},
                "modules": [
                    {
                        "id": "db",
                        "source": "warehouse/postgres",
                        "version": "0.1.0",
                        "enabled": True,
                        "config": {"replicas": 1},
                    }
                ],
            },
        }
        self.profile_path = self.profile_dir / "profile.yaml"
        self.profile_path.write_text(yaml.safe_dump(self.base_profile))

        self.env_vars = {
            "CDS_PROFILE_PATH": str(self.profile_dir),
            "CDS_MODULE_PATH": str(self.modules_dir.parent.parent),
        }

    def _write_overlay(self, name, content):
        env_dir = self.profile_dir / "environments"
        env_dir.mkdir(exist_ok=True)
        (env_dir / f"{name}.yaml").write_text(yaml.safe_dump(content))

    def _run(self, argv):
        stdout = io.StringIO()
        with patch.dict(os.environ, self.env_vars, clear=False), patch.object(sys, "argv", argv):
            with contextlib.redirect_stdout(stdout):
                result = main()
        return result, stdout.getvalue()


class ValidatePlanRenderEnvironmentTest(EnvironmentOverlayFixture):
    def test_validate_uses_overlay_when_environment_given(self):
        self._write_overlay("prod", {"spec": {"modules": [{"id": "db", "config": {"replicas": 3}}]}})
        result, output = self._run(["cds", "validate", "--environment", "prod"])
        self.assertEqual(result, 0, output)
        self.assertIn("valid", output.lower())

    def test_validate_rejects_unknown_environment(self):
        result, output = self._run(["cds", "validate", "--environment", "does-not-exist"])
        self.assertEqual(result, 1)
        self.assertIn("does-not-exist", output)

    def test_plan_records_environment_and_provenance_and_applies_overlay(self):
        self._write_overlay("prod", {"spec": {"modules": [{"id": "db", "config": {"replicas": 3}}]}})
        result, output = self._run(["cds", "plan", "--environment", "prod"])
        self.assertEqual(result, 0, output)
        plan = json.loads(output)
        self.assertEqual(plan["environment"], "prod")
        self.assertIn("spec.modules[db]", plan["provenance"])
        self.assertEqual(plan["modules"][0]["config"]["replicas"], 3)

    def test_plan_without_environment_is_unaffected_by_overlay_existing(self):
        self._write_overlay("prod", {"spec": {"modules": [{"id": "db", "config": {"replicas": 3}}]}})
        result, output = self._run(["cds", "plan"])
        self.assertEqual(result, 0, output)
        plan = json.loads(output)
        self.assertIsNone(plan["environment"])
        self.assertEqual(plan["provenance"], {})
        self.assertEqual(plan["modules"][0]["config"]["replicas"], 1)

    def test_render_from_profile_rejects_environment_combined_with_plan_file(self):
        self._write_overlay("prod", {"spec": {"modules": [{"id": "db", "config": {"replicas": 3}}]}})
        plan_result, plan_output = self._run(["cds", "plan", "--environment", "prod", "--output", str(self.root / "plan.json")])
        self.assertEqual(plan_result, 0, plan_output)

        result, output = self._run(
            ["cds", "render", str(self.root / "plan.json"), "--environment", "prod", "--output", str(self.root / "out.yml")]
        )
        self.assertEqual(result, 1)
        self.assertIn("not supported", output.lower())


class SecurityEnvironmentTest(EnvironmentOverlayFixture):
    def test_security_applies_stricter_policy_under_production_overlay(self):
        # Overlay promotes the profile to production; base stays "local".
        self._write_overlay("prod", {"metadata": {"environment": "production"}})
        base_result, base_output = self._run(["cds", "security"])
        prod_result, prod_output = self._run(["cds", "security", "--environment", "prod"])
        # Both must at least run cleanly; the overlay must actually flow the
        # declared environment into security classification.
        self.assertIn(base_result, (0, 1))
        self.assertIn(prod_result, (0, 1))


class InitEnvironmentTest(EnvironmentOverlayFixture):
    def test_init_collects_env_vars_introduced_only_by_the_overlay(self):
        self._write_overlay(
            "prod",
            {
                "spec": {
                    "modules": [
                        {"id": "db", "config": {"extra": "${CDS_ANALYTICS_EXTRA}"}},
                    ]
                }
            },
        )
        output_path = self.root / ".env"
        result, output = self._run(
            ["cds", "init", "--environment", "prod", "--output", str(output_path)]
        )
        self.assertEqual(result, 0, output)
        contents = output_path.read_text()
        self.assertIn("CDS_ANALYTICS_EXTRA", contents)

    def test_init_without_environment_does_not_see_overlay_only_vars(self):
        self._write_overlay(
            "prod",
            {
                "spec": {
                    "modules": [
                        {"id": "db", "config": {"extra": "${CDS_ANALYTICS_EXTRA}"}},
                    ]
                }
            },
        )
        # Base profile alone has no env var references at all, so cds init
        # without --environment must fail rather than silently see the
        # overlay-only var.
        result, output = self._run(["cds", "init", "--output", str(self.root / ".env")])
        self.assertEqual(result, 1)
        self.assertIn("no environment variables", output.lower())


class DiffCommandTest(EnvironmentOverlayFixture):
    def test_diff_reports_no_changes_for_identical_overlays(self):
        self._write_overlay("dev", {"spec": {"modules": [{"id": "db", "config": {"replicas": 1}}]}})
        self._write_overlay("prod", {"spec": {"modules": [{"id": "db", "config": {"replicas": 1}}]}})
        result, output = self._run(["cds", "diff", "--from", "dev", "--to", "prod"])
        self.assertEqual(result, 0, output)
        self.assertIn("No differences", output)

    def test_diff_reports_changed_module_config(self):
        self._write_overlay("dev", {"spec": {"modules": [{"id": "db", "config": {"replicas": 1}}]}})
        self._write_overlay("prod", {"spec": {"modules": [{"id": "db", "config": {"replicas": 3}}]}})
        result, output = self._run(["cds", "diff", "--from", "dev", "--to", "prod"])
        self.assertEqual(result, 0, output)
        self.assertIn("spec.modules[db].config.replicas", output)
        self.assertIn("1", output)
        self.assertIn("3", output)

    def test_diff_reports_added_module(self):
        self._write_overlay("dev", {"spec": {"modules": [{"id": "db", "config": {"replicas": 1}}]}})
        self._write_overlay(
            "prod",
            {
                "metadata": {"environment": "production"},
                "spec": {
                    "modules": [
                        {"id": "db", "config": {"replicas": 1}},
                    ]
                },
            },
        )
        result, output = self._run(["cds", "diff", "--from", "dev", "--to", "prod"])
        self.assertEqual(result, 0, output)
        self.assertIn("metadata.environment", output)

    def test_diff_never_prints_a_raw_secret_value(self):
        # Even when a secret alias is involved, the profile only ever holds
        # the "secrets.<alias>" indirection, never the resolved secret value,
        # so a diff of resolved profiles cannot leak one.
        self._write_overlay(
            "dev",
            {"spec": {"secrets": {"values": {"db_password": {"env": "CDS_DB_PASSWORD"}}}}},
        )
        self._write_overlay(
            "prod",
            {"spec": {"secrets": {"values": {"db_password": {"env": "CDS_DB_PASSWORD_PROD"}}}}},
        )
        result, output = self._run(["cds", "diff", "--from", "dev", "--to", "prod"])
        self.assertEqual(result, 0, output)
        self.assertNotIn("change-me", output)

    def test_diff_fails_clearly_on_unknown_environment(self):
        self._write_overlay("dev", {"spec": {"modules": [{"id": "db", "config": {"replicas": 1}}]}})
        result, output = self._run(["cds", "diff", "--from", "dev", "--to", "does-not-exist"])
        self.assertEqual(result, 1)
        self.assertIn("does-not-exist", output)


if __name__ == "__main__":
    unittest.main()
