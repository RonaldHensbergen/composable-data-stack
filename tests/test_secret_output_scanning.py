"""Regression tests for CDS-SEC-006/030/071 (#356): resolved-secret scanning of
rendered output, generated files and CLI output."""
import contextlib
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from cli import main as cli_main
from cli import secret_leak
from cli.k8s_renderer import render_helm
from cli.planner import build_plan
from cli.renderer import render_compose
from cli.security import run_security_validation

_REPO_ROOT = Path(__file__).resolve().parent.parent
_FIXTURE = _REPO_ROOT / "tests" / "fixtures" / "security" / "literal-secret-output"
_LEAKING = _FIXTURE / "profile" / "profile.yaml"
_SAFE = _FIXTURE / "profile-safe" / "profile.yaml"
_SECRET = "fixture-secret-value"  # noqa: S105


def _plan(profile_path: Path) -> dict:
    plan, diagnostics = build_plan(str(profile_path), env_file=str(profile_path.parent / ".env"))
    assert plan is not None and not [d for d in diagnostics if d.level == "error"]
    return plan


class SecretLeakHelpersTest(unittest.TestCase):
    def test_find_and_redact_literal_values(self):
        values = {"CDS_A": "alpha-secret-1", "CDS_B": "beta-secret-22"}
        text = "x: alpha-secret-1\ny: ok\nz: beta-secret-22 alpha-secret-1\n"
        self.assertEqual(
            secret_leak.find_secret_leaks(text, values),
            [("CDS_A", 1), ("CDS_A", 3), ("CDS_B", 3)],
        )
        redacted, hit = secret_leak.redact_text(text, values)
        self.assertEqual(hit, {"CDS_A", "CDS_B"})
        self.assertNotIn("secret", redacted.replace(secret_leak.REDACTED, ""))

    def test_overlapping_secret_values_are_fully_redacted(self):
        values = {"CDS_SHORT": "password-1", "CDS_LONG": "password-1-extended"}
        redacted, _ = secret_leak.redact_text("v=password-1-extended", values)
        self.assertEqual(redacted, f"v={secret_leak.REDACTED}")

    def test_short_and_non_secret_values_are_ignored(self):
        env = {
            "CDS_DB_PASSWORD": "short",
            "CDS_CHECK_UPDATES": "definitely-enabled",
            "CDS_REGION_NAME": "a-long-region-name",
            "CDS_API_TOKEN": "a-long-token-value",
        }
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, env, clear=True):
            values = secret_leak.resolved_secret_values(env_file=str(Path(tmp) / ".env"))
            self.assertEqual(values, {"CDS_API_TOKEN": "a-long-token-value"})
            declared = secret_leak.resolved_secret_values(
                {"CDS_REGION_NAME"}, str(Path(tmp) / ".env")
            )
            self.assertIn("CDS_REGION_NAME", declared)

    def test_environment_secrets_survive_unreadable_env_file(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {"CDS_API_TOKEN": "a-long-token-value"}, clear=True
        ):
            unreadable = Path(tmp) / "env-dir"
            unreadable.mkdir()
            values = secret_leak.resolved_secret_values(env_file=str(unreadable))
        self.assertEqual(values, {"CDS_API_TOKEN": "a-long-token-value"})

    def test_stream_redacts_value_split_across_writes(self):
        sink, hits = io.StringIO(), set()
        stream = secret_leak.RedactingStream(sink, {"CDS_A": "alpha-secret-1"}, hits)
        stream.write("token=alpha-")
        stream.write("secret-1")
        stream.write("\n")
        stream.flush()
        self.assertEqual(sink.getvalue(), f"token={secret_leak.REDACTED}\n")
        self.assertEqual(hits, {"CDS_A"})


class GeneratedFileScanTest(unittest.TestCase):
    """CDS-SEC-030."""

    def test_render_refuses_to_write_file_containing_secret(self):
        plan = _plan(_LEAKING)
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "docker-compose.yml"
            _, diagnostics = render_compose(
                plan, output_path=str(output), env_file=str(_LEAKING.parent / ".env")
            )
            errors = [d for d in diagnostics if d.code == "E096"]
            self.assertEqual(len(errors), 1)
            self.assertIn("CDS_FAKE_TOKEN", errors[0].message)
            self.assertNotIn(_SECRET, errors[0].message + errors[0].path)
            self.assertFalse(output.exists())

    def test_render_writes_file_without_secret(self):
        plan = _plan(_SAFE)
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "docker-compose.yml"
            _, diagnostics = render_compose(
                plan, output_path=str(output), env_file=str(_SAFE.parent / ".env")
            )
            self.assertEqual([d for d in diagnostics if d.level == "error"], [])
            self.assertTrue(output.exists())
            self.assertNotIn(_SECRET, output.read_text())

    def test_helm_chart_not_written_when_a_file_contains_secret(self):
        profile = _REPO_ROOT / "profiles" / "local-dagster-postgres-superset" / "profile.yaml"
        secret = "sentinel-postgres-password"  # noqa: S105
        env = {
            "CDS_ANALYTICS_DB_NAME": "analytics",
            "CDS_ANALYTICS_DB_PASSWORD": "sentinel-analytics-password",
            "CDS_ANALYTICS_DB_USER": "analytics",
            "CDS_DAGSTER_DB_NAME": "dagster",
            "CDS_DAGSTER_DB_PASSWORD": "sentinel-dagster-password",
            "CDS_DAGSTER_DB_USER": "dagster",
            "CDS_POSTGRES_SUPERUSER_PASSWORD": secret,
            "CDS_SUPERSET_ADMIN_PASSWORD": "sentinel-admin-password",
            "CDS_SUPERSET_DB_NAME": "superset",
            "CDS_SUPERSET_DB_PASSWORD": "sentinel-superset-password",
            "CDS_SUPERSET_DB_USER": "superset",
            "CDS_SUPERSET_SECRET_KEY": "sentinel-superset-secret-key",
        }
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, env):
            env_file = Path(tmp) / ".env"
            env_file.write_text("", encoding="utf-8")
            plan, _ = build_plan(str(profile), env_file=str(env_file))
            plan["metadata"]["description"] = f"deployed with {secret}"
            chart_dir = Path(tmp) / "chart"
            _, diagnostics = render_helm(plan, output_dir=str(chart_dir), env_file=str(env_file))
            self.assertEqual([d.code for d in diagnostics if d.code == "E096"], ["E096"])
            self.assertFalse(chart_dir.exists())


