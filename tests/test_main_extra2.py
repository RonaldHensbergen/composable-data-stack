import argparse
import contextlib
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from cli import main as cli_main
from cli.diagnostics import Diagnostic


def _capture(fn, *args, **kwargs):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        result = fn(*args, **kwargs)
    return result, out.getvalue(), err.getvalue()


def _err(code="E001"):
    return Diagnostic(level="error", code=code, message="boom", path="spec")


class TmpDirCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()


class LoadEnvFileTest(TmpDirCase):
    def test_unreadable_env_file_warns(self):
        env = self.root / ".env"
        env.write_bytes(b"\xff\xfe\x00bad\xff")
        env.write_bytes(b"A=\x80\x81\n")
        _, _, err = _capture(cli_main.load_env_file, str(env))
        self.assertIn("WARNING Could not read", err)


class ComplianceReportTextTest(unittest.TestCase):
    def test_full_report_rendering(self):
        report = {
            "profile": {"sourceProfile": "p.yaml", "environment": "prod"},
            "generatedAt": "now",
            "modules": [
                {"id": "a", "source": "s", "version": "1", "dependsOn": ["b"]},
                {"id": "b", "source": "s", "version": "2"},
            ],
            "images": [
                {
                    "service": "svc",
                    "image": "img@sha256:1",
                    "digestPinned": True,
                    "evidence": {
                        "status": "available",
                        "signed": True,
                        "provenanceAttested": False,
                        "sbomAttested": True,
                    },
                },
                {"service": "s2", "image": "i2", "evidence": {"status": "x", "reason": "offline"}},
            ],
            "topology": [
                {
                    "id": "a",
                    "dependsOn": ["b"],
                    "provides": [{"name": "db", "kind": "sql"}],
                    "consumes": [{"name": "w", "kind": "k", "provider": "b.w"}],
                },
            ],
            "secretLeakCheck": {"status": "fail", "checkedSecretCount": 2, "leakedSecretNames": ["CDS_X"]},
            "disclaimer": "not legal advice",
        }
        text = cli_main._format_compliance_report_text(report)
        self.assertIn("Environment: prod", text)
        self.assertIn("a: source=s version=1 (dependsOn: b)", text)
        self.assertIn("[digest-pinned] evidence: signed=True provenanceAttested=False sbomAttested=True", text)
        self.assertIn("[NOT digest-pinned] evidence: NOT AVAILABLE (offline)", text)
        self.assertIn("provides: db (sql)", text)
        self.assertIn("consumes: w (k) from b.w", text)
        self.assertIn("leaked: CDS_X", text)
        self.assertIn("NOTE: not legal advice", text)


