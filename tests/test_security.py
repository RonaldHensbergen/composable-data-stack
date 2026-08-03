import fnmatch
import json
import os
import tempfile
import tomllib
import unittest
from pathlib import Path

from cli.security import _eval_condition, _validate_rule_set, run_security_validation

_REPO_ROOT = Path(__file__).resolve().parent.parent
_RULE_SCHEMA_PATH = _REPO_ROOT / "cli" / "resources" / "rule-schema.json"
_RULE_SET_PATH = _REPO_ROOT / "cli" / "resources" / "rule-set.json"


class BundledSecurityRulesTest(unittest.TestCase):
    def test_default_rule_set_is_available_as_package_data(self):
        rule_set = _validate_rule_set()

        self.assertEqual(rule_set["version"], "1.0.0")
        self.assertGreater(len(rule_set["rules"]), 0)


class PackageDataConfigurationTest(unittest.TestCase):
    """
    Regression guard for the packaging side of _validate_rule_set()'s default
    importlib.resources loading path.

    Every test that exercises _validate_rule_set() runs against an editable
    install, where importlib.resources.files("cli.resources") resolves
    straight to the repo's cli/resources/ directory and never consults
    [tool.setuptools.package-data]. That means a regression that drops the
    rule files from pyproject.toml's package-data configuration (so they are
    missing from a real built wheel/sdist) would pass every other test in
    this suite while breaking `cds` for anyone who installs the published
    package. This test checks the declared package-data configuration
    directly instead, independent of how the package happens to be installed
    locally.
    """

    def test_package_data_covers_bundled_resource_files(self):
        pyproject = tomllib.loads((_REPO_ROOT / "pyproject.toml").read_text())

        tool = pyproject.get("tool", {})
        setuptools_cfg = tool.get("setuptools", {})
        packages = setuptools_cfg.get("packages", [])
        self.assertIn(
            "cli.resources",
            packages,
            "cli.resources must be declared under [tool.setuptools].packages "
            "or its contents will not be installed at all",
        )

        package_data = setuptools_cfg.get("package-data", {})
        patterns = package_data.get("cli.resources", [])
        if isinstance(patterns, str):
            patterns = [patterns]
        self.assertTrue(
            patterns,
            "[tool.setuptools.package-data] must declare at least one "
            "pattern for cli.resources",
        )

        resources_dir = _REPO_ROOT / "cli" / "resources"
        self.assertTrue(
            resources_dir.is_dir(),
            "cli/resources/ directory does not exist; bundled resource files are missing",
        )
        bundled_files = {
            path.name
            for path in resources_dir.iterdir()
            if path.is_file() and path.name != "__init__.py"
        }
        self.assertTrue(
            bundled_files,
            "expected at least one bundled resource file under cli/resources/",
        )

        uncovered = [
            name
            for name in sorted(bundled_files)
            if not any(fnmatch.fnmatch(name, pattern) for pattern in patterns)
        ]
        self.assertEqual(
            uncovered,
            [],
            f"cli/resources/ files not covered by any package-data pattern "
            f"{patterns}: {uncovered}. These files would be missing from a "
            "built wheel/sdist even though editable-install tests pass.",
        )


