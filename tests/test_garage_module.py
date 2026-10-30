import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml
from jsonschema import Draft202012Validator

from cli.planner import (
    build_plan,
    resolve_consumed_contracts,
    resolve_provided_contracts,
)
from cli.renderer import render_compose
from cli.security import run_security_validation
from cli.validator import validate_profile

REPO_ROOT = Path(__file__).resolve().parent.parent
MODULE_PATH = REPO_ROOT / "modules" / "storage" / "garage" / "module.yaml"
CONTRACT_PATH = REPO_ROOT / "shared" / "contracts" / "object-storage.yaml"
RULE_SCHEMA_PATH = REPO_ROOT / "cli" / "resources" / "rule-schema.json"
RULE_SET_PATH = REPO_ROOT / "cli" / "resources" / "rule-set.json"

_TEST_SECRETS = {
    "object_storage_access_key": "CDS_OBJECT_STORAGE_ACCESS_KEY",
    "object_storage_secret_key": "CDS_OBJECT_STORAGE_SECRET_KEY",
    "garage_rpc_secret": "CDS_GARAGE_RPC_SECRET",
}


def _garage_profile(environment: str = "local", config: dict | None = None) -> dict:
    return {
        "apiVersion": "cds/v1alpha1",
        "kind": "Profile",
        "metadata": {"name": "garage-test", "environment": environment},
        "spec": {
            "runtime": {"type": "docker-compose"},
            "modules": [
                {
                    # The contract endpoint advertises the Compose service DNS
                    # name derived from the instance id; the conventional
                    # id "garage" equals the module's service name.
                    "id": "garage",
                    "source": "storage/garage",
                    "version": "0.1.0",
                    "enabled": True,
                    "config": config
                    or {
                        "bucket": "cds-data",
                        "accessKeyFrom": "secrets.object_storage_access_key",
                        "secretKeyFrom": "secrets.object_storage_secret_key",
                        "rpcSecretFrom": "secrets.garage_rpc_secret",
                    },
                }
            ],
            "secrets": {
                "provider": {"type": "env"},
                "values": {
                    "object_storage_access_key": {
                        "env": "CDS_OBJECT_STORAGE_ACCESS_KEY",
                        "required": True,
                    },
                    "object_storage_secret_key": {
                        "env": "CDS_OBJECT_STORAGE_SECRET_KEY",
                        "required": True,
                    },
                    "garage_rpc_secret": {
                        "env": "CDS_GARAGE_RPC_SECRET",
                        "required": True,
                    },
                },
            },
        },
    }


def _write_profile(tmp: str, profile: dict) -> Path:
    profile_path = Path(tmp) / "profile.yaml"
    profile_path.write_text(yaml.safe_dump(profile), encoding="utf-8")
    return profile_path


