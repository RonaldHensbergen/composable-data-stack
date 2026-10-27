import importlib.util
import json
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

import yaml

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


def _report(*ids, fixed=None):
    return json.dumps(
        {
            "Results": [
                {
                    "Vulnerabilities": [
                        {
                            "VulnerabilityID": i,
                            "PkgName": "libuuid",
                            "InstalledVersion": "1.0",
                            **({"FixedVersion": fixed} if fixed else {}),
                        }
                        for i in ids
                    ]
                }
            ]
        }
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

    def test_fixable_lists_still_reported_entries_with_fixed_version(self):
        entries = audit_trivyignore.parse_entries(IGNORE)
        fixes = audit_trivyignore.reported_fixes(
            [_report("CVE-2026-0001", fixed="2.0"), _report("CVE-2026-0002")]
        )
        result = audit_trivyignore.fixable(entries, fixes)
        self.assertEqual([e.vuln_id for e, _ in result], ["CVE-2026-0001"])
        self.assertEqual(result[0][1][0].fixed, "2.0")
        report = audit_trivyignore.render_report([], [], 1, result)
        self.assertIn("`libuuid` 1.0 -> 2.0", report)

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

            report.write_text(
                _report("CVE-2099-0001", "CVE-2099-0002", fixed="2.0"), encoding="utf-8"
            )
            self.assertEqual(audit_trivyignore.main(args + ["--warn-days", "0"]), 0)
            self.assertIn("a fix is available", out.read_text(encoding="utf-8"))
            out.unlink()

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


class TrivyignoreAuditWorkflowTest(unittest.TestCase):
    """The audit scan must not drop unfixed findings (#963, #983).

    A finding Trivy currently lists as unfixed is still present in the
    image, so its .trivyignore exception is still needed: the DB's
    fixed-version data fluctuates, and an unfixed finding filtered out by
    --ignore-unfixed made the audit report every entry as resolved.
    """

    def setUp(self) -> None:
        workflow_path = REPO_ROOT / ".github" / "workflows" / "trivyignore-audit.yml"
        self.workflow = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
        steps = self.workflow["jobs"]["audit"]["steps"]
        self.scan_step = next(
            step for step in steps if step.get("name") == "Scan published images without the ignore file"
        )

    def test_scan_does_not_ignore_unfixed_findings(self) -> None:
        run = self.scan_step["run"]
        self.assertIn("trivy image", run)
        self.assertIn("--severity HIGH,CRITICAL", run)
        self.assertNotIn(
            "--ignore-unfixed",
            run,
            "the audit scan must keep unfixed findings visible, otherwise "
            "exceptions for them are falsely reported as resolved",
        )

    def test_scan_explicitly_disables_the_default_ignorefile(self) -> None:
        run = self.scan_step["run"]
        self.assertIn(
            "--ignorefile /dev/null",
            run,
            "Trivy loads .trivyignore by default; the audit must explicitly disable it",
        )


if __name__ == "__main__":
    unittest.main()
