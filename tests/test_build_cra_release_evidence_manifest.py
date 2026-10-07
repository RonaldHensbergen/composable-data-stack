import importlib.util
import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = REPO_ROOT / "scripts" / "build_cra_release_evidence_manifest.py"

_spec = importlib.util.spec_from_file_location(
    "build_cra_release_evidence_manifest", SCRIPT_PATH
)
assert _spec is not None and _spec.loader is not None
build_cra_release_evidence_manifest = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = build_cra_release_evidence_manifest
_spec.loader.exec_module(build_cra_release_evidence_manifest)


class BuildManifestTest(unittest.TestCase):
    def _manifest(self, **overrides):
        kwargs = dict(
            name="composable-data-stack",
            version="0.9.1",
            commit_sha="abc123",
            source_ref="v0.9.1",
            sbom_file="cli-sbom.cyclonedx.json",
            release_inventory_file="release-inventory.json",
            generated_at="2026-01-01T00:00:00Z",
            generated_by="https://example.invalid/run/1",
        )
        kwargs.update(overrides)
        return build_cra_release_evidence_manifest.build_manifest(**kwargs)

    def test_always_disabled_by_default(self) -> None:
        manifest = self._manifest()
        self.assertFalse(manifest["conformityClaimEnabled"])
        self.assertEqual(manifest["approvals"], [])

    def test_evidence_references_existing_docs_not_copies(self) -> None:
        manifest = self._manifest()
        evidence = manifest["evidence"]
        self.assertEqual(evidence["scopeDecision"]["ref"], "docs/cra-scope-decision.md")
        self.assertEqual(evidence["riskAssessment"]["ref"], "docs/cra-risk-assessment.md")
        self.assertEqual(
            evidence["technicalDocumentation"]["ref"],
            "docs/cra-technical-documentation.md",
        )
        self.assertEqual(evidence["sbom"]["ref"], "cli-sbom.cyclonedx.json")
        self.assertEqual(evidence["releaseInventory"]["ref"], "release-inventory.json")
        self.assertIn("abc123", evidence["testResults"]["ref"])

    def test_classification_defaults_to_default_category(self) -> None:
        manifest = self._manifest()
        self.assertEqual(manifest["classification"]["category"], "default")
        self.assertTrue(manifest["classification"]["rationale"])

    def test_declaration_of_conformity_not_applicable_by_default(self) -> None:
        manifest = self._manifest()
        self.assertEqual(manifest["declarationOfConformity"]["status"], "not-applicable")
        self.assertIsNone(manifest["declarationOfConformity"]["ref"])

    def test_product_identity_and_commit_recorded(self) -> None:
        manifest = self._manifest()
        self.assertEqual(manifest["product"], {"name": "composable-data-stack", "version": "0.9.1"})
        self.assertEqual(manifest["sourceCommit"], "abc123")
        self.assertEqual(manifest["sourceRef"], "v0.9.1")


class MainTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.tmp_path = Path(self._tmpdir.name)
        self.pyproject_path = self.tmp_path / "pyproject.toml"
        self.pyproject_path.write_text(
            '[project]\nname = "example"\nversion = "0.1.0"\n', encoding="utf-8"
        )
        self.output_path = self.tmp_path / "manifest.json"

    def test_writes_deterministic_manifest_file(self) -> None:
        argv = [
            "build_cra_release_evidence_manifest.py",
            "--commit-sha",
            "abc123",
            "--source-ref",
            "v0.1.0",
            "--generated-at",
            "2026-01-01T00:00:00Z",
            "--generated-by",
            "https://example.invalid/run/1",
            "--pyproject",
            str(self.pyproject_path),
            "--output",
            str(self.output_path),
        ]
        old_argv = sys.argv
        sys.argv = argv
        try:
            result = build_cra_release_evidence_manifest.main()
        finally:
            sys.argv = old_argv

        self.assertEqual(result, 0)
        manifest = json.loads(self.output_path.read_text(encoding="utf-8"))
        self.assertEqual(manifest["product"]["version"], "0.1.0")
        self.assertFalse(manifest["conformityClaimEnabled"])


if __name__ == "__main__":
    unittest.main()
