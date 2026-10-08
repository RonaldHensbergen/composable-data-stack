import importlib.util
import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = REPO_ROOT / "scripts" / "check_cra_release_gate.py"

_spec = importlib.util.spec_from_file_location("check_cra_release_gate", SCRIPT_PATH)
assert _spec is not None and _spec.loader is not None
check_cra_release_gate = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = check_cra_release_gate
_spec.loader.exec_module(check_cra_release_gate)


def _complete_manifest(**overrides) -> dict:
    manifest = {
        "conformityClaimEnabled": True,
        "evidence": {
            key: {"ref": "README.md"}
            for key in check_cra_release_gate._REQUIRED_EVIDENCE_REFS
        },
        "classification": {"category": "default", "rationale": "not IAM/VPN/SIEM/etc."},
        "conformityAssessmentRoute": {"route": "Annex VIII Part I"},
        "declarationOfConformity": {"status": "approved", "ref": "https://example.invalid/doc"},
        "approvals": [{"name": "Jane Doe", "role": "Maintainer", "date": "2026-01-01"}],
    }
    manifest.update(overrides)
    return manifest


class GateIsEnabledTest(unittest.TestCase):
    def test_false_when_flag_absent(self) -> None:
        self.assertFalse(check_cra_release_gate.gate_is_enabled({}))

    def test_false_when_flag_explicitly_false(self) -> None:
        self.assertFalse(
            check_cra_release_gate.gate_is_enabled({"conformityClaimEnabled": False})
        )

    def test_true_when_flag_true(self) -> None:
        self.assertTrue(
            check_cra_release_gate.gate_is_enabled({"conformityClaimEnabled": True})
        )


class CheckManifestTest(unittest.TestCase):
    def test_complete_manifest_has_no_problems(self) -> None:
        self.assertEqual(check_cra_release_gate.check_manifest(_complete_manifest()), [])

    def test_missing_evidence_ref_is_reported(self) -> None:
        manifest = _complete_manifest()
        del manifest["evidence"]["sbom"]
        problems = check_cra_release_gate.check_manifest(manifest)
        self.assertTrue(any("evidence.sbom.ref" in p for p in problems))

    def test_invalid_classification_category_is_reported(self) -> None:
        manifest = _complete_manifest(classification={"category": "bogus", "rationale": "x"})
        problems = check_cra_release_gate.check_manifest(manifest)
        self.assertTrue(any("classification.category" in p for p in problems))

    def test_declaration_not_approved_is_reported(self) -> None:
        manifest = _complete_manifest(
            declarationOfConformity={"status": "not-applicable", "ref": "x"}
        )
        problems = check_cra_release_gate.check_manifest(manifest)
        self.assertTrue(any("declarationOfConformity.status" in p for p in problems))

    def test_empty_approvals_is_reported(self) -> None:
        manifest = _complete_manifest(approvals=[])
        problems = check_cra_release_gate.check_manifest(manifest)
        self.assertTrue(any("approvals" in p for p in problems))

    def test_approval_missing_field_is_reported(self) -> None:
        manifest = _complete_manifest(approvals=[{"name": "Jane Doe", "role": "Maintainer"}])
        problems = check_cra_release_gate.check_manifest(manifest)
        self.assertTrue(any("approvals[0].date" in p for p in problems))


class RepoFileEvidenceTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.root = Path(self._tmpdir.name)
        (self.root / "ok.md").write_text("# Real document\n", encoding="utf-8")

    def _problems(self, ref: str, key: str = "scopeDecision") -> list[str]:
        manifest = _complete_manifest()
        manifest["evidence"][key] = {"ref": ref}
        return check_cra_release_gate.check_manifest(manifest, self.root)

    def test_existing_file_is_accepted(self) -> None:
        self.assertEqual(
            [p for p in self._problems("ok.md") if "scopeDecision" in p], []
        )

    def test_missing_file_is_reported(self) -> None:
        self.assertTrue(any("does not exist" in p for p in self._problems("nope.md")))

    def test_path_outside_repository_is_reported(self) -> None:
        self.assertTrue(
            any("outside the repository" in p for p in self._problems("../etc/passwd"))
        )

    def test_draft_marked_declaration_is_reported(self) -> None:
        (self.root / "draft.md").write_text(
            "> **DRAFT TEMPLATE \u2014 NOT VALID.**\n", encoding="utf-8"
        )
        manifest = _complete_manifest(
            declarationOfConformity={"status": "approved", "ref": "draft.md"}
        )
        problems = check_cra_release_gate.check_manifest(manifest, self.root)
        self.assertTrue(any("DRAFT" in p for p in problems))

    def test_url_refs_are_not_checked_against_the_checkout(self) -> None:
        self.assertEqual(
            [p for p in self._problems("https://example.invalid/x") if "scopeDecision" in p],
            [],
        )

    def test_invalid_approval_date_is_reported(self) -> None:
        manifest = _complete_manifest(
            approvals=[{"name": "Jane Doe", "role": "Maintainer", "date": "yesterday"}]
        )
        problems = check_cra_release_gate.check_manifest(manifest, self.root)
        self.assertTrue(any("approvals[0].date" in p for p in problems))


class MainTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.manifest_path = Path(self._tmpdir.name) / "manifest.json"

    def _run(self, manifest: dict) -> int:
        self.manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        old_argv = sys.argv
        sys.argv = ["check_cra_release_gate.py", str(self.manifest_path)]
        try:
            return check_cra_release_gate.main()
        finally:
            sys.argv = old_argv

    def test_disabled_manifest_passes_as_a_no_op(self) -> None:
        self.assertEqual(self._run({"conformityClaimEnabled": False}), 0)

    def test_manifest_with_no_flag_passes_as_a_no_op(self) -> None:
        self.assertEqual(self._run({}), 0)

    def test_enabled_and_complete_manifest_passes(self) -> None:
        self.assertEqual(self._run(_complete_manifest()), 0)

    def test_enabled_and_incomplete_manifest_fails(self) -> None:
        manifest = _complete_manifest()
        del manifest["approvals"]
        self.assertEqual(self._run(manifest), 1)

    def test_invalid_json_fails_without_traceback(self) -> None:
        self.manifest_path.write_text("{not json", encoding="utf-8")
        old_argv = sys.argv
        sys.argv = ["check_cra_release_gate.py", str(self.manifest_path)]
        try:
            result = check_cra_release_gate.main()
        finally:
            sys.argv = old_argv
        self.assertEqual(result, 1)

    def test_non_object_manifest_fails(self) -> None:
        self.manifest_path.write_text("[]", encoding="utf-8")
        old_argv = sys.argv
        sys.argv = ["check_cra_release_gate.py", str(self.manifest_path)]
        try:
            result = check_cra_release_gate.main()
        finally:
            sys.argv = old_argv
        self.assertEqual(result, 1)

    def test_missing_manifest_file_fails(self) -> None:
        old_argv = sys.argv
        sys.argv = ["check_cra_release_gate.py", str(self.manifest_path.with_suffix(".missing"))]
        try:
            result = check_cra_release_gate.main()
        finally:
            sys.argv = old_argv
        self.assertEqual(result, 1)


if __name__ == "__main__":
    unittest.main()
