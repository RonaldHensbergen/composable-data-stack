"""Regression tests for CDS-SEC-032 (#357): secret file permission checks."""
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from cli.main import _write_env_file
from cli.security import _validate_rule_set, run_security_validation

_FIXTURE = (
    Path(__file__).resolve().parent / "fixtures" / "security" / "profile-scoped-env" / "profile.yaml"
)


@unittest.skipIf(os.name == "nt", "POSIX permission bits are not meaningful on Windows")
class SecretFilePermissionsTest(unittest.TestCase):
    def _scan(self, mode: int):
        with tempfile.TemporaryDirectory() as tmp:
            env = Path(tmp) / ".env"
            env.write_text("CDS_X=value\n", encoding="utf-8")
            env.chmod(mode)
            findings, _ = run_security_validation(_FIXTURE, env_file=str(env))
        return [f for f in findings if f["rule_id"] == "CDS-SEC-032"]

    def test_world_readable_env_file_is_flagged(self):
        hits = self._scan(0o644)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["value"], "0644")
        self.assertTrue(hits[0]["path"].endswith(".env.permissions"))
        self.assertEqual(hits[0]["category"], "secrets-management")

    def test_world_writable_env_file_is_flagged_even_when_redacting(self):
        findings = None
        with tempfile.TemporaryDirectory() as tmp:
            env = Path(tmp) / ".env"
            env.write_text("CDS_X=value\n", encoding="utf-8")
            env.chmod(0o666)
            findings, _ = run_security_validation(_FIXTURE, env_file=str(env), redact_values=True)
        hits = [f for f in findings if f["rule_id"] == "CDS-SEC-032"]
        self.assertEqual([h["value"] for h in hits], ["0666"])

    def test_group_readable_env_file_is_flagged(self):
        self.assertEqual(len(self._scan(0o640)), 1)

    def test_owner_only_env_file_is_not_flagged(self):
        self.assertEqual(self._scan(0o600), [])
        self.assertEqual(self._scan(0o400), [])

    def test_missing_env_file_is_not_flagged(self):
        with tempfile.TemporaryDirectory() as tmp:
            findings, _ = run_security_validation(_FIXTURE, env_file=str(Path(tmp) / ".env"))
        self.assertEqual([f for f in findings if f["rule_id"] == "CDS-SEC-032"], [])

    def test_check_is_skipped_on_windows(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = Path(tmp) / ".env"
            env.write_text("CDS_X=value\n", encoding="utf-8")
            env.chmod(0o644)
            with mock.patch("cli.security._is_windows", return_value=True):
                findings, _ = run_security_validation(_FIXTURE, env_file=str(env))
        self.assertEqual([f for f in findings if f["rule_id"] == "CDS-SEC-032"], [])

    def test_cds_init_writes_owner_only_env_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / ".env"
            old_umask = os.umask(0o000)
            try:
                _write_env_file(out, ["CDS_DB_PASSWORD"], {"CDS_DB_PASSWORD"}, "profile.yaml", force=False)
            finally:
                os.umask(old_umask)
            self.assertEqual(stat.S_IMODE(out.stat().st_mode), 0o600)


class RuleDeclarationTest(unittest.TestCase):
    def test_rule_has_real_scope_and_is_enabled(self):
        rule = next(r for r in _validate_rule_set()["rules"] if r["id"] == "CDS-SEC-032")
        self.assertEqual(rule["scope"], ["file-permissions"])
        self.assertTrue(rule["enabled"])
        self.assertNotIn("Deferred", rule["$comment"])


if __name__ == "__main__":
    unittest.main()
