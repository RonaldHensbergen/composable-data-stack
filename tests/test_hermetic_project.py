"""Tests for the hermetic in-process CLI test harness itself (issue #1277)."""

import contextlib
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cli.main import get_config_path, load_saved_target, main
from tests.hermetic_project import HermeticMainTestCase


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


if __name__ == "__main__":
    unittest.main()
