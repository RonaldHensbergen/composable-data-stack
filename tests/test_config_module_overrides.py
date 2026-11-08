"""
Coverage for `cds config set module.<id>.config.<path> <value>` (issue #1220):
persisted module-config overrides stored in the gitignored .cds/config.json,
deep-merged into the matching spec.modules[] entry of the resolved profile at
load time with precedence CLI flag > persisted config > environment overlay >
base profile.yaml. Uses a real, minimal profile+module fixture on disk, the
same style as tests/test_environment_cli.py.
"""

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from cli.main import main
from cli.overlay import resolve_profile


class ModuleOverrideFixture(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.profile_dir = self.root / "profiles" / "analytics"
        self.profile_dir.mkdir(parents=True)
        self.modules_dir = self.root / "modules" / "warehouse" / "postgres"
        self.modules_dir.mkdir(parents=True)

        (self.modules_dir / "module.yaml").write_text(
            yaml.safe_dump(
                {
                    "apiVersion": "cds/v1alpha1",
                    "kind": "Module",
                    "metadata": {
                        "name": "postgres",
                        "category": "warehouse",
                        "version": "0.1.0",
                    },
                    "spec": {
                        "runtime": {
                            "type": "container",
                            "service": {
                                "name": "postgres",
                                "ports": [
                                    {
                                        "name": "db",
                                        "containerPort": 5432,
                                        "protocol": "TCP",
                                    }
                                ],
                            },
                        },
                        "configSchema": {
                            "type": "object",
                            "additionalProperties": True,
                        },
                        "implementation": {
                            "kind": "docker-compose",
                            "compose": {"services": {}},
                        },
                    },
                }
            )
        )

        self.base_profile = {
            "apiVersion": "cds/v1alpha1",
            "kind": "Profile",
            "metadata": {"name": "analytics", "environment": "local"},
            "spec": {
                "runtime": {"type": "docker-compose"},
                "modules": [
                    {
                        "id": "db",
                        "source": "warehouse/postgres",
                        "version": "0.1.0",
                        "enabled": True,
                        "config": {
                            "replicas": 1,
                            "debug": False,
                            "hostPath": "./workdirs/db",
                            "nested": {"inner": "base"},
                        },
                    }
                ],
            },
        }
        self.profile_path = self.profile_dir / "profile.yaml"
        self.profile_path.write_text(yaml.safe_dump(self.base_profile))

        self.config_path = self.root / ".cds" / "config.json"
        self.config_path.parent.mkdir()
        self.config_path.write_text(json.dumps({"profile": str(self.profile_path)}))
        self.shared_path = self.root / "cds.shared.json"

        self.env_vars = {
            "CDS_PROFILE_PATH": str(self.profile_dir),
            "CDS_MODULE_PATH": str(self.modules_dir.parent.parent),
            "CDS_CONFIG_PATH": str(self.config_path),
            "CDS_SHARED_CONFIG_PATH": str(self.shared_path),
        }

    def _write_overlay(self, name, content):
        env_dir = self.profile_dir / "environments"
        env_dir.mkdir(exist_ok=True)
        (env_dir / f"{name}.yaml").write_text(yaml.safe_dump(content))

    def _write_config(self, data):
        self.config_path.write_text(json.dumps(data))

    def _read_config(self):
        return json.loads(self.config_path.read_text())

    def _write_shared(self, data):
        self.shared_path.write_text(json.dumps(data))

    def _read_shared(self):
        return json.loads(self.shared_path.read_text())

    def _run(self, argv, extra_env=None):
        stdout = io.StringIO()
        env_vars = {**self.env_vars, **(extra_env or {})}
        with (
            patch.dict(os.environ, env_vars, clear=False),
            patch.object(sys, "argv", argv),
        ):
            with contextlib.redirect_stdout(stdout):
                result = main()
        return result, stdout.getvalue()


class ConfigSetModuleOverrideTest(ModuleOverrideFixture):
    def test_set_persists_nested_override_and_get_reads_it_back(self):
        result, output = self._run(
            [
                "cds",
                "config",
                "set",
                "module.db.config.hostPath",
                "./my-workdirs/db",
            ]
        )
        self.assertEqual(result, 0, output)
        self.assertIn("module.db.config.hostPath", output)
        stored = self._read_config()
        self.assertEqual(
            stored["module"]["db"]["config"]["hostPath"], "./my-workdirs/db"
        )
        # the saved profile key is preserved alongside the module section
        self.assertEqual(stored["profile"], str(self.profile_path))

        result, output = self._run(
            ["cds", "config", "get", "module.db.config.hostPath"]
        )
        self.assertEqual(result, 0, output)
        self.assertIn("./my-workdirs/db", output)

    def test_config_list_shows_module_section(self):
        self._run(["cds", "config", "set", "module.db.config.hostPath", "./w"])
        result, output = self._run(["cds", "config", "list"])
        self.assertEqual(result, 0, output)
        listed = json.loads(output)
        self.assertEqual(listed["module"]["db"]["config"]["hostPath"], "./w")

    def test_unset_removes_leaf_and_prunes_empty_parents(self):
        self._run(["cds", "config", "set", "module.db.config.hostPath", "./w"])
        result, output = self._run(
            ["cds", "config", "unset", "module.db.config.hostPath"]
        )
        self.assertEqual(result, 0, output)
        self.assertIn("Unset", output)
        stored = self._read_config()
        self.assertNotIn("module", stored)
        self.assertEqual(stored["profile"], str(self.profile_path))

        result, output = self._run(
            ["cds", "config", "get", "module.db.config.hostPath"]
        )
        self.assertEqual(result, 1)
        self.assertIn("not set", output)

    def test_unset_of_unset_key_reports_not_set(self):
        result, output = self._run(
            ["cds", "config", "unset", "module.db.config.hostPath"]
        )
        self.assertEqual(result, 0, output)
        self.assertIn("not set", output)

    def test_set_rejects_unknown_module_id_with_e161(self):
        result, output = self._run(
            ["cds", "config", "set", "module.nope.config.hostPath", "./w"]
        )
        self.assertEqual(result, 1)
        self.assertIn("E161", output)
        self.assertIn("Unknown module id 'nope'", output)
        self.assertIn("db", output)  # lists the known ids
        self.assertNotIn("module", self._read_config())

    def test_set_rejects_non_scalar_leaf_with_e161(self):
        result, output = self._run(
            ["cds", "config", "set", "module.db.config.nested", "x"]
        )
        self.assertEqual(result, 1)
        self.assertIn("E161", output)
        self.assertIn("non-scalar", output)

    def test_set_rejects_path_traversing_a_scalar_with_e161(self):
        result, output = self._run(
            ["cds", "config", "set", "module.db.config.hostPath.deeper", "x"]
        )
        self.assertEqual(result, 1)
        self.assertIn("E161", output)

    def test_set_rejects_non_config_module_keys(self):
        # `enabled` is module-level, not config; the key shape is rejected by
        # argparse before any validation runs (argparse exits with code 2).
        with self.assertRaises(SystemExit) as cm:
            self._run(["cds", "config", "set", "module.db.enabled", "false"])
        self.assertEqual(cm.exception.code, 2)
        self.assertNotIn("module", self._read_config())

    def test_set_requires_saved_profile(self):
        self._write_config({})
        result, output = self._run(
            ["cds", "config", "set", "module.db.config.hostPath", "./w"]
        )
        self.assertEqual(result, 1)
        self.assertIn("E161", output)
        self.assertIn("saved default profile", output)

    def test_set_coerces_value_to_existing_type(self):
        result, output = self._run(
            ["cds", "config", "set", "module.db.config.replicas", "5"]
        )
        self.assertEqual(result, 0, output)
        self.assertEqual(self._read_config()["module"]["db"]["config"]["replicas"], 5)

        result, output = self._run(
            ["cds", "config", "set", "module.db.config.debug", "true"]
        )
        self.assertEqual(result, 0, output)
        self.assertIs(self._read_config()["module"]["db"]["config"]["debug"], True)

    def test_set_rejects_wrong_type_for_existing_value(self):
        result, output = self._run(
            ["cds", "config", "set", "module.db.config.replicas", "five"]
        )
        self.assertEqual(result, 1)
        self.assertIn("E161", output)
        result, output = self._run(
            ["cds", "config", "set", "module.db.config.debug", "yes"]
        )
        self.assertEqual(result, 1)
        self.assertIn("E161", output)

    def test_set_rejects_value_matching_a_resolved_secret(self):
        # alias-only rule: the persisted override may carry a "secrets.<alias>"
        # reference, never a resolved secret value.
        result, output = self._run(
            [
                "cds",
                "config",
                "set",
                "module.db.config.password",
                "supersecretvalue123",
            ],
            extra_env={"CDS_DB_PASSWORD": "supersecretvalue123"},
        )
        self.assertEqual(result, 1)
        self.assertIn("E161", output)
        self.assertIn("secrets.<alias>", output)
        self.assertNotIn("module", self._read_config())

        # ...while the alias reference itself is accepted.
        result, output = self._run(
            ["cds", "config", "set", "module.db.config.password", "secrets.db_password"]
        )
        self.assertEqual(result, 0, output)
        self.assertEqual(
            self._read_config()["module"]["db"]["config"]["password"],
            "secrets.db_password",
        )


class PersistedOverrideMergeTest(ModuleOverrideFixture):
    def test_validate_picks_up_override_without_editing_profile(self):
        result, output = self._run(
            ["cds", "config", "set", "module.db.config.hostPath", "./my-workdirs/db"]
        )
        self.assertEqual(result, 0, output)
        # profile.yaml on disk is untouched
        on_disk = yaml.safe_load(self.profile_path.read_text())
        self.assertEqual(
            on_disk["spec"]["modules"][0]["config"]["hostPath"], "./workdirs/db"
        )
        result, output = self._run(["cds", "validate"])
        self.assertEqual(result, 0, output)
        self.assertIn("valid", output.lower())

    def test_plan_applies_override_without_environment(self):
        self._run(["cds", "config", "set", "module.db.config.hostPath", "./my-w"])
        result, output = self._run(["cds", "plan"])
        self.assertEqual(result, 0, output)
        plan = json.loads(output)
        self.assertEqual(plan["modules"][0]["config"]["hostPath"], "./my-w")
        self.assertEqual(
            plan["provenance"]["spec.modules[db].config.hostPath"],
            str(self.config_path),
        )

    def test_render_picks_up_override(self):
        self._run(["cds", "config", "set", "module.db.config.hostPath", "./my-w"])
        result, output = self._run(
            ["cds", "render", "--output", str(self.root / "compose.yml")]
        )
        self.assertEqual(result, 0, output)

    def test_persisted_override_beats_environment_overlay_and_base(self):
        # base: replicas 1; prod overlay: replicas 3; persisted override: 5.
        self._write_overlay(
            "prod", {"spec": {"modules": [{"id": "db", "config": {"replicas": 3}}]}}
        )
        result, output = self._run(["cds", "plan", "--environment", "prod"])
        self.assertEqual(result, 0, output)
        self.assertEqual(json.loads(output)["modules"][0]["config"]["replicas"], 3)

        self._run(["cds", "config", "set", "module.db.config.replicas", "5"])
        result, output = self._run(["cds", "plan", "--environment", "prod"])
        self.assertEqual(result, 0, output)
        self.assertEqual(json.loads(output)["modules"][0]["config"]["replicas"], 5)

        # and the override also applies with no environment selected
        result, output = self._run(["cds", "plan"])
        self.assertEqual(result, 0, output)
        self.assertEqual(json.loads(output)["modules"][0]["config"]["replicas"], 5)

    def test_resolve_profile_unit_precedence(self):
        self._write_overlay(
            "prod", {"spec": {"modules": [{"id": "db", "config": {"replicas": 3}}]}}
        )
        self._write_config(
            {
                "profile": str(self.profile_path),
                "module": {"db": {"config": {"replicas": 7}}},
            }
        )
        with patch.dict(os.environ, self.env_vars, clear=False):
            profile, provenance, diags = resolve_profile(str(self.profile_path), "prod")
        self.assertIsNotNone(profile, [d.format() for d in diags])
        module = profile["spec"]["modules"][0]
        self.assertEqual(module["config"]["replicas"], 7)
        self.assertEqual(
            provenance["spec.modules[db].config.replicas"], str(self.config_path)
        )
        # the rest of the overlay still merges underneath
        self.assertEqual(module["config"]["hostPath"], "./workdirs/db")

        # apply_persisted=False resolves the profile as committed
        with patch.dict(os.environ, self.env_vars, clear=False):
            profile, _, _ = resolve_profile(
                str(self.profile_path), "prod", apply_persisted=False
            )
        self.assertEqual(profile["spec"]["modules"][0]["config"]["replicas"], 3)

    def test_load_time_unknown_module_id_fails_validate_with_e161(self):
        self._write_config(
            {
                "profile": str(self.profile_path),
                "module": {"ghost": {"config": {"hostPath": "./w"}}},
            }
        )
        result, output = self._run(["cds", "validate"])
        self.assertEqual(result, 1)
        self.assertIn("E161", output)
        self.assertIn("ghost", output)

    def test_load_time_non_scalar_override_fails_validate_with_e161(self):
        self._write_config(
            {
                "profile": str(self.profile_path),
                "module": {"db": {"config": {"nested": "scalar-instead-of-map"}}},
            }
        )
        result, output = self._run(["cds", "validate"])
        self.assertEqual(result, 1)
        self.assertIn("E161", output)
        self.assertIn("non-scalar", output)

    def test_no_overrides_leaves_resolution_untouched(self):
        with patch.dict(os.environ, self.env_vars, clear=False):
            profile, provenance, diags = resolve_profile(str(self.profile_path))
        self.assertIsNotNone(profile, [d.format() for d in diags])
        self.assertEqual(profile["spec"]["modules"][0]["config"]["replicas"], 1)
        self.assertNotIn("spec.modules[db].config.replicas", provenance)


class DiffShowsPersistedOverridesTest(ModuleOverrideFixture):
    def test_diff_shows_persisted_override_section(self):
        self._write_overlay(
            "dev", {"spec": {"modules": [{"id": "db", "config": {"replicas": 1}}]}}
        )
        self._write_overlay(
            "prod", {"spec": {"modules": [{"id": "db", "config": {"replicas": 3}}]}}
        )
        self._run(["cds", "config", "set", "module.db.config.hostPath", "./my-w"])
        result, output = self._run(["cds", "diff", "--from", "dev", "--to", "prod"])
        self.assertEqual(result, 0, output)
        self.assertIn("spec.modules[db].config.replicas", output)
        self.assertIn("Persisted module overrides", output)
        self.assertIn(str(self.config_path), output)
        self.assertIn("spec.modules[db].config.hostPath", output)
        self.assertIn("./my-w", output)

    def test_diff_without_overrides_has_no_persisted_section(self):
        self._write_overlay(
            "dev", {"spec": {"modules": [{"id": "db", "config": {"replicas": 1}}]}}
        )
        self._write_overlay(
            "prod", {"spec": {"modules": [{"id": "db", "config": {"replicas": 3}}]}}
        )
        result, output = self._run(["cds", "diff", "--from", "dev", "--to", "prod"])
        self.assertEqual(result, 0, output)
        self.assertNotIn("Persisted module overrides", output)

    def test_diff_persisted_section_appears_even_without_env_changes(self):
        self._write_overlay(
            "dev", {"spec": {"modules": [{"id": "db", "config": {"replicas": 1}}]}}
        )
        self._write_overlay(
            "prod", {"spec": {"modules": [{"id": "db", "config": {"replicas": 1}}]}}
        )
        self._run(["cds", "config", "set", "module.db.config.hostPath", "./my-w"])
        result, output = self._run(["cds", "diff", "--from", "dev", "--to", "prod"])
        self.assertEqual(result, 0, output)
        self.assertNotIn("No differences", output)
        self.assertIn("Persisted module overrides", output)
        self.assertIn("./my-w", output)


class SharedConfigFileTest(ModuleOverrideFixture):
    """The committable team-shared file (cds.shared.json) selected with
    `cds config ... --shared` (issue #1220's "One Question": both exist)."""

    def test_set_shared_writes_shared_file_not_local(self):
        result, output = self._run(
            [
                "cds",
                "config",
                "set",
                "--shared",
                "module.db.config.hostPath",
                "./team-workdirs/db",
            ]
        )
        self.assertEqual(result, 0, output)
        self.assertIn(str(self.shared_path), output)
        self.assertEqual(
            self._read_shared()["module"]["db"]["config"]["hostPath"],
            "./team-workdirs/db",
        )
        self.assertNotIn("module", self._read_config())

    def test_shared_rejects_cli_level_keys(self):
        for argv in (
            ["cds", "config", "set", "--shared", "target", "helm"],
            ["cds", "config", "get", "--shared", "target"],
            ["cds", "config", "unset", "--shared", "profile"],
        ):
            result, output = self._run(argv)
            self.assertEqual(result, 1, (argv, output))
            self.assertIn("module.<id>.config.<path>", output)
        self.assertFalse(self.shared_path.exists())

    def test_get_unset_list_shared(self):
        self._run(
            [
                "cds",
                "config",
                "set",
                "--shared",
                "module.db.config.hostPath",
                "./team-w",
            ]
        )
        result, output = self._run(
            ["cds", "config", "get", "--shared", "module.db.config.hostPath"]
        )
        self.assertEqual(result, 0, output)
        self.assertIn("./team-w", output)

        result, output = self._run(["cds", "config", "list", "--shared"])
        self.assertEqual(result, 0, output)
        self.assertEqual(
            json.loads(output)["module"]["db"]["config"]["hostPath"], "./team-w"
        )
        # the local listing is untouched
        result, output = self._run(["cds", "config", "list"])
        self.assertNotIn("module", json.loads(output))

        result, output = self._run(
            ["cds", "config", "unset", "--shared", "module.db.config.hostPath"]
        )
        self.assertEqual(result, 0, output)
        self.assertIn("Unset", output)
        self.assertFalse(self.shared_path.exists())  # emptied file is removed
        result, output = self._run(
            ["cds", "config", "get", "--shared", "module.db.config.hostPath"]
        )
        self.assertEqual(result, 1)
        self.assertIn("not set", output)

    def test_shared_override_applies_at_load_and_beats_environment_overlay(self):
        self._write_overlay(
            "prod", {"spec": {"modules": [{"id": "db", "config": {"replicas": 3}}]}}
        )
        self._run(
            ["cds", "config", "set", "--shared", "module.db.config.replicas", "4"]
        )
        result, output = self._run(["cds", "plan", "--environment", "prod"])
        self.assertEqual(result, 0, output)
        plan = json.loads(output)
        self.assertEqual(plan["modules"][0]["config"]["replicas"], 4)
        self.assertEqual(
            plan["provenance"]["spec.modules[db].config.replicas"],
            str(self.shared_path),
        )

    def test_per_machine_override_beats_shared(self):
        self._run(
            ["cds", "config", "set", "--shared", "module.db.config.replicas", "4"]
        )
        self._run(["cds", "config", "set", "module.db.config.replicas", "5"])
        result, output = self._run(["cds", "plan"])
        self.assertEqual(result, 0, output)
        plan = json.loads(output)
        self.assertEqual(plan["modules"][0]["config"]["replicas"], 5)
        self.assertEqual(
            plan["provenance"]["spec.modules[db].config.replicas"],
            str(self.config_path),
        )
        # without the per-machine override the shared value applies
        self._run(["cds", "config", "unset", "module.db.config.replicas"])
        result, output = self._run(["cds", "plan"])
        plan = json.loads(output)
        self.assertEqual(plan["modules"][0]["config"]["replicas"], 4)
        self.assertEqual(
            plan["provenance"]["spec.modules[db].config.replicas"],
            str(self.shared_path),
        )

    def test_shared_secret_value_rejected(self):
        result, output = self._run(
            [
                "cds",
                "config",
                "set",
                "--shared",
                "module.db.config.password",
                "supersecretvalue123",
            ],
            extra_env={"CDS_DB_PASSWORD": "supersecretvalue123"},
        )
        self.assertEqual(result, 1)
        self.assertIn("E161", output)
        self.assertIn("secrets.<alias>", output)
        self.assertFalse(self.shared_path.exists())

    def test_shared_unknown_module_id_fails_validate_with_e161(self):
        self._write_shared({"module": {"ghost": {"config": {"hostPath": "./w"}}}})
        result, output = self._run(["cds", "validate"])
        self.assertEqual(result, 1)
        self.assertIn("E161", output)
        self.assertIn("ghost", output)

    def test_diff_lists_shared_file_as_source(self):
        self._write_overlay(
            "dev", {"spec": {"modules": [{"id": "db", "config": {"replicas": 1}}]}}
        )
        self._write_overlay(
            "prod", {"spec": {"modules": [{"id": "db", "config": {"replicas": 3}}]}}
        )
        self._run(
            [
                "cds",
                "config",
                "set",
                "--shared",
                "module.db.config.hostPath",
                "./team-w",
            ]
        )
        result, output = self._run(["cds", "diff", "--from", "dev", "--to", "prod"])
        self.assertEqual(result, 0, output)
        self.assertIn("Persisted module overrides", output)
        self.assertIn(str(self.shared_path), output)
        self.assertIn("spec.modules[db].config.hostPath", output)
        self.assertIn("./team-w", output)


if __name__ == "__main__":
    unittest.main()
