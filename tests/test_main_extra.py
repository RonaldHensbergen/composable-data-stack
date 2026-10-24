import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cli import main as cli_main
from cli.diagnostics import Diagnostic
from cli.main import (
    ConfigIOError,
    _resolve_profile_for_config,
    main,
    resolve_profile_path,
    set_config_value,
    unset_config_value,
)


def _run(argv, env=None):
    out, err = io.StringIO(), io.StringIO()
    code = 0
    with patch.object(sys, "argv", ["cds", *argv]), \
            patch.dict(os.environ, env or {}, clear=False), \
            contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            result = main()
            code = 0 if result is None else result
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 1
    return code, out.getvalue(), err.getvalue()


def _error(code="E001"):
    return Diagnostic(level="error", code=code, message="boom", path="spec")


class ConfigMutationErrorTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.config_path = Path(self._tmp.name) / "config.json"
        patcher = patch.dict(os.environ, {"CDS_CONFIG_PATH": str(self.config_path)})
        patcher.start()
        self.addCleanup(patcher.stop)

    def _write(self, data):
        self.config_path.write_text(json.dumps(data), encoding="utf-8")

    def test_set_rejects_non_mapping_sections(self):
        for key, section in (
            ("security.strict", "security"),
            ("image.source", "image"),
            ("audit.enabled", "audit"),
            ("get.allowedSources", "get"),
        ):
            with self.subTest(key=key):
                self._write({section: "nope"})
                with self.assertRaises(ConfigIOError):
                    set_config_value(key, "true")

    def test_set_and_unset_all_simple_keys(self):
        for key, value in (
            ("profile", "p"),
            ("environment", "dev"),
            ("target", "helm"),
            ("security.strict", "true"),
            ("image.source", "local"),
            ("audit.enabled", "true"),
            ("get.allowedSources", "a"),
        ):
            with self.subTest(key=key):
                set_config_value(key, value)
                self.assertTrue(unset_config_value(key))
                self.assertFalse(unset_config_value(key))

    def test_unknown_key_rejected(self):
        with self.assertRaises(ValueError):
            set_config_value("nope", "x")
        with self.assertRaises(ValueError):
            unset_config_value("nope")

    def test_unset_missing_nested_returns_false(self):
        self._write({"security": "x", "image": [], "audit": 1, "get": None})
        for key in ("security.strict", "image.source", "audit.enabled", "get.allowedSources"):
            with self.subTest(key=key):
                self.assertFalse(unset_config_value(key))

    def test_resolve_profile_for_config_missing_file(self):
        with patch.dict(os.environ, {"CDS_PROFILE_PATH": self._tmp.name}):
            with self.assertRaises(ValueError):
                _resolve_profile_for_config("does-not-exist")

    def test_resolve_profile_for_config_single_file_mismatch(self):
        profile = Path(self._tmp.name) / "only" / "profile.yaml"
        profile.parent.mkdir()
        profile.write_text("x: 1\n", encoding="utf-8")
        with patch.dict(os.environ, {"CDS_PROFILE_PATH": str(profile)}):
            with self.assertRaises(ValueError) as ctx:
                _resolve_profile_for_config("other")
            self.assertIn("other", str(ctx.exception))
            self.assertEqual(
                _resolve_profile_for_config("only"), str(profile.resolve())
            )


