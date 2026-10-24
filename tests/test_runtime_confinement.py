"""Tests for declarative runtime confinement (#207): seccomp/AppArmor/SELinux."""
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import yaml

from cli.confinement import apply_confinement, validate_confinement
from cli.k8s_renderer import render_helm
from cli.planner import build_plan
from cli.renderer import render_compose
from cli.validator import validate_profile

_FIXTURE = (
    Path(__file__).resolve().parent / "fixtures" / "security" / "literal-secret-output"
)


class _ProfileCase(unittest.TestCase):
    def _profile(self, spec_security=None, module_security=None, seccomp_file=False):
        tmp = Path(tempfile.mkdtemp(dir=os.environ.get("CDS_TEST_TMPDIR")))
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

    def test_unrelated_security_opt_is_kept_and_matching_keys_replaced(self):
        service = {
            "security_opt": ["no-new-privileges:true", "apparmor=old", "label:disable"]
        }
        apply_confinement(
            service,
            {"apparmor": "cds-custom", "selinux": ["type:container_t"]},
            Path("/c"),
            Path("/c"),
        )
        self.assertEqual(
            service["security_opt"],
            ["no-new-privileges:true", "apparmor=cds-custom", "label=type:container_t"],
        )

    def test_seccomp_default_clears_module_set_value_only(self):
        service = {"security_opt": ["seccomp=unconfined", "no-new-privileges:true"]}
        apply_confinement(service, {"seccomp": "default"}, Path("/c"), Path("/c"))
        self.assertEqual(service["security_opt"], ["no-new-privileges:true"])

    def test_module_level_seccomp_file_is_rendered_relative_to_compose_file(self):
        path = self._profile(
            module_security={"confinement": {"seccomp": "file:seccomp.json"}},
            seccomp_file=True,
        )
        self.assertIn("seccomp:../seccomp.json", self._render(path)["security_opt"])

    def test_plan_with_escaping_seccomp_path_is_rejected_at_render_time(self):
        path = self._profile(
            {"runtime": {"confinement": {"seccomp": "file:seccomp.json"}}},
            seccomp_file=True,
        )
        plan, _ = build_plan(str(path), env_file=str(path.parent / ".env"))
        plan["security"]["runtime"]["confinement"]["seccomp"] = "file:../../etc/x.json"
        _, diags = render_compose(plan, output_path=str(path.parent / "out" / "c.yml"))
        self.assertIn("E127", [d.code for d in diags if d.level == "error"])


class ConfinementHelmWarningTest(_ProfileCase):
    def test_helm_render_warns_that_confinement_is_not_rendered(self):
        plan = {
            "metadata": {"name": "demo"},
            "security": {"runtime": {"confinement": {"apparmor": "runtime/default"}}},
            "modules": [],
        }
        _, diags = render_helm(plan)
        self.assertIn("W127", [d.code for d in diags if d.level == "warning"])

    def test_helm_render_is_silent_without_confinement(self):
        _, diags = render_helm({"metadata": {"name": "demo"}, "modules": []})
        self.assertNotIn("W127", [d.code for d in diags])


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
            ({"seccomp": "file:\\etc\\x.json"}, "seccomp"),
            ({"seccomp": "file:C:x.json"}, "seccomp"),
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

    def test_invalid_selinux_string_reports_e127_not_a_schema_error(self):
        path = self._profile({"runtime": {"confinement": {"selinux": "foo"}}})
        codes = [d.code for d in validate_profile(str(path)) if d.level == "error"]
        self.assertEqual(codes, ["E127"])

    def test_invalid_module_confinement_fails_validation(self):
        path = self._profile(module_security={"confinement": {"apparmor": "x y"}})
        errors = [d for d in validate_profile(str(path)) if d.level == "error"]
        self.assertTrue(errors)


if __name__ == "__main__":
    unittest.main()


class ConfinementHardeningTest(_ProfileCase):
    def test_seccomp_file_without_profile_directory_is_rejected(self):
        diags = validate_confinement({"seccomp": "file:seccomp.json"}, "c", None)
        self.assertEqual([d.code for d in diags], ["E127"])

    def test_unreferenceable_seccomp_path_is_a_diagnostic_not_a_traceback(self):
        service = {}
        with mock.patch("cli.confinement.os.path.relpath", side_effect=ValueError):
            diags = apply_confinement(
                service, {"seccomp": "file:s.json"}, Path("/c"), Path("/p"), "m.security"
            )
        self.assertEqual([d.code for d in diags], ["E127"])
        self.assertNotIn("security_opt", service)

    def test_invalid_profile_default_is_reported_once(self):
        path = self._profile({"runtime": {"confinement": {"seccomp": "file:seccomp.json"}}},
                             seccomp_file=True)
        plan, _ = build_plan(str(path), env_file=str(path.parent / ".env"))
        plan["security"]["runtime"]["confinement"]["seccomp"] = "bogus"
        plan["modules"] = plan["modules"] * 2
        _, diags = render_compose(plan, output_path=str(path.parent / "out" / "c.yml"))
        self.assertEqual([d.code for d in diags if d.level == "error"].count("E127"), 1)

    def test_duplicate_selinux_label_kinds_are_rejected(self):
        diags = validate_confinement(
            {"selinux": ["type:a_t", "type:b_t", "level:s0"]}, "c", None
        )
        self.assertEqual([d.code for d in diags], ["E127"])
        self.assertIn("type", diags[0].message)

    @unittest.skipUnless(
        os.environ.get("CDS_DOCKER_TESTS") == "1" and shutil.which("docker"),
        "set CDS_DOCKER_TESTS=1 with Docker available to run (CDS_TEST_TMPDIR if Docker cannot read /tmp)",
    )
    def test_rendered_seccomp_file_loads_in_docker_compose(self):
        path = self._profile(
            {"runtime": {"confinement": {"seccomp": "file:seccomp.json"}}}, seccomp_file=True
        )
        plan, _ = build_plan(str(path), env_file=str(path.parent / ".env"))
        compose_file = path.parent / "out" / "docker-compose.yml"
        output, _ = render_compose(plan, output_path=str(compose_file))
        compose_file.parent.mkdir(exist_ok=True)
        compose_file.write_text(output)
        result = subprocess.run(
            ["docker", "compose", "-f", str(compose_file), "config", "--quiet"],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_helm_warns_even_when_a_module_is_unsupported(self):
        plan = {
            "metadata": {"name": "demo"},
            "security": {"runtime": {"confinement": {"apparmor": "runtime/default"}}},
            "modules": [{"id": "x", "implementation": {"kind": "docker-compose"}}],
        }
        _, diags = render_helm(plan)
        self.assertIn("W127", [d.code for d in diags])
        self.assertIn("E072", [d.code for d in diags])
