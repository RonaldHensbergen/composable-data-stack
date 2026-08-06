import json
import re
import unittest
from pathlib import Path

import yaml

_OWNER = "ronaldhensbergen"
_ON_KEY = True
_STATIC_SECRET = re.compile(r"secrets\.(?!GITHUB_TOKEN)\w+")


class PublishImagesWorkflowTest(unittest.TestCase):
    def setUp(self) -> None:
        repo_root = Path(__file__).resolve().parent.parent
        workflow_path = repo_root / ".github" / "workflows" / "publish-images.yml"
        self.workflow = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
        self.jobs = self.workflow["jobs"]
        self.publish_steps = self.jobs["publish"]["steps"]

    def test_does_not_trigger_on_pull_requests(self) -> None:
        triggers = self.workflow[_ON_KEY]
        self.assertNotIn("pull_request", triggers)
        self.assertIn("push", triggers)
        self.assertEqual(triggers["push"]["branches"], ["main"])

    def test_has_permissions_required_for_keyless_signing(self) -> None:
        perms = self.jobs["publish"]["permissions"]
        self.assertEqual(perms.get("id-token"), "write")
        self.assertEqual(perms.get("packages"), "write")

    def test_no_static_secrets_referenced(self) -> None:
        raw = yaml.dump(self.jobs["publish"])
        matches = _STATIC_SECRET.findall(raw)
        self.assertEqual(matches, [], f"found non-GITHUB_TOKEN secret references: {matches}")

    def test_signing_and_attestation_use_the_digest_not_a_tag(self) -> None:
        for step_name in ("Sign image (keyless)", "Attest SBOM", "Attest provenance"):
            with self.subTest(step=step_name):
                step = next(s for s in self.publish_steps if s.get("name") == step_name)
                self.assertIn("steps.build.outputs.digest", step["run"])

    def test_sbom_and_provenance_are_both_attested(self) -> None:
        names = {s.get("name") for s in self.publish_steps}
        self.assertIn("Attest SBOM", names)
        self.assertIn("Attest provenance", names)
        sbom_attest = next(s for s in self.publish_steps if s.get("name") == "Attest SBOM")
        self.assertIn("--type cyclonedx", sbom_attest["run"])
        provenance_attest = next(s for s in self.publish_steps if s.get("name") == "Attest provenance")
        self.assertIn("--type slsaprovenance", provenance_attest["run"])

    def test_image_name_is_lowercased_before_use(self) -> None:
        name_step = next(s for s in self.publish_steps if s.get("id") == "name")
        self.assertIn("tr '[:upper:]' '[:lower:]'", name_step["run"])

    def test_fixture_recording_does_not_use_always(self) -> None:
        record_step = next(s for s in self.publish_steps if s.get("name") == "Record fixture data")
        condition = str(record_step.get("if", ""))
        self.assertNotIn("always()", condition)

    def test_signed_images_fixture_covers_every_published_image(self) -> None:
        repo_root = Path(__file__).resolve().parent.parent
        fixture_path = repo_root / "tests" / "fixtures" / "signed-images.json"
        self.assertTrue(
            fixture_path.is_file(),
            "tests/fixtures/signed-images.json is missing; refresh it from the "
            "latest publish-images run (see docs/image-signing.md)",
        )
        fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
        self.assertFalse(
            fixture.get("refreshRequired", True),
            "tests/fixtures/signed-images.json still uses placeholder digests; "
            "refresh it from the latest publish-images run (docs/image-signing.md)",
        )
        fixture_repos = {
            entry["repository"]
            for entry in fixture.get("images", {}).values()
            if isinstance(entry, dict)
        }
        images_dir = repo_root / "images"
        published = []
        for entry in sorted(images_dir.iterdir()):
            if not entry.is_dir():
                continue
            if (entry / "Dockerfile").is_file() or (entry / "base" / "Dockerfile").is_file():
                published.append(entry.name)
        self.assertTrue(published, "expected at least one published runtime image")
        for image in published:
            self.assertIn(
                f"ghcr.io/{_OWNER}/cds-{image}",
                fixture_repos,
                f"signed-images fixture is missing an entry for the published {image} image",
            )


if __name__ == "__main__":
    unittest.main()
