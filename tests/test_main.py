import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cli.diagnostics import Diagnostic
from cli.image_updates import collect_module_images
from cli.main import list_modules, list_profiles, resolve_profile_path, main
from cli.preflight import PreflightCheck


class MainCLITest(unittest.TestCase):
    def setUp(self):
        self.repo_root = Path(__file__).resolve().parent.parent
        self.profiles_root = self.repo_root / "profiles"
        self.modules_root = self.repo_root / "modules"

    def test_resolve_profile_path_with_env_root_and_profile_name(self):
        with patch.dict(os.environ, {"CDS_PROFILE_PATH": str(self.profiles_root)}, clear=False):
            resolved = resolve_profile_path("local-dagster-postgres-superset")
            expected = str(self.profiles_root / "local-dagster-postgres-superset" / "profile.yaml")
            self.assertEqual(resolved, expected)

    def test_resolve_profile_path_with_env_profile_file_and_no_arg(self):
        profile_file = self.profiles_root / "local-dagster-postgres-superset" / "profile.yaml"
        with patch.dict(os.environ, {"CDS_PROFILE_PATH": str(profile_file)}, clear=False):
            resolved = resolve_profile_path(None)
            self.assertEqual(resolved, str(profile_file))

    def test_list_profiles_uses_env_root(self):
        with patch.dict(os.environ, {"CDS_PROFILE_PATH": str(self.profiles_root)}, clear=False):
            profiles = list_profiles()
            self.assertIn("local-dagster-postgres-superset", profiles)

    def test_list_modules_uses_env_root(self):
        with patch.dict(os.environ, {"CDS_MODULE_PATH": str(self.modules_root)}, clear=False):
            modules = list_modules()
            self.assertIn("bi/superset", modules)
            self.assertIn("orchestration/dagster", modules)
            self.assertIn("warehouse/postgres", modules)

    @patch("cli.main.collect_module_images")
    @patch("cli.main.check_image_update")
    def test_list_images_command_reports_status(self, mock_check, mock_collect):
        mock_collect.return_value = [
            {"module": "orchestration/dagster", "service": "dagster", "image": "mock:1.0"}
        ]
        mock_check.return_value = {
            "image": "mock:1.0",
            "status": "update-available",
            "latest": "1.1",
        }

        with patch.object(sys, "argv", ["cds", "list", "images"]):
            result = main()

        self.assertEqual(result, 0)
        mock_collect.assert_called_once()
        mock_check.assert_called_once_with("mock:1.0", dockerfile=None)

    @patch("cli.main.collect_module_images")
    @patch("cli.main.check_image_update")
    def test_list_images_command_caches_duplicate_checks(self, mock_check, mock_collect):
        mock_collect.return_value = [
            {"module": "orchestration/dagster", "service": "web", "image": "mock:1.0"},
            {"module": "orchestration/dagster", "service": "daemon", "image": "mock:1.0"},
        ]
        mock_check.return_value = {
            "image": "mock:1.0",
            "status": "up-to-date",
            "latest": None,
        }

        with patch.object(sys, "argv", ["cds", "list", "images"]):
            result = main()

        self.assertEqual(result, 0)
        mock_collect.assert_called_once()
        mock_check.assert_called_once_with("mock:1.0", dockerfile=None)

    @patch("cli.main.run_security_validation")
    @patch("cli.main.validate_profile")
    def test_security_command_resolves_profile_and_runs_validation(self, mock_validate, mock_run_security):
        profile_file = self.profiles_root / "local-dagster-postgres-superset" / "profile.yaml"
        mock_validate.return_value = []
        mock_run_security.return_value = ([], [])

        with patch.dict(os.environ, {"CDS_PROFILE_PATH": str(self.profiles_root)}, clear=False), patch.object(
            sys, "argv", ["cds", "security", "local-dagster-postgres-superset"]
        ):
            result = main()

        self.assertEqual(result, 0)
        mock_validate.assert_called_once_with(str(profile_file))
        mock_run_security.assert_called_once()
        self.assertEqual(mock_run_security.call_args.kwargs["profile_path"], Path(str(profile_file)))

    @patch("cli.main.build_plan")
    @patch("cli.main.validate_profile")
    def test_plan_saves_to_file_with_output_flag(self, mock_validate, mock_build_plan):
        """Test that plan --output saves the plan to a file."""
        import tempfile
        
        profile_file = self.profiles_root / "local-dagster-postgres-superset" / "profile.yaml"
        test_plan = {
            "apiVersion": "cds/v1alpha1",
            "kind": "Plan",
            "metadata": {"name": "test"},
            "modules": [],
        }
        
        mock_validate.return_value = []
        mock_build_plan.return_value = (test_plan, [])
        
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            output_file = f.name
        
        try:
            with patch.dict(os.environ, {"CDS_PROFILE_PATH": str(self.profiles_root)}, clear=False), patch.object(
                sys, "argv", ["cds", "plan", "local-dagster-postgres-superset", "--output", output_file]
            ):
                result = main()
            
            self.assertEqual(result, 0)
            mock_validate.assert_called_once_with(str(profile_file))
            mock_build_plan.assert_called_once()
            called_args, called_kwargs = mock_build_plan.call_args
            self.assertEqual(called_args[0], str(profile_file))
            self.assertIn("env_file", called_kwargs)
            
            # Verify file was written
            saved_plan = json.loads(Path(output_file).read_text())
            self.assertEqual(saved_plan["apiVersion"], "cds/v1alpha1")
            self.assertEqual(saved_plan["metadata"]["name"], "test")
        finally:
            Path(output_file).unlink(missing_ok=True)

    @patch("cli.main.render_compose")
    @patch("cli.main.build_plan")
    @patch("cli.main.validate_profile")
    def test_render_from_profile_uses_default_project_root_output(
        self,
        mock_validate,
        mock_build_plan,
        mock_render,
    ):
        profile_file = self.profiles_root / "local-dagster-postgres-superset" / "profile.yaml"

        mock_validate.return_value = []
        mock_build_plan.return_value = ({"metadata": {"name": "cds-test"}, "modules": []}, [])
        mock_render.return_value = ("name: cds-test\nservices: {}\n", [])

        with patch.object(sys, "argv", ["cds", "render", "local-dagster-postgres-superset"]):
            result = main()

        self.assertEqual(result, 0)
        self.assertEqual(mock_render.call_count, 1)
        _, kwargs = mock_render.call_args
        expected_output = str(self.repo_root / "docker-compose.yml")
        self.assertEqual(kwargs["output_path"], expected_output)
        self.assertIn("env_file", kwargs)

    def test_render_from_plan_file(self):
        """Test rendering from a saved plan file."""
        import tempfile
        
        # Create a minimal valid plan
        plan = {
            "apiVersion": "cds/v1alpha1",
            "kind": "Plan",
            "metadata": {"name": "test-plan"},
            "sourceProfile": str(self.profiles_root / "local-dagster-postgres-superset" / "profile.yaml"),
            "runtime": {},
            "secrets": {},
            "outputs": {},
            "modules": [],
        }
        
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(plan, f)
            plan_file = f.name
        
        try:
            with patch("cli.main.render_compose") as mock_render:
                mock_render.return_value = ("name: test\nservices: {}\n", [])
                
                with patch.object(sys, "argv", ["cds", "render", plan_file]):
                    result = main()
                
                self.assertEqual(result, 0)
                # Verify render was called with the plan from file
                mock_render.assert_called_once()
                call_args = mock_render.call_args[0]
                self.assertEqual(call_args[0]["apiVersion"], "cds/v1alpha1")
        finally:
            Path(plan_file).unlink()

    def test_resolve_project_root_fallback_to_cwd(self):
        import tempfile
        from cli.main import resolve_project_root

        with tempfile.TemporaryDirectory() as td:
            # Create a mock profile file in the temporary directory
            # The temp dir does not have .git or pyproject.toml
            profile_path = Path(td) / "profile.yaml"
            profile_path.touch()
            
            with patch.object(Path, "cwd", return_value=Path("/mock/cwd")):
                resolved = resolve_project_root(str(profile_path))
                
                self.assertEqual(resolved, Path("/mock/cwd").resolve())

    def test_init_generates_env_file_from_profile_secrets(self):
        import tempfile

        output_file = Path(tempfile.gettempdir()) / "cds-init-test.env"
        output_file.unlink(missing_ok=True)

        try:
            with patch.object(
                sys,
                "argv",
                ["cds", "init", "local-dagster-postgres-superset", "--output", str(output_file)],
            ):
                result = main()

            self.assertEqual(result, 0)
            self.assertTrue(output_file.exists())

            content = output_file.read_text(encoding="utf-8")
            self.assertIn("CDS_ANALYTICS_DB_NAME=analytics", content)
            self.assertIn("CDS_ANALYTICS_DB_USER=analytics", content)
            self.assertIn("CDS_ANALYTICS_DB_PASSWORD=change-me", content)
            self.assertIn("CDS_DAGSTER_DB_NAME=dagster", content)
            self.assertIn("CDS_DAGSTER_DB_USER=dagster", content)
            self.assertIn("CDS_DAGSTER_DB_PASSWORD=change-me", content)
            self.assertIn("CDS_SUPERSET_DB_NAME=superset", content)
            self.assertIn("CDS_SUPERSET_DB_USER=superset", content)
            self.assertIn("CDS_SUPERSET_DB_PASSWORD=change-me", content)
            self.assertIn("CDS_SUPERSET_SECRET_KEY=change-me", content)
            self.assertIn("CDS_SUPERSET_ADMIN_PASSWORD=change-me", content)
        finally:
            output_file.unlink(missing_ok=True)

    @patch("cli.main.run_preflight")
    @patch("cli.main.render_compose")
    @patch("cli.main.build_plan")
    @patch("cli.main.validate_profile")
    def test_preflight_resolves_profile_and_runs_checks(
        self,
        mock_validate,
        mock_plan,
        mock_render,
        mock_preflight,
    ):
        mock_validate.return_value = []
        mock_plan.return_value = (
            {"runtime": {"type": "docker-compose"}, "modules": []},
            [],
        )
        mock_render.return_value = ("services: {}\n", [])
        mock_preflight.return_value = [
            PreflightCheck("PASS", "runtime.cli", "Docker CLI found.")
        ]

        with patch.object(
            sys,
            "argv",
            ["cds", "preflight", "local-dagster-postgres-superset"],
        ):
            result = main()

        self.assertEqual(result, 0)
        mock_validate.assert_called_once()
        mock_plan.assert_called_once()
        mock_render.assert_called_once()
        mock_preflight.assert_called_once()

    @patch("cli.main.subprocess.run")
    @patch("cli.main.render_compose")
    @patch("cli.main.build_plan")
    @patch("cli.main.validate_profile")
    def test_up_command_runs_docker_compose_up(self, mock_validate, mock_plan, mock_render, mock_run):
        mock_validate.return_value = []
        mock_plan.return_value = ({"metadata": {"name": "cds-test"}}, [])
        mock_render.return_value = ("services: {}", [])
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0)

        with patch.dict(os.environ, {"CDS_PROFILE_PATH": str(self.profiles_root)}, clear=False), patch.object(
            sys, "argv", ["cds", "up", "local-dagster-postgres-superset"]
        ):
            result = main()

        self.assertEqual(result, 0)
        self.assertEqual(mock_run.call_count, 2)
        build_cmd = mock_run.call_args_list[0][0][0]
        up_cmd = mock_run.call_args_list[1][0][0]
        self.assertEqual(build_cmd[:4], ["docker", "compose", "-f", build_cmd[3]])
        self.assertEqual(up_cmd[:4], ["docker", "compose", "-f", up_cmd[3]])
        self.assertIn("build", build_cmd)
        self.assertIn("up", up_cmd)
        self.assertNotIn("--detach", up_cmd)

    @patch("cli.main.subprocess.run")
    @patch("cli.main.render_compose")
    @patch("cli.main.build_plan")
    @patch("cli.main.validate_profile")
    def test_up_command_detach_flag_passes_through(self, mock_validate, mock_plan, mock_render, mock_run):
        mock_validate.return_value = []
        mock_plan.return_value = ({"metadata": {"name": "cds-test"}}, [])
        mock_render.return_value = ("services: {}", [])
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0)

        with patch.dict(os.environ, {"CDS_PROFILE_PATH": str(self.profiles_root)}, clear=False), patch.object(
            sys, "argv", ["cds", "up", "local-dagster-postgres-superset", "--detach"]
        ):
            result = main()

        self.assertEqual(result, 0)
        up_cmd = mock_run.call_args_list[1][0][0]
        self.assertIn("--detach", up_cmd)

    @patch("cli.main.subprocess.run")
    @patch("cli.main.render_compose")
    @patch("cli.main.build_plan")
    @patch("cli.main.validate_profile")
    def test_up_command_short_detach_flag_passes_through(self, mock_validate, mock_plan, mock_render, mock_run):
        mock_validate.return_value = []
        mock_plan.return_value = ({"metadata": {"name": "cds-test"}}, [])
        mock_render.return_value = ("services: {}", [])
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0)

        with patch.dict(os.environ, {"CDS_PROFILE_PATH": str(self.profiles_root)}, clear=False), patch.object(
            sys, "argv", ["cds", "up", "local-dagster-postgres-superset", "-d"]
        ):
            result = main()

        self.assertEqual(result, 0)
        up_cmd = mock_run.call_args_list[1][0][0]
        self.assertIn("--detach", up_cmd)

    @patch("cli.main.subprocess.run")
    @patch("cli.main.render_compose")
    @patch("cli.main.build_plan")
    @patch("cli.main.validate_profile")
    def test_up_command_stops_when_build_fails(self, mock_validate, mock_plan, mock_render, mock_run):
        mock_validate.return_value = []
        mock_plan.return_value = ({"metadata": {"name": "cds-test"}}, [])
        mock_render.return_value = ("services: {}", [])
        mock_run.side_effect = [
            subprocess.CompletedProcess(args=[], returncode=9),
            subprocess.CompletedProcess(args=[], returncode=0),
        ]

        with patch.dict(os.environ, {"CDS_PROFILE_PATH": str(self.profiles_root)}, clear=False), patch.object(
            sys, "argv", ["cds", "up", "local-dagster-postgres-superset"]
        ):
            result = main()

        self.assertEqual(result, 9)
        self.assertEqual(mock_run.call_count, 1)
        cmd = mock_run.call_args[0][0]
        self.assertIn("build", cmd)

    @patch("cli.main.subprocess.run")
    @patch("cli.main.validate_profile")
    def test_up_command_stops_on_validation_failure_without_calling_docker(self, mock_validate, mock_run):
        mock_validate.return_value = [Diagnostic("error", "E001", "bad profile", "spec")]

        with patch.dict(os.environ, {"CDS_PROFILE_PATH": str(self.profiles_root)}, clear=False), patch.object(
            sys, "argv", ["cds", "up", "local-dagster-postgres-superset"]
        ):
            result = main()

        self.assertEqual(result, 1)
        mock_run.assert_not_called()

    @patch("cli.main.subprocess.run")
    @patch("cli.main.build_plan")
    @patch("cli.main.validate_profile")
    def test_up_command_stops_on_plan_failure_without_calling_docker(self, mock_validate, mock_plan, mock_run):
        mock_validate.return_value = []
        mock_plan.return_value = (None, [Diagnostic("error", "E041", "bad binding", "spec")])

        with patch.dict(os.environ, {"CDS_PROFILE_PATH": str(self.profiles_root)}, clear=False), patch.object(
            sys, "argv", ["cds", "up", "local-dagster-postgres-superset"]
        ):
            result = main()

        self.assertEqual(result, 1)
        mock_run.assert_not_called()

    @patch("cli.main.subprocess.run")
    @patch("cli.main.render_compose")
    @patch("cli.main.build_plan")
    @patch("cli.main.validate_profile")
    def test_up_command_stops_on_render_failure_without_calling_docker(
        self, mock_validate, mock_plan, mock_render, mock_run
    ):
        mock_validate.return_value = []
        mock_plan.return_value = ({"metadata": {"name": "cds-test"}}, [])
        mock_render.return_value = (None, [Diagnostic("error", "E060", "bad render", "spec")])

        with patch.dict(os.environ, {"CDS_PROFILE_PATH": str(self.profiles_root)}, clear=False), patch.object(
            sys, "argv", ["cds", "up", "local-dagster-postgres-superset"]
        ):
            result = main()

        self.assertEqual(result, 1)
        mock_run.assert_not_called()

    @patch("cli.main.subprocess.run")
    @patch("cli.main.render_compose")
    @patch("cli.main.build_plan")
    @patch("cli.main.validate_profile")
    def test_up_command_reports_clear_error_when_docker_missing(self, mock_validate, mock_plan, mock_render, mock_run):
        mock_validate.return_value = []
        mock_plan.return_value = ({"metadata": {"name": "cds-test"}}, [])
        mock_render.return_value = ("services: {}", [])
        mock_run.side_effect = FileNotFoundError()

        with patch.dict(os.environ, {"CDS_PROFILE_PATH": str(self.profiles_root)}, clear=False), patch.object(
            sys, "argv", ["cds", "up", "local-dagster-postgres-superset"]
        ):
            result = main()

        self.assertEqual(result, 1)

    @patch("cli.main.subprocess.run")
    @patch("cli.main.render_compose")
    @patch("cli.main.build_plan")
    @patch("cli.main.validate_profile")
    def test_up_command_propagates_docker_compose_exit_code(self, mock_validate, mock_plan, mock_render, mock_run):
        mock_validate.return_value = []
        mock_plan.return_value = ({"metadata": {"name": "cds-test"}}, [])
        mock_render.return_value = ("services: {}", [])
        mock_run.side_effect = [
            subprocess.CompletedProcess(args=[], returncode=0),
            subprocess.CompletedProcess(args=[], returncode=17),
        ]

        with patch.dict(os.environ, {"CDS_PROFILE_PATH": str(self.profiles_root)}, clear=False), patch.object(
            sys, "argv", ["cds", "up", "local-dagster-postgres-superset"]
        ):
            result = main()

        self.assertEqual(result, 17)

    @patch("cli.main.subprocess.run")
    @patch("cli.main.render_compose")
    @patch("cli.main.build_plan")
    @patch("cli.main.validate_profile")
    def test_up_command_no_build_skips_build_step(self, mock_validate, mock_plan, mock_render, mock_run):
        mock_validate.return_value = []
        mock_plan.return_value = ({"metadata": {"name": "cds-test"}}, [])
        mock_render.return_value = ("services: {}", [])
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0)

        with patch.dict(os.environ, {"CDS_PROFILE_PATH": str(self.profiles_root)}, clear=False), patch.object(
            sys, "argv", ["cds", "up", "local-dagster-postgres-superset", "--no-build"]
        ):
            result = main()

        self.assertEqual(result, 0)
        self.assertEqual(mock_run.call_count, 1)
        cmd = mock_run.call_args[0][0]
        self.assertIn("up", cmd)
        self.assertNotIn("build", cmd)

    @patch("cli.main.subprocess.run")
    @patch("cli.main.render_compose")
    @patch("cli.main.build_plan")
    @patch("cli.main.validate_profile")
    def test_up_command_no_build_and_detach_flags_both_pass_through(
        self, mock_validate, mock_plan, mock_render, mock_run
    ):
        mock_validate.return_value = []
        mock_plan.return_value = ({"metadata": {"name": "cds-test"}}, [])
        mock_render.return_value = ("services: {}", [])
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0)

        with patch.dict(os.environ, {"CDS_PROFILE_PATH": str(self.profiles_root)}, clear=False), patch.object(
            sys,
            "argv",
            ["cds", "up", "local-dagster-postgres-superset", "--no-build", "--detach"],
        ):
            result = main()

        self.assertEqual(result, 0)
        self.assertEqual(mock_run.call_count, 1)
        cmd = mock_run.call_args[0][0]
        self.assertIn("up", cmd)
        self.assertIn("--detach", cmd)


    @patch("cli.main.render_compose")
    @patch("cli.main.build_plan")
    @patch("cli.main.run_security_validation")
    @patch("cli.main.validate_profile")
    def test_test_command_reports_pass_when_all_stages_succeed(
        self, mock_validate, mock_security, mock_plan, mock_render
    ):
        mock_validate.return_value = []
        mock_security.return_value = ([], [])
        mock_plan.return_value = ({"metadata": {"name": "cds-test"}}, [])
        mock_render.return_value = ("services: {}", [])

        with patch.dict(os.environ, {"CDS_PROFILE_PATH": str(self.profiles_root)}, clear=False), patch.object(
            sys, "argv", ["cds", "test", "local-dagster-postgres-superset"]
        ):
            result = main()

        self.assertEqual(result, 0)
        mock_render.assert_called_once()
        render_kwargs = mock_render.call_args
        self.assertNotIn("output_path", render_kwargs.kwargs)

    @patch("cli.main.render_compose")
    @patch("cli.main.build_plan")
    @patch("cli.main.run_security_validation")
    @patch("cli.main.validate_profile")
    def test_test_command_skips_downstream_stages_on_validate_failure(
        self, mock_validate, mock_security, mock_plan, mock_render
    ):
        mock_validate.return_value = [Diagnostic("error", "E001", "bad profile", "spec")]

        with patch.dict(os.environ, {"CDS_PROFILE_PATH": str(self.profiles_root)}, clear=False), patch.object(
            sys, "argv", ["cds", "test", "local-dagster-postgres-superset"]
        ):
            result = main()

        self.assertEqual(result, 1)
        mock_security.assert_not_called()
        mock_plan.assert_not_called()
        mock_render.assert_not_called()

    @patch("cli.main.render_compose")
    @patch("cli.main.build_plan")
    @patch("cli.main.run_security_validation")
    @patch("cli.main.validate_profile")
    def test_test_command_still_runs_plan_and_render_when_security_fails(
        self, mock_validate, mock_security, mock_plan, mock_render
    ):
        mock_validate.return_value = []
        mock_security.return_value = (
            [{"severity": "high", "rule_id": "CDS-SEC-001", "message": "bad", "path": "x", "module": "x", "value": None, "recommendation": []}],
            [],
        )
        mock_plan.return_value = ({"metadata": {"name": "cds-test"}}, [])
        mock_render.return_value = ("services: {}", [])

        with patch.dict(os.environ, {"CDS_PROFILE_PATH": str(self.profiles_root)}, clear=False), patch.object(
            sys, "argv", ["cds", "test", "local-dagster-postgres-superset"]
        ):
            result = main()

        self.assertEqual(result, 1)
        mock_plan.assert_called_once()
        mock_render.assert_called_once()

    @patch("cli.main.render_compose")
    @patch("cli.main.build_plan")
    @patch("cli.main.run_security_validation")
    @patch("cli.main.validate_profile")
    def test_test_command_only_high_severity_findings_fail_security_stage(
        self, mock_validate, mock_security, mock_plan, mock_render
    ):
        mock_validate.return_value = []
        mock_security.return_value = (
            [{"severity": "medium", "rule_id": "CDS-SEC-033", "message": "meh", "path": "x", "module": "x", "value": None, "recommendation": []}],
            [],
        )
        mock_plan.return_value = ({"metadata": {"name": "cds-test"}}, [])
        mock_render.return_value = ("services: {}", [])

        with patch.dict(os.environ, {"CDS_PROFILE_PATH": str(self.profiles_root)}, clear=False), patch.object(
            sys, "argv", ["cds", "test", "local-dagster-postgres-superset"]
        ):
            result = main()

        self.assertEqual(result, 0)

    @patch("cli.main.render_compose")
    @patch("cli.main.build_plan")
    @patch("cli.main.run_security_validation")
    @patch("cli.main.validate_profile")
    def test_test_command_skips_render_when_plan_fails(self, mock_validate, mock_security, mock_plan, mock_render):
        mock_validate.return_value = []
        mock_security.return_value = ([], [])
        mock_plan.return_value = (None, [Diagnostic("error", "E041", "bad binding", "spec")])

        with patch.dict(os.environ, {"CDS_PROFILE_PATH": str(self.profiles_root)}, clear=False), patch.object(
            sys, "argv", ["cds", "test", "local-dagster-postgres-superset"]
        ):
            result = main()

        self.assertEqual(result, 1)
        mock_render.assert_not_called()


