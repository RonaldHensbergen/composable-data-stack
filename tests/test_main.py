import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from cli.diagnostics import Diagnostic
from cli.image_updates import collect_module_images
from cli.main import list_modules, list_profiles, resolve_profile_path, main


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
            self.assertIn("CDS_ANALYTICS_DB_PASSWORD=change-me", content)
            self.assertIn("CDS_DAGSTER_DB_PASSWORD=change-me", content)
            self.assertIn("CDS_SUPERSET_DB_PASSWORD=change-me", content)
            self.assertIn("CDS_SUPERSET_SECRET_KEY=change-me", content)
            self.assertIn("CDS_SUPERSET_ADMIN_PASSWORD=change-me", content)
        finally:
            output_file.unlink(missing_ok=True)

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

if __name__ == "__main__":
    unittest.main()
