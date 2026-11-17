"""Tests for the hermetic in-process CLI test harness itself (issue #1277)."""

import contextlib
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cli.audit_log import is_enabled as audit_log_enabled
from cli.main import get_config_path, load_saved_target, main
from tests.hermetic_project import HermeticMainTestCase, hermetic_env


class HermeticMainTestCaseTest(HermeticMainTestCase):
    def test_project_local_config_and_env_file_do_not_leak(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            (project / ".cds").mkdir()
            (project / ".cds" / "config.json").write_text('{"target": "helm"}')
            (project / ".env").write_text("CDS_HERMATIC_PROBE=from-dotenv\n")
            original_cwd = Path.cwd()
            os.chdir(project)
            try:
                # The saved target from the CWD's .cds/config.json is not visible.
                self.assertFalse(get_config_path().exists())
                self.assertIsNone(load_saved_target())
                captured = io.StringIO()
                with (
                    patch.object(sys, "argv", ["cds", "config", "get", "target"]),
                    contextlib.redirect_stdout(captured),
                ):
                    result = main()
                self.assertEqual(result, 1)
                self.assertIn("not set", captured.getvalue())
                self.assertNotIn("helm", captured.getvalue())
                # .env loading is disabled: the probe key never reaches os.environ.
                self.assertNotIn("CDS_HERMATIC_PROBE", os.environ)
            finally:
                os.chdir(original_cwd)

    def test_saved_config_set_by_the_test_itself_wins(self):
        # A test that points CDS_CONFIG_PATH at its own config file (a nested
        # patch.dict) still sees that saved config.
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.json"
            config_path.write_text('{"target": "helm"}')
            with patch.dict(os.environ, {"CDS_CONFIG_PATH": str(config_path)}):
                self.assertEqual(load_saved_target(), "helm")

    def test_audit_log_disabled_by_default(self):
        # In-process main() calls must not append audit entries to the
        # checkout's own .cds/audit-log.jsonl (#1312).
        self.assertEqual(os.environ.get("CDS_AUDIT_LOG_DISABLE"), "true")
        self.assertFalse(audit_log_enabled())


class AuditLogOptOutTestCase(HermeticMainTestCase):
    """Classes that test audit behavior opt out via audit_log_enabled."""

    audit_log_enabled = True

    def test_audit_log_enabled_when_opted_out(self):
        self.assertNotIn("CDS_AUDIT_LOG_DISABLE", os.environ)
        self.assertTrue(audit_log_enabled())


class InheritedAuditDisableOptOutTestCase(HermeticMainTestCase):
    audit_log_enabled = True

    def setUp(self):
        # Simulate an outer environment (e.g. a CI job) that disables the
        # audit log globally; the opt-out must not inherit that (#1312).
        os.environ["CDS_AUDIT_LOG_DISABLE"] = "true"
        self.addCleanup(os.environ.pop, "CDS_AUDIT_LOG_DISABLE", None)
        super().setUp()

    def test_inherited_disable_flag_is_not_seen_by_opt_out(self):
        self.assertNotIn("CDS_AUDIT_LOG_DISABLE", os.environ)
        self.assertTrue(audit_log_enabled())


class HermeticEnvTest(unittest.TestCase):
    def test_strips_cds_vars_and_disables_audit_log(self):
        # A host CDS_* variable (e.g. a locally exported disable flag) must
        # not leak into a test subprocess (#1312).
        with patch.dict(
            os.environ,
            {"CDS_HOST_LOCAL_KEY": "x", "CDS_AUDIT_LOG_DISABLE": "false"},
        ):
            env = hermetic_env()
        self.assertNotIn("CDS_HOST_LOCAL_KEY", env)
        # The host's own disable flag value is replaced, not inherited.
        self.assertEqual(env["CDS_AUDIT_LOG_DISABLE"], "true")

    def test_extra_overrides_the_audit_disable(self):
        env = hermetic_env({"CDS_AUDIT_LOG_DISABLE": "false"})
        self.assertEqual(env["CDS_AUDIT_LOG_DISABLE"], "false")


if __name__ == "__main__":
    unittest.main()