class RenderedOutputScanTest(unittest.TestCase):
    """CDS-SEC-006."""

    def _hits(self, profile: Path):
        findings, _ = run_security_validation(
            profile, env_file=str(profile.parent / ".env"), redact_values=True
        )
        return [f for f in findings if f["rule_id"] == "CDS-SEC-006"]

    def test_flags_literal_secret_in_rendered_compose(self):
        hits = self._hits(_LEAKING)
        self.assertEqual([h["path"] for h in hits], ["services.fake-literal.environment.SETTING"])
        self.assertEqual(hits[0]["module"], "fake-literal")
        self.assertNotIn(_SECRET, str(hits[0]))

    def test_does_not_flag_unrelated_value(self):
        self.assertEqual(self._hits(_SAFE), [])

    def test_can_be_disabled_through_code_enforced_toggle(self):
        import json

        from cli.security import _validate_rule_set

        rule_set = _validate_rule_set()
        for rule in rule_set["rules"]:
            if rule["id"] == "CDS-SEC-006":
                rule["codeEnforced"] = False
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "rules.json"
            path.write_text(json.dumps(rule_set), encoding="utf-8")
            findings, _ = run_security_validation(
                _LEAKING, rule_set_path=path, env_file=str(_LEAKING.parent / ".env")
            )
        self.assertEqual([f for f in findings if f["rule_id"] == "CDS-SEC-006"], [])


class CliOutputRedactionTest(unittest.TestCase):
    """CDS-SEC-071."""

    def _run(self, *argv: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with (
            mock.patch("sys.argv", ["cds", *argv]),
            mock.patch.dict(os.environ, {"CDS_FAKE_TOKEN": _SECRET}),
            contextlib.redirect_stdout(out),
            contextlib.redirect_stderr(err),
        ):
            code = cli_main.main()
        return code, out.getvalue(), err.getvalue()

    def test_plan_output_is_redacted_and_warns(self):
        code, out, err = self._run("plan", str(_LEAKING))
        self.assertEqual(code, 0)
        self.assertNotIn(_SECRET, out + err)
        self.assertIn(secret_leak.REDACTED, out)
        self.assertIn("W102", err)
        self.assertIn("CDS_FAKE_TOKEN", err)

    def test_plan_without_leak_is_untouched_and_silent(self):
        code, out, err = self._run("plan", str(_SAFE))
        self.assertEqual(code, 0)
        self.assertNotIn(secret_leak.REDACTED, out)
        self.assertNotIn("W102", err)

    def test_streams_are_restored_after_command(self):
        before = (cli_main.sys.stdout, cli_main.sys.stderr)
        self._run("plan", str(_LEAKING))
        self.assertEqual((cli_main.sys.stdout, cli_main.sys.stderr), before)

    def test_reveal_secrets_disables_redaction(self):
        code, out, _ = self._run("security", str(_LEAKING), "--reveal-secrets")
        self.assertEqual(code, 1)
        self.assertIn(_SECRET, out)


class RuleDeclarationTest(unittest.TestCase):
    def test_rules_are_code_enforced_and_documented(self):
        import json

        rule_set = json.loads(
            (_REPO_ROOT / "cli" / "resources" / "rule-set.json").read_text(encoding="utf-8")
        )
        rules = {r["id"]: r for r in rule_set["rules"]}
        for rule_id in ("CDS-SEC-006", "CDS-SEC-030", "CDS-SEC-071"):
            with self.subTest(rule=rule_id):
                self.assertTrue(rules[rule_id]["codeEnforced"])
                self.assertFalse(rules[rule_id]["enabled"])
                self.assertNotIn("Deferred", rules[rule_id]["$comment"])


if __name__ == "__main__":
    unittest.main()
