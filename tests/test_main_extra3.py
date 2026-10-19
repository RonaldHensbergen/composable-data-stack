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

from cli import main as cli_main
from cli.diagnostics import Diagnostic
from cli.main import ConfigIOError, main


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


def _diag(level="error", code="E001"):
    return Diagnostic(level=level, code=code, message="boom", path="spec")


def _finding(severity="high", value="v", category=None):
    finding = {
        "severity": severity,
        "rule_id": "CDS-SEC-999",
        "message": "something bad",
        "path": "spec.modules[0]",
        "module": "mod",
        "value": value,
        "recommendation": ["do this"],
    }
    if category is not None:
        finding["category"] = category
    return finding


class _IsolatedConfigCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.config_path = self.tmp / "config.json"
        patcher = patch.dict(os.environ, {"CDS_CONFIG_PATH": str(self.config_path)})
        patcher.start()
        self.addCleanup(patcher.stop)
        audit = patch.object(cli_main, "_record_audit")
        self.audit = audit.start()
        self.addCleanup(audit.stop)


class ProfileResolutionErrorTest(_IsolatedConfigCase):
    def test_commands_report_unresolvable_profile(self):
        commands = ["plan", "up", "down", "test", "report", "preflight", "state", "init", "security"]
        for command in commands:
            with self.subTest(command=command), \
                    patch.object(cli_main, "resolve_profile_path", side_effect=ValueError("no such profile")):
                code, out, _ = _run([command, "missing"])
            self.assertEqual(code, 1)
            self.assertIn("ERROR no such profile", out)

    def test_diff_reports_unresolvable_profile(self):
        with patch.object(cli_main, "resolve_profile_path", side_effect=ValueError("no such profile")):
            code, out, _ = _run(["diff", "missing", "--from", "dev", "--to", "prod"])
        self.assertEqual(code, 1)
        self.assertIn("ERROR no such profile", out)


class PlanCommandTest(_IsolatedConfigCase):
    def _patches(self, diagnostics=None, plan=None, plan_diags=None):
        return (
            patch.object(cli_main, "resolve_profile_path", return_value=self.tmp / "profile.yaml"),
            patch.object(cli_main, "validate_profile", return_value=diagnostics or []),
            patch.object(cli_main, "resolve_env_file_path", return_value=self.tmp / ".env"),
            patch.object(cli_main, "build_plan", return_value=(plan, plan_diags or [])),
        )

    def _run_with(self, argv, **kwargs):
        with contextlib.ExitStack() as stack:
            for p in self._patches(**kwargs):
                stack.enter_context(p)
            return _run(argv)

    def test_plan_stops_when_validation_fails(self):
        code, out, _ = self._run_with(["plan", "p"], diagnostics=[_diag()])
        self.assertEqual(code, 1)
        self.assertIn("Cannot build plan because validation failed.", out)

    def test_plan_fails_when_planning_has_errors_and_prints_warnings(self):
        code, out, _ = self._run_with(
            ["plan", "p"], plan={}, plan_diags=[_diag(), _diag("warning", "W001")]
        )
        self.assertEqual(code, 1)
        self.assertIn("Plan generation failed.", out)
        self.assertIn("ERROR", out)
        self.assertIn("WARN", out)

    def test_plan_writes_output_file(self):
        target = self.tmp / "plan.json"
        code, out, _ = self._run_with(
            ["plan", "p", "--output", str(target)], plan={"apiVersion": "cds/v1alpha1"}
        )
        self.assertEqual(code, 0)
        self.assertIn(f"Plan saved to {target}", out)
        self.assertEqual(json.loads(target.read_text()), {"apiVersion": "cds/v1alpha1"})

    def test_plan_prints_json_to_stdout(self):
        code, out, _ = self._run_with(["plan", "p"], plan={"apiVersion": "cds/v1alpha1"})
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out), {"apiVersion": "cds/v1alpha1"})


