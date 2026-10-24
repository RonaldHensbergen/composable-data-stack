import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from cli import update_check


class CachePathTest(unittest.TestCase):
    def test_xdg_cache_home_is_used(self):
        with mock.patch.dict(os.environ, {"XDG_CACHE_HOME": "/xdg"}):
            self.assertEqual(
                update_check._cache_path(), Path("/xdg") / "cds" / "update-check.json"
            )

    def test_windows_localappdata_is_used(self):
        env = {"LOCALAPPDATA": "/local"}
        with mock.patch.dict(os.environ, env), mock.patch.object(
            update_check.sys, "platform", "win32"
        ):
            os.environ.pop("XDG_CACHE_HOME", None)
            self.assertEqual(
                update_check._cache_path(), Path("/local") / "cds" / "update-check.json"
            )

    def test_home_cache_fallback(self):
        with mock.patch.dict(os.environ, {}), mock.patch.object(
            update_check.sys, "platform", "linux"
        ), mock.patch.object(update_check.Path, "home", return_value=Path("/home/u")):
            os.environ.pop("XDG_CACHE_HOME", None)
            self.assertEqual(
                update_check._cache_path(),
                Path("/home/u") / ".cache" / "cds" / "update-check.json",
            )


class ParseVersionTest(unittest.TestCase):
    def test_unparseable_version_returns_none_and_is_not_newer(self):
        self.assertIsNone(update_check._parse_version("not-a-version"))
        self.assertFalse(update_check.is_newer("garbage", "1.0"))
        self.assertFalse(update_check.is_newer("1.0", "garbage"))


class FetchLatestVersionTest(unittest.TestCase):
    def _response(self, body: bytes):
        response = mock.MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = body
        return response

    def test_returns_version_from_pypi_payload(self):
        response = self._response(b'{"info": {"version": "1.2.3"}}')
        with mock.patch.object(update_check, "urlopen", return_value=response):
            self.assertEqual(update_check.fetch_latest_version(), "1.2.3")

    def test_non_string_version_returns_none(self):
        response = self._response(b'{"info": {"version": 5}}')
        with mock.patch.object(update_check, "urlopen", return_value=response):
            self.assertIsNone(update_check.fetch_latest_version())

    def test_network_and_payload_errors_return_none(self):
        with mock.patch.object(update_check, "urlopen", side_effect=OSError("down")):
            self.assertIsNone(update_check.fetch_latest_version())
        for body in (b"not json", b"{}", b"[]"):
            with self.subTest(body=body), mock.patch.object(
                update_check, "urlopen", return_value=self._response(body)
            ):
                self.assertIsNone(update_check.fetch_latest_version())


class CacheEdgeCasesTest(unittest.TestCase):
    def test_store_cache_swallows_oserror(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {"XDG_CACHE_HOME": tmp}
        ), mock.patch.object(Path, "write_text", side_effect=OSError("ro")):
            self.assertIsNone(update_check._store_cache("1.0.0"))
            self.assertFalse(update_check._cache_path().exists())
            self.assertIsNone(update_check._cached_latest())

    def test_latest_version_without_cache_fetches_and_stores(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {"XDG_CACHE_HOME": tmp}
        ), mock.patch.object(
            update_check, "fetch_latest_version", return_value="2.0.0"
        ) as fetch:
            self.assertEqual(update_check.latest_version(use_cache=False), "2.0.0")
            self.assertEqual(fetch.call_count, 1)
            self.assertEqual(update_check._cached_latest(), "2.0.0")

    def test_maybe_notify_skips_unknown_current_version(self):
        with mock.patch.dict(os.environ, {update_check.ENV_VAR: "1"}), mock.patch.object(
            update_check, "latest_version"
        ) as latest, mock.patch("sys.stderr", new_callable=io.StringIO) as err:
            update_check.maybe_notify("unknown")
            latest.assert_not_called()
            self.assertEqual(err.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
