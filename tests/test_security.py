import fnmatch
import json
import os
import tempfile
import tomllib
import unittest
import unittest.mock
from pathlib import Path

from cli.security import (
    PrecomputedRender,
    _eval_condition,
    _validate_rule_set,
    run_security_validation,
)

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


class NonSecretReasonEntropyGuardTest(unittest.TestCase):
    def test_entropy_rule_ignores_plaintext_waiver_reason_field(self):
        matched = _eval_condition(
            path="spec.security.waivers.plaintextEndpointExposure.reason",
            key="reason",
            value="Temporary exception for external compatibility windows 123!",
            cond={"entropy": "high", "minLength": 16},
            profile_class="prod",
        )
        self.assertFalse(matched)


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
                "CDS-SEC-071",
            },
        )
        for rule in deferred_rules:
            with self.subTest(rule=rule["id"]):
                self.assertIn("$comment", rule)
                self.assertTrue(rule["$comment"].strip())

    def test_no_scope_none_rule_is_enabled(self):
        """A rule with scope: ["none"] can never produce a finding, so
        marking it enabled: true implies coverage that doesn't exist. Every
        scope-none rule must be disabled (honest "off"). Regression guard
        for #355: fails loudly if anyone reintroduces enabled: true on a
        scope-none rule."""
        rule_set = json.loads(_RULE_SET_PATH.read_text())

        for rule in rule_set["rules"]:
            with self.subTest(rule=rule["id"]):
                if rule["scope"] == ["none"]:
                    self.assertFalse(
                        rule.get("enabled", True),
                        f"{rule['id']} is enabled but has scope: ['none'] "
                        "and can never produce a finding -- disable it or "
                        "give it a real scope",
                    )