class ConfigAndUseErrorTest(_IsolatedConfigCase):
    def test_use_clear_reports_io_error(self):
        with patch.object(cli_main, "clear_saved_profile", side_effect=ConfigIOError("disk full")):
            code, out, _ = _run(["use", "--clear"])
        self.assertEqual(code, 1)
        self.assertIn("ERROR disk full", out)

    def test_use_save_reports_io_error(self):
        with patch.object(cli_main, "_resolve_profile_for_config", return_value="/x/profile.yaml"), \
                patch.object(cli_main, "save_profile", side_effect=ConfigIOError("read only")):
            code, out, _ = _run(["use", "p"])
        self.assertEqual(code, 1)
        self.assertIn("ERROR read only", out)

    def test_config_get_unset_key(self):
        code, out, _ = _run(["config", "get", "profile"])
        self.assertEqual(code, 1)
        self.assertIn("Config key 'profile' is not set.", out)

    def test_config_set_profile_resolution_failure(self):
        with patch.object(cli_main, "_resolve_profile_for_config", side_effect=ValueError("bad profile")):
            code, out, _ = _run(["config", "set", "profile", "x"])
        self.assertEqual(code, 1)
        self.assertIn("ERROR bad profile", out)

    def test_config_set_io_error(self):
        with patch.object(cli_main, "set_config_value", side_effect=ConfigIOError("nope")):
            code, out, _ = _run(["config", "set", "target", "helm"])
        self.assertEqual(code, 1)
        self.assertIn("ERROR nope", out)

    def test_config_unset_io_error(self):
        with patch.object(cli_main, "unset_config_value", side_effect=ConfigIOError("locked")):
            code, out, _ = _run(["config", "unset", "profile"])
        self.assertEqual(code, 1)
        self.assertIn("ERROR locked", out)

    def test_config_unset_missing_key(self):
        code, out, _ = _run(["config", "unset", "profile"])
        self.assertEqual(code, 0)
        self.assertIn("Config key 'profile' is not set.", out)


class SecurityCommandTest(_IsolatedConfigCase):
    def _run_security(self, findings, sec_diags=None, extra=None, strict=False, profile_ok=True):
        argv = ["security", "p", *(extra or [])]
        with patch.object(cli_main, "resolve_profile_path", return_value=self.tmp / "profile.yaml"), \
                patch.object(cli_main, "validate_profile", return_value=[] if profile_ok else [_diag()]), \
                patch.object(cli_main, "resolve_env_file_path", return_value=self.tmp / ".env"), \
                patch.object(cli_main, "run_security_validation", return_value=(findings, sec_diags or [])), \
                patch.object(cli_main, "load_saved_security_strict", return_value=strict):
            return _run(argv)

    def test_value_is_printed_in_default_and_grouped_output(self):
        findings = [_finding(severity="medium", value="secret-ish", category="supply-chain")]
        for extra in ([], ["--group-by-category"]):
            with self.subTest(extra=extra):
                code, out, _ = self._run_security(findings, extra=extra)
                self.assertEqual(code, 0)
                self.assertIn("  value: secret-ish", out)
                self.assertIn("  fix: do this", out)

    def test_skipped_render_scan_without_findings_fails(self):
        code, out, _ = self._run_security([], sec_diags=[_diag("warning", "W096")])
        self.assertEqual(code, 1)
        self.assertIn("some checks were skipped", out)

    def test_no_findings_passes(self):
        code, out, _ = self._run_security([])
        self.assertEqual(code, 0)
        self.assertIn("No security findings.", out)

    def test_verify_images_strict_uses_prod_profile_class(self):
        self._assert_verify_images(strict=True, resolved_profile={"x": 1}, expected_class="prod")

    def test_verify_images_without_profile_uses_local_class(self):
        self._assert_verify_images(strict=False, resolved_profile=None, expected_class="local")

    def _assert_verify_images(self, strict, resolved_profile, expected_class):
        verify = MagicMock(return_value=[])
        with patch.object(cli_main, "resolve_profile", return_value=(resolved_profile, None, [])), \
                patch.object(cli_main, "build_plan", return_value=({"plan": 1}, [])), \
                patch.object(cli_main, "render_compose", return_value=("services: {}", [])), \
                patch.object(cli_main, "_run_image_verification", verify):
            code, _, _ = self._run_security([], extra=["--verify-images"], strict=strict)
        self.assertEqual(code, 0)
        self.assertEqual(verify.call_args.kwargs["profile_class"], expected_class)