class ImageTagPolicyTest(unittest.TestCase):
    """
    Regression test for a fixed inversion bug in _eval_condition's
    imageTagPolicy handling. require-digest and require-tag previously
    suppressed the flag for the risky case (missing digest/tag) and
    flagged the safe case instead.
    """

    def test_require_digest_flags_image_missing_a_digest(self):
        matched = _eval_condition(
            path="spec.modules[0].config.image",
            key="image",
            value="postgres:16",
            cond={"imageTagPolicy": "require-digest"},
            profile_class="prod",
        )
        self.assertTrue(matched, "an image with no digest should be flagged")

    def test_require_digest_does_not_flag_image_with_a_digest(self):
        matched = _eval_condition(
            path="spec.modules[0].config.image",
            key="image",
            value="postgres@sha256:" + "a" * 64,
            cond={"imageTagPolicy": "require-digest"},
            profile_class="prod",
        )
        self.assertFalse(matched, "a digest-pinned image should not be flagged")

    def test_require_tag_flags_image_missing_a_tag(self):
        matched = _eval_condition(
            path="spec.modules[0].config.image",
            key="image",
            value="postgres",
            cond={"imageTagPolicy": "require-tag"},
            profile_class="prod",
        )
        self.assertTrue(matched, "an image with no tag or digest should be flagged")

    def test_require_tag_does_not_flag_image_with_an_explicit_tag(self):
        matched = _eval_condition(
            path="spec.modules[0].config.image",
            key="image",
            value="postgres:16",
            cond={"imageTagPolicy": "require-tag"},
            profile_class="prod",
        )
        self.assertFalse(matched, "an explicitly tagged image should not be flagged")

    def test_require_tag_does_not_flag_digest_pinned_image(self):
        matched = _eval_condition(
            path="spec.modules[0].config.image",
            key="image",
            value="postgres@sha256:" + "a" * 64,
            cond={"imageTagPolicy": "require-tag"},
            profile_class="prod",
        )
        self.assertFalse(matched, "digest pinning satisfies require-tag's intent too")

    def test_forbid_latest_still_flags_the_latest_tag(self):
        # Control: forbid-latest was never inverted. Confirms the fix to
        # the other two branches didn't regress this one.
        matched = _eval_condition(
            path="spec.modules[0].config.image",
            key="image",
            value="postgres:latest",
            cond={"imageTagPolicy": "forbid-latest"},
            profile_class="prod",
        )
        self.assertTrue(matched, "an image using :latest should be flagged")

    def test_forbid_latest_still_ignores_explicit_tags(self):
        matched = _eval_condition(
            path="spec.modules[0].config.image",
            key="image",
            value="postgres:16",
            cond={"imageTagPolicy": "forbid-latest"},
            profile_class="prod",
        )
        self.assertFalse(matched, "an explicitly tagged image should not be flagged")