class ResolveDocumentBestEffortTest(TmpDirCase):
    def _profile(self):
        path = self.root / "profile.yaml"
        path.write_text("metadata: {name: x}\nspec: {}\n", encoding="utf-8")
        return path

    def test_errors_return_empty(self):
        with patch.object(cli_main, "resolve_extends", return_value=(None, {}, [_err()])):
            self.assertEqual(cli_main._resolve_profile_document_best_effort("p.yaml", "dev"), {})

    def test_no_environment_returns_document(self):
        doc = {"a": 1}
        with patch.object(cli_main, "resolve_extends", return_value=(doc, {}, [])):
            self.assertEqual(cli_main._resolve_profile_document_best_effort("p.yaml"), doc)

    def test_missing_overlay_returns_document(self):
        path = self._profile()
        doc = {"a": 1}
        with patch.object(cli_main, "resolve_extends", return_value=(doc, {}, [])):
            self.assertEqual(cli_main._resolve_profile_document_best_effort(str(path), "dev"), doc)

    def test_invalid_overlay_returns_document(self):
        path = self._profile()
        (self.root / "environments").mkdir()
        (self.root / "environments" / "dev.yaml").write_text("a: [unclosed", encoding="utf-8")
        doc = {"a": 1}
        with patch.object(cli_main, "resolve_extends", return_value=(doc, {}, [])):
            self.assertEqual(cli_main._resolve_profile_document_best_effort(str(path), "dev"), doc)

    def test_merge_diagnostics_return_document(self):
        path = self._profile()
        (self.root / "environments").mkdir()
        (self.root / "environments" / "dev.yaml").write_text("a: 2\n", encoding="utf-8")
        doc = {"a": 1}
        with patch.object(cli_main, "resolve_extends", return_value=(doc, {}, [])), \
                patch.object(cli_main, "_merge_profile_docs", return_value=({"a": 2}, [_err()])):
            self.assertEqual(cli_main._resolve_profile_document_best_effort(str(path), "dev"), doc)

    def test_merged_returned_on_success(self):
        path = self._profile()
        (self.root / "environments").mkdir()
        (self.root / "environments" / "dev.yaml").write_text("a: 2\n", encoding="utf-8")
        with patch.object(cli_main, "resolve_extends", return_value=({"a": 1}, {}, [])), \
                patch.object(cli_main, "_merge_profile_docs", return_value=({"a": 2}, [])):
            self.assertEqual(cli_main._resolve_profile_document_best_effort(str(path), "dev"), {"a": 2})

    def test_k8s_runtime_defaults_delegates(self):
        with patch.object(cli_main, "_resolve_profile_document_best_effort",
                          return_value={"metadata": {"name": "m"}, "spec": {"runtime": {}}}), \
                patch.object(cli_main, "_helm_identity", return_value=("r", "n")) as helm:
            self.assertEqual(cli_main._k8s_runtime_defaults("p.yaml", "dev"), ("r", "n"))
        helm.assert_called_once_with({"name": "m"}, {}, "p.yaml")

    def test_profile_completer_filters_by_prefix(self):
        with patch.object(cli_main, "list_profiles", return_value=["alpha", "beta", "alpine"]):
            self.assertEqual(cli_main.profile_completer("al", None), ["alpha", "alpine"])


class ConfigIOTest(TmpDirCase):
    def _with_config(self, text):
        path = self.root / "config.json"
        path.write_text(text, encoding="utf-8")
        return patch.dict(os.environ, {"CDS_CONFIG_PATH": str(path)}), path

    def test_invalid_json_warns_and_returns_empty(self):
        ctx, _ = self._with_config("{not json")
        with ctx:
            result, _, err = _capture(cli_main._read_config)
        self.assertEqual(result, {})
        self.assertIn("is not valid JSON", err)

    def test_non_mapping_json_warns(self):
        ctx, _ = self._with_config("[1, 2]")
        with ctx:
            result, _, err = _capture(cli_main._read_config)
        self.assertEqual(result, {})
        self.assertIn("not a mapping", err)

    def test_oserror_on_read_warns(self):
        ctx, _ = self._with_config("{}")
        with ctx, patch.object(Path, "read_text", side_effect=OSError("denied")):
            result, _, err = _capture(cli_main._read_config)
        self.assertEqual(result, {})
        self.assertIn("Could not read", err)

    def test_write_empty_config_removes_file_and_wraps_errors(self):
        ctx, path = self._with_config("{}")
        with ctx:
            self.assertEqual(cli_main._write_config({}), path)
            self.assertFalse(path.exists())
            with self.assertRaises(cli_main.ConfigIOError):
                cli_main._write_config({})  # unlink on a missing file -> OSError


class ValidateEnvironmentSettingTest(unittest.TestCase):
    def test_no_saved_profile(self):
        with patch.object(cli_main, "load_saved_profile", return_value=None):
            with self.assertRaisesRegex(ValueError, "without a saved default profile"):
                cli_main._validate_environment_setting("dev")

    def test_unresolvable_saved_profile(self):
        with patch.object(cli_main, "load_saved_profile", return_value="x"), \
                patch.object(cli_main, "resolve_profile_path", side_effect=ValueError("nope")):
            with self.assertRaisesRegex(ValueError, "could not be resolved"):
                cli_main._validate_environment_setting("dev")

    def test_environment_resolution_failure_details(self):
        with patch.object(cli_main, "load_saved_profile", return_value="x"), \
                patch.object(cli_main, "resolve_profile_path", return_value="p.yaml"), \
                patch.object(cli_main, "resolve_profile", return_value=(None, None, [_err()])):
            with self.assertRaisesRegex(ValueError, "boom"):
                cli_main._validate_environment_setting("dev")

    def test_environment_resolution_failure_generic(self):
        with patch.object(cli_main, "load_saved_profile", return_value="x"), \
                patch.object(cli_main, "resolve_profile_path", return_value="p.yaml"), \
                patch.object(cli_main, "resolve_profile", return_value=(None, None, [])):
            with self.assertRaisesRegex(ValueError, "could not be resolved for the saved profile"):
                cli_main._validate_environment_setting("dev")

    def test_success(self):
        with patch.object(cli_main, "load_saved_profile", return_value="x"), \
                patch.object(cli_main, "resolve_profile_path", return_value="p.yaml"), \
                patch.object(cli_main, "resolve_profile", return_value=({"a": 1}, None, [])):
            self.assertIsNone(cli_main._validate_environment_setting("dev"))