class TestCommandUnexpectedErrorTest(_IsolatedConfigCase):
    def test_security_stage_exception_is_reported_as_e095(self):
        with patch.object(cli_main, "resolve_profile_path", return_value=self.tmp / "profile.yaml"), \
                patch.object(cli_main, "validate_profile", return_value=[]), \
                patch.object(cli_main, "resolve_env_file_path", return_value=self.tmp / ".env"), \
                patch.object(cli_main, "build_plan", return_value=({}, [])), \
                patch.object(cli_main, "render_compose", return_value=("services: {}", [])), \
                patch.object(cli_main, "run_security_validation", side_effect=RuntimeError("kaboom")):
            code, out, err = _run(["test", "p"])
        self.assertEqual(code, 1)
        self.assertIn("E095", err)
        self.assertIn("kaboom", err)
        self.assertIn("[FAIL] security", out)

    def test_group_by_category_prints_headings(self):
        findings = [_finding(severity="low", category="supply-chain")]
        with patch.object(cli_main, "resolve_profile_path", return_value=self.tmp / "profile.yaml"), \
                patch.object(cli_main, "validate_profile", return_value=[]), \
                patch.object(cli_main, "resolve_env_file_path", return_value=self.tmp / ".env"), \
                patch.object(cli_main, "build_plan", return_value=({}, [])), \
                patch.object(cli_main, "render_compose", return_value=("services: {}", [])), \
                patch.object(cli_main, "run_security_validation", return_value=(findings, [])):
            code, out, _ = _run(["test", "p", "--group-by-category"])
        self.assertEqual(code, 0)
        self.assertIn("[LOW] CDS-SEC-999 something bad", out)
        self.assertIn("[PASS] security", out)


class ReportCommandTest(_IsolatedConfigCase):
    def _run_report(self, report, diagnostics=None, extra=None):
        with patch.object(cli_main, "resolve_profile_path", return_value=self.tmp / "profile.yaml"), \
                patch.object(cli_main, "resolve_env_file_path", return_value=self.tmp / ".env"), \
                patch.object(cli_main, "build_compliance_report", return_value=(report, diagnostics or [])):
            return _run(["report", "p", *(extra or [])])

    def test_report_failure_goes_to_stderr(self):
        code, out, err = self._run_report(None, diagnostics=[_diag()])
        self.assertEqual(code, 1)
        self.assertIn("Cannot build compliance report for p", err)
        self.assertEqual(out, "")

    def test_report_json_to_stdout_with_diagnostics_on_stderr(self):
        report = {"secretLeakCheck": {"status": "pass"}}
        code, out, err = self._run_report(report, diagnostics=[_diag("warning", "W001")], extra=["--json"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out), report)
        self.assertIn("W001", err)

    def test_report_text_to_file_and_leak_exit_code(self):
        target = self.tmp / "report.txt"
        report = {"secretLeakCheck": {"status": "fail"}}
        with patch.object(cli_main, "_format_compliance_report_text", return_value="TEXT REPORT"):
            code, out, _ = self._run_report(report, extra=["--output", str(target)])
        self.assertEqual(code, 1)
        self.assertIn(f"Compliance report saved to {target}", out)
        self.assertEqual(target.read_text(), "TEXT REPORT")

    def test_report_text_to_stdout(self):
        report = {"secretLeakCheck": {"status": "pass"}}
        with patch.object(cli_main, "_format_compliance_report_text", return_value="TEXT REPORT"):
            code, out, _ = self._run_report(report)
        self.assertEqual(code, 0)
        self.assertIn("TEXT REPORT", out)


