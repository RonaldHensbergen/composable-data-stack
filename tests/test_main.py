import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

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

    @patch("cli.main.render_compose")
    @patch("cli.main.build_plan")
    @patch("cli.main.validate_profile")
    def test_render_uses_default_project_root_output_when_no_output_arg(
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

class CollectModuleImagesTest(unittest.TestCase):

    _ROOT = Path(__file__).parent.parent
    _MODULES = _ROOT / "modules"
    _DOCKERFILE = _ROOT / "dagster" / "Dockerfile"

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
        entry = dagster_entries[0]

        # The entry image is the compose-declared local tag
        self.assertEqual(entry["image"], "local/dagster:custom")
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
