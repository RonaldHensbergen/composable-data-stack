"""Tests for CDS-SEC-090/091 (#207): production runtime policy and unconfined runtimes."""
import json
import shutil
import tempfile
import unittest
from pathlib import Path

import yaml

from cli.security import run_security_validation
from cli.validator import validate_profile

_FIXTURE = (
    Path(__file__).resolve().parent / "fixtures" / "security" / "literal-secret-output"
)
_RULES = {"CDS-SEC-090", "CDS-SEC-091"}


class RuntimePolicyTest(unittest.TestCase):
    def _profile(self, environment="production", security=None, hardened=False, confinement=None,
                 service_extra=None):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        modules = tmp / "modules"
        shutil.copytree(_FIXTURE / "modules", modules)
        if hardened:
            module_file = modules / "secrets" / "fake-literal" / "module.yaml"
            data = yaml.safe_load(module_file.read_text())
            service = data["spec"]["implementation"]["compose"]["services"]["fake-literal"]
            service.update({
                "user": "1000:1000",
                "read_only": True,
                "cap_drop": ["ALL"],
                "security_opt": ["no-new-privileges:true"],
            })
            service.update(service_extra or {})
            module_file.write_text(yaml.safe_dump(data))
        profile_dir = tmp / "profile"
        profile_dir.mkdir()
        shutil.copy(_FIXTURE / "profile-safe" / ".env", profile_dir / ".env")
        data = yaml.safe_load((_FIXTURE / "profile-safe" / "profile.yaml").read_text())
        data["metadata"]["environment"] = environment
        if security:
            data["spec"]["security"] = security
        if confinement:
            data["spec"].setdefault("security", {})["runtime"] = {"confinement": confinement}
        path = profile_dir / "profile.yaml"
        path.write_text(yaml.safe_dump(data))
        return path

    def _findings(self, path):
        findings, _ = run_security_validation(path, env_file=str(path.parent / ".env"))
        return [f for f in findings if f["rule_id"] in _RULES]

    def test_unhardened_service_violates_policy_in_production(self):
        findings = self._findings(self._profile())
        self.assertEqual([f["rule_id"] for f in findings], ["CDS-SEC-090"])
        for fragment in ("cap_drop", "no-new-privileges", "non-root", "writable"):
            self.assertIn(fragment, findings[0]["message"])

    def test_hardened_service_passes(self):
        self.assertEqual(self._findings(self._profile(hardened=True)), [])

    def test_policy_only_applies_to_production_profiles(self):
        self.assertEqual(self._findings(self._profile(environment="local")), [])

    def test_waiver_removes_only_listed_checks(self):
        waiver = {"waivers": {"runtimePolicy": {
            "reason": "image sets USER", "checks": ["nonRootUser"],
        }}}
        path = self._profile(security=waiver)
        self.assertEqual([d for d in validate_profile(str(path)) if d.level == "error"], [])
        message = self._findings(path)[0]["message"]
        self.assertNotIn("non-root", message)
        self.assertIn("cap_drop", message)

    def _codes(self, path):
        _, diags = run_security_validation(path, env_file=str(path.parent / ".env"))
        return [d.code for d in diags]

    def test_applied_waiver_emits_warning_with_checks(self):
        waiver = {"waivers": {"runtimePolicy": {
            "reason": "image sets USER", "checks": ["nonRootUser"],
        }}}
        self.assertIn("W103", self._codes(self._profile(security=waiver)))

    def test_unused_waiver_on_hardened_service_does_not_warn(self):
        waiver = {"waivers": {"runtimePolicy": {
            "reason": "image sets USER", "checks": ["nonRootUser"],
        }}}
        self.assertNotIn("W103", self._codes(self._profile(security=waiver, hardened=True)))

    def test_waiver_on_non_production_profile_warns_it_has_no_effect(self):
        waiver = {"waivers": {"runtimePolicy": {
            "reason": "image sets USER", "checks": ["nonRootUser"],
        }}}
        path = self._profile(environment="local", security=waiver)
        self.assertIn("W104", self._codes(path))

    def test_dangerous_cap_add_is_flagged_and_cannot_be_waived(self):
        waiver = {"waivers": {"runtimePolicy": {
            "reason": "x", "checks": ["capDrop", "nonRootUser"],
        }}}
        path = self._profile(
            security=waiver, hardened=True, service_extra={"cap_add": ["SYS_ADMIN"]}
        )
        findings = self._findings(path)
        self.assertEqual([f["rule_id"] for f in findings], ["CDS-SEC-090"])
        self.assertIn("cap_add grants SYS_ADMIN", findings[0]["message"])

    def test_benign_cap_add_is_allowed(self):
        path = self._profile(hardened=True, service_extra={"cap_add": ["NET_BIND_SERVICE"]})
        self.assertEqual(self._findings(path), [])

    def test_kernel_escape_cap_add_is_flagged_and_cannot_be_waived(self):
        waiver = {"waivers": {"runtimePolicy": {
            "reason": "x", "checks": ["capDrop", "nonRootUser"],
        }}}
        for cap in ("SYS_MODULE", "SYS_PTRACE", "SYS_RAWIO", "DAC_READ_SEARCH"):
            with self.subTest(cap=cap):
                path = self._profile(
                    security=waiver, hardened=True, service_extra={"cap_add": [cap]}
                )
                findings = self._findings(path)
                self.assertEqual([f["rule_id"] for f in findings], ["CDS-SEC-090"])
                self.assertIn(f"cap_add grants {cap}", findings[0]["message"])

    def test_waiver_requires_reason_and_known_checks(self):
        for waiver in (
            {"reason": " ", "checks": ["capDrop"]},
            {"reason": "x", "checks": ["bogus"]},
            {"reason": "x", "checks": []},
        ):
            with self.subTest(waiver=waiver):
                path = self._profile(security={"waivers": {"runtimePolicy": waiver}})
                self.assertTrue([d for d in validate_profile(str(path)) if d.level == "error"])

    def test_unconfined_runtime_is_flagged(self):
        path = self._profile(
            hardened=True,
            confinement={"seccomp": "unconfined", "apparmor": "unconfined", "selinux": "disable"},
        )
        findings = self._findings(path)
        self.assertEqual([f["rule_id"] for f in findings], ["CDS-SEC-091"])
        for name in ("seccomp", "apparmor", "selinux"):
            self.assertIn(name, findings[0]["message"])

    def test_declared_confinement_profile_is_not_flagged(self):
        path = self._profile(hardened=True, confinement={"apparmor": "runtime/default"})
        self.assertEqual(self._findings(path), [])

    def test_rules_can_be_disabled_in_a_custom_rule_set(self):
        from cli import security

        rule_set = json.loads(
            (Path(security.__file__).parent / "resources" / "rule-set.json").read_text()
        )
        for rule in rule_set["rules"]:
            if rule["id"] in _RULES:
                rule["codeEnforced"] = False
        rules_path = Path(tempfile.mkdtemp()) / "rules.json"
        self.addCleanup(shutil.rmtree, rules_path.parent, True)
        rules_path.write_text(json.dumps(rule_set))
        path = self._profile()
        findings, _ = run_security_validation(
            path, rule_set_path=rules_path, env_file=str(path.parent / ".env")
        )
        self.assertEqual([f for f in findings if f["rule_id"] in _RULES], [])

    def test_user_root_variants_are_flagged(self):
        """Test that user: root, 0, root:root, 0:0 are all flagged as running as root."""
        for user_value in ("root", "0", "root:root", "0:0", "  root  ", "0:1000", "0000", "00"):
            with self.subTest(user=user_value):
                path = self._profile(hardened=True, service_extra={"user": user_value})
                findings = self._findings(path)
                self.assertEqual([f["rule_id"] for f in findings], ["CDS-SEC-090"])
                self.assertIn("non-root", findings[0]["message"])

    def test_user_non_root_variants_pass(self):
        """Test that non-root users like 1000:1000, appuser, etc. pass."""
        for user_value in ("1000:1000", "appuser", "1000", "appuser:appuser"):
            with self.subTest(user=user_value):
                path = self._profile(hardened=True, service_extra={"user": user_value})
                self.assertEqual(self._findings(path), [])


if __name__ == "__main__":
    unittest.main()