class PreflightCommandTest(_IsolatedConfigCase):
    def _run_preflight(self, validate=None, plan=({"p": 1}, []), render=("services: {}", []), checks=None):
        with patch.object(cli_main, "resolve_profile_path", return_value=self.tmp / "profile.yaml"), \
                patch.object(cli_main, "resolve_env_file_path", return_value=self.tmp / ".env"), \
                patch.object(cli_main, "validate_profile", return_value=validate or []), \
                patch.object(cli_main, "build_plan", return_value=plan), \
                patch.object(cli_main, "render_compose", return_value=render), \
                patch.object(cli_main, "load_saved_security_strict", return_value=False), \
                patch.object(cli_main, "run_preflight", return_value=checks or []):
            return _run(["preflight", "p"])

    def test_validation_failure(self):
        code, out, _ = self._run_preflight(validate=[_diag()])
        self.assertEqual(code, 1)
        self.assertIn("Cannot run preflight because validation failed.", out)

    def test_plan_failure(self):
        code, out, _ = self._run_preflight(plan=(None, [_diag()]))
        self.assertEqual(code, 1)
        self.assertIn("Cannot run preflight because plan generation failed.", out)

    def test_render_failure(self):
        code, out, _ = self._run_preflight(render=("", [_diag()]))
        self.assertEqual(code, 1)
        self.assertIn("Cannot run preflight because render failed.", out)

    def test_checks_pass_and_fail(self):
        ok = MagicMock(status="PASS", message="fine")
        ok.name = "docker"
        bad = MagicMock(status="FAIL", message="missing")
        bad.name = "env"
        with patch.object(cli_main, "preflight_passed", return_value=True):
            code, out, _ = self._run_preflight(checks=[ok])
        self.assertEqual(code, 0)
        self.assertIn("[PASS] docker: fine", out)
        self.assertIn("Preflight passed.", out)
        with patch.object(cli_main, "preflight_passed", return_value=False):
            code, out, _ = self._run_preflight(checks=[bad])
        self.assertEqual(code, 1)
        self.assertIn("Preflight failed.", out)


class StateCommandHelmTest(_IsolatedConfigCase):
    def _run_state(self, **kwargs):
        with patch.object(cli_main, "resolve_profile_path", return_value=self.tmp / "profile.yaml"), \
                patch.object(cli_main, "_k8s_runtime_defaults", return_value=("rel", "ns")), \
                patch.object(cli_main, "get_k8s_state", **kwargs) as getter:
            code, out, _ = _run(["state", "p", "--target", "helm"])
        return code, out, getter

    def test_helm_state_prints_grouped_services(self):
        code, out, getter = self._run_state(return_value=[{"Service": "svc-a", "Health": "healthy"}])
        self.assertEqual(code, 0)
        getter.assert_called_once_with("ns", "rel", None)
        self.assertIn("svc-a", out)

    def test_helm_state_error(self):
        code, out, _ = self._run_state(side_effect=RuntimeError("no cluster"))
        self.assertEqual(code, 1)
        self.assertIn("ERROR no cluster", out)

    def test_compose_state_timeout_is_not_swallowed_but_missing_file_reported(self):
        with patch.object(cli_main, "resolve_profile_path", return_value=self.tmp / "profile.yaml"), \
                patch.object(cli_main, "resolve_project_root", return_value=self.tmp):
            code, out, _ = _run(["state", "p"])
        self.assertEqual(code, 1)
        self.assertIn("Run 'cds up' first.", out)


