"""Tests for declarative runtime confinement (#207): seccomp/AppArmor/SELinux."""
import shutil
import tempfile
import unittest
from pathlib import Path

import yaml

from cli.confinement import validate_confinement
from cli.planner import build_plan
from cli.renderer import render_compose
from cli.validator import validate_profile

_FIXTURE = (
    Path(__file__).resolve().parent / "fixtures" / "security" / "literal-secret-output"
)


class _ProfileCase(unittest.TestCase):
    def _profile(self, spec_security=None, module_security=None, seccomp_file=False):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        shutil.copytree(_FIXTURE / "modules", tmp / "modules")
        profile_dir = tmp / "profile"
        profile_dir.mkdir()
        shutil.copy(_FIXTURE / "profile-safe" / ".env", profile_dir / ".env")
        data = yaml.safe_load((_FIXTURE / "profile-safe" / "profile.yaml").read_text())
        if spec_security is not None:
            data["spec"]["security"] = spec_security
        if module_security is not None:
            data["spec"]["modules"][0]["security"] = module_security
        if seccomp_file:
            (profile_dir / "seccomp.json").write_text("{}")
        path = profile_dir / "profile.yaml"
        path.write_text(yaml.safe_dump(data))
        return path

    def _render(self, path):
        plan, diags = build_plan(str(path), env_file=str(path.parent / ".env"))
        self.assertEqual([d for d in diags if d.level == "error"], [])
        output, render_diags = render_compose(
            plan, output_path=str(path.parent / "out" / "docker-compose.yml")
        )
        self.assertEqual([d for d in render_diags if d.level == "error"], [])
        return next(iter(yaml.safe_load(output)["services"].values()))


class ConfinementRenderTest(_ProfileCase):
    def test_profile_default_is_rendered_into_security_opt(self):
        path = self._profile(
            {"runtime": {"confinement": {
                "seccomp": "file:seccomp.json",
                "apparmor": "runtime/default",
                "selinux": ["type:container_t", "level:s0:c1,c2"],
            }}},
            seccomp_file=True,
        )
        opts = self._render(path)["security_opt"]
        self.assertIn("seccomp:../seccomp.json", opts)
        self.assertIn("apparmor=docker-default", opts)
        self.assertIn("label=type:container_t", opts)
        self.assertIn("label=level:s0:c1,c2", opts)

    def test_module_override_wins_over_profile_default(self):
        path = self._profile(
            {"runtime": {"confinement": {"apparmor": "runtime/default"}}},
            {"confinement": {"apparmor": "cds-custom"}},
        )
        opts = self._render(path)["security_opt"]
        self.assertIn("apparmor=cds-custom", opts)
        self.assertNotIn("apparmor=docker-default", opts)

    def test_no_confinement_leaves_services_untouched(self):
        path = self._profile()
        self.assertNotIn("seccomp=unconfined", self._render(path).get("security_opt", []))


class ConfinementValidationTest(_ProfileCase):
    def test_valid_profile_passes_validation(self):
        path = self._profile(
            {"runtime": {"confinement": {"seccomp": "default", "selinux": "disable"}}}
        )
        self.assertEqual([d for d in validate_profile(str(path)) if d.level == "error"], [])

    def test_invalid_values_are_rejected_with_paths(self):
        cases = [
            ({"seccomp": "bogus"}, "seccomp"),
            ({"seccomp": "file:../escape.json"}, "seccomp"),
            ({"seccomp": "file:/etc/x.json"}, "seccomp"),
            ({"seccomp": "file:missing.json"}, "seccomp"),
            ({"apparmor": "bad name;rm"}, "apparmor"),
            ({"selinux": ["nonsense"]}, "selinux"),
            ({"selinux": []}, "selinux"),
            ({"unknown": "x"}, "unknown"),
        ]
        base = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, base, True)
        for block, key in cases:
            with self.subTest(block=block):
                diags = validate_confinement(block, "c", base)
                self.assertTrue(diags)
                self.assertTrue(all(d.code == "E127" and d.path == f"c.{key}" for d in diags))

    def test_invalid_module_confinement_fails_validation(self):
        path = self._profile(module_security={"confinement": {"apparmor": "x y"}})
        errors = [d for d in validate_profile(str(path)) if d.level == "error"]
        self.assertTrue(errors)


if __name__ == "__main__":
    unittest.main()
