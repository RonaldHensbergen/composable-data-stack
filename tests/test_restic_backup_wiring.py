"""
Committed restic backup wiring test (issue #668).

Proves, via the real validate/plan/render pipeline (not an uncommitted
scratch profile), that the restic module can protect both
persistence patterns a file-backed module exposes through the backup-source
contract:

- a bind-kind source (the duckdb module's shared host directory) mounts
  read-only into the backup sidecar at its slot path, with the restore
  service mounting the same directory read-write;
- a volume-kind source (the dlt module's state volume) mounts cross-module
  by its fully rendered "<module-id>-<volume>" Compose name without being
  re-namespaced with the restic module's id;
- unbound source slots leave no literal ${bindings.*} expression in the
  rendered Compose;
- the repository encryption key stays an unresolved ${CDS_*} placeholder
  for Docker Compose to fill in at runtime (per repo convention: secrets
  never resolve into plans or rendered Compose);
- the production security rule pass raises no finding against the restic
  services.
"""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import yaml

from cli.planner import build_plan
from cli.renderer import render_compose
from cli.security import PrecomputedRender, run_security_validation
from cli.validator import validate_profile

_REPO_ROOT = Path(__file__).resolve().parent.parent
_RULE_SCHEMA_PATH = _REPO_ROOT / "cli" / "resources" / "rule-schema.json"
_RULE_SET_PATH = _REPO_ROOT / "cli" / "resources" / "rule-set.json"