class CollectModuleImagesTest(unittest.TestCase):

    _ROOT = Path(__file__).parent.parent
    _MODULES = _ROOT / "modules"
    _DOCKERFILE = _ROOT / "images" / "dagster" / "Dockerfile"

    def test_collects_images_from_real_modules(self):
        if not self._MODULES.exists():
            self.skipTest("modules directory not available")

        images = collect_module_images(self._MODULES)

class CollectModuleImagesTest(unittest.TestCase):

    _ROOT = Path(__file__).parent.parent
    _MODULES = _ROOT / "modules"
    _DOCKERFILE = _ROOT / "images" / "dagster" / "Dockerfile"

    def test_collects_images_from_real_modules(self):
        if not self._MODULES.exists():
            self.skipTest("modules directory not available")

        images = collect_module_images(self._MODULES)

        self.assertIsInstance(images, list)
        self.assertTrue(len(images) > 0)

        for entry in images:
            self.assertIn("module", entry)
            self.assertIn("service", entry)
            self.assertIn("image", entry)
            self.assertIsInstance(entry["image"], str)
            self.assertTrue(len(entry["image"]) > 0)

    def test_dagster_image_matches_dockerfile(self):
        if not self._MODULES.exists():
            self.skipTest("modules directory not available")
        if not self._DOCKERFILE.exists():
            self.skipTest("dagster Dockerfile not available")

        images = collect_module_images(self._MODULES)
        dagster_entries = [e for e in images if "dagster" in e["module"]]
        self.assertTrue(len(dagster_entries) > 0, "No dagster image found")

        # Find the main webserver entry (not user-code); fall back to first entry
        entry = next(
            (e for e in dagster_entries if e.get("service") == "dagster-webserver"),
            dagster_entries[0],
        )

        # All locally built dagster images must use the :custom tag
        image = entry["image"]
        self.assertTrue(image.startswith("local/"), f"Expected local image, got: {image}")
        self.assertTrue(image.endswith(":custom"), f"Expected :custom tag, got: {image}")
        from cli.image_updates import extract_base_image
        base = extract_base_image(Path(entry["dockerfile"]))
        
        content = self._DOCKERFILE.read_text()
        from_line = next(
            line for line in content.splitlines()
            if line.strip().startswith("FROM")
        )
        declared_image = from_line.split()[1]
        self.assertEqual(base, declared_image)