class ProfileResolutionTest(TmpDirCase):
    def _mk(self, rel):
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x: 1\n", encoding="utf-8")
        return path

    def test_resolve_root_single_subdir(self):
        profile = self._mk("only/profile.yaml")
        self.assertEqual(cli_main._resolve_profile_root(self.root), str(profile.resolve()))

    def test_resolve_root_ambiguous_subdirs_is_none(self):
        self._mk("a/profile.yaml")
        self._mk("b/profile.yaml")
        with patch.object(cli_main, "find_project_root", return_value=self.root / "nowhere"):
            self.assertIsNone(cli_main._resolve_profile_root(self.root))

    def test_explicit_directory_with_single_subdir(self):
        profile = self._mk("only/profile.yaml")
        with patch.dict(os.environ, {"CDS_PROFILE_PATH": str(self.root / "empty")}):
            self.assertEqual(cli_main.resolve_profile_path(str(self.root)), str(profile.resolve()))

    def test_name_falls_back_to_default_root(self):
        default_root = self.root / "profiles"
        by_dir = self._mk("profiles/byname/profile.yaml")
        by_file = self._mk("profiles/flat.yaml")
        other = self.root / "other"
        other.mkdir()
        with patch.dict(os.environ, {"CDS_PROFILE_PATH": str(other)}), \
                patch.object(cli_main, "find_project_root", return_value=self.root):
            self.assertEqual(cli_main.resolve_profile_path("byname"), str(by_dir.resolve()))
            self.assertEqual(cli_main.resolve_profile_path("flat"), str(by_file.resolve()))
        self.assertTrue(default_root.is_dir())

    def test_no_profile_uses_ambient_root(self):
        profile = self._mk("amb/profile.yaml")
        with patch.dict(os.environ, {"CDS_PROFILE_PATH": str(self.root)}), \
                patch.object(cli_main, "load_saved_profile", return_value=None):
            self.assertEqual(cli_main.resolve_profile_path(None), str(profile.resolve()))

    def test_no_profile_and_nothing_raises(self):
        empty = self.root / "empty"
        empty.mkdir()
        with patch.dict(os.environ, {"CDS_PROFILE_PATH": str(empty)}), \
                patch.object(cli_main, "load_saved_profile", return_value=None), \
                patch.object(cli_main, "find_project_root", return_value=self.root):
            with self.assertRaisesRegex(ValueError, "No profile specified"):
                cli_main.resolve_profile_path(None)

    def test_resolve_env_file_prefers_profile_local(self):
        profile = self._mk("p/profile.yaml")
        env = self.root / "p" / ".env"
        env.write_text("A=1\n", encoding="utf-8")
        self.assertEqual(cli_main.resolve_env_file_path(str(profile)), env.resolve())

    def test_list_profiles_variants(self):
        profile = self._mk("solo/profile.yaml")
        self.assertEqual(cli_main.list_profiles(profile), [str(profile)])
        self.assertEqual(cli_main.list_profiles(self.root / "missing"), [])
        self.assertEqual(cli_main.list_profiles(self.root), ["solo"])
        self.assertEqual(cli_main.list_profiles(self.root / "solo"), ["solo"])

    def test_list_modules_variants(self):
        module = self._mk("modules/warehouse/pg/module.yaml")
        self.assertEqual(cli_main.list_modules(module), [str(module)])
        self.assertEqual(cli_main.list_modules(self.root / "missing"), [])
        self.assertEqual(cli_main.list_modules(self.root / "modules"), ["warehouse/pg"])