class InitCommandTest(_IsolatedConfigCase):
    def _run_init(self, collect=None, write=None):
        with patch.object(cli_main, "resolve_profile_path", return_value=self.tmp / "profile.yaml"), \
                patch.object(cli_main, "resolve_project_root", return_value=self.tmp), \
                patch.object(cli_main, "_collect_profile_env_vars", **(collect or {"return_value": ({}, set())})), \
                patch.object(cli_main, "_write_env_file", **(write or {})) as writer:
            code, out, _ = _run(["init", "p"])
        return code, out, writer

    def test_collect_failure(self):
        code, out, writer = self._run_init(collect={"side_effect": ValueError("bad env")})
        self.assertEqual(code, 1)
        self.assertIn("ERROR bad env", out)
        writer.assert_not_called()

    def test_existing_env_file(self):
        code, out, _ = self._run_init(write={"side_effect": FileExistsError("exists")})
        self.assertEqual(code, 1)
        self.assertIn("ERROR exists", out)

    def test_success_message(self):
        code, out, writer = self._run_init()
        self.assertEqual(code, 0)
        self.assertIn("Initialized environment for p.", out)
        self.assertEqual(writer.call_args.args[0], self.tmp / ".env")


class ListImagesStatusTest(_IsolatedConfigCase):
    def test_every_status_is_rendered(self):
        statuses = {
            "update-available": {"status": "update-available", "image": "a:1", "latest": "a:2"},
            "up-to-date": {"status": "up-to-date", "image": "b:1"},
            "local": {"status": "local", "image": "c:1"},
            "unsupported-registry": {"status": "unsupported-registry", "image": "d:1"},
            "lookup-failed": {"status": "lookup-failed", "image": "e:1"},
            "other": {"status": "weird", "image": "f:1"},
        }
        entries = [
            {"module": "m", "service": name, "image": info["image"], "dockerfile": None}
            for name, info in statuses.items()
        ]
        by_image = {info["image"]: info for info in statuses.values()}
        roots = MagicMock()
        roots.__enter__.return_value = (self.tmp, self.tmp)
        roots.__exit__.return_value = False
        with patch.object(cli_main, "_resolve_list_source_roots", return_value=roots), \
                patch.object(cli_main, "collect_module_images", return_value=entries), \
                patch.object(cli_main, "check_image_update", side_effect=lambda image, dockerfile=None: by_image[image]):
            code, out, _ = _run(["list", "images"])
        self.assertEqual(code, 0)
        for expected in (
            "m::update-available: a:1 -> update available: a:2",
            "m::up-to-date: b:1 -> up to date",
            "m::local: c:1 -> local image, no remote check",
            "m::unsupported-registry: d:1 -> unsupported registry",
            "m::lookup-failed: e:1 -> registry lookup failed",
            "m::other: f:1 -> unknown status",
        ):
            self.assertIn(expected, out)


