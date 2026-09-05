import tempfile
import unittest
from pathlib import Path
from unittest import mock

from cli.secrets import load_profile_secrets, load_secrets_from_env, resolve_secret


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

    def test_blank_lines_and_comments_are_skipped(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            env_file = Path(tmpdir) / ".env"
            env_file.write_text(
                "\n# a comment\nCDS_OK=value\n",
                encoding="utf-8",
            )

            with mock.patch.dict("os.environ", {}, clear=True):
                secrets, diagnostics = load_secrets_from_env(env_file)

            self.assertEqual(secrets, {"CDS_OK": "value"})
            self.assertEqual(diagnostics, [])

    def test_line_without_equals_sign_reports_w090_warning_and_is_skipped(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            env_file = Path(tmpdir) / ".env"
            env_file.write_text("CDS_OK=value\nthis-line-has-no-equals\n", encoding="utf-8")

            with mock.patch.dict("os.environ", {}, clear=True):
                secrets, diagnostics = load_secrets_from_env(env_file)

            self.assertEqual(secrets, {"CDS_OK": "value"})
            warnings = [d for d in diagnostics if d.level == "warning"]
            self.assertEqual(len(warnings), 1)
            self.assertEqual(warnings[0].code, "W090")

    def test_quoted_values_have_surrounding_quotes_stripped(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            env_file = Path(tmpdir) / ".env"
            env_file.write_text(
                'CDS_DOUBLE="double-quoted"\nCDS_SINGLE=\'single-quoted\'\n',
                encoding="utf-8",
            )

            with mock.patch.dict("os.environ", {}, clear=True):
                secrets, _ = load_secrets_from_env(env_file)

            self.assertEqual(
                secrets,
                {"CDS_DOUBLE": "double-quoted", "CDS_SINGLE": "single-quoted"},
            )


class ResolveSecretTest(unittest.TestCase):
    def test_returns_value_when_key_present(self):
        value, diagnostic = resolve_secret("CDS_DB_PASSWORD", {"CDS_DB_PASSWORD": "secret"})
        self.assertEqual(value, "secret")
        self.assertIsNone(diagnostic)

    def test_missing_optional_secret_returns_none_without_diagnostic(self):
        value, diagnostic = resolve_secret("CDS_MISSING", {}, required=False)
        self.assertIsNone(value)
        self.assertIsNone(diagnostic)

    def test_missing_required_secret_returns_e081_diagnostic(self):
        value, diagnostic = resolve_secret("CDS_MISSING", {}, required=True)
        self.assertIsNone(value)
        self.assertIsNotNone(diagnostic)
        self.assertEqual(diagnostic.level, "error")
        self.assertEqual(diagnostic.code, "E081")


class LoadProfileSecretsTest(unittest.TestCase):
    def test_non_dict_values_block_returns_env_only_secrets(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            missing_env_file = Path(tmpdir) / ".env"
            with mock.patch.dict("os.environ", {"CDS_DB_PASSWORD": "x"}, clear=True):
                secrets, diagnostics = load_profile_secrets(
                    {"values": ["not-a-dict"]}, env_file=missing_env_file
                )

        self.assertEqual(secrets, {"CDS_DB_PASSWORD": "CDS_DB_PASSWORD"})
        self.assertEqual(diagnostics, [])

    def test_non_dict_secret_definition_reports_e082(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            missing_env_file = Path(tmpdir) / ".env"
            with mock.patch.dict("os.environ", {}, clear=True):
                secrets, diagnostics = load_profile_secrets(
                    {"values": {"db_password": "not-a-dict"}}, env_file=missing_env_file
                )

        self.assertNotIn("db_password", secrets)
        errors = [d for d in diagnostics if d.code == "E082"]
        self.assertEqual(len(errors), 1)

    def test_secret_definition_missing_env_name_reports_e082(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            missing_env_file = Path(tmpdir) / ".env"
            with mock.patch.dict("os.environ", {}, clear=True):
                secrets, diagnostics = load_profile_secrets(
                    {"values": {"db_password": {"required": True}}}, env_file=missing_env_file
                )

        self.assertNotIn("db_password", secrets)
        errors = [d for d in diagnostics if d.code == "E082"]
        self.assertEqual(len(errors), 1)
        self.assertIn("db_password.env", errors[0].path)

    def test_secret_definition_with_empty_env_name_reports_e082(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            missing_env_file = Path(tmpdir) / ".env"
            with mock.patch.dict("os.environ", {}, clear=True):
                secrets, diagnostics = load_profile_secrets(
                    {"values": {"db_password": {"env": ""}}}, env_file=missing_env_file
                )

        self.assertNotIn("db_password", secrets)
        errors = [d for d in diagnostics if d.code == "E082"]
        self.assertEqual(len(errors), 1)


if __name__ == "__main__":
    unittest.main()