class CollectEnvVarsTest(unittest.TestCase):
    def test_profile_load_failure(self):
        with patch.object(cli_main, "resolve_extends", return_value=(None, {}, [_err()])):
            with self.assertRaisesRegex(ValueError, r"(?s)Could not load profile: .*boom"):
                cli_main._collect_profile_env_vars("p.yaml")

    def test_profile_load_failure_unknown_with_environment(self):
        with patch.object(cli_main, "resolve_profile", return_value=(None, None, [])):
            with self.assertRaisesRegex(ValueError, "unknown error"):
                cli_main._collect_profile_env_vars("p.yaml", "dev")

    def test_secret_without_env_name(self):
        profile = {"spec": {"secrets": {"values": {"pw": {"env": ""}}}}}
        with patch.object(cli_main, "resolve_extends", return_value=(profile, {}, [])):
            with self.assertRaisesRegex(ValueError, 'Secret "pw" is missing'):
                cli_main._collect_profile_env_vars("p.yaml")

    def test_collects_secret_env_vars_and_skips_non_dicts(self):
        profile = {"spec": {"secrets": {"values": {"pw": {"env": "CDS_PW"}, "junk": "str"}}}}
        with patch.object(cli_main, "resolve_extends", return_value=(profile, {}, [])):
            names, secrets = cli_main._collect_profile_env_vars("p.yaml")
        self.assertIn("CDS_PW", names)
        self.assertEqual(secrets, {"CDS_PW"})


class WriteEnvFileTest(TmpDirCase):
    def test_refuses_overwrite_without_force(self):
        out = self.root / ".env"
        out.write_text("x", encoding="utf-8")
        with self.assertRaises(FileExistsError):
            cli_main._write_env_file(out, ["CDS_A"], set(), "p.yaml", False)

    def test_writes_owner_only_file_with_placeholder_for_secret(self):
        out = self.root / ".env"
        cli_main._write_env_file(out, ["CDS_PW"], {"CDS_PW"}, "p.yaml", False)
        text = out.read_text(encoding="utf-8")
        self.assertIn("# Source profile: p.yaml", text)
        self.assertIn("CDS_PW=change-me", text)
        if os.name != "nt":
            self.assertEqual(out.stat().st_mode & 0o777, 0o600)
        cli_main._write_env_file(out, ["CDS_PW"], {"CDS_PW"}, "p.yaml", True)

    def test_cds_version_unknown_when_not_installed(self):
        with patch.object(cli_main, "_package_version", side_effect=cli_main.PackageNotFoundError):
            self.assertEqual(cli_main._cds_version(), "unknown")


class DiffValuesTest(unittest.TestCase):
    def _diff(self, a, b):
        changes = []
        cli_main._diff_values("", a, b, changes)
        return changes

    def test_dict_added_removed_changed(self):
        changes = self._diff({"a": 1, "b": 2, "c": 3}, {"a": 1, "c": 4, "d": 5})
        self.assertEqual(
            changes,
            [("b", "removed", 2, None), ("c", "changed", 3, 4), ("d", "added", None, 5)],
        )

    def test_id_keyed_lists_ignore_order_and_report_changes(self):
        a = [{"id": "x", "v": 1}, {"id": "y"}, {"id": "gone"}]
        b = [{"id": "y"}, {"id": "x", "v": 2}, {"id": "new"}]
        changes = self._diff({"m": a}, {"m": b})
        kinds = {(p, k) for p, k, _, _ in changes}
        self.assertEqual(
            kinds,
            {("m[gone]", "removed"), ("m[new]", "added"), ("m[x].v", "changed")},
        )

    def test_heterogeneous_list_compared_whole(self):
        changes = self._diff({"m": [{"id": "x"}, {"n": 1}]}, {"m": [{"id": "x"}]})
        self.assertEqual([(p, k) for p, k, _, _ in changes], [("m", "changed")])

    def test_category_heading_fallback(self):
        self.assertEqual(cli_main._category_heading("zzz-unknown"), "== zzz-unknown ==")