class StateCLITest(unittest.TestCase):
    def setUp(self):
        self.repo_root = Path(__file__).resolve().parent.parent
        self.profiles_root = self.repo_root / "profiles"

    @patch("cli.main.subprocess.run")
    @patch("cli.main.resolve_project_root")
    def test_state_command_prints_grouped_output(self, mock_root, mock_run):
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir)
            (project_root / "docker-compose.yml").write_text("services: {}")
            mock_root.return_value = project_root
            mock_run.return_value = subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout='{"Service": "svc-a", "Health": "healthy"}\n',
                stderr="",
            )

            with patch.dict(
                os.environ, {"CDS_PROFILE_PATH": str(self.profiles_root)}, clear=False
            ), patch.object(sys, "argv", ["cds", "state", "local-dagster-postgres-superset"]):
                result = main()

        self.assertEqual(result, 0)
        cmd = mock_run.call_args[0][0]
        self.assertEqual(cmd[:4], ["docker", "compose", "-f", cmd[3]])
        self.assertIn("ps", cmd)
        self.assertIn("-a", cmd)
        self.assertIn("--format", cmd)
        self.assertIn("json", cmd)

    @patch("cli.main.subprocess.run")
    @patch("cli.main.resolve_project_root")
    def test_state_command_errors_when_compose_file_missing(self, mock_root, mock_run):
        with tempfile.TemporaryDirectory() as tmpdir:
            mock_root.return_value = Path(tmpdir)

            with patch.dict(
                os.environ, {"CDS_PROFILE_PATH": str(self.profiles_root)}, clear=False
            ), patch.object(sys, "argv", ["cds", "state", "local-dagster-postgres-superset"]):
                result = main()

        self.assertEqual(result, 1)
        mock_run.assert_not_called()

    @patch("cli.main.subprocess.run")
    @patch("cli.main.resolve_project_root")
    def test_state_command_docker_not_found(self, mock_root, mock_run):
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir)
            (project_root / "docker-compose.yml").write_text("services: {}")
            mock_root.return_value = project_root
            mock_run.side_effect = FileNotFoundError()

            with patch.dict(
                os.environ, {"CDS_PROFILE_PATH": str(self.profiles_root)}, clear=False
            ), patch.object(sys, "argv", ["cds", "state", "local-dagster-postgres-superset"]):
                result = main()

        self.assertEqual(result, 1)

    @patch("cli.main.subprocess.run")
    @patch("cli.main.resolve_project_root")
    def test_state_command_propagates_compose_ps_failure(self, mock_root, mock_run):
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir)
            (project_root / "docker-compose.yml").write_text("services: {}")
            mock_root.return_value = project_root
            mock_run.return_value = subprocess.CompletedProcess(
                args=[], returncode=17, stdout="", stderr="boom"
            )

            with patch.dict(
                os.environ, {"CDS_PROFILE_PATH": str(self.profiles_root)}, clear=False
            ), patch.object(sys, "argv", ["cds", "state", "local-dagster-postgres-superset"]):
                result = main()

        self.assertEqual(result, 17)

    def _run_state_with_tty(self, argv_extra, isatty_return):
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir)
            (project_root / "docker-compose.yml").write_text("services: {}")
            captured = io.StringIO()
            captured.isatty = lambda: isatty_return

            with patch("cli.main.resolve_project_root", return_value=project_root), patch(
                "cli.main.subprocess.run",
                return_value=subprocess.CompletedProcess(
                    args=[],
                    returncode=0,
                    stdout='{"Service": "svc-a", "Health": "healthy"}\n',
                    stderr="",
                ),
            ), patch.dict(
                os.environ, {"CDS_PROFILE_PATH": str(self.profiles_root)}, clear=False
            ), patch.object(
                sys, "argv", ["cds", "state", "local-dagster-postgres-superset"] + argv_extra
            ), contextlib.redirect_stdout(
                captured
            ):
                result = main()

        return result, captured.getvalue()

    def test_no_color_flag_suppresses_ansi_even_on_a_tty(self):
        result, output = self._run_state_with_tty(["--no-color"], isatty_return=True)
        self.assertEqual(result, 0)
        self.assertNotIn("\033", output)

    def test_color_suppressed_on_a_non_tty_without_the_flag(self):
        result, output = self._run_state_with_tty([], isatty_return=False)
        self.assertEqual(result, 0)
        self.assertNotIn("\033", output)

    def test_color_enabled_on_a_tty_without_the_flag(self):
        result, output = self._run_state_with_tty([], isatty_return=True)
        self.assertEqual(result, 0)
        self.assertIn("\033", output)