class GarageModuleTest(unittest.TestCase):
    def setUp(self) -> None:
        self.module = yaml.safe_load(MODULE_PATH.read_text(encoding="utf-8"))
        self.contract = yaml.safe_load(CONTRACT_PATH.read_text(encoding="utf-8"))
        self.config_schema = self.module["spec"]["configSchema"]
        self.service = self.module["spec"]["implementation"]["compose"]["services"]["garage"]

    def test_module_config_is_valid_json_schema(self) -> None:
        Draft202012Validator.check_schema(self.config_schema)

    def test_single_node_module_is_not_production_suitable(self) -> None:
        self.assertFalse(self.module["metadata"]["productionSuitable"])

    def test_config_requires_secret_aliases_and_safe_bucket_name(self) -> None:
        properties = self.config_schema["properties"]
        self.assertIn("accessKeyFrom", self.config_schema["required"])
        self.assertIn("secretKeyFrom", self.config_schema["required"])
        self.assertIn("rpcSecretFrom", self.config_schema["required"])
        self.assertRegex("cds-data", properties["bucket"]["pattern"])
        self.assertNotRegex("CDS_Data", properties["bucket"]["pattern"])
        self.assertNotRegex("../private", properties["bucket"]["pattern"])
        self.assertRegex("secrets.object_storage_access_key", properties["accessKeyFrom"]["pattern"])
        self.assertRegex("secrets.garage_rpc_secret", properties["rpcSecretFrom"]["pattern"])

    def test_provides_complete_object_storage_contract(self) -> None:
        provided = next(
            item
            for item in self.module["spec"]["provides"]
            if item["name"] == "object-storage"
        )
        self.assertEqual(provided["contract"]["kind"], "object-storage")
        self.assertEqual(
            set(provided["contract"]["spec"]),
            set(self.contract["spec"]["fields"]),
        )
        self.assertEqual(provided["contract"]["spec"]["pathStyle"], True)
        self.assertEqual(provided["contract"]["spec"]["tls"], False)

    def test_image_is_digest_pinned_and_data_volumes_are_persistent(self) -> None:
        self.assertRegex(
            self.service["image"],
            r"^dxflrs/garage:v2\.5\.0@sha256:[0-9a-f]{64}$",
        )
        self.assertTrue(self.service["read_only"])
        self.assertEqual(self.service["cap_drop"], ["ALL"])
        targets = {
            volume.get("target")
            for volume in self.service["volumes"]
            if isinstance(volume, dict)
        }
        self.assertIn("/etc/garage.toml", targets)
        self.assertTrue(
            any(
                isinstance(volume, str) and volume.endswith(":/var/lib/garage/data")
                for volume in self.service["volumes"]
            )
        )
        self.assertTrue(
            any(
                isinstance(volume, str) and volume.endswith(":/var/lib/garage/meta")
                for volume in self.service["volumes"]
            )
        )

    def test_host_port_is_loopback_only_and_admin_api_is_not_published(self) -> None:
        self.assertIn("127.0.0.1:${config.port}:3900", self.service["ports"])
        self.assertEqual(
            self.service["healthcheck"]["test"],
            ["CMD", "/garage", "status"],
        )
        config_text = (
            MODULE_PATH.parent / "garage.toml"
        ).read_text(encoding="utf-8")
        self.assertIn('api_bind_addr = "127.0.0.1:3903"', config_text)
        self.assertNotIn("3903:", "\n".join(self.service["ports"]))

    def test_profile_validation_and_render_keep_credentials_as_placeholders(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            profile_path = _write_profile(tmp, _garage_profile())
            with patch.dict(
                os.environ,
                {
                    "CDS_MODULE_PATH": str(REPO_ROOT / "modules"),
                    "CDS_OBJECT_STORAGE_ACCESS_KEY": "GK0123456789abcdef0123456789abcdef",
                    "CDS_OBJECT_STORAGE_SECRET_KEY": "a" * 64,
                    "CDS_GARAGE_RPC_SECRET": "b" * 64,
                },
            ):
                diagnostics = validate_profile(str(profile_path))
                self.assertEqual(
                    [diagnostic.format() for diagnostic in diagnostics if diagnostic.level == "error"],
                    [],
                )
                plan, plan_diagnostics = build_plan(str(profile_path))
                self.assertIsNotNone(plan)
                self.assertEqual(
                    [diagnostic.format() for diagnostic in plan_diagnostics if diagnostic.level == "error"],
                    [],
                )
                assert plan is not None
                provided_contract = plan["modules"][0]["provides"]["object-storage"]
                self.assertEqual(provided_contract["spec"]["bucket"], "cds-data")
                self.assertEqual(provided_contract["spec"]["region"], "garage")
                self.assertEqual(
                    provided_contract["spec"]["accessKey"],
                    "${CDS_OBJECT_STORAGE_ACCESS_KEY}",
                )
                self.assertEqual(
                    provided_contract["spec"]["secretKey"],
                    "${CDS_OBJECT_STORAGE_SECRET_KEY}",
                )
                compose_text, render_diagnostics = render_compose(plan)

        self.assertEqual(
            [diagnostic.format() for diagnostic in render_diagnostics if diagnostic.level == "error"],
            [],
        )
        compose = yaml.safe_load(compose_text)
        # The contract endpoint host must be a DNS name Compose actually
        # creates: the instance id, which equals the module's service name.
        self.assertIn("garage", compose["services"])
        self.assertEqual(provided_contract["spec"]["endpoint"], "http://garage:3900")
        garage = compose["services"]["garage"]
        self.assertEqual(
            garage["environment"]["GARAGE_DEFAULT_ACCESS_KEY"],
            "${CDS_OBJECT_STORAGE_ACCESS_KEY}",
        )
        self.assertEqual(
            garage["environment"]["GARAGE_DEFAULT_SECRET_KEY"],
            "${CDS_OBJECT_STORAGE_SECRET_KEY}",
        )
        self.assertEqual(
            garage["environment"]["GARAGE_RPC_SECRET"],
            "${CDS_GARAGE_RPC_SECRET}",
        )
        self.assertNotIn("GK0123456789abcdef0123456789abcdef", compose_text)
        self.assertNotIn("a" * 64, compose_text)
        self.assertNotIn("b" * 64, compose_text)

    def test_contract_endpoint_keeps_container_port_when_host_port_is_overridden(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            profile_path = _write_profile(
                tmp,
                _garage_profile(
                    config={
                        "bucket": "cds-data",
                        "accessKeyFrom": "secrets.object_storage_access_key",
                        "secretKeyFrom": "secrets.object_storage_secret_key",
                        "rpcSecretFrom": "secrets.garage_rpc_secret",
                        "port": 8901,
                    }
                ),
            )
            with patch.dict(
                os.environ,
                {
                    "CDS_MODULE_PATH": str(REPO_ROOT / "modules"),
                    "CDS_OBJECT_STORAGE_ACCESS_KEY": "GK0123456789abcdef0123456789abcdef",
                    "CDS_OBJECT_STORAGE_SECRET_KEY": "a" * 64,
                    "CDS_GARAGE_RPC_SECRET": "b" * 64,
                },
            ):
                plan, plan_diagnostics = build_plan(str(profile_path))
                assert plan is not None
                self.assertEqual(
                    [diagnostic.format() for diagnostic in plan_diagnostics if diagnostic.level == "error"],
                    [],
                )
                compose_text, render_diagnostics = render_compose(plan)

        self.assertEqual(
            [diagnostic.format() for diagnostic in render_diagnostics if diagnostic.level == "error"],
            [],
        )
        provided_contract = plan["modules"][0]["provides"]["object-storage"]
        # The in-network endpoint must not follow the host loopback override:
        # the S3 API keeps listening on the fixed container port.
        self.assertEqual(provided_contract["spec"]["endpoint"], "http://garage:3900")
        compose = yaml.safe_load(compose_text)
        self.assertEqual(compose["services"]["garage"]["ports"], ["127.0.0.1:8901:3900"])

    def test_consumed_object_storage_contract_stays_placeholder_for_consumers(self) -> None:
        provider = {
            "id": "garage",
            "config": {
                "bucket": "cds-data",
                "accessKeyFrom": "secrets.object_storage_access_key",
                "secretKeyFrom": "secrets.object_storage_secret_key",
                "rpcSecretFrom": "secrets.garage_rpc_secret",
            },
            "module": self.module,
        }
        consumer = {
            "id": "s3-client",
            "config": {"objectStorage": {"contractRef": "garage.object-storage"}},
            "module": {
                "spec": {
                    "consumes": [
                        {
                            "name": "object-storage",
                            "contract": {"kind": "object-storage"},
                            "required": True,
                            "mappedFrom": "spec.config.objectStorage",
                        }
                    ]
                }
            },
        }
        diagnostics = []
        provided = resolve_provided_contracts(provider, _TEST_SECRETS)
        consumed = resolve_consumed_contracts(
            consumer,
            {"garage": provider},
            {"garage": provided},
            diagnostics,
            _TEST_SECRETS,
        )
        self.assertEqual(
            [diagnostic.format() for diagnostic in diagnostics if diagnostic.level == "error"],
            [],
        )
        spec = consumed["object-storage"]["contract"]["spec"]
        self.assertEqual(spec["endpoint"], "http://garage:3900")
        self.assertEqual(spec["bucket"], "cds-data")
        self.assertEqual(spec["region"], "garage")
        self.assertEqual(spec["accessKey"], "${CDS_OBJECT_STORAGE_ACCESS_KEY}")
        self.assertEqual(spec["secretKey"], "${CDS_OBJECT_STORAGE_SECRET_KEY}")
        self.assertEqual(spec["pathStyle"], True)
        self.assertEqual(spec["tls"], False)

    def test_non_local_profile_using_garage_is_flagged_by_security_rules(self) -> None:
        for environment, expected in (("local", []), ("staging", ["garage"]), ("production", ["garage"])):
            with self.subTest(environment=environment):
                with tempfile.TemporaryDirectory() as tmp:
                    profile_path = _write_profile(tmp, _garage_profile(environment=environment))
                    env_path = Path(tmp) / ".env"
                    env_path.write_text(
                        "CDS_OBJECT_STORAGE_ACCESS_KEY=GK0123456789abcdef0123456789abcdef\n"
                        "CDS_OBJECT_STORAGE_SECRET_KEY=" + "c" * 64 + "\n"
                        "CDS_GARAGE_RPC_SECRET=" + "d" * 64 + "\n",
                        encoding="utf-8",
                    )
                    with patch.dict(os.environ, {"CDS_MODULE_PATH": str(REPO_ROOT / "modules")}):
                        findings, _diagnostics = run_security_validation(
                            profile_path,
                            RULE_SCHEMA_PATH,
                            RULE_SET_PATH,
                            env_file=str(env_path),
                        )
                    hits = [
                        finding["module"]
                        for finding in findings
                        if finding["rule_id"] == "CDS-SEC-073"
                    ]
                    self.assertEqual(hits, expected)


if __name__ == "__main__":
    unittest.main()