class ImageDigestAndVerificationTest(unittest.TestCase):
    def test_digest_check_skipped_when_disabled_or_errors(self):
        diags = [_err()]
        with patch.object(cli_main, "image_digest_check_enabled", return_value=False):
            self.assertIs(cli_main._maybe_check_pinned_image_digests(diags, "p", None, False), diags)
        with patch.object(cli_main, "image_digest_check_enabled", return_value=True):
            self.assertIs(cli_main._maybe_check_pinned_image_digests(diags, "p", None, True), diags)

    def test_digest_check_resolution_and_load_failures(self):
        with patch.object(cli_main, "image_digest_check_enabled", return_value=True):
            with patch.object(cli_main, "resolve_profile", return_value=(None, None, [])):
                self.assertEqual(cli_main._maybe_check_pinned_image_digests([], "p", None, True), [])
            with patch.object(cli_main, "resolve_profile", return_value=({}, None, [])), \
                    patch.object(cli_main, "load_module_instances", return_value=([], [_err()])):
                self.assertEqual(cli_main._maybe_check_pinned_image_digests([], "p", None, True), [])

    def test_digest_check_appends_warnings(self):
        warn = Diagnostic(level="warning", code="W100", message="stale", path="x")
        with patch.object(cli_main, "image_digest_check_enabled", return_value=True), \
                patch.object(cli_main, "resolve_profile", return_value=({}, None, [])), \
                patch.object(cli_main, "load_module_instances", return_value=(["m"], [])), \
                patch.object(cli_main, "validate_pinned_image_digests", return_value=[warn]):
            result = cli_main._maybe_check_pinned_image_digests([], "p", None, True)
        self.assertEqual(result, [warn])

    def test_verification_render_failed(self):
        findings = cli_main._run_image_verification("p", None, render_failed=True)
        self.assertEqual(findings[0]["rule_id"], "CDS-VER-004")
        self.assertIn("rendering failed", findings[0]["message"] if "message" in findings[0] else str(findings[0]))

    def test_verification_with_compose_uses_policy(self):
        policy = MagicMock()
        with patch.object(cli_main, "verify_images", return_value=["ok"]) as verify:
            self.assertEqual(
                cli_main._run_image_verification("p", None, compose_yaml="y", policy=policy), ["ok"]
            )
        self.assertIs(verify.call_args.args[1], policy)

    def test_verification_with_compose_default_policy_and_failure(self):
        with patch.object(cli_main, "load_policy_from_env", return_value=MagicMock()) as load, \
                patch.object(cli_main, "verify_images", side_effect=RuntimeError("kaboom")):
            result, _, err = _capture(
                cli_main._run_image_verification, "p", None, compose_yaml="y", profile_class="prod"
            )
        load.assert_called_once_with("prod", mode_override="full")
        self.assertIn("E095", err)
        self.assertIn("kaboom", str(result))

    def _patch_pipeline(self, **overrides):
        patches = {
            "resolve_profile": ({"spec": {}}, None, []),
            "resolve_env_file_path": Path("/x/.env"),
            "build_plan": ({"plan": 1}, []),
            "render_compose": ("yaml", []),
            "infer_profile_class": "local",
            "load_policy_from_env": MagicMock(),
            "verify_images": ["verified"],
        }
        patches.update(overrides)
        stack = contextlib.ExitStack()
        for name, value in patches.items():
            stack.enter_context(patch.object(cli_main, name, return_value=value))
        return stack

    def test_verification_full_pipeline_success(self):
        with self._patch_pipeline():
            result, _, _ = _capture(cli_main._run_image_verification, "p", None)
        self.assertEqual(result, ["verified"])

    def test_verification_plan_failure(self):
        with self._patch_pipeline(build_plan=(None, [_err()])):
            result, out, _ = _capture(cli_main._run_image_verification, "p", None)
        self.assertIn("plan generation failed", out)
        self.assertEqual(result[0]["rule_id"], "CDS-VER-004")

    def test_verification_render_failure(self):
        with self._patch_pipeline(render_compose=(None, [_err()])):
            result, out, _ = _capture(cli_main._run_image_verification, "p", None)
        self.assertIn("render failed", out)
        self.assertEqual(result[0]["rule_id"], "CDS-VER-004")

    def test_verification_unexpected_exception(self):
        with self._patch_pipeline(verify_images=None), \
                patch.object(cli_main, "verify_images", side_effect=RuntimeError("bang")):
            result, _, err = _capture(cli_main._run_image_verification, "p", None)
        self.assertIn("E095", err)
        self.assertIn("bang", str(result))


