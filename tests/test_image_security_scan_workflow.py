import re
import unittest
from pathlib import Path

import yaml

_ON_KEY = True

_SHA_PINNED_ACTION = re.compile(r"^aquasecurity/trivy-action@[0-9a-f]{40}$")


class ImageSecurityScanWorkflowTest(unittest.TestCase):
    def setUp(self) -> None:
        repo_root = Path(__file__).resolve().parent.parent
        workflow_path = repo_root / ".github" / "workflows" / "image-security-scan.yml"
        self.workflow = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
        self.jobs = self.workflow["jobs"]

    def test_triggers_on_prs_touching_images_and_not_unconditionally(self) -> None:
        triggers = self.workflow[_ON_KEY]
        pr_trigger = triggers["pull_request"]
        self.assertIn("images/**", pr_trigger["paths"])
        self.assertIn("paths", pr_trigger)

    def test_images_are_discovered_rather_than_hardcoded(self) -> None:
        discover_steps = self.jobs["discover-images"]["steps"]
        find_step = next(s for s in discover_steps if s.get("id") == "find")
        self.assertIn("images", str(find_step["run"]))
        self.assertEqual(
            self.jobs["discover-images"]["outputs"]["images"],
            "${{ steps.find.outputs.images }}",
        )

    def _scan_steps(self) -> list:
        return self.jobs["scan"]["steps"]

    def test_scan_matrix_consumes_discovered_images(self) -> None:
        matrix = self.jobs["scan"]["strategy"]["matrix"]
        self.assertEqual(
            matrix["image"],
            "${{ fromJson(needs.discover-images.outputs.images) }}",
        )

    def test_vulnerability_scan_gates_on_high_and_critical(self) -> None:
        scan_step = next(
            s
            for s in self._scan_steps()
            if "trivy-action" in str(s.get("uses", ""))
            and s.get("with", {}).get("format") == "table"
        )
        self.assertEqual(scan_step["with"]["severity"], "HIGH,CRITICAL")
        # exit-code must actually fail the job on a match, not just report.
        self.assertEqual(str(scan_step["with"]["exit-code"]), "1")
        self.assertEqual(scan_step["with"]["scanners"], "vuln")
        # continue-on-error keeps the job status "success" after a scan
        # failure, so later steps whose `if:` lacks a status function (like
        # the issue-filing step) aren't implicitly skipped by GitHub Actions.
        self.assertTrue(scan_step.get("continue-on-error"))

    def _find_step(self, predicate, description: str):
        # Prefer an explicit assertion failure over a StopIteration error so
        # a missing/renamed step surfaces as a clear test failure.
        step = next((s for s in self._scan_steps() if predicate(s)), None)
        self.assertIsNotNone(step, f"expected to find {description}")
        return step

    def test_job_fails_when_the_scan_found_vulnerabilities(self) -> None:
        # Use assertIn on the bare condition fragments rather than an exact
        # string match, so this survives the gate's `if:` growing a `${{ }}`
        # wrapper (as the issue-filing step's `if:` already has).
        gate_step = self._find_step(
            lambda s: "steps.scan.outcome" in str(s.get("if", ""))
            and "failure" in str(s.get("if", ""))
            and "exit 1" in str(s.get("run", "")),
            "the scan-outcome gate step",
        )
        self.assertIn("exit 1", gate_step["run"])

    def test_gate_step_runs_after_issue_filing_and_sbom_steps(self) -> None:
        # The gate step failing the job makes any later implicit success()
        # check false, so it must stay last: a reorder would silently
        # reintroduce bug #388 by skipping issue filing or SBOM upload.
        steps = self._scan_steps()
        gate_index = steps.index(
            self._find_step(
                lambda s: "steps.scan.outcome" in str(s.get("if", ""))
                and "failure" in str(s.get("if", ""))
                and "exit 1" in str(s.get("run", "")),
                "the scan-outcome gate step",
            )
        )
        issue_index = steps.index(
            self._find_step(
                lambda s: "gh issue create" in str(s.get("run", "")),
                "the issue-filing step",
            )
        )
        sbom_index = steps.index(
            self._find_step(
                lambda s: "upload-artifact" in str(s.get("uses", "")),
                "the SBOM upload step",
            )
        )
        self.assertGreater(gate_index, issue_index)
        self.assertGreater(gate_index, sbom_index)

    def test_issue_filing_skips_on_missing_or_inconclusive_report(self) -> None:
        # steps.scan.outcome == 'failure' also fires on transient Trivy
        # errors that never produce real findings; the step must bail out
        # before filing unless the report exists and shows a non-zero total.
        issue_step = self._find_step(
            lambda s: "gh issue create" in str(s.get("run", "")),
            "the issue-filing step",
        )
        run = str(issue_step["run"])
        self.assertIn('[ ! -s "$report" ]', run)
        self.assertIn("Total: [1-9]", run)
        self.assertIn("exit 0", run)

    def test_issue_filing_ensures_the_vuln_scan_label_exists(self) -> None:
        issue_step = self._find_step(
            lambda s: "gh issue create" in str(s.get("run", "")),
            "the issue-filing step",
        )
        run = str(issue_step["run"])
        self.assertIn("gh label create", run)
        self.assertIn("vuln-scan", run)
        self.assertIn("--force", run)
        # The label must be created before it is used, otherwise the first
        # scheduled failure still gets a 422 from a missing label.
        self.assertLess(run.index("gh label create"), run.index("gh issue create"))

    def test_third_party_scan_action_is_pinned_to_a_commit_sha(self) -> None:
        trivy_steps = [
            s for s in self._scan_steps() if "trivy-action" in str(s.get("uses", ""))
        ]
        self.assertTrue(trivy_steps, "expected at least one trivy-action step")
        for step in trivy_steps:
            with self.subTest(step=step.get("name")):
                self.assertRegex(step["uses"], _SHA_PINNED_ACTION)

    def test_sbom_is_generated_and_uploaded_as_an_artifact(self) -> None:
        sbom_step = next(
            s
            for s in self._scan_steps()
            if "trivy-action" in str(s.get("uses", ""))
            and s.get("with", {}).get("format") == "cyclonedx"
        )
        self.assertTrue(str(sbom_step["with"]["output"]).endswith(".cdx.json"))

        upload_step = next(
            s for s in self._scan_steps() if "upload-artifact" in str(s.get("uses", ""))
        )
        self.assertEqual(upload_step["with"]["path"], sbom_step["with"]["output"])

    def test_sbom_upload_survives_a_failed_vulnerability_gate(self) -> None:
        sbom_step = next(
            s
            for s in self._scan_steps()
            if "trivy-action" in str(s.get("uses", ""))
            and s.get("with", {}).get("format") == "cyclonedx"
        )
        upload_step = next(
            s for s in self._scan_steps() if "upload-artifact" in str(s.get("uses", ""))
        )
        self.assertEqual(sbom_step.get("if"), "always()")
        self.assertEqual(upload_step.get("if"), "always()")

    def test_scheduled_rescan_is_configured(self) -> None:
        triggers = self.workflow[_ON_KEY]
        self.assertIn("schedule", triggers)
        cron = triggers["schedule"]
        self.assertTrue(cron, "expected at least one scheduled cron entry")
        self.assertEqual(len(cron), 1)

    def test_scheduled_scan_targets_published_digests_from_fixture(self) -> None:
        ref_step = next(s for s in self._scan_steps() if s.get("id") == "ref")
        self.assertIn("tests/fixtures/signed-images.json", ref_step["run"])
        self.assertIn("github.event_name", ref_step["run"])
        self.assertIn("image-ref=cds/${{ matrix.image.name }}:scan", ref_step["run"])

        scan_step = next(
            s
            for s in self._scan_steps()
            if "trivy-action" in str(s.get("uses", ""))
            and s.get("with", {}).get("format") == "table"
        )
        self.assertEqual(
            scan_step["with"]["image-ref"], "${{ steps.ref.outputs.image-ref }}"
        )
        self.assertEqual(scan_step.get("id"), "scan")

    def test_failing_scheduled_scan_files_a_deduped_issue(self) -> None:
        job = self.jobs["scan"]
        self.assertEqual(job["permissions"].get("issues"), "write")
        issues_step = next(
            s
            for s in self._scan_steps()
            if "issue" in (s.get("name") or "").lower()
            and "gh issue" in s.get("run", "")
        )
        self.assertIn("github.event_name == 'schedule'", issues_step["if"])
        self.assertIn("steps.scan.outcome == 'failure'", issues_step["if"])
        # Regression guard: since the scan step has continue-on-error, the
        # job status stays "success" after a scan failure, so this step's
        # `if:` does NOT need an explicit always()/failure() to run. But the
        # scan step itself must have continue-on-error, or this condition
        # would be implicitly ANDed with success() and never fire.
        scan_step = next(s for s in self._scan_steps() if s.get("id") == "scan")
        self.assertTrue(scan_step.get("continue-on-error"))
        run = issues_step["run"]
        self.assertIn('label="vuln-scan"', run)
        self.assertIn('--label "$label"', run)
        self.assertIn("gh issue list", run)
        self.assertIn("in:title", run)
        self.assertIn("gh issue create", run)
        self.assertIn("gh issue edit", run)


if __name__ == "__main__":
    unittest.main()