class UpCommandTest(_IsolatedConfigCase):
    def _base(self, stack, validate=None, plan=({"metadata": {}}, []), render=("services:\n  a:\n    image: x\n", [])):
        stack.enter_context(patch.object(cli_main, "resolve_profile_path", return_value=self.tmp / "p" / "profile.yaml"))
        stack.enter_context(patch.object(cli_main, "resolve_project_root", return_value=self.tmp))
        stack.enter_context(patch.object(cli_main, "resolve_env_file_path", return_value=self.tmp / ".env"))
        stack.enter_context(patch.object(cli_main, "validate_profile", return_value=validate or []))
        stack.enter_context(patch.object(cli_main, "build_plan", return_value=plan))
        stack.enter_context(patch.object(cli_main, "render_compose", return_value=render))

    def test_up_prints_digest_warnings_once(self):
        warn = _diag("warning", "W100")
        with contextlib.ExitStack() as stack:
            self._base(stack, plan=(None, [_diag()]))
            stack.enter_context(patch.object(
                cli_main, "_maybe_check_pinned_image_digests", return_value=[warn]
            ))
            code, out, _ = _run(["up", "p"])
        self.assertEqual(code, 1)
        self.assertEqual(out.count("W100"), 1)
        self.assertIn("Cannot start stack because plan generation failed.", out)

    def test_up_helm_render_failure(self):
        with contextlib.ExitStack() as stack:
            self._base(stack)
            stack.enter_context(patch.object(cli_main, "_render_helm_chart", return_value=(3, [])))
            code, out, _ = _run(["up", "p", "--target", "helm", "--chart-dir", str(self.tmp / "chart")])
        self.assertEqual(code, 3)
        self.assertIn("Cannot start stack because Helm rendering failed.", out)

    def _helm_up(self, helm_result=None, helm_error=None, extra=None):
        with contextlib.ExitStack() as stack:
            self._base(stack)
            stack.enter_context(patch.object(cli_main, "_render_helm_chart", return_value=(0, [])))
            stack.enter_context(patch.object(cli_main, "_helm_identity", return_value=("rel", "ns")))
            stack.enter_context(patch.object(
                cli_main, "helm_up", return_value=helm_result, side_effect=helm_error
            ))
            argv = ["up", "p", "--target", "helm", "--chart-dir", str(self.tmp / "chart"),
                    "--log-file", str(self.tmp / "logs" / "up.log"), *(extra or [])]
            return _run(argv)

    def test_helm_up_error(self):
        code, out, _ = self._helm_up(helm_error=RuntimeError("helm missing"))
        self.assertEqual(code, 1)
        self.assertIn("ERROR helm missing", out)

    def test_helm_up_nonzero_result(self):
        code, out, _ = self._helm_up(helm_result=5)
        self.assertEqual(code, 5)
        self.assertIn("Helm deployment failed (exit 5)", out)

    def test_helm_up_detach_and_no_build_note(self):
        code, out, _ = self._helm_up(helm_result=0, extra=["--detach", "--no-build"])
        self.assertEqual(code, 0)
        self.assertIn("Helm release rel submitted in ns.", out)
        self.assertIn("NOTE --no-build has no effect", out)

    def test_helm_up_ready(self):
        code, out, _ = self._helm_up(helm_result=0)
        self.assertEqual(code, 0)
        self.assertIn("Helm release rel is ready in ns.", out)

    def test_up_tolerates_unparseable_compose_and_reports_detached_failure(self):
        with contextlib.ExitStack() as stack:
            self._base(stack, render=("services: [unclosed", []))
            runner = stack.enter_context(patch.object(cli_main, "run_streamed", return_value=9))
            code, out, _ = _run([
                "up", "p", "--detach", "--no-build", "--log-file", str(self.tmp / "up.log"),
            ])
        self.assertEqual(code, 9)
        self.assertIn("'docker compose up' failed (exit 9)", out)
        runner.assert_called_once()


