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
        self.assertEqual(
            matches, [], f"found non-GITHUB_TOKEN secret references: {matches}"
        )

    def test_signing_and_attestation_use_the_digest_not_a_tag(self) -> None:
        for step_name in ("Sign image (keyless)", "Attest SBOM", "Attest provenance"):
            with self.subTest(step=step_name):
                step = next(s for s in self.publish_steps if s.get("name") == step_name)
                self.assertIn("steps.push.outputs.digest", step["run"])

    def test_push_build_and_gate_run_in_order_before_push(self) -> None:
        # The vulnerability gate must scan the locally built image before the
        # push step so a HIGH/CRITICAL finding blocks publication entirely.
        names = [s.get("name") for s in self.publish_steps]
        build_idx = names.index("Build image for publication")
        gate_idx = names.index("Gate on HIGH/CRITICAL vulnerabilities before push")
        push_idx = names.index("Push image")
        self.assertLess(build_idx, gate_idx)
        self.assertLess(gate_idx, push_idx)
        build_run = self.publish_steps[build_idx]["run"]
        self.assertNotIn("docker push", build_run, "build must not push")
        self.assertIn(
            ":scan", build_run, "build must tag a local :scan image for the gate"
        )
        push_run = self.publish_steps[push_idx]["run"]
        self.assertIn("docker push", push_run)
        digest_output = [s for s in self.publish_steps if s.get("id") == "push"]
        self.assertEqual(
            len(digest_output), 1, "digest must be produced by the push step"
        )
        self.assertIn("$(docker inspect", push_run)
        self.assertIn('echo "digest=$digest" >> "$GITHUB_OUTPUT"', push_run)

    def _find_vuln_gate(self, job: dict) -> dict:
        return next(
            s
            for s in job["steps"]
            if s.get("name") == "Gate on HIGH/CRITICAL vulnerabilities before push"
        )

    def test_vuln_gate_fails_publish_on_high_critical(self) -> None:
        gate = self._find_vuln_gate(self.jobs["publish"])
        with_ = gate["with"]
        self.assertEqual(with_.get("severity"), "HIGH,CRITICAL")
        self.assertEqual(with_.get("exit-code"), "1")
        self.assertEqual(with_.get("ignore-unfixed"), "true")
        self.assertEqual(with_.get("scanners"), "vuln")
        self.assertEqual(
            with_.get("image-ref"),
            "cds/${{ matrix.image.name }}${{ matrix.image.variant != '' && format('-{0}', matrix.image.variant) || '' }}:scan",
        )
        self.assertEqual(with_.get("trivyignores"), ".trivyignore")
        self.assertNotIn("output", with_, "gate must stream findings to the run log")
        # Trivy CLI must be pinned the same way as the SBOM step.
        self.assertRegex(str(with_.get("version")), r"^v\d+\.\d+\.\d+$")

    def test_vuln_gate_present_in_dockerhub_job(self) -> None:
        job = self.jobs["publish-dockerhub"]
        gate = self._find_vuln_gate(job)
        self.assertEqual(gate["with"]["severity"], "HIGH,CRITICAL")
        self.assertEqual(gate["with"]["exit-code"], "1")
        self.assertEqual(gate["with"]["ignore-unfixed"], "true")
        self.assertEqual(
            gate["with"]["image-ref"],
            "cds/${{ matrix.image.name }}${{ matrix.image.variant != '' && format('-{0}', matrix.image.variant) || '' }}:scan",
        )
        names = [s.get("name") for s in job["steps"]]
        self.assertLess(
            names.index("Gate on HIGH/CRITICAL vulnerabilities before push"),
            names.index("Push image"),
        )

    def test_dockerhub_job_has_id_token_permission_for_keyless_signing(self) -> None:
        perms = self.jobs["publish-dockerhub"]["permissions"]
        self.assertEqual(perms.get("id-token"), "write")

    def test_dockerhub_job_signs_and_attests_by_digest(self) -> None:
        job = self.jobs["publish-dockerhub"]
        push_step = next(s for s in job["steps"] if s.get("id") == "push")
        self.assertIn("$(docker inspect", push_step["run"])
        self.assertIn('echo "digest=$digest" >> "$GITHUB_OUTPUT"', push_step["run"])

        for step_name in ("Sign image (keyless)", "Attest SBOM", "Attest provenance"):
            with self.subTest(step=step_name):
                step = next(s for s in job["steps"] if s.get("name") == step_name)
                self.assertIn("steps.push.outputs.digest", step["run"])

    def test_dockerhub_job_sbom_and_provenance_are_both_attested(self) -> None:
        job = self.jobs["publish-dockerhub"]
        names = {s.get("name") for s in job["steps"]}
        self.assertIn("Attest SBOM", names)
        self.assertIn("Attest provenance", names)
        sbom_attest = next(s for s in job["steps"] if s.get("name") == "Attest SBOM")
        self.assertIn("--type cyclonedx", sbom_attest["run"])
        provenance_attest = next(
            s for s in job["steps"] if s.get("name") == "Attest provenance"
        )
        self.assertIn("--type slsaprovenance", provenance_attest["run"])

    def test_dockerhub_job_uses_cosign_installer(self) -> None:
        job = self.jobs["publish-dockerhub"]
        uses = [str(s.get("uses", "")) for s in job["steps"]]
        self.assertTrue(any(u.startswith("sigstore/cosign-installer@") for u in uses))

    def test_dockerhub_gate_runs_before_sign_and_push(self) -> None:
        job = self.jobs["publish-dockerhub"]
        names = [s.get("name") for s in job["steps"]]
        gate_idx = names.index("Gate on HIGH/CRITICAL vulnerabilities before push")
        push_idx = names.index("Push image")
        sign_idx = names.index("Sign image (keyless)")
        self.assertLess(gate_idx, push_idx)
        self.assertLess(push_idx, sign_idx)

    def test_sbom_and_provenance_are_both_attested(self) -> None:
        names = {s.get("name") for s in self.publish_steps}
        self.assertIn("Attest SBOM", names)
        self.assertIn("Attest provenance", names)
        sbom_attest = next(
            s for s in self.publish_steps if s.get("name") == "Attest SBOM"
        )
        self.assertIn("--type cyclonedx", sbom_attest["run"])
        provenance_attest = next(
            s for s in self.publish_steps if s.get("name") == "Attest provenance"
        )
        self.assertIn("--type slsaprovenance", provenance_attest["run"])

    def test_image_name_is_lowercased_before_use(self) -> None:
        name_step = next(s for s in self.publish_steps if s.get("id") == "name")
        self.assertIn("tr '[:upper:]' '[:lower:]'", name_step["run"])

    def test_trivy_cli_version_is_pinned_explicitly(self) -> None:
        # trivy-action's own action.yaml bundles a default `version:` input
        # that lags behind the latest Trivy CLI release, and Renovate cannot
        # track that implicit default since it never appears in this repo's
        # own files. Pin `version:` explicitly so the CLI stays current and
        # future bumps are visible to dependency tooling.
        sbom_step = next(
            s for s in self.publish_steps if s.get("name") == "Generate SBOM"
        )
        version = sbom_step.get("with", {}).get("version")
        self.assertIsNotNone(version, "trivy-action step must pin `version:`")
        self.assertRegex(str(version), r"^v\d+\.\d+\.\d+$")

    def test_fixture_recording_does_not_use_always(self) -> None:
        record_step = next(
            s for s in self.publish_steps if s.get("name") == "Record fixture data"
        )
        condition = str(record_step.get("if", ""))
        self.assertNotIn("always()", condition)

    def test_has_scheduled_weekly_rebuild_trigger(self) -> None:
        triggers = self.workflow[_ON_KEY]
        self.assertIn("schedule", triggers)
        cron = triggers["schedule"]
        self.assertEqual(len(cron), 1)
        self.assertRegex(cron[0]["cron"], r"^\d+ \d+ \* \* [0-7]$")

    def test_update_fixture_job_refreshes_digests_after_publish(self) -> None:
        self.assertIn("update-fixture", self.jobs)
        job = self.jobs["update-fixture"]
        self.assertEqual(job["permissions"].get("contents"), "write")
        self.assertIn("publish", job["needs"])
        refresh = next(
            s
            for s in job["steps"]
            if s.get("name") == "Refresh signed-images fixture after successful publish"
        )
        self.assertIn("tests/fixtures/signed-images.json", refresh["run"])
        self.assertIn('["docker", "pull", tagged]', refresh["run"])
        self.assertIn(".RepoDigests 0", refresh["run"])

    def test_update_fixture_commits_only_when_digests_changed(self) -> None:
        job = self.jobs["update-fixture"]
        refresh = next(
            s
            for s in job["steps"]
            if s.get("name") == "Refresh signed-images fixture after successful publish"
        )
        self.assertIn("no digest changes", refresh["run"])

    def test_update_fixture_opens_a_pull_request_instead_of_pushing_to_main(
        self,
    ) -> None:
        # main is a protected branch; a direct `git push` to it is rejected
        # (GH006). The refresh must be routed through a pull request.
        job = self.jobs["update-fixture"]
        self.assertEqual(job["permissions"].get("pull-requests"), "write")
        pr_step = next(
            s
            for s in job["steps"]
            if s.get("name") == "Open a pull request if digests changed"
        )
        self.assertTrue(
            str(pr_step.get("uses", "")).startswith("peter-evans/create-pull-request@")
        )
        self.assertEqual(
            pr_step["with"]["add-paths"], "tests/fixtures/signed-images.json"
        )
        self.assertNotEqual(pr_step["with"].get("branch"), "main")
        for step in job["steps"]:
            self.assertNotIn("git push origin", str(step.get("run", "")))

    def test_update_fixture_cannot_retrigger_the_workflow(self) -> None:
        paths = self.workflow[_ON_KEY]["push"]["paths"]
        for path in paths:
            self.assertNotIn("tests/fixtures", path)
            self.assertNotIn("signed-images", path)

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
            if (entry / "Dockerfile").is_file():
                published.append(entry.name)
                continue
            if any((variant_dir / "Dockerfile").is_file() for variant_dir in entry.iterdir() if variant_dir.is_dir()):
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
