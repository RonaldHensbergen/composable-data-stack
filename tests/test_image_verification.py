import json
import os
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from cli.image_verification import (
    _verification_findings,
    collect_compose_images,
    default_fixture_path,
    load_policy_from_env,
    validate_fixture,
    verify_images,
)
from cli.preflight import PreflightCheck, run_preflight

_REPO_ROOT = Path(__file__).resolve().parent.parent
_FIXTURE_PATH = _REPO_ROOT / "tests" / "fixtures" / "signed-images.json"
_DIGEST_A = "sha256:" + "a" * 64
_DIGEST_B = "sha256:" + "b" * 64

_COMPOSE = yaml.safe_dump(
    {
        "services": {
            "app": {"image": f"ghcr.io/ronaldhensbergen/cds-dagster@{_DIGEST_A}"},
            "web": {"image": "ghcr.io/ronaldhensbergen/cds-superset:1.2.3"},
            "cache": {"image": "redis:latest"},
            "custom": {"image": "local/dagster:custom", "build": {"context": "."}},
            "unknown": {"image": "quay.io/example/tool:1.0"},
        }
    },
    sort_keys=False,
)


def _policy(mode: str = "full", **overrides) -> dict:
    defaults = {
        "mode": mode,
        "trusted_registries": ("ghcr.io", "docker.io"),
        "oidc_issuer": "https://token.actions.githubusercontent.com",
        "cert_identity_regexp": r"^https://github\.com/example/repo/.+$",
        "cosign_bin": "cosign",
        "key_path": None,
        "require_digest": True,
    }
    defaults.update(overrides)
    from cli.image_verification import ImagePolicy

    return ImagePolicy(**defaults)


class CollectComposeImagesTest(unittest.TestCase):
    def test_extracts_service_image_references(self) -> None:
        self.assertEqual(
            collect_compose_images(_COMPOSE),
            [
                ("app", f"ghcr.io/ronaldhensbergen/cds-dagster@{_DIGEST_A}", False),
                ("web", "ghcr.io/ronaldhensbergen/cds-superset:1.2.3", False),
                ("cache", "redis:latest", False),
                ("custom", "local/dagster:custom", True),
                ("unknown", "quay.io/example/tool:1.0", False),
            ],
        )

    def test_ignores_services_without_images(self) -> None:
        compose = yaml.safe_dump({"services": {"web": {"build": {"context": "."}}}})
        self.assertEqual(collect_compose_images(compose), [])

    def test_handles_malformed_compose(self) -> None:
        self.assertEqual(collect_compose_images("not: [valid"), [])