class GetAndDownCommandTest(_IsolatedConfigCase):
    def test_get_dry_run_with_conflicts_mentions_force(self):
        with patch.object(cli_main, "fetch_profile", return_value=(["a"], self.tmp / "m.json", ["c1"])), \
                patch.object(cli_main, "load_allowed_get_sources", return_value=None), \
                patch.object(cli_main, "format_get_plan", return_value="PLAN"):
            code, out, _ = _run(["get", "p", "--dry-run", "--into", str(self.tmp)])
        self.assertEqual(code, 0)
        self.assertIn("PLAN", out)
        self.assertIn("1 file(s) would need --force to overwrite.", out)

    def test_get_success_reports_counts(self):
        with patch.object(cli_main, "fetch_profile", return_value=(["a", "b"], self.tmp / "m.json", [])), \
                patch.object(cli_main, "load_allowed_get_sources", return_value=None):
            code, out, _ = _run(["get", "p", "--into", str(self.tmp)])
        self.assertEqual(code, 0)
        self.assertIn("Fetched 2 file(s)", out)
        self.assertIn("Tracking metadata written to", out)

    def _down(self, extra=None, **patches):
        with patch.object(cli_main, "resolve_profile_path", return_value=self.tmp / "p" / "profile.yaml"), \
                patch.object(cli_main, "resolve_project_root", return_value=self.tmp), \
                patch.object(cli_main, "_k8s_runtime_defaults", return_value=("rel", "ns")), \
                patch.object(cli_main, "run_streamed", **patches.get("run_streamed", {"return_value": 0})), \
                patch.object(cli_main, "helm_down", **patches.get("helm_down", {"return_value": 0})) as helm:
            code, out, _ = _run(["down", "p", "--log-file", str(self.tmp / "logs" / "down.log"), *(extra or [])])
        return code, out, helm

    def test_down_compose_success_failure_and_oserror(self):
        code, out, _ = self._down()
        self.assertEqual((code, "Stack removed." in out), (0, True))
        code, out, _ = self._down(run_streamed={"return_value": 4})
        self.assertEqual(code, 4)
        self.assertIn("Shutdown failed (exit 4)", out)
        code, out, _ = self._down(run_streamed={"side_effect": FileNotFoundError("docker")})
        self.assertEqual(code, 1)
        self.assertIn("ERROR docker", out)

    def test_down_helm_variants(self):
        code, out, helm = self._down(["--target", "helm"])
        self.assertEqual(code, 0)
        self.assertIn("PersistentVolumeClaims were retained", out)
        self.assertEqual(helm.call_args.kwargs["namespace"], "ns")
        self.assertEqual(helm.call_args.kwargs["release"], "rel")
        code, out, helm = self._down(["--target", "helm", "--delete-pvcs"])
        self.assertEqual(code, 0)
        self.assertIn("Stack removed.", out)
        self.assertTrue(helm.call_args.kwargs["delete_pvcs"])


class CheckUpdateCommandTest(_IsolatedConfigCase):
    def test_check_update_outcomes(self):
        with patch.object(cli_main.update_check, "latest_version", return_value=None):
            code, _, err = _run(["check-update"])
        self.assertEqual(code, 1)
        self.assertIn("Could not determine the latest CDS release", err)

        with patch.object(cli_main.update_check, "latest_version", return_value="999.0.0"), \
                patch.object(cli_main, "_cds_version", return_value="1.0.0"):
            code, out, _ = _run(["check-update"])
        self.assertEqual(code, 0)
        self.assertIn("999.0.0", out)

        with patch.object(cli_main.update_check, "latest_version", return_value="1.0.0"), \
                patch.object(cli_main, "_cds_version", return_value="1.0.0"):
            code, out, _ = _run(["check-update"])
        self.assertEqual(code, 0)
        self.assertIn("CDS 1.0.0 is up to date (latest release: 1.0.0).", out)


class StateComposeTest(_IsolatedConfigCase):
    def test_state_compose_ps_nonzero_without_stderr(self):
        (self.tmp / "docker-compose.yml").write_text("services: {}")
        proc = subprocess.CompletedProcess(args=[], returncode=2, stdout="", stderr="")
        with patch.object(cli_main, "resolve_profile_path", return_value=self.tmp / "profile.yaml"), \
                patch.object(cli_main, "resolve_project_root", return_value=self.tmp), \
                patch.object(cli_main.subprocess, "run", return_value=proc):
            code, out, _ = _run(["state", "p"])
        self.assertEqual(code, 2)
        self.assertIn("ERROR docker compose ps failed.", out)