class RenderedCommandSecretLeakRuleTest(unittest.TestCase):
    """
    Regression tests for CDS-SEC-070 (#297, #353).

    CDS-SEC-070 previously had `scope: ["none"]`, which meant it was never
    dispatched by run_security_validation() regardless of its
    `"enabled": true` flag -- and its `valueRegex` was also malformed
    (`"(?i)(--******"`, an invalid/unbalanced regex) since nothing ever
    compiled or exercised it. This is exactly the rule that should have
    caught the Vault dev-root-token-as-command-arg issue fixed in PR #290.

    The fixtures under tests/fixtures/security/rendered-command-secret/
    mirror that real module.yaml bug (and its fix) in miniature: one module
    (id "fake-vault", rendered Compose service also named "fake-vault")
    passes the same secret alias through three leak-prone surfaces --
    a "--flag=" style command argument, a bare positional command
    argument, and a healthcheck probe -- while the other module passes it
    through "environment:" instead (safe, post-#290 shape).
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

        hits = {f["path"]: f for f in findings if f["rule_id"] == "CDS-SEC-070"}

        # "--flag=${CDS_*}" style: command[2] is the "-dev-root-token-id=..."
        # element the real PR #290 bug rendered.
        flag_style = hits["services.fake-vault.command[2]"]
        self.assertEqual(flag_style["module"], "fake-vault")
        self.assertEqual(flag_style["value"], "-dev-root-token-id=${CDS_FAKE_TOKEN}")

        # Bare positional argument (no "--flag=" prefix): command[3] is just
        # the raw "${CDS_*}" placeholder, which the original valueRegex
        # (anchored on a "--flag=" prefix) could never have matched.
        bare_positional = hits["services.fake-vault.command[3]"]
        self.assertEqual(bare_positional["module"], "fake-vault")
        self.assertEqual(bare_positional["value"], "${CDS_FAKE_TOKEN}")

        # Healthcheck probes run as a subprocess too, so they leak the same
        # way as command/entrypoint.
        healthcheck = hits["services.fake-vault.healthcheck.test[1]"]
        self.assertEqual(healthcheck["module"], "fake-vault")
        self.assertEqual(healthcheck["value"], "check-token ${CDS_FAKE_TOKEN}")

        self.assertEqual(len(hits), 3, f"unexpected extra/missing findings: {hits}")

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

    def test_cds_sec_070_has_no_dead_match_branches(self):
        """
        Regression guard: two of the original three `match.any` branches
        could never fire against rendered output. `keyRegex` only tests
        the final path segment (`path.split(".")[-1]`), which for
        flattened command/entrypoint/healthcheck items is a list index
        like "command[2]" and for logging is an ordinary leaf key like
        "driver" -- never a password/secret/token/key-shaped name. And
        "${secrets.*}" always resolves to "${CDS_*}" by render time, while
        an unresolved "${config.*}" raises E071 and stops the render
        before this rule ever sees it, so a "config." / "secrets."
        alternative in the valueRegex is equally dead. Only a single
        working branch should remain.
        """
        rule = next(r for r in _validate_rule_set()["rules"] if r["id"] == "CDS-SEC-070")
        branches = rule["match"]["any"]
        self.assertEqual(len(branches), 1, f"expected exactly one live branch, got {branches}")
        self.assertNotIn("keyRegex", branches[0])
        self.assertNotIn("config\\.", branches[0].get("valueRegex", ""))
        self.assertNotIn("secrets\\.", branches[0].get("valueRegex", ""))

    def test_findings_are_attributed_to_the_module_id_not_the_compose_service_name(self):
        """
        Regression guard: _flatten_rendered_leak_surfaces() previously used
        the rendered Compose *service* name as the finding's "module",
        which only happened to look right in the fixture because the
        service name and module id coincided. Prove the mapping is actually
        derived from the plan (via _compose_service_name), not a
        pass-through of the service name, by checking a case where the
        renderer namespaces the service name away from the module id.
        """
        from cli.security import _map_service_to_module

        plan = {
            "modules": [
                {
                    "id": "vault",
                    "implementation": {
                        "compose": {"services": {"secrets-vault": {}}},
                    },
                },
            ],
        }
        self.assertEqual(
            _map_service_to_module(plan),
            {"vault-secrets-vault": "vault"},
        )

    def test_reuses_a_caller_supplied_plan_and_rendered_compose_without_replanning(self):
        """
        Callers such as `cds test` (cli/main.py) already run their own
        "plan" and "render" stages. run_security_validation() should reuse
        those results (via the `plan`/`rendered_compose_yaml` kwargs)
        instead of silently planning/rendering the same profile a second
        time internally.
        """
        from cli.planner import build_plan
        from cli.renderer import render_compose

        profile_path = self._FIXTURE_ROOT / "profile" / "profile.yaml"
        env_path = profile_path.parent / ".env"

        plan, plan_diags = build_plan(str(profile_path), env_file=str(env_path))
        self.assertFalse(any(d.level == "error" for d in plan_diags))
        rendered_compose_yaml, render_diags = render_compose(plan, env_file=str(env_path))
        self.assertFalse(any(d.level == "error" for d in render_diags))

        with (
            unittest.mock.patch(
                "cli.security.build_plan",
                side_effect=AssertionError("build_plan should not be called again"),
            ),
            unittest.mock.patch(
                "cli.security.render_compose",
                side_effect=AssertionError("render_compose should not be called again"),
            ),
        ):
            findings, _diags = run_security_validation(
                profile_path,
                _RULE_SCHEMA_PATH,
                _RULE_SET_PATH,
                env_file=str(env_path),
                precomputed_render=PrecomputedRender(
                    plan=plan,
                    rendered_compose_yaml=rendered_compose_yaml,
                ),
            )

        hits = [f for f in findings if f["rule_id"] == "CDS-SEC-070"]
        self.assertEqual(len(hits), 3)

    def test_unexpected_render_error_produces_a_warning_diagnostic_not_a_silent_failure(self):
        """
        _try_render_compose_for_scan() previously swallowed *any* exception
        (including genuine bugs) with a bare `except Exception: return
        None`. An unexpected internal error should now surface as a
        warning diagnostic (W096) instead of silently vanishing, while
        still letting the rest of security validation (profile/.env
        scoped rules) complete normally.
        """
        profile_path = self._FIXTURE_ROOT / "profile" / "profile.yaml"
        env_path = profile_path.parent / ".env"

        with unittest.mock.patch(
            "cli.security.build_plan",
            side_effect=RuntimeError("boom"),
        ):
            findings, diags = run_security_validation(
                profile_path,
                _RULE_SCHEMA_PATH,
                _RULE_SET_PATH,
                env_file=str(env_path),
            )

        warning_codes = [d.code for d in diags if d.level == "warning"]
        self.assertIn("W096", warning_codes)

        # The rendered-compose scoped rule can't fire without a rendered
        # document, but other scopes are unaffected.
        self.assertEqual(
            [f for f in findings if f["rule_id"] == "CDS-SEC-070"], [],
        )

    def test_skip_self_plan_render_avoids_replanning_a_profile_known_to_fail(self):
        """
        Regression guard: `cds test` used to always pass `plan=None,
        rendered_compose_yaml=None` when its own "plan"/"render" stages
        failed, which made run_security_validation() silently retry (and
        re-fail) the same build_plan()/render_compose() calls a caller had
        already run and reported diagnostics for.
        `PrecomputedRender(failed=True)` lets a caller that already knows
        planning/rendering failed opt out of that redundant retry.
        """
        profile_path = self._FIXTURE_ROOT / "profile" / "profile.yaml"
        env_path = profile_path.parent / ".env"

        with unittest.mock.patch(
            "cli.security.build_plan",
            side_effect=AssertionError("build_plan should not be retried"),
        ):
            findings, _diags = run_security_validation(
                profile_path,
                _RULE_SCHEMA_PATH,
                _RULE_SET_PATH,
                env_file=str(env_path),
                precomputed_render=PrecomputedRender(failed=True),
            )

        self.assertEqual(
            [f for f in findings if f["rule_id"] == "CDS-SEC-070"], [],
        )

    def test_does_not_plan_or_render_when_no_enabled_rule_uses_rendered_compose_scope(self):
        """
        Planning and rendering a profile is pure overhead when the active
        rule set has no enabled "rendered-compose"-scoped rule (e.g. a
        custom rule set that omits CDS-SEC-070, or has it disabled). Guard
        against doing that work unconditionally on every security scan.
        """
        profile_path = self._FIXTURE_ROOT / "profile" / "profile.yaml"
        env_path = profile_path.parent / ".env"

        rule_set = json.loads(_RULE_SET_PATH.read_text())
        for rule in rule_set["rules"]:
            if rule["id"] == "CDS-SEC-070":
                rule["enabled"] = False

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False,
        ) as tmp_rule_set:
            json.dump(rule_set, tmp_rule_set)
            tmp_rule_set_path = Path(tmp_rule_set.name)

        try:
            with unittest.mock.patch(
                "cli.security.build_plan",
                side_effect=AssertionError("build_plan should not be called"),
            ):
                findings, _diags = run_security_validation(
                    profile_path,
                    _RULE_SCHEMA_PATH,
                    tmp_rule_set_path,
                    env_file=str(env_path),
                )
            self.assertEqual(
                [f for f in findings if f["rule_id"] == "CDS-SEC-070"], [],
            )
        finally:
            tmp_rule_set_path.unlink()


class ExtendsAwareSecurityScanTest(unittest.TestCase):
    """cds security must resolve `extends` even without --environment (issue #175)."""

    def test_finding_declared_only_in_extends_parent_is_still_detected(self):
        # base/profile.yaml declares default admin credentials on a
        # "superset" module; child/profile.yaml only has `extends: [base]`
        # and no modules of its own. Scanning the child without an
        # --environment flag must still surface the parent's finding.
        profile_path = (
            _REPO_ROOT
            / "tests"
            / "fixtures"
            / "security"
            / "extends-parent-secret"
            / "profiles"
            / "child"
            / "profile.yaml"
        )

        findings, diags = run_security_validation(profile_path, _RULE_SCHEMA_PATH, _RULE_SET_PATH)

        self.assertFalse(any(d.level == "error" for d in diags), diags)
        hits = {f["path"] for f in findings if f["rule_id"] == "CDS-SEC-010"}
        self.assertIn("services.superset.environment.ADMIN_USERNAME", hits)
        self.assertIn("services.superset.environment.ADMIN_PASSWORD", hits)


if __name__ == "__main__":
    unittest.main()
