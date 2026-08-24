import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from cli.diagnostics import Diagnostic
from cli.image_updates import collect_module_images
from cli.main import (
    _resolve_profile_root,
    list_modules,
    list_profiles,
    load_env_file,
    load_saved_profile,
    main,
    resolve_profile_path,
)
from cli.preflight import PreflightCheck


@contextlib.contextmanager
def _subdirectory_without_overrides(repo_root: Path, *keys: str):
    """Run a test body from the `images/` subdirectory with the given env
    overrides removed, restoring the original cwd afterwards."""
    subdirectory = repo_root / "images"
    cleaned = {key: value for key, value in os.environ.items() if key not in keys}
    original_cwd = Path.cwd()
    try:
        os.chdir(subdirectory)
        with patch.dict(os.environ, cleaned, clear=True):
            yield
    finally:
        os.chdir(original_cwd)


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

    def test_resolve_profile_path_from_subdirectory_without_env_override(self):
        # Regression test: without CDS_PROFILE_PATH set, the default
        # "profiles/" root must resolve relative to the project root, not
        # the current working directory, so invocations from subdirectories
        # (e.g. `images/`) still find profiles at the repo root.
        with _subdirectory_without_overrides(
            self.repo_root, "CDS_PROFILE_PATH", "CDS_MODULE_PATH"
        ):
            resolved = resolve_profile_path("local-dagster-postgres-superset")
        expected = str(self.profiles_root / "local-dagster-postgres-superset" / "profile.yaml")
        self.assertEqual(resolved, expected)

    def test_list_modules_from_subdirectory_without_env_override(self):
        with _subdirectory_without_overrides(
            self.repo_root, "CDS_PROFILE_PATH", "CDS_MODULE_PATH"
        ):
            modules = list_modules()
        self.assertIn("bi/superset", modules)
        self.assertIn("orchestration/dagster", modules)
        self.assertIn("warehouse/postgres", modules)

    def test_resolve_profile_root_bare_name_fallback_from_subdirectory(self):
        with _subdirectory_without_overrides(
            self.repo_root, "CDS_PROFILE_PATH", "CDS_MODULE_PATH"
        ):
            resolved = _resolve_profile_root(Path("local-dagster-postgres-superset"))
        expected = str(self.profiles_root / "local-dagster-postgres-superset" / "profile.yaml")
        self.assertEqual(resolved, expected)

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
        mock_validate.assert_called_once_with(str(profile_file), environment=None)
        mock_run_security.assert_called_once()
        self.assertEqual(mock_run_security.call_args.kwargs["profile_path"], Path(str(profile_file)))

    @patch("cli.main.run_security_validation")
    @patch("cli.main.build_plan")
    @patch("cli.main.validate_profile")
    def test_security_verify_images_fails_closed_when_plan_fails(
        self, mock_validate, mock_build_plan, mock_run_security
    ):
        """--verify-images must fail closed (CDS-VER-004, exit 1) when plan
        generation fails, so verification can never silently pass."""
        profile_file = self.profiles_root / "local-dagster-postgres-superset" / "profile.yaml"
        mock_validate.return_value = []
        mock_run_security.return_value = ([], [])
        mock_build_plan.return_value = (
            None,
            [
                Diagnostic(
                    level="error",
                    code="E081",
                    message="missing required environment variable",
                    path="spec.modules",
                )
            ],
        )

        stdout = io.StringIO()
        with (
            patch.dict(os.environ, {"CDS_PROFILE_PATH": str(self.profiles_root)}, clear=False),
            patch.object(
                sys,
                "argv",
                ["cds", "security", "local-dagster-postgres-superset", "--verify-images"],
            ),
            contextlib.redirect_stdout(stdout),
        ):
            result = main()

        self.assertEqual(result, 1)
        self.assertIn("CDS-VER-004", stdout.getvalue())

    @patch("cli.main.verify_images")
    @patch("cli.main.render_compose")
    @patch("cli.security.render_compose")
    @patch("cli.main.build_plan")
    @patch("cli.security.build_plan")
    @patch("cli.main.validate_profile")
    def test_security_verify_images_plans_and_renders_profile_exactly_once(
        self,
        mock_validate,
        mock_sec_build_plan,
        mock_main_build_plan,
        mock_sec_render,
        mock_main_render,
        mock_verify_images,
    ):
        profile_file = self.profiles_root / "local-dagster-postgres-superset" / "profile.yaml"
        canned_plan = {
            "apiVersion": "cds/v1alpha1",
            "kind": "Plan",
            "metadata": {"name": "local-dagster-postgres-superset"},
            "modules": [],
        }
        canned_compose = "services:\n  app:\n    image: ghcr.io/example/app:1.0\n"
        mock_validate.return_value = []
        mock_sec_build_plan.return_value = (canned_plan, [])
        mock_main_build_plan.return_value = (canned_plan, [])
        mock_sec_render.return_value = (canned_compose, [])
        mock_main_render.return_value = (canned_compose, [])
        mock_verify_images.return_value = []

        with patch.dict(os.environ, {"CDS_PROFILE_PATH": str(self.profiles_root)}, clear=False), patch.object(
            sys, "argv", ["cds", "security", "local-dagster-postgres-superset", "--verify-images"]
        ):
            result = main()

        self.assertIn(result, (0, 1))
        self.assertEqual(
            mock_main_build_plan.call_count + mock_sec_build_plan.call_count,
            1,
            "the profile must be planned exactly once across cds security "
            "and image verification",
        )
        self.assertEqual(
            mock_main_render.call_count + mock_sec_render.call_count,
            1,
            "the profile must be rendered exactly once across cds security "
            "and image verification",
        )

    @patch("cli.main.run_security_validation")
    @patch("cli.main.validate_profile")
    def test_security_command_fails_closed_when_render_scan_skipped(self, mock_validate, mock_run_security):
        """GHSA-mx5p-cv63-6829: a W096 warning means rendered-compose-scoped
        rules (e.g. CDS-SEC-070) were silently skipped. Even when the
        remaining findings are only medium/low severity (or there are none
        at all), `cds security` must not report success -- otherwise an
        attacker-supplied module that trips an unexpected render error can
        evade CDS-SEC-070 entirely while the scan still exits 0."""
        profile_file = self.profiles_root / "local-dagster-postgres-superset" / "profile.yaml"
        mock_validate.return_value = []
        mock_run_security.return_value = (
            [
                {
                    "severity": "medium",
                    "rule_id": "CDS-SEC-022",
                    "message": "meh",
                    "path": "x",
                    "module": "x",
                    "value": None,
                    "recommendation": [],
                }
            ],
            [
                Diagnostic(
                    level="warning",
                    code="W096",
                    message="Rendered-compose security checks were skipped due to an unexpected error",
                    path="spec.modules",
                )
            ],
        )

        with patch.dict(os.environ, {"CDS_PROFILE_PATH": str(self.profiles_root)}, clear=False), patch.object(
            sys, "argv", ["cds", "security", "local-dagster-postgres-superset"]
        ):
            result = main()

        self.assertEqual(result, 1)

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
            mock_validate.assert_called_once_with(str(profile_file), environment=None)
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

    def test_find_project_root_prefers_nearby_cds_over_ancestor_git(self):
        """Regression test: a `cds get`/`cds use` working directory that has
        its own `.cds` marker must not be shadowed by an unrelated `.git`
        repository further up the tree (e.g. a dotfiles repo at $HOME)."""
        import tempfile
        from cli.main import find_project_root

        with tempfile.TemporaryDirectory() as td:
            home_like_root = Path(td)
            (home_like_root / ".git").mkdir()
            work_dir = home_like_root / "tmp"
            work_dir.mkdir()
            (work_dir / ".cds").mkdir()

            resolved = find_project_root(work_dir)

            self.assertEqual(resolved, work_dir.resolve())

    def test_find_project_root_falls_back_to_git_when_no_cds_marker(self):
        """A fresh checkout of the CDS repo itself (no `.cds` yet) should
        still resolve to the repo root via `.git`/`pyproject.toml`."""
        import tempfile
        from cli.main import find_project_root

        with tempfile.TemporaryDirectory() as td:
            repo_root = Path(td)
            (repo_root / ".git").mkdir()
            sub_dir = repo_root / "profiles" / "some-profile"
            sub_dir.mkdir(parents=True)

            resolved = find_project_root(sub_dir)

            self.assertEqual(resolved, repo_root.resolve())

    def test_resolve_project_root_prefers_nearby_cds_over_ancestor_git(self):
        """Same regression as find_project_root, but for the resolver used
        to place rendered docker-compose.yml output for `cds up`/`render`."""
        import tempfile
        from cli.main import resolve_project_root

        with tempfile.TemporaryDirectory() as td:
            home_like_root = Path(td)
            (home_like_root / ".git").mkdir()
            work_dir = home_like_root / "tmp"
            work_dir.mkdir()
            (work_dir / ".cds").mkdir()
            profile_path = work_dir / "profiles" / "some-profile" / "profile.yaml"
            profile_path.parent.mkdir(parents=True)
            profile_path.touch()

            resolved = resolve_project_root(str(profile_path))

            self.assertEqual(resolved, work_dir.resolve())

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

    def test_get_command_dry_run_reports_planned_files_without_writing(self):
        import tempfile

        def write(path: Path, content: str) -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")

        with tempfile.TemporaryDirectory() as source_dir, tempfile.TemporaryDirectory() as dest_dir:
            source_root = Path(source_dir)
            destination_root = Path(dest_dir)
            write(
                source_root / "profiles" / "demo" / "profile.yaml",
                """apiVersion: cds/v1alpha1
kind: Profile
metadata:
  name: demo
spec:
  runtime:
    type: docker-compose
  modules:
    - id: demo
      source: ../../modules/apps/demo
""",
            )
            write(
                source_root / "modules" / "apps" / "demo" / "module.yaml",
                """apiVersion: cds/v1alpha1
kind: Module
metadata:
  name: demo
spec:
  implementation:
    kind: docker-compose
    compose:
      services:
        app:
          image: demo:latest
""",
            )

            stdout = io.StringIO()
            with patch.object(
                sys,
                "argv",
                [
                    "cds",
                    "get",
                    "demo",
                    "--local",
                    str(source_root),
                    "--into",
                    str(destination_root),
                    "--dry-run",
                ],
            ), contextlib.redirect_stdout(stdout):
                result = main()

            self.assertEqual(result, 0)
            self.assertIn("Planned", stdout.getvalue())
            self.assertFalse((destination_root / "profiles" / "demo" / "profile.yaml").exists())
            self.assertFalse((destination_root / ".cds" / "get-manifest.json").exists())

    def test_get_command_reports_errors_on_stderr(self):
        stdout = io.StringIO()
        stderr = io.StringIO()

        with patch.object(
            sys,
            "argv",
            [
                "cds",
                "get",
                "missing-profile",
                "--local",
                str(Path(tempfile.gettempdir()) / "cds-missing-source-repo"),
            ],
        ), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            result = main()

        self.assertEqual(result, 1)
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("ERROR Source repository does not exist", stderr.getvalue())

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

    @patch("cli.main.poll_state_until_settled")
    @patch("cli.main.start_log_tail")
    @patch("cli.main.start_up_in_background")
    @patch("cli.main.run_streamed")
    @patch("cli.main.render_compose")
    @patch("cli.main.build_plan")
    @patch("cli.main.validate_profile")
    def test_up_command_builds_then_starts_live_view_and_polls(
        self, mock_validate, mock_plan, mock_render, mock_run_streamed, mock_start_up, mock_start_tail, mock_poll
    ):
        mock_validate.return_value = []
        mock_plan.return_value = ({"metadata": {"name": "cds-test"}}, [])
        mock_render.return_value = ("services: {app: {}}", [])
        mock_run_streamed.return_value = 0
        mock_up_process = MagicMock()
        mock_up_process.wait.return_value = 0
        mock_start_up.return_value = mock_up_process
        mock_start_tail.return_value = MagicMock()
        mock_poll.return_value = (True, {"RUNNING": ["app"]})

        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = os.path.join(tmpdir, "up.log")
            with patch.dict(os.environ, {"CDS_PROFILE_PATH": str(self.profiles_root)}, clear=False), patch.object(
                sys,
                "argv",
                ["cds", "up", "local-dagster-postgres-superset", "--log-file", log_path],
            ):
                result = main()

        self.assertEqual(result, 0)
        self.assertEqual(mock_run_streamed.call_count, 1)
        build_cmd = mock_run_streamed.call_args_list[0][0][0]
        mock_start_up.assert_called_once()
        up_cmd = mock_start_up.call_args[0][0]
        self.assertEqual(build_cmd[:4], ["docker", "compose", "-f", build_cmd[3]])
        self.assertEqual(up_cmd[:4], ["docker", "compose", "-f", up_cmd[3]])
        self.assertIn("build", build_cmd)
        self.assertIn("up", up_cmd)
        self.assertIn("--detach", up_cmd)
        mock_poll.assert_called_once()
        self.assertEqual(mock_poll.call_args.kwargs["expected_service_count"], 1)

    @patch("cli.main.poll_state_until_settled")
    @patch("cli.main.start_log_tail")
    @patch("cli.main.start_up_in_background")
    @patch("cli.main.run_streamed")
    @patch("cli.main.render_compose")
    @patch("cli.main.build_plan")
    @patch("cli.main.validate_profile")
    def test_up_command_starts_log_tail_only_after_up_finishes(
        self, mock_validate, mock_plan, mock_render, mock_run_streamed, mock_start_up, mock_start_tail, mock_poll
    ):
        mock_validate.return_value = []
        mock_plan.return_value = ({"metadata": {"name": "cds-test"}}, [])
        mock_render.return_value = ("services: {app: {}}", [])
        mock_run_streamed.return_value = 0
        mock_up_process = MagicMock()
        mock_up_process.wait.return_value = 0
        mock_start_up.return_value = mock_up_process
        mock_start_tail.return_value = MagicMock()

        def fake_poll(*args, **kwargs):
            # Mirrors what the real poll loop does: invoke on_up_finished
            # once `up` reports a result, before returning. Starting the
            # log tail any earlier would have it write to the same log
            # file `up` is still writing to, interleaving output mid-line.
            on_up_finished = kwargs["on_up_finished"]
            mock_start_tail.assert_not_called()
            on_up_finished(0)
            return (True, {"RUNNING": ["app"]})

        mock_poll.side_effect = fake_poll

        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = os.path.join(tmpdir, "up.log")
            with patch.dict(os.environ, {"CDS_PROFILE_PATH": str(self.profiles_root)}, clear=False), patch.object(
                sys,
                "argv",
                ["cds", "up", "local-dagster-postgres-superset", "--log-file", log_path],
            ):
                result = main()

        self.assertEqual(result, 0)
        mock_start_tail.assert_called_once()

    @patch("cli.main.poll_state_until_settled")
    @patch("cli.main.start_log_tail")
    @patch("cli.main.run_streamed")
    @patch("cli.main.render_compose")
    @patch("cli.main.build_plan")
    @patch("cli.main.validate_profile")
    def test_up_command_detach_flag_skips_poll_loop(
        self, mock_validate, mock_plan, mock_render, mock_run_streamed, mock_start_tail, mock_poll
    ):
        mock_validate.return_value = []
        mock_plan.return_value = ({"metadata": {"name": "cds-test"}}, [])
        mock_render.return_value = ("services: {}", [])
        mock_run_streamed.return_value = 0

        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = os.path.join(tmpdir, "up.log")
            with patch.dict(os.environ, {"CDS_PROFILE_PATH": str(self.profiles_root)}, clear=False), patch.object(
                sys,
                "argv",
                ["cds", "up", "local-dagster-postgres-superset", "--detach", "--log-file", log_path],
            ):
                result = main()

        self.assertEqual(result, 0)
        up_cmd = mock_run_streamed.call_args_list[1][0][0]
        self.assertIn("--detach", up_cmd)
        mock_start_tail.assert_not_called()
        mock_poll.assert_not_called()

    @patch("cli.main.poll_state_until_settled")
    @patch("cli.main.start_log_tail")
    @patch("cli.main.run_streamed")
    @patch("cli.main.render_compose")
    @patch("cli.main.build_plan")
    @patch("cli.main.validate_profile")
    def test_up_command_short_detach_flag_skips_poll_loop(
        self, mock_validate, mock_plan, mock_render, mock_run_streamed, mock_start_tail, mock_poll
    ):
        mock_validate.return_value = []
        mock_plan.return_value = ({"metadata": {"name": "cds-test"}}, [])
        mock_render.return_value = ("services: {}", [])
        mock_run_streamed.return_value = 0

        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = os.path.join(tmpdir, "up.log")
            with patch.dict(os.environ, {"CDS_PROFILE_PATH": str(self.profiles_root)}, clear=False), patch.object(
                sys,
                "argv",
                ["cds", "up", "local-dagster-postgres-superset", "-d", "--log-file", log_path],
            ):
                result = main()

        self.assertEqual(result, 0)
        up_cmd = mock_run_streamed.call_args_list[1][0][0]
        self.assertIn("--detach", up_cmd)
        mock_poll.assert_not_called()

    @patch("cli.main.run_streamed")
    @patch("cli.main.render_compose")
    @patch("cli.main.build_plan")
    @patch("cli.main.validate_profile")
    def test_up_command_stops_when_build_fails(self, mock_validate, mock_plan, mock_render, mock_run_streamed):
        mock_validate.return_value = []
        mock_plan.return_value = ({"metadata": {"name": "cds-test"}}, [])
        mock_render.return_value = ("services: {}", [])
        mock_run_streamed.return_value = 9

        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = os.path.join(tmpdir, "up.log")
            with patch.dict(os.environ, {"CDS_PROFILE_PATH": str(self.profiles_root)}, clear=False), patch.object(
                sys,
                "argv",
                ["cds", "up", "local-dagster-postgres-superset", "--log-file", log_path],
            ):
                result = main()

        self.assertEqual(result, 9)
        self.assertEqual(mock_run_streamed.call_count, 1)
        cmd = mock_run_streamed.call_args[0][0]
        self.assertIn("build", cmd)

    @patch("cli.main.run_streamed")
    @patch("cli.main.render_compose")
    @patch("cli.main.build_plan")
    @patch("cli.main.validate_profile")
    def test_up_command_interrupt_during_build_exits_130_cleanly(
        self, mock_validate, mock_plan, mock_render, mock_run_streamed
    ):
        mock_validate.return_value = []
        mock_plan.return_value = ({"metadata": {"name": "cds-test"}}, [])
        mock_render.return_value = ("services: {}", [])
        mock_run_streamed.side_effect = KeyboardInterrupt()

        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = os.path.join(tmpdir, "up.log")
            with patch.dict(os.environ, {"CDS_PROFILE_PATH": str(self.profiles_root)}, clear=False), patch.object(
                sys,
                "argv",
                ["cds", "up", "local-dagster-postgres-superset", "--log-file", log_path],
            ):
                result = main()

        self.assertEqual(result, 130)

    @patch("cli.main.poll_state_until_settled")
    @patch("cli.main.start_log_tail")
    @patch("cli.main.start_up_in_background")
    @patch("cli.main.run_streamed")
    @patch("cli.main.render_compose")
    @patch("cli.main.build_plan")
    @patch("cli.main.validate_profile")
    def test_up_command_interrupt_during_up_exits_130_cleanly(
        self, mock_validate, mock_plan, mock_render, mock_run_streamed, mock_start_up, mock_start_tail, mock_poll
    ):
        mock_validate.return_value = []
        mock_plan.return_value = ({"metadata": {"name": "cds-test"}}, [])
        mock_render.return_value = ("services: {}", [])
        mock_run_streamed.return_value = 0
        mock_up_process = MagicMock()
        mock_up_process.wait.return_value = 0
        mock_start_up.return_value = mock_up_process
        mock_start_tail.return_value = MagicMock()
        mock_poll.side_effect = KeyboardInterrupt()

        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = os.path.join(tmpdir, "up.log")
            with patch.dict(os.environ, {"CDS_PROFILE_PATH": str(self.profiles_root)}, clear=False), patch.object(
                sys,
                "argv",
                ["cds", "up", "local-dagster-postgres-superset", "--log-file", log_path],
            ):
                result = main()

        self.assertEqual(result, 130)
        # On Ctrl+C, `up` returns immediately with 130 rather than blocking
        # on a potentially long-running `up_process.wait()` call.
        mock_up_process.wait.assert_not_called()

    @patch("cli.main.run_streamed")
    @patch("cli.main.validate_profile")
    def test_up_command_stops_on_validation_failure_without_calling_docker(
        self, mock_validate, mock_run_streamed
    ):
        mock_validate.return_value = [Diagnostic("error", "E001", "bad profile", "spec")]

        with patch.dict(os.environ, {"CDS_PROFILE_PATH": str(self.profiles_root)}, clear=False), patch.object(
            sys, "argv", ["cds", "up", "local-dagster-postgres-superset"]
        ):
            result = main()

        self.assertEqual(result, 1)
        mock_run_streamed.assert_not_called()

    @patch("cli.main.run_streamed")
    @patch("cli.main.build_plan")
    @patch("cli.main.validate_profile")
    def test_up_command_stops_on_plan_failure_without_calling_docker(
        self, mock_validate, mock_plan, mock_run_streamed
    ):
        mock_validate.return_value = []
        mock_plan.return_value = (None, [Diagnostic("error", "E041", "bad binding", "spec")])

        with patch.dict(os.environ, {"CDS_PROFILE_PATH": str(self.profiles_root)}, clear=False), patch.object(
            sys, "argv", ["cds", "up", "local-dagster-postgres-superset"]
        ):
            result = main()

        self.assertEqual(result, 1)
        mock_run_streamed.assert_not_called()

    @patch("cli.main.run_streamed")
    @patch("cli.main.render_compose")
    @patch("cli.main.build_plan")
    @patch("cli.main.validate_profile")
    def test_up_command_stops_on_render_failure_without_calling_docker(
        self, mock_validate, mock_plan, mock_render, mock_run_streamed
    ):
        mock_validate.return_value = []
        mock_plan.return_value = ({"metadata": {"name": "cds-test"}}, [])
        mock_render.return_value = (None, [Diagnostic("error", "E060", "bad render", "spec")])

        with patch.dict(os.environ, {"CDS_PROFILE_PATH": str(self.profiles_root)}, clear=False), patch.object(
            sys, "argv", ["cds", "up", "local-dagster-postgres-superset"]
        ):
            result = main()

        self.assertEqual(result, 1)
        mock_run_streamed.assert_not_called()

    @patch("cli.main.start_up_in_background")
    @patch("cli.main.run_streamed")
    @patch("cli.main.render_compose")
    @patch("cli.main.build_plan")
    @patch("cli.main.validate_profile")
    def test_up_command_reports_clear_error_when_docker_missing(
        self, mock_validate, mock_plan, mock_render, mock_run_streamed, mock_start_up
    ):
        mock_validate.return_value = []
        mock_plan.return_value = ({"metadata": {"name": "cds-test"}}, [])
        mock_render.return_value = ("services: {}", [])
        mock_run_streamed.return_value = 0
        mock_start_up.side_effect = FileNotFoundError()

        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = os.path.join(tmpdir, "up.log")
            with patch.dict(os.environ, {"CDS_PROFILE_PATH": str(self.profiles_root)}, clear=False), patch.object(
                sys,
                "argv",
                ["cds", "up", "local-dagster-postgres-superset", "--log-file", log_path],
            ):
                result = main()

        self.assertEqual(result, 1)

    @patch("cli.main.poll_state_until_settled")
    @patch("cli.main.start_log_tail")
    @patch("cli.main.start_up_in_background")
    @patch("cli.main.run_streamed")
    @patch("cli.main.render_compose")
    @patch("cli.main.build_plan")
    @patch("cli.main.validate_profile")
    def test_up_command_propagates_docker_compose_up_exit_code(
        self, mock_validate, mock_plan, mock_render, mock_run_streamed, mock_start_up, mock_start_tail, mock_poll
    ):
        mock_validate.return_value = []
        mock_plan.return_value = ({"metadata": {"name": "cds-test"}}, [])
        mock_render.return_value = ("services: {}", [])
        mock_run_streamed.return_value = 0
        mock_up_process = MagicMock()
        mock_up_process.wait.return_value = 17
        mock_start_up.return_value = mock_up_process
        mock_start_tail.return_value = MagicMock()
        # A real poll_state_until_settled given a failing `up` would bail
        # out with settled=False (services that never started can't
        # settle), not settled=True; using (False, {}) here exercises the
        # actual priority the command applies: `up`'s own exit code is
        # reported over the generic "did not settle" message.
        mock_poll.return_value = (False, {})

        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = os.path.join(tmpdir, "up.log")
            with patch.dict(os.environ, {"CDS_PROFILE_PATH": str(self.profiles_root)}, clear=False), patch.object(
                sys,
                "argv",
                ["cds", "up", "local-dagster-postgres-superset", "--log-file", log_path],
            ):
                result = main()

        self.assertEqual(result, 17)
        # `up_done_fn` must be wired to the background `up` process's own
        # poll(), so poll_state_until_settled can bail out as soon as `up`
        # fails instead of waiting out the full poll timeout.
        self.assertEqual(mock_poll.call_args.kwargs.get("up_done_fn"), mock_up_process.poll)

    @patch("cli.main.poll_state_until_settled")
    @patch("cli.main.start_log_tail")
    @patch("cli.main.start_up_in_background")
    @patch("cli.main.run_streamed")
    @patch("cli.main.render_compose")
    @patch("cli.main.build_plan")
    @patch("cli.main.validate_profile")
    def test_up_command_returns_1_when_stack_does_not_settle(
        self, mock_validate, mock_plan, mock_render, mock_run_streamed, mock_start_up, mock_start_tail, mock_poll
    ):
        mock_validate.return_value = []
        mock_plan.return_value = ({"metadata": {"name": "cds-test"}}, [])
        mock_render.return_value = ("services: {app: {}}", [])
        mock_run_streamed.return_value = 0
        mock_up_process = MagicMock()
        mock_up_process.wait.return_value = 0
        mock_start_up.return_value = mock_up_process
        mock_start_tail.return_value = MagicMock()
        mock_poll.return_value = (False, {"UNHEALTHY": ["app"]})

        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = os.path.join(tmpdir, "up.log")
            with patch.dict(os.environ, {"CDS_PROFILE_PATH": str(self.profiles_root)}, clear=False), patch.object(
                sys,
                "argv",
                ["cds", "up", "local-dagster-postgres-superset", "--log-file", log_path],
            ):
                result = main()

        self.assertEqual(result, 1)

    @patch("cli.main.run_streamed")
    @patch("cli.main.render_compose")
    @patch("cli.main.build_plan")
    @patch("cli.main.validate_profile")
    def test_up_command_no_build_skips_build_step(self, mock_validate, mock_plan, mock_render, mock_run_streamed):
        mock_validate.return_value = []
        mock_plan.return_value = ({"metadata": {"name": "cds-test"}}, [])
        mock_render.return_value = ("services: {}", [])
        mock_run_streamed.return_value = 0

        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = os.path.join(tmpdir, "up.log")
            with patch.dict(os.environ, {"CDS_PROFILE_PATH": str(self.profiles_root)}, clear=False), patch.object(
                sys,
                "argv",
                ["cds", "up", "local-dagster-postgres-superset", "--no-build", "--detach", "--log-file", log_path],
            ):
                result = main()

        self.assertEqual(result, 0)
        self.assertEqual(mock_run_streamed.call_count, 1)
        cmd = mock_run_streamed.call_args[0][0]
        self.assertIn("up", cmd)
        self.assertNotIn("build", cmd)

    @patch("cli.main.run_streamed")
    @patch("cli.main.render_compose")
    @patch("cli.main.build_plan")
    @patch("cli.main.validate_profile")
    def test_up_command_no_build_and_detach_flags_both_pass_through(
        self, mock_validate, mock_plan, mock_render, mock_run_streamed
    ):
        mock_validate.return_value = []
        mock_plan.return_value = ({"metadata": {"name": "cds-test"}}, [])
        mock_render.return_value = ("services: {}", [])
        mock_run_streamed.return_value = 0

        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = os.path.join(tmpdir, "up.log")
            with patch.dict(os.environ, {"CDS_PROFILE_PATH": str(self.profiles_root)}, clear=False), patch.object(
                sys,
                "argv",
                [
                    "cds",
                    "up",
                    "local-dagster-postgres-superset",
                    "--no-build",
                    "--detach",
                    "--log-file",
                    log_path,
                ],
            ):
                result = main()

        self.assertEqual(result, 0)
        self.assertEqual(mock_run_streamed.call_count, 1)
        cmd = mock_run_streamed.call_args[0][0]
        self.assertIn("up", cmd)
        self.assertIn("--detach", cmd)

    @patch("cli.main.default_log_path")
    @patch("cli.main.poll_state_until_settled")
    @patch("cli.main.start_log_tail")
    @patch("cli.main.start_up_in_background")
    @patch("cli.main.run_streamed")
    @patch("cli.main.render_compose")
    @patch("cli.main.build_plan")
    @patch("cli.main.validate_profile")
    def test_up_command_uses_default_log_path_when_not_given(
        self,
        mock_validate,
        mock_plan,
        mock_render,
        mock_run_streamed,
        mock_start_up,
        mock_start_tail,
        mock_poll,
        mock_default_log_path,
    ):
        mock_validate.return_value = []
        mock_plan.return_value = ({"metadata": {"name": "cds-test"}}, [])
        mock_render.return_value = ("services: {}", [])
        mock_run_streamed.return_value = 0
        mock_up_process = MagicMock()
        mock_up_process.wait.return_value = 0
        mock_start_up.return_value = mock_up_process
        mock_start_tail.return_value = MagicMock()
        mock_poll.return_value = (True, {})

        with tempfile.TemporaryDirectory() as tmpdir:
            fake_log_path = Path(tmpdir) / "up-fake.log"
            mock_default_log_path.return_value = fake_log_path

            with patch.dict(os.environ, {"CDS_PROFILE_PATH": str(self.profiles_root)}, clear=False), patch.object(
                sys, "argv", ["cds", "up", "local-dagster-postgres-superset"]
            ):
                result = main()

        self.assertEqual(result, 0)
        mock_default_log_path.assert_called_once_with("local-dagster-postgres-superset")

    @patch("cli.main.default_log_path")
    @patch("cli.main.poll_state_until_settled")
    @patch("cli.main.start_log_tail")
    @patch("cli.main.start_up_in_background")
    @patch("cli.main.run_streamed")
    @patch("cli.main.render_compose")
    @patch("cli.main.build_plan")
    @patch("cli.main.validate_profile")
    def test_up_command_default_log_path_uses_profile_directory_name_not_literal_profile(
        self,
        mock_validate,
        mock_plan,
        mock_render,
        mock_run_streamed,
        mock_start_up,
        mock_start_tail,
        mock_poll,
        mock_default_log_path,
    ):
        mock_validate.return_value = []
        mock_plan.return_value = ({"metadata": {"name": "cds-test"}}, [])
        mock_render.return_value = ("services: {}", [])
        mock_run_streamed.return_value = 0
        mock_up_process = MagicMock()
        mock_up_process.wait.return_value = 0
        mock_start_up.return_value = mock_up_process
        mock_start_tail.return_value = MagicMock()
        mock_poll.return_value = (True, {})

        with tempfile.TemporaryDirectory() as tmpdir:
            mock_default_log_path.return_value = Path(tmpdir) / "up-fake.log"
            profile_dir = self.profiles_root / "local-dagster-postgres-superset"

            with patch.dict(os.environ, {"CDS_PROFILE_PATH": str(profile_dir)}, clear=False), patch.object(
                sys, "argv", ["cds", "up"]
            ):
                result = main()

        self.assertEqual(result, 0)
        mock_default_log_path.assert_called_once_with("local-dagster-postgres-superset")

    @patch("cli.main.poll_state_until_settled")
    @patch("cli.main.start_log_tail")
    @patch("cli.main.start_up_in_background")
    @patch("cli.main.run_streamed")
    @patch("cli.main.render_compose")
    @patch("cli.main.build_plan")
    @patch("cli.main.validate_profile")
    def test_up_command_no_color_flag_disables_color_in_live_view(
        self, mock_validate, mock_plan, mock_render, mock_run_streamed, mock_start_up, mock_start_tail, mock_poll
    ):
        mock_validate.return_value = []
        mock_plan.return_value = ({"metadata": {"name": "cds-test"}}, [])
        mock_render.return_value = ("services: {}", [])
        mock_run_streamed.return_value = 0
        mock_up_process = MagicMock()
        mock_up_process.wait.return_value = 0
        mock_start_up.return_value = mock_up_process
        mock_start_tail.return_value = MagicMock()
        mock_poll.return_value = (True, {})

        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = os.path.join(tmpdir, "up.log")
            with patch.dict(os.environ, {"CDS_PROFILE_PATH": str(self.profiles_root)}, clear=False), patch.object(
                sys,
                "argv",
                ["cds", "up", "local-dagster-postgres-superset", "--no-color", "--log-file", log_path],
            ), patch("sys.stdout.isatty", return_value=True):
                result = main()

        self.assertEqual(result, 0)
        self.assertFalse(mock_poll.call_args.kwargs["use_color"])

    @patch("cli.main.poll_state_until_settled")
    @patch("cli.main.start_log_tail")
    @patch("cli.main.start_up_in_background")
    @patch("cli.main.run_streamed")
    @patch("cli.main.render_compose")
    @patch("cli.main.build_plan")
    @patch("cli.main.validate_profile")
    def test_up_command_uses_color_on_a_tty_without_no_color_flag(
        self, mock_validate, mock_plan, mock_render, mock_run_streamed, mock_start_up, mock_start_tail, mock_poll
    ):
        mock_validate.return_value = []
        mock_plan.return_value = ({"metadata": {"name": "cds-test"}}, [])
        mock_render.return_value = ("services: {}", [])
        mock_run_streamed.return_value = 0
        mock_up_process = MagicMock()
        mock_up_process.wait.return_value = 0
        mock_start_up.return_value = mock_up_process
        mock_start_tail.return_value = MagicMock()
        mock_poll.return_value = (True, {})

        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = os.path.join(tmpdir, "up.log")
            with patch.dict(os.environ, {"CDS_PROFILE_PATH": str(self.profiles_root)}, clear=False), patch.object(
                sys,
                "argv",
                ["cds", "up", "local-dagster-postgres-superset", "--log-file", log_path],
            ), patch("sys.stdout.isatty", return_value=True):
                result = main()

        self.assertEqual(result, 0)
        self.assertTrue(mock_poll.call_args.kwargs["use_color"])

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

    @patch("cli.main.render_compose")
    @patch("cli.main.build_plan")
    @patch("cli.main.run_security_validation")
    @patch("cli.main.validate_profile")
    def test_test_command_fails_security_stage_when_render_scan_skipped(
        self, mock_validate, mock_security, mock_plan, mock_render
    ):
        """GHSA-mx5p-cv63-6829: `cds test`'s security stage must FAIL when
        rendered-compose-scoped rules (e.g. CDS-SEC-070) were silently
        skipped (W096), even if the only findings present are non-high
        severity (or there are none at all)."""
        mock_validate.return_value = []
        mock_security.return_value = (
            [
                {
                    "severity": "medium",
                    "rule_id": "CDS-SEC-022",
                    "message": "meh",
                    "path": "x",
                    "module": "x",
                    "value": None,
                    "recommendation": [],
                }
            ],
            [
                Diagnostic(
                    level="warning",
                    code="W096",
                    message="Rendered-compose security checks were skipped due to an unexpected error",
                    path="spec.modules",
                )
            ],
        )
        mock_plan.return_value = ({"metadata": {"name": "cds-test"}}, [])
        mock_render.return_value = ("services: {}", [])

        with patch.dict(os.environ, {"CDS_PROFILE_PATH": str(self.profiles_root)}, clear=False), patch.object(
            sys, "argv", ["cds", "test", "local-dagster-postgres-superset"]
        ):
            result = main()

        self.assertEqual(result, 1)

    def test_test_command_fails_closed_end_to_end_on_typed_substitution_injection_poc(self):
        """End-to-end regression for GHSA-gmc4-jw3j-mqcf / E072: a profile
        that drives a module's `command` field via a pure `${config.*}`
        substitution resolving to a list must fail `cds test` overall, not
        just the isolated renderer unit check. This exercises the real
        validate -> security -> plan -> render pipeline (no mocks): the
        renderer's E072 diagnostic must cause the "render" stage to fail
        and the overall `cds test` exit code to be non-zero, locking in the
        end-to-end behavior rather than only unit-testing render_compose()
        in isolation."""

        def write(path: Path, content: str) -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            profiles_root = root / "profiles"
            modules_root = root / "modules"

            write(
                modules_root / "apps" / "evil" / "module.yaml",
                """apiVersion: cds/v1alpha1
kind: Module
metadata:
  name: evil
  category: apps
  version: "0.1.0"
spec:
  runtime:
    type: container
    service:
      name: evil
      ports:
        - name: app
          containerPort: 8080
          protocol: TCP
  configSchema:
    type: object
    additionalProperties: false
    properties:
      cmd:
        type: array
        default: []
  implementation:
    kind: docker-compose
    compose:
      services:
        app:
          image: alpine:3.19
          command: "${config.cmd}"
""",
            )
            write(
                profiles_root / "poc" / "profile.yaml",
                """apiVersion: cds/v1alpha1
kind: Profile
metadata:
  name: poc
  environment: local
spec:
  runtime:
    type: docker-compose
  modules:
    - id: evil
      source: apps/evil
      version: "0.1.0"
      enabled: true
      config:
        cmd:
          - /bin/sh
          - -c
          - cat /etc/shadow > /host/tmp/shadow.txt
""",
            )

            with patch.dict(
                os.environ,
                {"CDS_PROFILE_PATH": str(profiles_root), "CDS_MODULE_PATH": str(modules_root)},
                clear=False,
            ), patch.object(sys, "argv", ["cds", "test", "poc"]), contextlib.redirect_stdout(io.StringIO()) as stdout:
                result = main()

        self.assertEqual(result, 1)
        output = stdout.getvalue()
        self.assertIn("E072", output)
        self.assertIn("[FAIL] render", output)


