"""Tests for the TLS-capable reverse-proxy contract metadata (#579).

The shared contract document (shared/contracts/reverse-proxy.yaml) declares
the TLS metadata fields; these tests pin that the Traefik provider actually
exposes them through its provide entry, with its configSchema defaults
applied, and that the resolved contract stays provider-neutral for
downstream TLS checks.
"""
import unittest
from pathlib import Path

import yaml

from cli.planner import _apply_schema_defaults, resolve_provided_contracts

_REPO_ROOT = Path(__file__).resolve().parent.parent
_TRAEFIK_MODULE = _REPO_ROOT / "modules" / "integration" / "traefik" / "module.yaml"


def _traefik_instance(config=None):
    module = yaml.safe_load(_TRAEFIK_MODULE.read_text(encoding="utf-8"))
    config = _apply_schema_defaults(config or {}, module["spec"]["configSchema"])
    return {"id": "traefik", "config": config, "module": module}


class ReverseProxyTlsMetadataTest(unittest.TestCase):
    def test_resolved_contract_carries_tls_metadata_from_defaults(self):
        resolved = resolve_provided_contracts(_traefik_instance())
        spec = resolved["reverse-proxy"]["spec"]

        self.assertEqual(spec["tls"], True)
        self.assertEqual(spec["tlsMinVersion"], "VersionTLS12")
        self.assertEqual(spec["tlsMaxVersion"], "VersionTLS13")
        self.assertEqual(spec["tlsSniStrict"], True)
        # The proxy's certificate is locally mounted (not CA-verified), so the
        # provider must not advertise certVerified: consumers treat the
        # endpoint as unverified.
        self.assertNotIn("certVerified", spec)

    def test_tls_metadata_follows_config_overrides(self):
        resolved = resolve_provided_contracts(
            _traefik_instance({"tls": {"minVersion": "VersionTLS13"}})
        )
        spec = resolved["reverse-proxy"]["spec"]

        self.assertEqual(spec["tlsMinVersion"], "VersionTLS13")
        self.assertEqual(spec["tlsMaxVersion"], "VersionTLS13")
        self.assertEqual(spec["tlsSniStrict"], True)

    def test_contract_document_declares_the_metadata_fields(self):
        contract = yaml.safe_load(
            (_REPO_ROOT / "shared" / "contracts" / "reverse-proxy.yaml").read_text(encoding="utf-8")
        )
        fields = contract["spec"]["fields"]
        for field in ("tls", "tlsMinVersion", "tlsMaxVersion", "tlsSniStrict", "certVerified"):
            self.assertIn(field, fields, f"contract document is missing field {field}")
        # Every metadata field documents its TLS semantics.
        for field in ("tlsMinVersion", "tlsMaxVersion", "tlsSniStrict", "certVerified"):
            self.assertTrue(fields[field].get("description", "").strip())
        # The example reflects secure defaults.
        example = contract["spec"]["examples"][0]
        self.assertEqual(example["tlsMinVersion"], "VersionTLS12")
        self.assertEqual(example["tlsSniStrict"], True)


if __name__ == "__main__":
    unittest.main()
