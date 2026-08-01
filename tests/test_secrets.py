import tempfile
import unittest
from pathlib import Path
from unittest import mock

from cli.secrets import load_secrets_from_env


class LoadSecretsFromEnvRegressionTest(unittest.TestCase):
    def test_non_utf8_env_file_reports_diagnostic_instead_of_crashing(self):
        """A .env file containing invalid UTF-8 bytes must be reported as a
        clean E080 error Diagnostic, not raise an uncaught UnicodeDecodeError
        -- this is the same failure mode load_yaml_file()/load_env_file()
        were hardened against elsewhere, so load_secrets_from_env() (used by
        every 'cds plan'/'cds render' invocation via load_profile_secrets())
        must handle it the same way."""
        with tempfile.TemporaryDirectory() as tmpdir:
            env_file = Path(tmpdir) / ".env"
            with open(env_file, "wb") as f:
                f.write(b"CDS_DB_PASSWORD=ok\n")
                f.write(b"\xff\xfe garbage-non-utf8-bytes\n")

            with mock.patch.dict("os.environ", {}, clear=True):
                secrets, diagnostics = load_secrets_from_env(env_file)

            self.assertEqual(secrets, {})
            errors = [d for d in diagnostics if d.level == "error"]
            self.assertEqual(len(errors), 1)
            self.assertEqual(errors[0].code, "E080")

    def test_bom_prefixed_env_file_is_read_correctly(self):
        """UTF-8 BOM at the start of a .env file must not corrupt the first
        key (regression test for the utf-8-sig encoding fix)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            env_file = Path(tmpdir) / ".env"
            with open(env_file, "wb") as f:
                f.write(b"\xef\xbb\xbf")  # UTF-8 BOM
                f.write(b"CDS_DB_PASSWORD=supersecret\n")

            with mock.patch.dict("os.environ", {}, clear=True):
                secrets, diagnostics = load_secrets_from_env(env_file)

            self.assertEqual(len([d for d in diagnostics if d.level == "error"]), 0)
            self.assertEqual(secrets, {"CDS_DB_PASSWORD": "supersecret"})


if __name__ == "__main__":
    unittest.main()
