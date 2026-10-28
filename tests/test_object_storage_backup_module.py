import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml
from jsonschema import Draft202012Validator

from cli.planner import build_plan
from cli.renderer import render_compose
from cli.security import PrecomputedRender, run_security_validation
from cli.validator import validate_profile

REPO_ROOT = Path(__file__).resolve().parent.parent
MODULE_PATH = REPO_ROOT / "modules-experimental" / "backup" / "object-storage" / "module.yaml"
_RULE_SCHEMA_PATH = REPO_ROOT / "cli" / "resources" / "rule-schema.json"
_RULE_SET_PATH = REPO_ROOT / "cli" / "resources" / "rule-set.json"

_ENV = {
    "CDS_OBJECT_STORAGE_ACCESS_KEY": "GK0123456789abcdef0123456789abcdef",
    "CDS_OBJECT_STORAGE_SECRET_KEY": "a" * 64,
    "CDS_GARAGE_RPC_SECRET": "b" * 64,
    "CDS_BACKUP_ACCESS_KEY": "destination-access",
    "CDS_BACKUP_SECRET_KEY": "destination-secret",
    "CDS_BACKUP_ENCRYPTION_KEY": "profile-backup-policy-key",
}


def _profile() -> dict:
    secret_values = {
        "object_storage_access_key": "CDS_OBJECT_STORAGE_ACCESS_KEY",
        "object_storage_secret_key": "CDS_OBJECT_STORAGE_SECRET_KEY",
        "garage_rpc_secret": "CDS_GARAGE_RPC_SECRET",
        "backup_access_key": "CDS_BACKUP_ACCESS_KEY",
        "backup_secret_key": "CDS_BACKUP_SECRET_KEY",
        "backup_encryption_key": "CDS_BACKUP_ENCRYPTION_KEY",
    }
    return {
        "apiVersion": "cds/v1alpha1",
        "kind": "Profile",
        "metadata": {"name": "object-storage-backup-test", "environment": "production"},
        "spec": {
            "runtime": {"type": "docker-compose"},
            "modules": [
                {
                    "id": "garage",
                    "source": "../../modules/storage/garage",
                    "version": "0.1.0",
                    "enabled": True,
                    "config": {
                        "bucket": "cds-data",
                        "accessKeyFrom": "secrets.object_storage_access_key",
                        "secretKeyFrom": "secrets.object_storage_secret_key",
                        "rpcSecretFrom": "secrets.garage_rpc_secret",
                    },
                },
                {
                    "id": "object-storage-backup",
                    "source": "../../modules-experimental/backup/object-storage",
                    "version": "0.1.0",
                    "enabled": True,
                    "dependsOn": ["garage"],
                    "config": {
                        "sourceStorage": {
                            "contractRef": "garage.object-storage",
                        },
                        "destination": {
                            "endpoint": "https://s3.example.net",
                            "bucket": "cds-backups",
                            "region": "us-east-1",
                            "accessKeyFrom": "secrets.backup_access_key",
                            "secretKeyFrom": "secrets.backup_secret_key",
                        },
                    },
                },
            ],
            "secrets": {
                "provider": {"type": "env"},
                "values": {
                    alias: {"env": name, "required": True}
                    for alias, name in secret_values.items()
                },
            },
            # Production profiles must declare a backup policy per workload
            # class (E133). The storage category is deliberately unmapped,
            # so only the always-required "configuration" class applies.
            "backup": {
                "encryption": {"keyFrom": "secrets.backup_encryption_key"},
                "policies": [
                    {
                        "workloadClass": "configuration",
                        "retentionDays": 90,
                        "rpoMinutes": 1440,
                        "rtoMinutes": 120,
                        "restoreVerification": {"frequencyDays": 90},
                    },
                ],
            },
        },
    }