class ImagePolicyTest(unittest.TestCase):
    def test_default_mode_off_for_non_production(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            policy = load_policy_from_env("local")
            self.assertEqual(policy.mode, "off")
            self.assertNotIn("local", policy.trusted_registries)
            self.assertEqual(load_policy_from_env("dev").mode, "off")

    def test_default_mode_policy_for_production(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            policy = load_policy_from_env("prod")
        self.assertEqual(policy.mode, "policy")
        self.assertTrue(policy.require_digest)

    def test_mode_override_wins(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            policy = load_policy_from_env("local", mode_override="full")
        self.assertEqual(policy.mode, "full")

    def test_environment_overrides_policy_inputs(self) -> None:
        with patch.dict(
            os.environ,
            {
                "CDS_IMAGE_VERIFICATION": "full",
                "CDS_TRUSTED_REGISTRIES": "ghcr.io,registry.example.com",
                "CDS_TRUSTED_OIDC_ISSUER": "https://issuer.example.com",
                "CDS_TRUSTED_CERT_IDENTITY_REGEXP": "^https://example.com/workflow$",
                "CDS_COSIGN_BIN": "/opt/cosign",
                "CDS_COSIGN_KEY": "/keys/cosign.pub",
            },
            clear=True,
        ):
            policy = load_policy_from_env("prod")
        self.assertEqual(policy.mode, "full")
        self.assertEqual(policy.trusted_registries, ("ghcr.io", "registry.example.com"))
        self.assertEqual(policy.oidc_issuer, "https://issuer.example.com")
        self.assertEqual(
            policy.cert_identity_regexp, "^https://example.com/workflow$"
        )
        self.assertEqual(policy.cosign_bin, "/opt/cosign")
        self.assertEqual(policy.key_path, "/keys/cosign.pub")

    def test_unknown_mode_fails_safe_to_policy_for_production(self) -> None:
        with patch.dict(os.environ, {"CDS_IMAGE_VERIFICATION": "bogus"}, clear=True):
            self.assertEqual(load_policy_from_env("prod").mode, "policy")

    def test_unknown_mode_falls_back_to_off_for_non_production(self) -> None:
        with patch.dict(os.environ, {"CDS_IMAGE_VERIFICATION": "bogus"}, clear=True):
            self.assertEqual(load_policy_from_env("local").mode, "off")


class StaticPolicyTest(unittest.TestCase):
    def test_registry_allowlist_and_latest_tag_flagged(self) -> None:
        findings = verify_images(_COMPOSE, _policy(mode="policy"))
        rule_ids = {f["rule_id"] for f in findings}
        self.assertIn("CDS-SEC-052", rule_ids)
        self.assertIn("CDS-SEC-050", rule_ids)
        self.assertIn("CDS-SEC-051", rule_ids)

        untrusted = [f for f in findings if f["rule_id"] == "CDS-SEC-052"]
        self.assertEqual(untrusted[0]["path"], "services.unknown.image")
        self.assertEqual(untrusted[0]["value"], "quay.io/example/tool:1.0")

        latest = [f for f in findings if f["rule_id"] == "CDS-SEC-050"]
        self.assertEqual(latest[0]["value"], "redis:latest")

    def test_local_images_are_not_flagged(self) -> None:
        compose = yaml.safe_dump(
            {
                "services": {
                    "a": {
                        "image": "local/dagster:custom",
                        "build": {"context": "."},
                    }
                }
            }
        )
        self.assertEqual(verify_images(compose, _policy(mode="policy")), [])

    def test_local_prefix_without_build_does_not_bypass_checks(self) -> None:
        compose = yaml.safe_dump({"services": {"a": {"image": "local/evil:custom"}}})
        findings = verify_images(compose, _policy(mode="policy"))
        self.assertIn("CDS-SEC-051", {f["rule_id"] for f in findings})

    def test_local_prefix_with_null_build_does_not_bypass_checks(self) -> None:
        compose = yaml.safe_dump(
            {"services": {"a": {"image": "local/evil:custom", "build": None}}}
        )
        findings = verify_images(compose, _policy(mode="policy"))
        self.assertIn("CDS-SEC-051", {f["rule_id"] for f in findings})

    def test_custom_suffix_alone_does_not_bypass_checks(self) -> None:
        compose = yaml.safe_dump({"services": {"a": {"image": "quay.io/example/tool:custom"}}})
        findings = verify_images(compose, _policy(mode="policy"))
        self.assertIn("CDS-SEC-052", {f["rule_id"] for f in findings})

    def test_registry_allowlist_is_case_insensitive(self) -> None:
        compose = yaml.safe_dump(
            {"services": {"a": {"image": f"GHCR.IO/example/tool@{_DIGEST_A}"}}}
        )
        findings = verify_images(compose, _policy(mode="policy"))
        self.assertNotIn("CDS-SEC-052", {f["rule_id"] for f in findings})

    def test_untagged_image_reference_flagged_as_latest(self) -> None:
        compose = yaml.safe_dump({"services": {"cache": {"image": "redis"}}})
        findings = verify_images(compose, _policy(mode="policy"))
        self.assertIn("CDS-SEC-050", {f["rule_id"] for f in findings})

    def test_digest_pinned_image_not_flagged_as_latest(self) -> None:
        compose = yaml.safe_dump({"services": {"app": {"image": f"ghcr.io/ronaldhensbergen/cds-dagster@{_DIGEST_A}"}}})
        rule_ids = {f["rule_id"] for f in verify_images(compose, _policy(mode="policy"))}
        self.assertNotIn("CDS-SEC-050", rule_ids)

    def test_digest_pin_skipped_when_not_required(self) -> None:
        policy = _policy(mode="policy", require_digest=False)
        compose = yaml.safe_dump({"services": {"a": {"image": "postgres:16"}}})
        self.assertEqual(verify_images(compose, policy), [])

    def test_off_mode_returns_nothing(self) -> None:
        self.assertEqual(verify_images(_COMPOSE, _policy(mode="off")), [])

    def test_findings_sorted_by_severity_then_rule(self) -> None:
        findings = verify_images(_COMPOSE, _policy(mode="policy"))
        self.assertEqual(findings[0]["severity"], "medium")
        self.assertEqual(findings[0]["rule_id"], "CDS-SEC-050")


class FixtureVerificationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = {
            "schemaVersion": 1,
            "trustRoot": {"oidcIssuer": "issuer", "certificateIdentityRegexp": "re", "registries": ["ghcr.io"]},
            "images": {
                "cds-dagster": {
                    "repository": "ghcr.io/ronaldhensbergen/cds-dagster",
                    "digest": _DIGEST_A,
                    "signed": True,
                    "provenanceAttested": True,
                    "sbomAttested": True,
                }
            },
        }

    def test_matching_fixture_entry_verifies_offline(self) -> None:
        bundled = json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))
        digest = bundled["images"]["cds-dagster"]["digest"]
        compose = yaml.safe_dump({"services": {"app": {"image": f"ghcr.io/ronaldhensbergen/cds-dagster@{digest}"}}})
        with patch("cli.image_verification.shutil.which", return_value=None) as mock_which:
            findings = verify_images(compose, _policy(), fixture=_FIXTURE_PATH)
        mock_which.assert_not_called()
        self.assertEqual(findings, [])

    def test_digest_mismatch_is_flagged(self) -> None:
        compose = yaml.safe_dump({"services": {"app": {"image": f"ghcr.io/ronaldhensbergen/cds-dagster@{_DIGEST_B}"}}})
        findings = verify_images(compose, _policy(), fixture=_FIXTURE_PATH)
        self.assertEqual(findings[0]["rule_id"], "CDS-VER-003")
        self.assertEqual(findings[0]["severity"], "high")

    def test_unsigned_fixture_entry_is_flagged(self) -> None:
        self.fixture["images"]["cds-dagster"]["signed"] = False
        images = [("app", f"ghcr.io/ronaldhensbergen/cds-dagster@{_DIGEST_A}", False)]
        findings = _verification_findings(images, _policy(), self.fixture)
        self.assertIn("CDS-VER-001", {f["rule_id"] for f in findings})

    def test_missing_provenance_is_flagged(self) -> None:
        self.fixture["images"]["cds-dagster"]["provenanceAttested"] = False
        images = [("app", f"ghcr.io/ronaldhensbergen/cds-dagster@{_DIGEST_A}", False)]
        findings = _verification_findings(images, _policy(), self.fixture)
        self.assertIn("CDS-VER-002", {f["rule_id"] for f in findings})

    def test_tagged_reference_cannot_verify_against_fixture(self) -> None:
        images = [("web", "ghcr.io/ronaldhensbergen/cds-dagster:latest", False)]
        findings = _verification_findings(images, _policy(), self.fixture)
        self.assertEqual(findings[0]["rule_id"], "CDS-VER-003")
        self.assertEqual(findings[0]["severity"], "high")

    def test_fixture_entry_from_untrusted_registry_is_not_accepted(self) -> None:
        self.fixture["images"]["evil"] = {
            "repository": "evil.example.com/cds-dagster",
            "digest": _DIGEST_A,
            "signed": True,
            "provenanceAttested": True,
            "sbomAttested": True,
        }
        image = f"evil.example.com/cds-dagster@{_DIGEST_A}"
        with patch("cli.image_verification.shutil.which", return_value=None):
            findings = _verification_findings(
                [("app", image, False)],
                _policy(trusted_registries=("ghcr.io",)),
                self.fixture,
            )
        self.assertEqual(findings[0]["rule_id"], "CDS-VER-001")


class CosignVerificationTest(unittest.TestCase):
    def _compose(self, image: str) -> str:
        return yaml.safe_dump({"services": {"app": {"image": image}}})

    @patch("cli.image_verification.subprocess.run")
    @patch("cli.image_verification.shutil.which", return_value="/usr/bin/cosign")
    def test_keyless_verification_command(self, _mock_which, mock_run) -> None:
        mock_run.return_value = subprocess.CompletedProcess([], 0)
        image = f"ghcr.io/ronaldhensbergen/cds-dagster@{_DIGEST_A}"
        findings = verify_images(self._compose(image), _policy())
        self.assertEqual(findings, [])

        command = mock_run.call_args_list[0].args[0]
        self.assertEqual(command[:2], ["/usr/bin/cosign", "verify"])
        self.assertIn("--certificate-identity-regexp", command)
        self.assertIn("--certificate-oidc-issuer", command)
        self.assertIn(image, command)
        self.assertNotIn("--key", command)

        attest_command = mock_run.call_args_list[1].args[0]
        self.assertEqual(attest_command[:3], ["/usr/bin/cosign", "verify-attestation", "--type"])
        self.assertIn("slsaprovenance", attest_command)

    @patch("cli.image_verification.subprocess.run")
    @patch("cli.image_verification.shutil.which", return_value="/usr/bin/cosign")
    def test_key_managed_verification_command(self, _mock_which, mock_run) -> None:
        mock_run.return_value = subprocess.CompletedProcess([], 0)
        image = f"ghcr.io/ronaldhensbergen/cds-dagster@{_DIGEST_A}"
        findings = verify_images(self._compose(image), _policy(key_path="/keys/cosign.pub"))
        self.assertEqual(findings, [])

        command = mock_run.call_args_list[0].args[0]
        self.assertIn("--key", command)
        self.assertIn("/keys/cosign.pub", command)
        self.assertNotIn("--certificate-identity-regexp", command)

    @patch("cli.image_verification.subprocess.run")
    @patch("cli.image_verification.shutil.which", return_value=None)
    def test_missing_cosign_fails_closed(self, _mock_which, mock_run) -> None:
        image = f"ghcr.io/ronaldhensbergen/cds-dagster@{_DIGEST_A}"
        findings = verify_images(self._compose(image), _policy())
        mock_run.assert_not_called()
        self.assertEqual(findings[0]["rule_id"], "CDS-VER-001")
        self.assertIn("not found", findings[0]["message"])

    @patch("cli.image_verification.subprocess.run")
    @patch("cli.image_verification.shutil.which", return_value="/usr/bin/cosign")
    def test_rejected_signature_fails_closed(self, _mock_which, mock_run) -> None:
        mock_run.return_value = subprocess.CompletedProcess([], 1, stderr="signature mismatch")
        image = f"ghcr.io/ronaldhensbergen/cds-dagster@{_DIGEST_A}"
        findings = verify_images(self._compose(image), _policy())
        self.assertEqual(findings[0]["rule_id"], "CDS-VER-001")
        self.assertIn("signature mismatch", findings[0]["message"])


class SignedImagesFixtureTest(unittest.TestCase):
    def test_bundled_fixture_is_valid(self) -> None:
        fixture = json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))
        errors = validate_fixture(fixture)
        self.assertEqual(
            errors,
            [],
            "fixture validation errors: "
            + "; ".join(errors)
            + ". Refresh tests/fixtures/signed-images.json with the real digests "
            "from the latest publish-images run (see docs/image-signing.md).",
        )
        self.assertFalse(
            fixture.get("refreshRequired", True),
            "fixture must be refreshed from the latest publish-images run",
        )
        self.assertIn("cds-dagster", fixture["images"])
        self.assertIn("cds-superset", fixture["images"])

    def test_validate_fixture_rejects_bad_entries(self) -> None:
        errors = validate_fixture({"schemaVersion": 2, "images": {}})
        self.assertTrue(any("schemaVersion" in e for e in errors))
        self.assertTrue(any("images" in e for e in errors))

    def test_validate_fixture_rejects_placeholder_digests(self) -> None:
        fixture = {
            "schemaVersion": 1,
            "trustRoot": {"oidcIssuer": "issuer", "certificateIdentityRegexp": "re", "registries": ["ghcr.io"]},
            "images": {
                "cds-dagster": {
                    "repository": "ghcr.io/ronaldhensbergen/cds-dagster",
                    "digest": "sha256:" + "0" * 64,
                    "signed": True,
                    "provenanceAttested": True,
                    "sbomAttested": True,
                }
            },
        }
        errors = validate_fixture(fixture)
        self.assertTrue(any("placeholder" in e for e in errors))

    def test_default_fixture_path_resolves_in_repo(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            path = default_fixture_path()
        self.assertIsNotNone(path)
        self.assertEqual(path.resolve(), _FIXTURE_PATH.resolve())

    def test_fixture_env_var_overrides_default_path(self) -> None:
        with patch.dict(os.environ, {"CDS_SIGNED_IMAGES_FIXTURE": "C:/custom/fixture.json"}, clear=True):
            self.assertEqual(default_fixture_path(), Path("C:/custom/fixture.json"))

    @patch("cli.image_verification.subprocess.run")
    def test_unreadable_explicit_fixture_fails_closed(self, mock_run) -> None:
        compose = yaml.safe_dump({"services": {"a": {"image": f"ghcr.io/ronaldhensbergen/cds-dagster@{_DIGEST_A}"}}})
        with patch.dict(
            os.environ,
            {"CDS_SIGNED_IMAGES_FIXTURE": "C:/missing/signed-images.json"},
            clear=True,
        ):
            findings = verify_images(compose, _policy(), fixture=default_fixture_path())
        mock_run.assert_not_called()
        self.assertEqual(findings[0]["rule_id"], "CDS-VER-004")
        self.assertIn("CDS_SIGNED_IMAGES_FIXTURE", findings[0]["recommendation"][0])


class PreflightIntegrationTest(unittest.TestCase):
    @patch("cli.preflight.shutil.which", return_value="/usr/bin/docker")
    @patch("cli.preflight.subprocess.run")
    def test_image_checks_off_by_default(self, mock_run, _mock_which) -> None:
        mock_run.return_value = subprocess.CompletedProcess([], 0)
        plan = {"runtime": {"type": "docker-compose"}}
        compose = yaml.safe_dump({"services": {"a": {"image": "quay.io/example/tool:1.0"}}})
        with patch.dict(os.environ, {}, clear=True):
            checks = run_preflight(plan, compose, Path("missing.env"))
        self.assertTrue(
            all(check.name != "images" for check in checks),
            "image checks must stay disabled when CDS_IMAGE_VERIFICATION is unset",
        )

    @patch("cli.preflight.shutil.which", return_value="/usr/bin/docker")
    @patch("cli.preflight.subprocess.run")
    def test_policy_mode_fails_deployment_check_for_untrusted_registry(
        self, mock_run, _mock_which
    ) -> None:
        mock_run.return_value = subprocess.CompletedProcess([], 0)
        plan = {
            "runtime": {"type": "docker-compose"},
            "metadata": {"environment": "production"},
        }
        compose = yaml.safe_dump({"services": {"a": {"image": "quay.io/example/tool:1.0"}}})
        with patch.dict(os.environ, {}, clear=True):
            checks = run_preflight(plan, compose, Path("missing.env"))
        image_failures = [
            check for check in checks if check.name.startswith("images")
        ]
        self.assertTrue(any(check.status == "FAIL" for check in image_failures))
        self.assertTrue(any("CDS-SEC-052" in check.message for check in image_failures))

    @patch("cli.preflight.shutil.which", return_value="/usr/bin/docker")
    @patch("cli.preflight.subprocess.run")
    def test_policy_mode_passes_for_policy_compliant_images(
        self, mock_run, _mock_which
    ) -> None:
        mock_run.return_value = subprocess.CompletedProcess([], 0)
        plan = {
            "runtime": {"type": "docker-compose"},
            "metadata": {"environment": "production"},
        }
        image = f"ghcr.io/ronaldhensbergen/cds-dagster@{_DIGEST_A}"
        compose = yaml.safe_dump({"services": {"a": {"image": image}}})
        with patch.dict(os.environ, {}, clear=True):
            checks = run_preflight(plan, compose, Path("missing.env"))
        image_checks = [check for check in checks if check.name == "images"]
        self.assertEqual(image_checks[0].status, "PASS")


if __name__ == "__main__":
    unittest.main()
