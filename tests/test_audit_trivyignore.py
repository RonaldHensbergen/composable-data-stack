import importlib.util
import json
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "audit_trivyignore", REPO_ROOT / "scripts" / "audit_trivyignore.py"
)
audit_trivyignore = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = audit_trivyignore
_spec.loader.exec_module(audit_trivyignore)

IGNORE = """# header comment
# owner:@alice
CVE-2026-0001 exp:2026-12-01

CVE-2026-0002 exp:2026-11-01
"""


def _report(*ids):
    return json.dumps(
        {"Results": [{"Vulnerabilities": [{"VulnerabilityID": i} for i in ids]}]}
    )


class AuditTrivyignoreTest(unittest.TestCase):
    def test_parse_entries_skips_comments_and_blank_lines(self):
        entries = audit_trivyignore.parse_entries(IGNORE)
        self.assertEqual([e.vuln_id for e in entries], ["CVE-2026-0001", "CVE-2026-0002"])
        self.assertEqual(entries[1].line, 5)

    def test_reported_ids_handles_results_without_vulnerabilities(self):
        text = json.dumps({"Results": [{"Target": "x"}, {"Vulnerabilities": None}]})
        self.assertEqual(audit_trivyignore.reported_ids([text, "{}"]), set())

    def test_entry_missing_from_reports_is_resolved(self):
        entries = audit_trivyignore.parse_entries(IGNORE)
        resolved, expiring = audit_trivyignore.audit(
            entries, {"CVE-2026-0002"}, date(2026, 10, 17), 14
        )
        self.assertEqual([e.vuln_id for e in resolved], ["CVE-2026-0001"])
        self.assertEqual(expiring, [])

    def test_still_reported_entry_near_expiry_is_flagged(self):
        entries = audit_trivyignore.parse_entries(IGNORE)
        resolved, expiring = audit_trivyignore.audit(
            entries, {"CVE-2026-0001", "CVE-2026-0002"}, date(2026, 10, 17), 14
        )
        self.assertEqual(resolved, [])
        self.assertEqual([e.vuln_id for e in expiring], [])
        _, expiring = audit_trivyignore.audit(
            entries, {"CVE-2026-0001", "CVE-2026-0002"}, date(2026, 10, 18), 14
        )
        self.assertEqual([e.vuln_id for e in expiring], ["CVE-2026-0002"])

    def test_main_writes_report_only_when_something_is_flagged(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            ignore = tmp / ".trivyignore"
            ignore.write_text(IGNORE.replace("2026", "2099"), encoding="utf-8")
            report = tmp / "r.json"
            out = tmp / "out.md"
            args = ["--ignore", str(ignore), "--output", str(out), str(report)]

            report.write_text(_report("CVE-2099-0001", "CVE-2099-0002"), encoding="utf-8")
            self.assertEqual(audit_trivyignore.main(args + ["--warn-days", "0"]), 0)
            self.assertFalse(out.exists())

            report.write_text(_report("CVE-2099-0002"), encoding="utf-8")
            self.assertEqual(audit_trivyignore.main(args), 0)
            self.assertIn("CVE-2099-0001", out.read_text(encoding="utf-8"))

    def test_main_fails_on_unreadable_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "bad.json"
            bad.write_text("not json", encoding="utf-8")
            self.assertEqual(
                audit_trivyignore.main(["--ignore", str(REPO_ROOT / ".trivyignore"), str(bad)]),
                1,
            )


if __name__ == "__main__":
    unittest.main()
