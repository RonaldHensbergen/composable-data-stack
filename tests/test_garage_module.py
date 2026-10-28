import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml
from jsonschema import Draft202012Validator

from cli.planner import build_plan
from cli.renderer import render_compose
from cli.validator import validate_profile

REPO_ROOT = Path(__file__).resolve().parent.parent
MODULE_PATH = REPO_ROOT / "modules" / "storage" / "garage" / "module.yaml"
CONTRACT_PATH = REPO_ROOT / "shared" / "contracts" / "object-storage.yaml"


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
        profile = {
            "apiVersion": "cds/v1alpha1",
            "kind": "Profile",
            "metadata": {"name": "garage-test", "environment": "local"},
            "spec": {
                "runtime": {"type": "docker-compose"},
                "modules": [
                    {
                        "id": "object-storage",
                        "source": "storage/garage",
                        "version": "0.1.0",
                        "enabled": True,
                        "config": {
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
        with tempfile.TemporaryDirectory() as tmp:
            profile_path = Path(tmp) / "profile.yaml"
            profile_path.write_text(yaml.safe_dump(profile), encoding="utf-8")
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
        garage = compose["services"]["object-storage-garage"]
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


if __name__ == "__main__":
    unittest.main()