class CollectModuleImagesTest(unittest.TestCase):

    _ROOT = Path(__file__).parent.parent
    _MODULES = _ROOT / "modules"
    _DOCKERFILE = _ROOT / "images" / "dagster" / "base" / "Dockerfile"

    def test_collects_images_from_real_modules(self):
        if not self._MODULES.exists():
            self.skipTest("modules directory not available")

        images = collect_module_images(self._MODULES)

class CollectModuleImagesTest(unittest.TestCase):

    _ROOT = Path(__file__).parent.parent
    _MODULES = _ROOT / "modules"
    _DOCKERFILE = _ROOT / "images" / "dagster" / "base" / "Dockerfile"

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
        expected_resolved = str((self.profiles_root / "local-dagster-postgres-superset" / "profile.yaml").resolve())
        self.assertEqual(json.loads(self.config_path.read_text())["profile"], expected_resolved)

        show_result, show_output = self._run([])
        self.assertEqual(show_result, 0)
        self.assertEqual(show_output.strip(), expected_resolved)

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

    def test_use_rejects_clear_combined_with_profile_argument(self):
        result, output = self._run(["--clear", "local-dagster-postgres-superset"])
        self.assertEqual(result, 1)
        self.assertIn("ERROR", output)
        self.assertIn("--clear", output)
        self.assertFalse(self.config_path.exists())

    def test_resolve_profile_path_rejects_stale_saved_profile(self):
        save_result, _ = self._run(["local-dagster-postgres-superset"])
        self.assertEqual(save_result, 0)

        # Simulate the saved profile having been renamed/deleted since `cds use`.
        stale_config = {"profile": str(self.profiles_root / "does-not-exist-anymore" / "profile.yaml")}
        self.config_path.write_text(json.dumps(stale_config))

        with self.assertRaises(ValueError) as ctx:
            resolve_profile_path(None)
        self.assertIn("no longer resolves to a file", str(ctx.exception))
        self.assertIn("cds use --clear", str(ctx.exception))

    def test_env_profile_path_overrides_saved_default(self):
        # `cds use` saves one profile as the default...
        save_result, _ = self._run(["local-dagster-postgres-superset"])
        self.assertEqual(save_result, 0)

        # ...but an explicit, more-current CDS_PROFILE_PATH pointing at a
        # different single profile should win over the persisted default,
        # matching common CLI precedence (env var overrides persisted config).
        other_profile_dir = self.profiles_root / "local-dagster-postgres-superset-vault"
        with patch.dict(os.environ, {"CDS_PROFILE_PATH": str(other_profile_dir)}, clear=False):
            resolved = resolve_profile_path(None)
        self.assertEqual(resolved, str((other_profile_dir / "profile.yaml").resolve()))

    def test_env_profile_path_falls_back_with_warning_when_unresolvable(self):
        # CDS_PROFILE_PATH that doesn't resolve to a single profile should
        # emit a warning and fall back to the saved default.
        self._run(["local-dagster-postgres-superset"])
        captured_err = io.StringIO()
        with patch.dict(
            os.environ, {"CDS_PROFILE_PATH": "nonexistent-path"}, clear=False
        ), patch.object(sys, "stderr", captured_err):
            resolved = resolve_profile_path(None)
        expected = str((self.profiles_root / "local-dagster-postgres-superset" / "profile.yaml").resolve())
        self.assertEqual(resolved, expected)
        self.assertIn("WARNING", captured_err.getvalue())
        self.assertIn("CDS_PROFILE_PATH", captured_err.getvalue())
        self.assertIn("falling back to saved default", captured_err.getvalue())

    def test_read_config_warns_on_non_dict_json(self):
        # Valid JSON that isn't a mapping should emit a warning and be treated as empty.
        self.config_path.write_text("[1, 2, 3]")
        captured_err = io.StringIO()
        with patch.object(sys, "stderr", captured_err):
            profile = load_saved_profile()
        self.assertIsNone(profile)
        self.assertIn("WARNING", captured_err.getvalue())
        self.assertIn("not a mapping", captured_err.getvalue())

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