class ResticBackupWiringTest(unittest.TestCase):
    """Wires the real duckdb, dlt, postgres, and restic modules together."""

    def _write_profile(self, profile_dir: Path, environment: str = "production") -> Path:
        profile = {
            "apiVersion": "cds/v1alpha1",
            "kind": "Profile",
            "metadata": {
                "name": "restic-backup-wiring",
                "environment": environment,
            },
            "spec": {
                "runtime": {"type": "docker-compose"},
                "modules": [
                    {
                        "id": "postgres",
                        "source": "modules/warehouse/postgres",
                        "version": "0.1.0",
                        "enabled": True,
                        "config": {
                            "database": "analytics",
                            "username": "analytics",
                            "passwordFrom": "secrets.analytics_db_password",
                            "superuserPasswordFrom": "secrets.postgres_superuser_password",
                            "port": 5432,
                            "authentication": {"hostAuth": "scram-sha-256"},
                        },
                    },
                    {
                        "id": "duckdb",
                        "source": "modules-experimental/warehouse/duckdb",
                        "version": "0.1.0",
                        "enabled": True,
                        "config": {"hostDirectory": "./data/duckdb"},
                    },
                    {
                        "id": "dlt",
                        "source": "modules-experimental/ingestion/dlt",
                        "version": "0.1.0",
                        "enabled": True,
                        "dependsOn": ["postgres"],
                        "config": {
                            "destinationDatabase": {"contractRef": "postgres.sql-database"},
                        },
                    },
                    {
                        "id": "restic",
                        "source": "modules/backup/restic",
                        "version": "0.1.0",
                        "enabled": True,
                        "config": {
                            "sources": {
                                "a": {"contractRef": "duckdb.backup-source"},
                                "b": {"contractRef": "dlt.backup-source"},
                            },
                            "encryptionKeyFrom": "secrets.backup_encryption_key",
                        },
                    },
                ],
                "secrets": {
                    "provider": {"type": "env"},
                    "values": {
                        "analytics_db_password": {
                            "env": "CDS_ANALYTICS_DB_PASSWORD",
                            "required": True,
                        },
                        "postgres_superuser_password": {
                            "env": "CDS_POSTGRES_SUPERUSER_PASSWORD",
                            "required": True,
                        },
                        "backup_encryption_key": {
                            "env": "CDS_BACKUP_ENCRYPTION_KEY",
                            "required": True,
                        },
                    },
                },
                "backup": {
                    "target": {"contractRef": "restic.backup-target"},
                    "encryption": {"keyFrom": "secrets.backup_encryption_key"},
                    "policies": [
                        {
                            "workloadClass": "warehouse",
                            "retentionDays": 30,
                            "rpoMinutes": 60,
                            "rtoMinutes": 240,
                            "restoreVerification": {"frequencyDays": 30},
                        },
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

        profile_file = profile_dir / "profile.yaml"
        profile_file.write_text(yaml.safe_dump(profile), encoding="utf-8")
        return profile_file

    def test_backup_sources_mount_into_backup_and_restore_services(self):
        with tempfile.TemporaryDirectory() as root:
            profile_dir = Path(root)
            profile_file = self._write_profile(profile_dir)

            env_file = profile_dir / ".env"
            env_file.write_text(
                "CDS_ANALYTICS_DB_PASSWORD=analytics_testpass\n"
                "CDS_POSTGRES_SUPERUSER_PASSWORD=superuser_testpass\n"
                "CDS_BACKUP_ENCRYPTION_KEY=restic_testkey\n",
                encoding="utf-8",
            )

            with mock.patch.dict(
                "os.environ", {"CDS_MODULE_PATH": str(_REPO_ROOT)}, clear=False
            ):
                diagnostics = validate_profile(str(profile_file))
                self.assertEqual(
                    [d for d in diagnostics if d.level == "error"],
                    [],
                    msg=f"unexpected validation errors: {diagnostics}",
                )

                plan, plan_diags = build_plan(str(profile_file), env_file=str(env_file))
                self.assertIsNotNone(plan)
                self.assertEqual(
                    [d for d in plan_diags if d.level == "error"],
                    [],
                    msg=f"unexpected plan errors: {plan_diags}",
                )

                # The restic instance's backup-target advertisement carries
                # the retention configured on the module.
                restic = next(m for m in plan["modules"] if m["id"] == "restic")
                backup_target = restic["provides"]["backup-target"]
                self.assertEqual(backup_target["kind"], "backup-target")
                self.assertTrue(backup_target["spec"]["encrypted"])
                self.assertTrue(backup_target["spec"]["supportsRestoreVerification"])
                self.assertEqual(backup_target["spec"]["maxRetentionDays"], 30)

                output, render_diags = render_compose(plan, env_file=str(env_file))
                self.assertEqual(
                    [d for d in render_diags if d.level == "error"],
                    [],
                    msg=f"unexpected render errors: {render_diags}",
                )
                self.assertNotIn("${bindings.", output)

                compose = yaml.safe_load(output)
                backup_volumes = compose["services"]["restic-backup"]["volumes"]
                restore_volumes = compose["services"]["restic-restore"]["volumes"]

                # Slot a: the duckdb bind-kind source, read-only for backup.
                # The host path is rewritten relative to the rendered
                # Compose file's directory, so assert by suffix.
                slot_a = next(v for v in backup_volumes if v.get("target") == "/backup/a")
                self.assertEqual(slot_a["type"], "bind")
                self.assertTrue(slot_a["source"].endswith("data/duckdb"))
                self.assertTrue(slot_a["read_only"])
                # Slot b: the dlt volume-kind source, mounted cross-module by
                # the producer's fully rendered volume name (not re-namespaced
                # with the restic module id), read-only for backup.
                self.assertIn(
                    {
                        "type": "volume",
                        "source": "dlt-dlt-state",
                        "target": "/backup/b",
                        "read_only": True,
                    },
                    backup_volumes,
                )
                # Unbound slot c leaves no mount behind.
                self.assertFalse([v for v in backup_volumes if v.get("target") == "/backup/c"])
                self.assertFalse([v for v in restore_volumes if v.get("target") == "/backup/c"])

                # The restore service mounts the same slots (read-write) plus
                # the encrypted repository, read-only.
                restore_slot_a = next(v for v in restore_volumes if v.get("target") == "/backup/a")
                self.assertEqual(restore_slot_a["type"], "bind")
                self.assertTrue(restore_slot_a["source"].endswith("data/duckdb"))
                self.assertNotIn("read_only", restore_slot_a)
                self.assertIn(
                    {"type": "volume", "source": "dlt-dlt-state", "target": "/backup/b"},
                    restore_volumes,
                )
                # Read-write for the repository as well: restic writes (and
                # removes) a lock file in the repository for every
                # operation, including restore.
                self.assertIn(
                    {
                        "type": "volume",
                        "source": "restic-restic-repo",
                        "target": "/var/lib/restic-repo",
                    },
                    restore_volumes,
                )

                # The repository encryption key stays a runtime placeholder.
                self.assertEqual(
                    compose["services"]["restic-backup"]["environment"]["RESTIC_PASSWORD"],
                    "${CDS_BACKUP_ENCRYPTION_KEY}",
                )
                self.assertEqual(
                    compose["services"]["restic-restore"]["environment"]["RESTIC_PASSWORD"],
                    "${CDS_BACKUP_ENCRYPTION_KEY}",
                )

                # The repository volume exists in the top-level volumes map.
                self.assertIn("restic-restic-repo", compose["volumes"])

                # The production security rule pass raises no unexpected
                # finding against the backup services (non-root user,
                # dropped capabilities, read-only filesystem, ...). The one
                # accepted finding is CDS-SEC-013 (medium): the repository
                # encryption key is deliberately shared between the backup
                # and restore services of the same module -- they protect
                # the same restic repository, exactly like the shipped
                # postgres backup profile reuses its key across the
                # postgres-backup and postgres-restore services.
                findings, _sec_diags = run_security_validation(
                    profile_file,
                    _RULE_SCHEMA_PATH,
                    _RULE_SET_PATH,
                    env_file=str(env_file),
                    precomputed_render=PrecomputedRender(
                        plan=plan, rendered_compose_yaml=output
                    ),
                )
                restic_findings = [
                    f for f in findings if f.get("module") == "restic"
                ]
                unexpected = [
                    f
                    for f in restic_findings
                    if not (
                        f.get("rule_id") == "CDS-SEC-013"
                        and f.get("path") == "encryptionKeyFrom"
                    )
                ]
                self.assertEqual(unexpected, [], msg=f"security findings: {unexpected}")

    def test_restitic_module_outside_production_reports_e143(self):
        with tempfile.TemporaryDirectory() as root:
            profile_dir = Path(root)
            profile_file = self._write_profile(profile_dir, environment="local")

            with mock.patch.dict(
                "os.environ", {"CDS_MODULE_PATH": str(_REPO_ROOT)}, clear=False
            ):
                diagnostics = validate_profile(str(profile_file))

            codes = [d.code for d in diagnostics if d.level == "error"]
            self.assertIn("E143", codes)
