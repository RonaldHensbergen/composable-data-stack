import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr
from unittest import mock

from cli import update_check


class UpdateCheckTest(unittest.TestCase):
    def test_is_newer(self):
        self.assertTrue(update_check.is_newer("0.10.0", "0.9.2"))
        self.assertTrue(update_check.is_newer("1.0.1", "1.0"))
        self.assertFalse(update_check.is_newer("0.9.2", "0.9.2"))
        self.assertFalse(update_check.is_newer("0.9.1", "0.9.2"))
        self.assertFalse(update_check.is_newer("1.0rc1", "0.9"))

    def test_disabled_by_default_makes_no_network_call(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop(update_check.ENV_VAR, None)
            with mock.patch.object(update_check, "fetch_latest_version") as fetch:
                update_check.maybe_notify("0.1.0")
                fetch.assert_not_called()

    def test_enabled_prints_notice_and_uses_cache(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {update_check.ENV_VAR: "1", "XDG_CACHE_HOME": tmp}
        ):
            with mock.patch.object(update_check, "fetch_latest_version", return_value="9.9.9") as fetch:
                buf = io.StringIO()
                with redirect_stderr(buf):
                    update_check.maybe_notify("0.1.0")
                    update_check.maybe_notify("0.1.0")
                self.assertEqual(fetch.call_count, 1)
                self.assertIn("9.9.9", buf.getvalue())
                self.assertIn("CHANGELOG.md", buf.getvalue())

    def test_up_to_date_and_failure_are_silent(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {update_check.ENV_VAR: "1", "XDG_CACHE_HOME": tmp}
        ):
            for latest in ("0.1.0", None):
                buf = io.StringIO()
                with mock.patch.object(update_check, "fetch_latest_version", return_value=latest):
                    with redirect_stderr(buf):
                        update_check.maybe_notify("0.1.0")
                self.assertEqual(buf.getvalue(), "")

    def test_fetch_failure_returns_none(self):
        with mock.patch.object(update_check, "urlopen", side_effect=OSError("offline")):
            self.assertIsNone(update_check.fetch_latest_version())

    def test_cache_file_content(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"XDG_CACHE_HOME": tmp}):
            update_check._store_cache("1.2.3")
            data = json.loads(update_check._cache_path().read_text())
            self.assertEqual(data["latest"], "1.2.3")
            self.assertEqual(update_check._cached_latest(), "1.2.3")


if __name__ == "__main__":
    unittest.main()