class EnvFilePathRuleTest(unittest.TestCase):
    def test_flags_a_profile_scoped_env_file_path(self):
        profile_path = (
            _REPO_ROOT / "tests" / "fixtures" / "security" / "profile-scoped-env" / "profile.yaml"
        )
        env_path = profile_path.parent / ".env"

        findings, _diags = run_security_validation(
            profile_path,
            _RULE_SCHEMA_PATH,
            _RULE_SET_PATH,
            env_file=str(env_path),
        )

        hits = [f for f in findings if f["rule_id"] == "CDS-SEC-031"]
        self.assertEqual(len(hits), 1)
        self.assertTrue(hits[0]["path"].endswith("tests/fixtures/security/profile-scoped-env/.env"))
        self.assertEqual(hits[0]["module"], "<env>")

    def test_cds_sec_010_has_no_unguarded_equalsany_branch(self):
        """CDS-SEC-010's dead first branch stays removed.

        That branch was an ``equalsAny`` on colon-joined ``user:password``
        defaults with no path/key gate. It could never match: every flattened
        value is a bare username or password (never a combined pair), and
        ``equalsAny`` is exact whole-string equality, so the branch contributed
        no detections while implying coverage it did not provide.
        """
        rule = next(r for r in _validate_rule_set()["rules"] if r["id"] == "CDS-SEC-010")
        for branch in rule["match"]["any"]:
            if "equalsAny" in branch:
                self.assertTrue(
                    "pathPatterns" in branch or "keyRegex" in branch,
                    "an unguarded equalsAny branch on 'user:password' values is dead code",
                )

    def test_cds_sec_010_still_flags_default_admin_password(self):
        """Removing the dead branch must not weaken the working detection."""
        rule = next(r for r in _validate_rule_set()["rules"] if r["id"] == "CDS-SEC-010")
        gated = next(b for b in rule["match"]["any"] if "pathPatterns" in b)
        path = "services.superset.environment.ADMIN_PASSWORD"
        self.assertTrue(_eval_condition(path, "ADMIN_PASSWORD", "password", gated, "prod"))
        self.assertFalse(
            _eval_condition(path, "ADMIN_PASSWORD", "s3cr3t-unique-value", gated, "prod")
        )

    def test_does_not_flag_the_conventional_project_root_env_file(self):
        """
        Regression guard for CDS-SEC-031 previously matching *every* env file
        path (including the project-root `.env`, which is the expected,
        already-`.gitignore`d default location per `resolve_env_file_path`'s
        fallback). Only a *nested* env file (no `.gitignore` coverage) should
        be flagged; a bare root `.env` must not produce a finding.
        """
        profile_path = (
            _REPO_ROOT / "tests" / "fixtures" / "security" / "profile-scoped-env" / "profile.yaml"
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_root = Path(tmp_dir)
            root_env = tmp_root / ".env"
            root_env.write_text("SOME_VAR=value\n", encoding="utf-8")

            original_cwd = Path.cwd()
            try:
                os.chdir(tmp_root)
                findings, _diags = run_security_validation(
                    profile_path,
                    _RULE_SCHEMA_PATH,
                    _RULE_SET_PATH,
                    env_file=str(root_env),
                )
            finally:
                os.chdir(original_cwd)

        hits = [f for f in findings if f["rule_id"] == "CDS-SEC-031"]
        self.assertEqual(hits, [], "a bare project-root .env must not be flagged")


class DeferredNoneScopeRuleDocumentationTest(unittest.TestCase):
    def test_each_remaining_none_scope_rule_declares_why_it_is_deferred(self):
        rule_set = json.loads(_RULE_SET_PATH.read_text())

        deferred_rules = [r for r in rule_set["rules"] if r["scope"] == ["none"]]
        self.assertEqual(
            {r["id"] for r in deferred_rules},
            {
                "CDS-SEC-006",
                "CDS-SEC-030",
                "CDS-SEC-032",
                "CDS-SEC-050",
                "CDS-SEC-051",
                "CDS-SEC-052",
                "CDS-SEC-053",
                "CDS-SEC-054",
                "CDS-SEC-071",
            },
        )
        for rule in deferred_rules:
            with self.subTest(rule=rule["id"]):
                self.assertIn("$comment", rule)
                self.assertTrue(rule["$comment"].strip())


class RenderedCommandSecretLeakRuleTest(unittest.TestCase):
    """
    Regression tests for CDS-SEC-070 (#297).

    CDS-SEC-070 previously had `scope: ["none"]`, which meant it was never
    dispatched by run_security_validation() regardless of its
    `"enabled": true` flag -- and its `valueRegex` was also malformed
    (`"(?i)(--******"`, an invalid/unbalanced regex) since nothing ever
    compiled or exercised it. This is exactly the rule that should have
    caught the Vault dev-root-token-as-command-arg issue fixed in PR #290.

    The fixtures under tests/fixtures/security/rendered-command-secret/
    mirror that real module.yaml bug (and its fix) in miniature: one module
    passes a secret through a Compose "command:" list argument (vulnerable,
    pre-#290 shape), the other passes the same secret through
    "environment:" instead (safe, post-#290 shape).
    """

    _FIXTURE_ROOT = _REPO_ROOT / "tests" / "fixtures" / "security" / "rendered-command-secret"

    def test_flags_a_secret_passed_via_command_line_argument(self):
        profile_path = self._FIXTURE_ROOT / "profile" / "profile.yaml"
        env_path = profile_path.parent / ".env"

        findings, _diags = run_security_validation(
            profile_path,
            _RULE_SCHEMA_PATH,
            _RULE_SET_PATH,
            env_file=str(env_path),
        )

        hits = [f for f in findings if f["rule_id"] == "CDS-SEC-070"]
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["module"], "fake-vault")
        self.assertIn("command", hits[0]["path"])

    def test_does_not_flag_the_same_secret_passed_via_environment(self):
        profile_path = self._FIXTURE_ROOT / "profile-safe" / "profile.yaml"
        env_path = profile_path.parent / ".env"

        findings, _diags = run_security_validation(
            profile_path,
            _RULE_SCHEMA_PATH,
            _RULE_SET_PATH,
            env_file=str(env_path),
        )

        hits = [f for f in findings if f["rule_id"] == "CDS-SEC-070"]
        self.assertEqual(hits, [], "a secret passed via environment must not be flagged")

    def test_cds_sec_070_scope_is_no_longer_none(self):
        rule = next(r for r in _validate_rule_set()["rules"] if r["id"] == "CDS-SEC-070")
        self.assertNotEqual(rule["scope"], ["none"])
        self.assertTrue(rule["enabled"])


if __name__ == "__main__":
    unittest.main()
