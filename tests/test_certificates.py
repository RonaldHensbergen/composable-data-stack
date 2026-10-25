"""Tests for the certificate reference mechanism (#578).

Certificate/key aliases map to externally managed material only: CDS plans
and renders environment variable NAMES (${CDS_*} runtime placeholders) and
never generates, reads, or embeds certificate or private key material.
"""
import tempfile
import unittest
from pathlib import Path

import yaml

from cli import planner
from cli.renderer import _build_context, _resolve_expr, render_compose
from cli.validator import validate_certificate_refs, validate_profile_shape


def _profile(certificates=None, tls_config=None):
    return {
        "apiVersion": "cds/v1alpha1",
        "kind": "Profile",
        "metadata": {"name": "certs", "environment": "local"},
        "spec": {
            "runtime": {"type": "docker-compose"},
            "modules": [
                {
                    "id": "proxy",
                    "source": "../../modules/integration/proxy",
                    "version": "0.1.0",
                    "enabled": True,
                    "config": tls_config or {},
                }
            ],
            "secrets": {"provider": {"type": "env"}, "values": {}},
            "certificates": certificates,
        },
    }


def _instances(config):
    return [{
        "index": 0,
        "id": "proxy",
        "config": config,
        "module": {"spec": {}},
    }]


def _cert_values():
    return {
        "values": {
            "tls": {"certEnv": "CDS_TLS_CERT", "keyEnv": "CDS_TLS_KEY"},
        }
    }


class CertificateSchemaTest(unittest.TestCase):
    def test_valid_certificates_block_passes(self):
        shape = validate_profile_shape(_profile(_cert_values()))
        self.assertEqual([d for d in shape if d.level == "error"], [])

    def test_missing_key_env_is_rejected(self):
        bad = _cert_values()
        del bad["values"]["tls"]["keyEnv"]
        errors = [d for d in validate_profile_shape(_profile(bad)) if d.level == "error"]
        self.assertTrue(errors)
        self.assertIn("keyEnv", errors[0].message)

    def test_lowercase_env_name_is_rejected(self):
        bad = _cert_values()
        bad["values"]["tls"]["certEnv"] = "cds_tls_cert"
        errors = [d for d in validate_profile_shape(_profile(bad)) if d.level == "error"]
        self.assertTrue(errors)


class CertificateRefValidationTest(unittest.TestCase):
    def test_valid_refs_pass(self):
        profile = _profile(_cert_values())
        config = {"certRef": "certificates.tls.cert", "keyRef": "certificates.tls.key"}
        self.assertEqual(validate_certificate_refs(profile, _instances(config)), [])

    def test_unknown_alias_reports_e129(self):
        profile = _profile(_cert_values())
        config = {"certRef": "certificates.ghost.cert"}
        diagnostics = validate_certificate_refs(profile, _instances(config))
        self.assertEqual([d.code for d in diagnostics], ["E129"])
        self.assertIn("not defined in spec.certificates.values", diagnostics[0].message)
        self.assertEqual(diagnostics[0].path, "spec.modules[0].config.certRef")

    def test_bare_alias_is_malformed(self):
        profile = _profile(_cert_values())
        config = {"certRef": "certificates.tls"}
        diagnostics = validate_certificate_refs(profile, _instances(config))
        self.assertEqual([d.code for d in diagnostics], ["E129"])
        self.assertIn("malformed", diagnostics[0].message)

    def test_unknown_part_is_malformed(self):
        profile = _profile(_cert_values())
        config = {"certRef": "certificates.tls.chain"}
        diagnostics = validate_certificate_refs(profile, _instances(config))
        self.assertEqual([d.code for d in diagnostics], ["E129"])

    def test_no_certificates_block_and_no_refs_is_clean(self):
        profile = _profile(None)
        self.assertEqual(validate_certificate_refs(profile, _instances({})), [])


class CertificatePlanningTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        modules_dir = root / "modules" / "integration" / "proxy"
        modules_dir.mkdir(parents=True)
        self.profile_dir = root / "profiles" / "certs"
        self.profile_dir.mkdir(parents=True)

        (modules_dir / "module.yaml").write_text(
            yaml.safe_dump(
                {
                    "apiVersion": "cds/v1alpha1",
                    "kind": "Module",
                    "metadata": {"name": "proxy", "category": "integration", "version": "0.1.0"},
                    "spec": {
                        "runtime": {
                            "type": "container",
                            "service": {
                                "name": "proxy",
                                "ports": [{"name": "web", "containerPort": 443, "protocol": "TCP"}],
                            },
                        },
                        "configSchema": {"type": "object", "additionalProperties": True},
                        "implementation": {
                            "kind": "docker-compose",
                            "compose": {
                                "services": {
                                    "proxy": {
                                        "image": "local/proxy:1",
                                        "environment": {
                                            "TLS_CERT": "${config.certRef}",
                                            "TLS_KEY": "${config.keyRef}",
                                        },
                                    }
                                }
                            },
                        },
                    },
                }
            ),
            encoding="utf-8",
        )

    def _profile(self, certificates, config):
        profile = _profile(certificates, config)
        profile["spec"]["modules"][0]["source"] = "../../modules/integration/proxy"
        return profile

    def test_plan_carries_env_names_only(self):
        profile = self._profile(_cert_values(), {
            "certRef": "certificates.tls.cert",
            "keyRef": "certificates.tls.key",
        })
        plan, diagnostics = planner.build_plan_from_profile(profile, self.profile_dir)

        self.assertFalse([d for d in diagnostics if d.level == "error"], diagnostics)
        self.assertEqual(
            plan["certificates"],
            {"tls": {"cert": "CDS_TLS_CERT", "key": "CDS_TLS_KEY"}},
        )
        # Refs stay refs in the plan -- never values or env names in config.
        module = plan["modules"][0]
        self.assertEqual(module["config"]["certRef"], "certificates.tls.cert")
        self.assertEqual(module["config"]["keyRef"], "certificates.tls.key")

    def test_unknown_ref_reports_e129_at_plan(self):
        profile = self._profile(_cert_values(), {"certRef": "certificates.ghost.cert"})
        plan, diagnostics = planner.build_plan_from_profile(profile, self.profile_dir)
        errors = [d for d in diagnostics if d.level == "error"]
        self.assertEqual([d.code for d in errors], ["E129"])

    def test_render_emits_runtime_placeholders_never_material(self):
        profile = self._profile(_cert_values(), {
            "certRef": "certificates.tls.cert",
            "keyRef": "certificates.tls.key",
        })
        plan, _ = planner.build_plan_from_profile(profile, self.profile_dir)
        compose_yaml, diagnostics = render_compose(plan)

        self.assertFalse([d for d in diagnostics if d.level == "error"], diagnostics)
        compose = yaml.safe_load(compose_yaml)
        env = compose["services"]["proxy"]["environment"]
        self.assertEqual(env["TLS_CERT"], "${CDS_TLS_CERT}")
        self.assertEqual(env["TLS_KEY"], "${CDS_TLS_KEY}")
        # Only env-var names in the rendered output; no cert/key material exists anywhere in it.
        self.assertNotIn("BEGIN CERTIFICATE", compose_yaml)
        self.assertNotIn("PRIVATE KEY", compose_yaml)


class CertificateSubstitutionUnitTest(unittest.TestCase):
    def test_direct_expression_resolves_cert_and_key(self):
        context = _build_context({"id": "m"}, {}, {"tls": {"cert": "CDS_TLS_CERT", "key": "CDS_TLS_KEY"}})
        self.assertEqual(_resolve_expr("certificates.tls.cert", context), "${CDS_TLS_CERT}")
        self.assertEqual(_resolve_expr("certificates.tls.key", context), "${CDS_TLS_KEY}")

    def test_walked_config_value_resolves(self):
        context = _build_context(
            {"id": "m", "config": {"certRef": "certificates.tls.cert"}},
            {},
            {"tls": {"cert": "CDS_TLS_CERT", "key": "CDS_TLS_KEY"}},
        )
        self.assertEqual(_resolve_expr("config.certRef", context), "${CDS_TLS_CERT}")

    def test_unknown_alias_stays_unresolved(self):
        context = _build_context({"id": "m"}, {}, {})
        self.assertIsNone(_resolve_expr("certificates.ghost.cert", context))

    def test_malformed_ref_stays_unresolved(self):
        context = _build_context({"id": "m"}, {}, {"tls": {"cert": "CDS_TLS_CERT", "key": "CDS_TLS_KEY"}})
        self.assertIsNone(_resolve_expr("certificates.tls", context))
        self.assertIsNone(_resolve_expr("certificates.tls.chain", context))


if __name__ == "__main__":
    unittest.main()