class LoadEnvFileTest(unittest.TestCase):
    def test_strips_double_quotes(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            env_file = Path(tmpdir) / ".env"
            env_file.write_text('CDS_TOKEN="value"\n', encoding="utf-8")
            with patch.dict(os.environ, {}, clear=True):
                load_env_file(str(env_file))
                self.assertEqual(os.environ.get("CDS_TOKEN"), "value")

    def test_strips_single_quotes(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            env_file = Path(tmpdir) / ".env"
            env_file.write_text("CDS_TOKEN='value'\n", encoding="utf-8")
            with patch.dict(os.environ, {}, clear=True):
                load_env_file(str(env_file))
                self.assertEqual(os.environ.get("CDS_TOKEN"), "value")

    def test_ignores_non_cds_keys(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            env_file = Path(tmpdir) / ".env"
            env_file.write_text("PATH=/malicious\nCDS_TOKEN=ok\n", encoding="utf-8")
            with patch.dict(os.environ, {}, clear=True):
                load_env_file(str(env_file))
                self.assertNotIn("PATH", os.environ)
                self.assertEqual(os.environ.get("CDS_TOKEN"), "ok")

    def test_does_not_override_existing_environment(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            env_file = Path(tmpdir) / ".env"
            env_file.write_text("CDS_TOKEN=from-file\n", encoding="utf-8")
            with patch.dict(os.environ, {"CDS_TOKEN": "from-env"}, clear=True):
                load_env_file(str(env_file))
                self.assertEqual(os.environ.get("CDS_TOKEN"), "from-env")

    def test_missing_file_is_noop(self):
        with patch.dict(os.environ, {}, clear=True):
            load_env_file("does-not-exist.env")
            self.assertNotIn("CDS_TOKEN", os.environ)


if __name__ == "__main__":
    unittest.main()