class UseCommandCLITest(unittest.TestCase):
    def setUp(self):
        self.repo_root = Path(__file__).resolve().parent.parent
        self.profiles_root = self.repo_root / "profiles"
        self.tmpdir = tempfile.TemporaryDirectory()
        self.config_path = Path(self.tmpdir.name) / "config.json"
        self.env_patch = patch.dict(
            os.environ,
            {"CDS_PROFILE_PATH": str(self.profiles_root), "CDS_CONFIG_PATH": str(self.config_path)},
            clear=False,
        )
        self.env_patch.start()

    def tearDown(self):
        self.env_patch.stop()
        self.tmpdir.cleanup()

    def _run(self, argv_extra):
        captured = io.StringIO()
        with patch.object(sys, "argv", ["cds", "use"] + argv_extra), contextlib.redirect_stdout(captured):
            result = main()
        return result, captured.getvalue()

    def test_use_with_no_saved_profile_reports_none(self):
        result, output = self._run([])
        self.assertEqual(result, 0)
        self.assertIn("No default profile saved", output)

    def test_use_saves_and_shows_default_profile(self):
        save_result, save_output = self._run(["local-dagster-postgres-superset"])
        self.assertEqual(save_result, 0)
        self.assertIn("Saved default profile: local-dagster-postgres-superset", save_output)
        self.assertEqual(json.loads(self.config_path.read_text())["profile"], "local-dagster-postgres-superset")

        show_result, show_output = self._run([])
        self.assertEqual(show_result, 0)
        self.assertEqual(show_output.strip(), "local-dagster-postgres-superset")

    def test_use_rejects_unresolvable_profile(self):
        result, output = self._run(["does-not-exist"])
        self.assertEqual(result, 1)
        self.assertIn("ERROR", output)
        self.assertFalse(self.config_path.exists())

    def test_use_clear_removes_saved_profile(self):
        self._run(["local-dagster-postgres-superset"])
        clear_result, clear_output = self._run(["--clear"])
        self.assertEqual(clear_result, 0)
        self.assertIn("Cleared saved default profile", clear_output)

        show_result, show_output = self._run([])
        self.assertEqual(show_result, 0)
        self.assertIn("No default profile saved", show_output)

    def test_use_clear_with_nothing_saved_reports_none_cleared(self):
        result, output = self._run(["--clear"])
        self.assertEqual(result, 0)
        self.assertIn("No saved default profile to clear", output)

    def test_saved_profile_used_by_other_commands_without_argument(self):
        self._run(["local-dagster-postgres-superset"])
        captured = io.StringIO()
        with patch.object(sys, "argv", ["cds", "validate"]), contextlib.redirect_stdout(captured):
            result = main()
        self.assertEqual(result, 0)
        self.assertIn("Profile is valid", captured.getvalue())


