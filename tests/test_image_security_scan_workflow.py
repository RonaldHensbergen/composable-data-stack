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
            s for s in self._scan_steps() if "trivy-action" in str(s.get("uses", ""))
            and s.get("with", {}).get("format") == "table"
        )
        self.assertEqual(scan_step["with"]["severity"], "HIGH,CRITICAL")
        # exit-code must actually fail the job on a match, not just report.
        self.assertEqual(str(scan_step["with"]["exit-code"]), "1")
        self.assertEqual(scan_step["with"]["scanners"], "vuln")

    def test_third_party_scan_action_is_pinned_to_a_commit_sha(self) -> None:
        trivy_steps = [s for s in self._scan_steps() if "trivy-action" in str(s.get("uses", ""))]
        self.assertTrue(trivy_steps, "expected at least one trivy-action step")
        for step in trivy_steps:
            with self.subTest(step=step.get("name")):
                self.assertRegex(step["uses"], _SHA_PINNED_ACTION)

    def test_sbom_is_generated_and_uploaded_as_an_artifact(self) -> None:
        sbom_step = next(
            s for s in self._scan_steps()
            if "trivy-action" in str(s.get("uses", "")) and s.get("with", {}).get("format") == "cyclonedx"
        )
        self.assertTrue(str(sbom_step["with"]["output"]).endswith(".cdx.json"))

        upload_step = next(s for s in self._scan_steps() if "upload-artifact" in str(s.get("uses", "")))
        self.assertEqual(upload_step["with"]["path"], sbom_step["with"]["output"])

    def test_sbom_upload_survives_a_failed_vulnerability_gate(self) -> None:
        sbom_step = next(
            s for s in self._scan_steps()
            if "trivy-action" in str(s.get("uses", "")) and s.get("with", {}).get("format") == "cyclonedx"
        )
        upload_step = next(s for s in self._scan_steps() if "upload-artifact" in str(s.get("uses", "")))
        self.assertEqual(sbom_step.get("if"), "always()")
        self.assertEqual(upload_step.get("if"), "always()")


if __name__ == "__main__":
    unittest.main()