class DiffCommandTest(_IsolatedConfigCase):
    def _run_diff(self, profiles):
        with patch.object(cli_main, "resolve_profile_path", return_value=self.tmp / "profile.yaml"), \
                patch.object(cli_main, "resolve_profile", side_effect=profiles):
            return _run(["diff", "p", "--from", "dev", "--to", "prod"])

    def test_unresolvable_environment(self):
        code, out, _ = self._run_diff([(None, None, [_diag()]), ({}, None, [])])
        self.assertEqual(code, 1)
        self.assertIn("Cannot diff because one or both environments failed to resolve.", out)

    def test_no_differences_prints_warnings(self):
        same = {"spec": {"a": 1}}
        code, out, _ = self._run_diff([(same, None, [_diag("warning", "W001")]), (same, None, [])])
        self.assertEqual(code, 0)
        self.assertIn("W001", out)
        self.assertIn("No differences between environment 'dev' and 'prod'.", out)

    def test_added_removed_and_changed(self):
        dev = {"gone": 1, "same": 1, "changed": "a"}
        prod = {"new": [1], "same": 1, "changed": "b"}
        code, out, _ = self._run_diff([(dev, None, []), (prod, None, [])])
        self.assertEqual(code, 0)
        self.assertIn("Differences from 'dev' to 'prod':", out)
        self.assertIn('  + new: [1]', out)
        self.assertIn('  - gone: 1', out)
        self.assertIn('  ~ changed: "a" -> "b"', out)
        self.assertNotIn("same", out)


class ValidateAndRenderEdgeTest(_IsolatedConfigCase):
    def test_validate_reports_unresolvable_profile(self):
        with patch.object(cli_main, "resolve_profile_path", side_effect=ValueError("no such profile")):
            code, out, _ = _run(["validate", "missing"])
        self.assertEqual(code, 1)
        self.assertIn("ERROR no such profile", out)

    def test_render_treats_non_json_file_as_profile(self):
        bogus = self.tmp / "not-a-plan.json"
        bogus.write_text("{not json", encoding="utf-8")
        with patch.object(cli_main, "resolve_profile_path", side_effect=ValueError("not a profile")) as resolver:
            code, out, _ = _run(["render", str(bogus), "--output", str(self.tmp / "out.yml")])
        self.assertEqual(code, 1)
        self.assertIn("ERROR not a profile", out)
        resolver.assert_called_once()

    def test_nis2_report_prints_finding_value(self):
        finding = _finding(severity="medium", value="the-value")
        with patch.object(cli_main, "resolve_profile_path", return_value=self.tmp / "profile.yaml"), \
                patch.object(cli_main, "validate_profile", return_value=[]), \
                patch.object(cli_main, "resolve_env_file_path", return_value=self.tmp / ".env"), \
                patch.object(cli_main, "run_security_validation", return_value=([finding], [])), \
                patch.object(cli_main, "_group_findings_by_nis2_measure",
                             return_value=[("a", "Title", "a note", [finding]), ("b", "Empty", None, [])]):
            code, out, _ = _run(["security", "p", "--report", "nis2"])
        self.assertEqual(code, 0)
        self.assertIn("  note: a note", out)
        self.assertIn("  value: the-value", out)
        self.assertIn("No CDS findings map to this measure.", out)


class UpAttachedWaitTest(_IsolatedConfigCase):
    def test_up_waits_through_timeouts_until_process_exits(self):
        process = MagicMock()
        process.wait.side_effect = [subprocess.TimeoutExpired("docker", 0.5), 7]
        with patch.object(cli_main, "resolve_profile_path", return_value=self.tmp / "p" / "profile.yaml"), \
                patch.object(cli_main, "resolve_project_root", return_value=self.tmp), \
                patch.object(cli_main, "resolve_env_file_path", return_value=self.tmp / ".env"), \
                patch.object(cli_main, "validate_profile", return_value=[]), \
                patch.object(cli_main, "build_plan", return_value=({"metadata": {}}, [])), \
                patch.object(cli_main, "render_compose", return_value=("services: {}\n", [])), \
                patch.object(cli_main, "start_up_in_background", return_value=process), \
                patch.object(cli_main, "poll_state_until_settled", return_value=(True, {})), \
                patch.object(cli_main, "start_log_tail", return_value=None):
            code, out, _ = _run([
                "up", "p", "--no-build", "--no-color", "--log-file", str(self.tmp / "up.log"),
            ])
        self.assertEqual(code, 7)
        self.assertEqual(process.wait.call_count, 2)
        self.assertIn("'docker compose up' failed (exit 7)", out)


if __name__ == "__main__":
    unittest.main()