class CompletionCommandCLITest(unittest.TestCase):
    def _run(self, shell):
        captured = io.StringIO()
        with patch.object(sys, "argv", ["cds", "completion", shell]), contextlib.redirect_stdout(captured):
            result = main()
        return result, captured.getvalue()

    def test_completion_bash_prints_bashrc_instructions(self):
        result, output = self._run("bash")
        self.assertEqual(result, 0)
        self.assertIn("~/.bashrc", output)
        self.assertIn('eval "$(register-python-argcomplete cds)"', output)
        self.assertIn("pip install argcomplete", output)
        self.assertIn("does not modify your shell config automatically", output)

    def test_completion_zsh_prints_zshrc_instructions(self):
        result, output = self._run("zsh")
        self.assertEqual(result, 0)
        self.assertIn("~/.zshrc", output)
        self.assertIn("bashcompinit", output)
        self.assertIn('eval "$(register-python-argcomplete cds)"', output)
        self.assertIn("does not modify your shell config automatically", output)

    def test_completion_powershell_prints_profile_instructions(self):
        result, output = self._run("powershell")
        self.assertEqual(result, 0)
        self.assertIn("$PROFILE", output)
        self.assertIn("register-python-argcomplete --shell powershell cds", output)
        self.assertIn("pip install argcomplete", output)
        self.assertIn("does not modify your shell config automatically", output)

    def test_completion_rejects_unsupported_shell(self):
        with patch.object(sys, "argv", ["cds", "completion", "fish"]), self.assertRaises(SystemExit) as ctx:
            main()
        self.assertEqual(ctx.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