class ObjectStorageBackupModuleTest(unittest.TestCase):
    def setUp(self) -> None:
        self.module = yaml.safe_load(MODULE_PATH.read_text(encoding="utf-8"))
        self.service = self.module["spec"]["implementation"]["compose"]["services"]["rclone"]

    def test_module_config_is_valid_json_schema(self) -> None:
        Draft202012Validator.check_schema(self.module["spec"]["configSchema"])

    def test_snapshot_copy_is_timestamped_immutable_and_non_deleting(self) -> None:
        command = "\n".join(self.service["command"])
        self.assertIn("Source and destination must not be the same endpoint and bucket", command)
        self.assertIn('date -u +%Y%m%dT%H%M%SZ', command)
        self.assertIn("rclone --config /dev/null copy", command)
        self.assertIn("--immutable", command)
        self.assertNotIn("rclone sync", command)
        self.assertEqual(self.service["restart"], "unless-stopped")

    def test_image_is_digest_pinned_and_service_is_hardened(self) -> None:
        self.assertRegex(
            self.service["image"],
            r"^rclone/rclone:1\.74\.0@sha256:[0-9a-f]{64}$",
        )
        self.assertTrue(self.service["read_only"])
        self.assertEqual(self.service["cap_drop"], ["ALL"])
        self.assertEqual(self.service["user"], "1000:1000")
        self.assertIn("no-new-privileges:true", self.service["security_opt"])
        self.assertFalse(self.module["metadata"]["productionSuitable"])

    def test_profile_validation_and_render_keep_both_credentials_as_placeholders(self) -> None:
        with tempfile.TemporaryDirectory(dir=REPO_ROOT / "tests") as tmp:
            profile_path = Path(tmp) / "profile.yaml"
            profile_path.write_text(yaml.safe_dump(_profile()), encoding="utf-8")
            with patch.dict(os.environ, _ENV):
                diagnostics = validate_profile(str(profile_path))
                self.assertEqual(
                    [d.format() for d in diagnostics if d.level == "error"],
                    [],
                )
                plan, plan_diagnostics = build_plan(str(profile_path))
                self.assertIsNotNone(plan)
                self.assertEqual(
                    [d.format() for d in plan_diagnostics if d.level == "error"],
                    [],
                )
                assert plan is not None
                compose_text, render_diagnostics = render_compose(plan)

        self.assertEqual(
            [d.format() for d in render_diagnostics if d.level == "error"],
            [],
        )
        compose = yaml.safe_load(compose_text)
        service = compose["services"]["object-storage-backup-rclone"]
        self.assertEqual(
            service["environment"]["RCLONE_CONFIG_SOURCE_ACCESS_KEY_ID"],
            "${CDS_OBJECT_STORAGE_ACCESS_KEY}",
        )
        self.assertEqual(
            service["environment"]["RCLONE_CONFIG_SOURCE_SECRET_ACCESS_KEY"],
            "${CDS_OBJECT_STORAGE_SECRET_KEY}",
        )
        self.assertEqual(
            service["environment"]["RCLONE_CONFIG_DESTINATION_ACCESS_KEY_ID"],
            "${CDS_BACKUP_ACCESS_KEY}",
        )
        self.assertEqual(
            service["environment"]["RCLONE_CONFIG_DESTINATION_SECRET_ACCESS_KEY"],
            "${CDS_BACKUP_SECRET_KEY}",
        )
        self.assertEqual(
            service["environment"]["RCLONE_CONFIG_SOURCE_ENDPOINT"],
            "http://garage:3900",
        )
        self.assertEqual(service["environment"]["RCLONE_SOURCE_BUCKET"], "cds-data")
        self.assertIn("garage", service["depends_on"])
        self.assertNotIn("destination-secret", compose_text)
        self.assertNotIn("GK0123456789abcdef0123456789abcdef", compose_text)
        self.assertNotIn("a" * 64, compose_text)
        self.assertNotIn("b" * 64, compose_text)

    def test_source_contract_binding_is_required(self) -> None:
        with tempfile.TemporaryDirectory(dir=REPO_ROOT / "tests") as tmp:
            profile = _profile()
            profile["spec"]["modules"][1]["config"]["sourceStorage"] = {}
            profile_path = Path(tmp) / "profile.yaml"
            profile_path.write_text(yaml.safe_dump(profile), encoding="utf-8")
            with patch.dict(os.environ, _ENV):
                diagnostics = validate_profile(str(profile_path))

        self.assertTrue(any(d.level == "error" for d in diagnostics))

    def test_module_outside_production_reports_e143(self) -> None:
        with tempfile.TemporaryDirectory(dir=REPO_ROOT / "tests") as tmp:
            profile = _profile()
            profile["metadata"]["environment"] = "local"
            profile_path = Path(tmp) / "profile.yaml"
            profile_path.write_text(yaml.safe_dump(profile), encoding="utf-8")
            with patch.dict(os.environ, _ENV):
                diagnostics = validate_profile(str(profile_path))

        self.assertTrue(
            any(d.level == "error" and d.code == "E143" for d in diagnostics),
            msg=[d.format() for d in diagnostics],
        )

    def test_production_security_pass_raises_no_finding_against_the_module(self) -> None:
        with tempfile.TemporaryDirectory(dir=REPO_ROOT / "tests") as tmp:
            profile_path = Path(tmp) / "profile.yaml"
            profile_path.write_text(yaml.safe_dump(_profile()), encoding="utf-8")
            env_file = Path(tmp) / ".env"
            env_file.write_text(
                "\n".join(f"{name}={value}" for name, value in _ENV.items()) + "\n",
                encoding="utf-8",
            )
            with patch.dict(os.environ, _ENV):
                plan, _plan_diagnostics = build_plan(str(profile_path))
                assert plan is not None
                output, _render_diagnostics = render_compose(plan)
                findings, _sec_diags = run_security_validation(
                    profile_path,
                    _RULE_SCHEMA_PATH,
                    _RULE_SET_PATH,
                    env_file=str(env_file),
                    precomputed_render=PrecomputedRender(
                        plan=plan, rendered_compose_yaml=output
                    ),
                )

        module_findings = [
            f for f in findings if f.get("module") == "object-storage-backup"
        ]
        # The one accepted finding is CDS-SEC-073 (high): the module is
        # honestly marked productionSuitable: false (plaintext snapshot
        # copies, no automated restore verification), while E143 makes
        # backup modules render only in production. Production use therefore
        # requires an explicit, scoped runtime-policy waiver for this rule.
        unexpected = [
            f
            for f in module_findings
            if not (
                f.get("rule_id") == "CDS-SEC-073"
                and f.get("path") == "_module.productionSuitable"
            )
        ]
        self.assertEqual(unexpected, [], msg=f"security findings: {unexpected}")


if __name__ == "__main__":
    unittest.main()