class ResolveProfilePathTest(unittest.TestCase):
    def test_directory_with_profile_yaml(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "profile.yaml").write_text("x: 1\n", encoding="utf-8")
            self.assertEqual(
                resolve_profile_path(tmp), str((Path(tmp) / "profile.yaml").resolve())
            )

    def test_directory_with_single_child_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            child = Path(tmp) / "child"
            child.mkdir()
            (child / "profile.yaml").write_text("x: 1\n", encoding="utf-8")
            self.assertEqual(
                resolve_profile_path(tmp), str((child / "profile.yaml").resolve())
            )

    def test_name_resolves_to_flat_yaml_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "flat.yaml").write_text("x: 1\n", encoding="utf-8")
            with patch.dict(os.environ, {"CDS_PROFILE_PATH": tmp}):
                self.assertEqual(
                    resolve_profile_path("flat"), str((Path(tmp) / "flat.yaml").resolve())
                )

    def test_nonexistent_yaml_suffix_resolves(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "missing.yaml"
            self.assertEqual(resolve_profile_path(str(missing)), str(missing.resolve()))


class RenderErrorPathsTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        patcher = patch.dict(
            os.environ, {"CDS_CONFIG_PATH": str(self.tmp / "config.json")}
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.plan = self.tmp / "plan.json"
        self.plan.write_text(
            json.dumps({"apiVersion": "cds/v1alpha1", "sourceProfile": str(self.tmp / "p.yaml")}),
            encoding="utf-8",
        )

    def test_plan_file_with_environment_flag_rejected(self):
        code, out, _ = _run(["render", str(self.plan), "--environment", "dev"])
        self.assertEqual(code, 1)
        self.assertIn("--environment is not supported", out)

    def test_plan_file_compose_render_errors(self):
        with patch.object(cli_main, "render_compose", return_value=("", [_error()])), \
                patch.object(cli_main, "_record_audit"):
            code, out, _ = _run(["render", str(self.plan), "-o", str(self.tmp / "c.yml")])
        self.assertEqual(code, 1)
        self.assertIn("Render failed.", out)

    def test_plan_file_compose_render_success(self):
        out_file = self.tmp / "c.yml"
        with patch.object(cli_main, "render_compose", return_value=("", [])), \
                patch.object(cli_main, "_record_audit"):
            code, out, _ = _run(["render", str(self.plan), "-o", str(out_file)])
        self.assertEqual(code, 0)
        self.assertIn("Rendered compose file written", out)

    def test_plan_file_helm_nonzero_code(self):
        with patch.object(cli_main, "_render_helm_chart", return_value=(3, [])), \
                patch.object(cli_main, "_record_audit"):
            code, _, _ = _run(
                ["render", str(self.plan), "--target", "helm", "-o", str(self.tmp / "chart")]
            )
        self.assertEqual(code, 3)

    def test_plan_file_helm_diagnostic_errors(self):
        with patch.object(cli_main, "_render_helm_chart", return_value=(0, [_error()])), \
                patch.object(cli_main, "_record_audit"):
            code, out, _ = _run(
                ["render", str(self.plan), "--target", "helm", "-o", str(self.tmp / "chart")]
            )
        self.assertEqual(code, 1)
        self.assertIn("Render failed.", out)

    def test_plan_file_helm_success(self):
        with patch.object(cli_main, "_render_helm_chart", return_value=(0, [])), \
                patch.object(cli_main, "_record_audit"):
            code, out, _ = _run(
                ["render", str(self.plan), "--target", "helm", "-o", str(self.tmp / "chart")]
            )
        self.assertEqual(code, 0)
        self.assertIn("Rendered Helm chart written", out)

    def _profile_render(self, extra, **patches):
        profile = str(self.tmp / "profile.yaml")
        stack = contextlib.ExitStack()
        with stack:
            stack.enter_context(patch.object(cli_main, "resolve_profile_path", return_value=profile))
            stack.enter_context(patch.object(cli_main, "_record_audit"))
            for name, value in patches.items():
                stack.enter_context(patch.object(cli_main, name, **value))
            return _run(["render", "prof", *extra])

    def test_profile_resolution_error(self):
        with patch.object(cli_main, "resolve_profile_path", side_effect=ValueError("nope")):
            code, out, _ = _run(["render", "prof"])
        self.assertEqual(code, 1)
        self.assertIn("ERROR nope", out)

    def test_profile_validation_failure(self):
        code, out, _ = self._profile_render(
            [], validate_profile={"return_value": [_error()]}
        )
        self.assertEqual(code, 1)
        self.assertIn("Cannot render because validation failed.", out)

    def test_profile_plan_failure(self):
        code, out, _ = self._profile_render(
            [],
            validate_profile={"return_value": []},
            build_plan={"return_value": (None, [_error()])},
        )
        self.assertEqual(code, 1)
        self.assertIn("plan generation failed", out)

    def test_profile_render_failure_and_success(self):
        base = {
            "validate_profile": {"return_value": []},
            "build_plan": {"return_value": ({"apiVersion": "cds/v1alpha1"}, [])},
        }
        code, out, _ = self._profile_render(
            ["-o", str(self.tmp / "o.yml")],
            render_compose={"return_value": ("", [_error()])},
            **base,
        )
        self.assertEqual(code, 1)
        self.assertIn("Render failed.", out)
        code, out, _ = self._profile_render(
            ["-o", str(self.tmp / "o.yml")],
            render_compose={"return_value": ("", [])},
            **base,
        )
        self.assertEqual(code, 0)
        self.assertIn("Rendered compose file written", out)

    def test_profile_helm_paths(self):
        base = {
            "validate_profile": {"return_value": []},
            "build_plan": {"return_value": ({"apiVersion": "cds/v1alpha1"}, [])},
        }
        args = ["--target", "helm", "-o", str(self.tmp / "chart")]
        for helm_result, expected, text in (
            ((2, []), 2, ""),
            ((0, [_error()]), 1, "Render failed."),
            ((0, []), 0, "Rendered Helm chart written"),
        ):
            with self.subTest(expected=expected):
                code, out, _ = self._profile_render(
                    args, _render_helm_chart={"return_value": helm_result}, **base
                )
                self.assertEqual(code, expected)
                self.assertIn(text, out)


class SecurityCommandErrorPathsTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        patcher = patch.dict(
            os.environ, {"CDS_CONFIG_PATH": str(self.tmp / "config.json")}
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.profile = str(self.tmp / "profile.yaml")

    def _run(self, extra, **patches):
        stack = contextlib.ExitStack()
        with stack:
            stack.enter_context(
                patch.object(cli_main, "resolve_profile_path", return_value=self.profile)
            )
            for name, value in patches.items():
                stack.enter_context(patch.object(cli_main, name, **value))
            return _run(["security", "prof", *extra])

    def test_report_and_group_by_category_conflict(self):
        code, out, err = _run(["security", "prof", "--report", "nis2", "--group-by-category"])
        self.assertEqual(code, 2)
        self.assertIn("mutually exclusive", out)

    def test_profile_resolution_error(self):
        with patch.object(cli_main, "resolve_profile_path", side_effect=ValueError("bad")):
            code, out, _ = _run(["security", "prof"])
        self.assertEqual(code, 1)
        self.assertIn("ERROR bad", out)

    def test_validation_failure(self):
        code, out, _ = self._run([], validate_profile={"return_value": [_error()]})
        self.assertEqual(code, 1)
        self.assertIn("profile validation failed", out)

    def test_helm_plan_failure(self):
        code, out, _ = self._run(
            ["--target", "helm"],
            validate_profile={"return_value": []},
            build_plan={"return_value": (None, [_error()])},
        )
        self.assertEqual(code, 1)
        self.assertIn("planning failed", out)

    def test_helm_render_failure(self):
        code, out, _ = self._run(
            ["--target", "helm"],
            validate_profile={"return_value": []},
            build_plan={"return_value": ({"x": 1}, [])},
            render_helm={"return_value": ({}, [_error()])},
        )
        self.assertEqual(code, 1)
        self.assertIn("rendering failed", out)

    def test_verify_images_resolve_profile_exception(self):
        code, _, err = self._run(
            ["--verify-images"],
            validate_profile={"return_value": []},
            resolve_profile={"side_effect": RuntimeError("kaboom")},
            run_security_validation={"return_value": ([], [])},
            _run_image_verification={"return_value": []},
        )
        self.assertIn("Image verification failed unexpectedly: kaboom", err)
        self.assertIn(code, (0, 1, 2))

    def test_verify_images_plan_failure(self):
        code, out, _ = self._run(
            ["--verify-images"],
            validate_profile={"return_value": []},
            resolve_profile={"return_value": ({}, None, [])},
            build_plan={"return_value": (None, [_error()])},
            run_security_validation={"return_value": ([], [])},
            _run_image_verification={"return_value": []},
        )
        self.assertIn("Cannot verify images because plan generation failed.", out)

    def test_verify_images_render_failure(self):
        code, out, _ = self._run(
            ["--verify-images"],
            validate_profile={"return_value": []},
            resolve_profile={"return_value": ({}, None, [])},
            build_plan={"return_value": ({"x": 1}, [])},
            render_compose={"return_value": ("", [_error()])},
            run_security_validation={"return_value": ([], [])},
            _run_image_verification={"return_value": []},
        )
        self.assertIn("Cannot verify images because render failed.", out)

    def test_security_validation_unexpected_exception(self):
        code, _, err = self._run(
            [],
            validate_profile={"return_value": []},
            run_security_validation={"side_effect": RuntimeError("explode")},
        )
        self.assertEqual(code, 2)
        self.assertIn("Security validation failed unexpectedly: explode", err)


class ComposeProfileErrorPathsTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        patcher = patch.dict(
            os.environ, {"CDS_CONFIG_PATH": str(self.tmp / "config.json")}
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.profile = self.tmp / "profile.yaml"
        self.profile.write_text("spec: {}\n", encoding="utf-8")

    def _run(self, extra, resolved=({"spec": {}}, {}, []), composed=({"spec": {}}, [])):
        with patch.object(cli_main, "resolve_profile_path", return_value=str(self.profile)), \
                patch.object(cli_main, "resolve_extends", return_value=resolved), \
                patch.object(cli_main, "compose_profile", return_value=composed):
            return _run(["compose-profile", "prof", "--add-module", "m", *extra])

    def test_resolve_profile_error(self):
        with patch.object(cli_main, "resolve_profile_path", side_effect=ValueError("x")):
            code, out, _ = _run(["compose-profile", "prof", "--add-module", "m"])
        self.assertEqual(code, 1)
        self.assertIn("ERROR x", out)

    def test_extends_failure(self):
        code, out, _ = self._run([], resolved=(None, {}, [_error()]))
        self.assertEqual(code, 1)
        self.assertIn("failed to resolve", out)

    def test_malformed_kv_flags(self):
        for flag in ("--bind", "--secret", "--set"):
            with self.subTest(flag=flag):
                code, out, _ = self._run([flag, "novalue"])
                self.assertEqual(code, 1)
                self.assertIn(f'{flag} value "novalue" must be in NAME=VALUE form.', out)

    def test_compose_failure(self):
        code, out, _ = self._run([], composed=(None, [_error()]))
        self.assertEqual(code, 1)
        self.assertIn("Compose failed.", out)

    def test_write_refuses_extends(self):
        self.profile.write_text("extends: base\nspec: {}\n", encoding="utf-8")
        code, out, _ = self._run(["--write"])
        self.assertEqual(code, 1)
        self.assertIn('declares "extends"', out)
        self.assertEqual(self.profile.read_text(encoding="utf-8"), "extends: base\nspec: {}\n")

    def test_write_and_output_and_stdout(self):
        code, out, _ = self._run(["--write"])
        self.assertEqual(code, 0)
        self.assertIn("Composed profile written", out)
        target = self.tmp / "out.yaml"
        code, out, _ = self._run(["--output", str(target)])
        self.assertEqual(code, 0)
        self.assertTrue(target.is_file())
        code, out, _ = self._run([])
        self.assertEqual(code, 0)
        self.assertIn("spec", out)

    def test_yaml_set_value_falls_back_to_raw_string(self):
        with patch.object(cli_main, "resolve_profile_path", return_value=str(self.profile)), \
                patch.object(cli_main, "resolve_extends", return_value=({"spec": {}}, {}, [])), \
                patch.object(cli_main, "compose_profile", return_value=({"spec": {}}, [])) as composer:
            code, _, _ = _run(["compose-profile", "prof", "--add-module", "m", "--set", "a=[unclosed"])
        self.assertEqual(code, 0)
        self.assertEqual(composer.call_args.kwargs["config_settings"], [("a", "[unclosed")])


if __name__ == "__main__":
    unittest.main()