class RenderHelmChartTest(TmpDirCase):
    def test_output_path_is_file_without_force(self):
        target = self.root / "chart"
        target.write_text("x", encoding="utf-8")
        (code, diags), out, _ = _capture(cli_main._render_helm_chart, {}, str(target), False)
        self.assertEqual((code, diags), (1, []))
        self.assertIn("is not a directory", out)

    def test_non_empty_foreign_directory_without_force(self):
        target = self.root / "chart"
        target.mkdir()
        (target / "other.txt").write_text("x", encoding="utf-8")
        (code, diags), out, _ = _capture(cli_main._render_helm_chart, {}, str(target), False)
        self.assertEqual(code, 1)
        self.assertIn("does not look like a CDS-generated chart", out)

    def test_existing_chart_is_refreshed(self):
        target = self.root / "chart"
        target.mkdir()
        (target / "Chart.yaml").write_text("x", encoding="utf-8")
        with patch.object(cli_main, "render_helm", return_value=([], ["d"])) as render:
            code, diags = cli_main._render_helm_chart({"p": 1}, str(target), False, env_file="e")
        self.assertEqual((code, diags), (0, ["d"]))
        render.assert_called_once_with({"p": 1}, output_dir=str(target), env_file="e")


class UpdateNoticeAndRedactionTest(unittest.TestCase):
    def tearDown(self):
        cli_main._output_redaction = None

    def test_notice_suppressed_for_help_and_check_update(self):
        for argv in (["cds", "check-update"], ["cds", "validate", "--help"]):
            with patch.object(sys, "argv", argv), \
                    patch.object(cli_main.update_check, "maybe_notify") as notify:
                cli_main._maybe_notify_update()
            notify.assert_not_called()

    def test_notice_invoked_and_errors_swallowed(self):
        with patch.object(sys, "argv", ["cds", "validate"]), \
                patch.object(cli_main, "_cds_version", return_value="1.0"), \
                patch.object(cli_main.update_check, "maybe_notify", side_effect=RuntimeError("x")) as notify:
            cli_main._maybe_notify_update()
        notify.assert_called_once_with("1.0")

    def test_install_skipped_for_other_commands_and_reveal(self):
        for ns in (
            argparse.Namespace(command="up"),
            argparse.Namespace(command="validate", reveal_secrets=True),
        ):
            cli_main._install_output_redaction(ns)
            self.assertIsNone(cli_main._output_redaction)

    def test_install_without_secret_values_is_noop(self):
        args = argparse.Namespace(profile=None, command="validate", environment=None)
        with patch.object(cli_main, "resolve_profile_path", side_effect=ValueError("none")), \
                patch.object(cli_main, "resolved_secret_values", return_value=[]):
            cli_main._install_output_redaction(args)
        self.assertIsNone(cli_main._output_redaction)

    def test_install_and_finish_redacts_and_warns(self):
        args = argparse.Namespace(profile=None, command="validate", environment=None)
        real_out, real_err = io.StringIO(), io.StringIO()
        with patch.object(sys, "stdout", real_out), patch.object(sys, "stderr", real_err):
            with patch.object(cli_main, "resolve_profile_path", side_effect=ValueError("none")), \
                    patch.object(cli_main, "resolved_secret_values",
                                 return_value={"CDS_PW": "hunter2hunter2"}):
                cli_main._install_output_redaction(args)
                self.assertIsNotNone(cli_main._output_redaction)
                print("value hunter2hunter2 here")
                cli_main._finish_output_redaction()
            self.assertIs(sys.stdout, real_out)
        self.assertNotIn("hunter2hunter2", real_out.getvalue())
        self.assertIn("CDS_PW", real_err.getvalue())

    def test_finish_without_install_is_noop(self):
        cli_main._output_redaction = None
        cli_main._finish_output_redaction()
        self.assertIsNone(cli_main._output_redaction)

    def test_finish_flushes_plain_streams(self):
        out, err = MagicMock(), MagicMock()
        cli_main._output_redaction = (out, err, set())
        with patch.object(sys, "stdout", out), patch.object(sys, "stderr", err):
            cli_main._finish_output_redaction()
        out.flush.assert_called_once()
        err.flush.assert_called_once()
        self.assertIsNone(cli_main._output_redaction)


if __name__ == "__main__":
    unittest.main()
