import importlib.util
import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = REPO_ROOT / "scripts" / "generate_release_inventory.py"

_spec = importlib.util.spec_from_file_location("generate_release_inventory", SCRIPT_PATH)
assert _spec is not None and _spec.loader is not None
generate_release_inventory = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = generate_release_inventory
_spec.loader.exec_module(generate_release_inventory)


class ArtifactRecordsTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.dist_dir = Path(self._tmpdir.name)

    def test_records_checksum_and_size_for_each_file(self) -> None:
        (self.dist_dir / "b.whl").write_bytes(b"wheel-bytes")
        (self.dist_dir / "a.tar.gz").write_bytes(b"sdist-bytes")

        records = generate_release_inventory.artifact_records(self.dist_dir)

        self.assertEqual([r["file"] for r in records], ["a.tar.gz", "b.whl"])
        self.assertEqual(records[0]["size"], len(b"sdist-bytes"))
        self.assertEqual(
            records[0]["sha256"],
            generate_release_inventory.sha256_of(self.dist_dir / "a.tar.gz"),
        )

    def test_ignores_subdirectories(self) -> None:
        (self.dist_dir / "sub").mkdir()
        (self.dist_dir / "a.whl").write_bytes(b"x")

        records = generate_release_inventory.artifact_records(self.dist_dir)

        self.assertEqual([r["file"] for r in records], ["a.whl"])

    def test_empty_directory_yields_no_records(self) -> None:
        self.assertEqual(generate_release_inventory.artifact_records(self.dist_dir), [])


class ImageEvidenceReferenceTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.fixture_path = Path(self._tmpdir.name) / "signed-images.json"

    def test_returns_none_when_fixture_absent(self) -> None:
        self.assertIsNone(generate_release_inventory.image_evidence_reference(self.fixture_path))

    def test_references_fixture_without_duplicating_image_list(self) -> None:
        self.fixture_path.write_text(
            json.dumps(
                {
                    "source": "https://example.invalid/workflow",
                    "updated": "2026-01-01",
                    "images": {"cds-dagster": {"digest": "sha256:deadbeef"}},
                }
            ),
            encoding="utf-8",
        )

        reference = generate_release_inventory.image_evidence_reference(self.fixture_path)

        assert reference is not None
        self.assertEqual(reference["source"], "https://example.invalid/workflow")
        self.assertEqual(reference["updated"], "2026-01-01")
        self.assertNotIn("images", reference)


class BuildInventoryTest(unittest.TestCase):
    def test_builds_expected_shape(self) -> None:
        inventory = generate_release_inventory.build_inventory(
            name="composable-data-stack",
            version="0.9.1",
            commit_sha="abc123",
            source_ref="v0.9.1",
            artifacts=[{"file": "a.whl", "sha256": "x", "size": 1}],
            sbom_file="cli-sbom.cyclonedx.json",
            sbom_format="CycloneDX",
            generated_at="2026-01-01T00:00:00Z",
            generated_by="https://example.invalid/run/1",
            image_evidence={"referenceFile": "tests/fixtures/signed-images.json"},
        )

        self.assertEqual(inventory["schemaVersion"], 1)
        self.assertEqual(inventory["cli"]["name"], "composable-data-stack")
        self.assertEqual(inventory["cli"]["version"], "0.9.1")
        self.assertEqual(inventory["cli"]["sourceCommit"], "abc123")
        self.assertEqual(inventory["cli"]["sbom"], {"file": "cli-sbom.cyclonedx.json", "format": "CycloneDX"})
        self.assertEqual(inventory["images"], {"referenceFile": "tests/fixtures/signed-images.json"})

    def test_missing_image_evidence_falls_back_to_a_documented_gap(self) -> None:
        inventory = generate_release_inventory.build_inventory(
            name="composable-data-stack",
            version="0.9.1",
            commit_sha="abc123",
            source_ref="v0.9.1",
            artifacts=[],
            sbom_file="cli-sbom.cyclonedx.json",
            sbom_format="CycloneDX",
            generated_at="2026-01-01T00:00:00Z",
            generated_by="https://example.invalid/run/1",
            image_evidence=None,
        )
        self.assertIn("note", inventory["images"])


class MainTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.tmp_path = Path(self._tmpdir.name)
        self.dist_dir = self.tmp_path / "dist"
        self.dist_dir.mkdir()
        (self.dist_dir / "example-0.1.0.whl").write_bytes(b"wheel")
        self.pyproject_path = self.tmp_path / "pyproject.toml"
        self.pyproject_path.write_text(
            '[project]\nname = "example"\nversion = "0.1.0"\n', encoding="utf-8"
        )
        self.output_path = self.tmp_path / "inventory.json"

    def test_writes_deterministic_inventory_file(self) -> None:
        argv = [
            "generate_release_inventory.py",
            "--dist-dir",
            str(self.dist_dir),
            "--sbom-file",
            "cli-sbom.cyclonedx.json",
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
            "--image-fixture",
            str(self.tmp_path / "does-not-exist.json"),
            "--output",
            str(self.output_path),
        ]
        old_argv = sys.argv
        sys.argv = argv
        try:
            result = generate_release_inventory.main()
        finally:
            sys.argv = old_argv

        self.assertEqual(result, 0)
        inventory = json.loads(self.output_path.read_text(encoding="utf-8"))
        self.assertEqual(inventory["cli"]["version"], "0.1.0")
        self.assertEqual(inventory["cli"]["artifacts"][0]["file"], "example-0.1.0.whl")

    def test_fails_when_no_artifacts_present(self) -> None:
        empty_dist = self.tmp_path / "empty-dist"
        empty_dist.mkdir()
        argv = [
            "generate_release_inventory.py",
            "--dist-dir",
            str(empty_dist),
            "--sbom-file",
            "cli-sbom.cyclonedx.json",
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
            result = generate_release_inventory.main()
        finally:
            sys.argv = old_argv

        self.assertEqual(result, 1)


if __name__ == "__main__":
    unittest.main()
