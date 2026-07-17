import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from cli.validator import validate_profile


class ValidatorRegressionTest(unittest.TestCase):
    def test_validate_profile_rejects_module_source_traversal_outside_modules_tree(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            profile_dir = root / "profiles" / "local"
            outside_root = root / "outside_zone"
            profile_dir.mkdir(parents=True)
            outside_root.mkdir()

            (outside_root / "module.yaml").write_text(
                yaml.safe_dump(
                    {
                        "apiVersion": "cds/v1alpha1",
                        "kind": "Module",
                        "metadata": {"name": "outside"},
                        "spec": {
                            "configSchema": {"type": "object", "additionalProperties": False},
                            "implementation": {"kind": "docker-compose", "compose": {"services": {}}},
                        },
                    }
                ),
                encoding="utf-8",
            )

            profile = {
                "apiVersion": "cds/v1alpha1",
                "kind": "Profile",
                "metadata": {"name": "local-test"},
                "spec": {
                    "runtime": {"type": "docker-compose"},
                    "modules": [
                        {
                            "id": "outside",
                            "source": "modules/../../../outside_zone",
                            "enabled": True,
                            "config": {},
                        }
                    ],
                    "secrets": {"provider": {"type": "env"}, "values": {}},
                },
            }

            profile_file = profile_dir / "profile.yaml"
            profile_file.write_text(yaml.safe_dump(profile), encoding="utf-8")

            diagnostics = validate_profile(str(profile_file))

            errors = [d for d in diagnostics if d.level == "error"]
            self.assertEqual(len(errors), 1)
            self.assertEqual(errors[0].code, "E022")
            self.assertIn("modules/", errors[0].message)

    def test_validate_profile_uses_cds_module_path_with_relative_module_source(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            profile_dir = root / "profiles" / "local"
            modules_root = root / "modules"
            module_dir = modules_root / "warehouse" / "postgres"
            profile_dir.mkdir(parents=True)
            module_dir.mkdir(parents=True)

            (module_dir / "module.yaml").write_text(
                yaml.safe_dump(
                    {
                        "apiVersion": "cds/v1alpha1",
                        "kind": "Module",
                        "metadata": {"name": "postgres", "category": "warehouse", "version": "0.1.0"},
                        "spec": {
                            "runtime": {
                                "type": "container",
                                "service": {
                                    "name": "postgres",
                                    "ports": [{"name": "db", "containerPort": 5432, "protocol": "TCP"}],
                                },
                            },
                            "configSchema": {"type": "object", "additionalProperties": False},
                            "implementation": {"kind": "docker-compose", "compose": {"services": {}}},
                        },
                    }
                ),
                encoding="utf-8",
            )

            profile = {
                "apiVersion": "cds/v1alpha1",
                "kind": "Profile",
                "metadata": {"name": "local-test"},
                "spec": {
                    "runtime": {"type": "docker-compose"},
                    "modules": [
                        {
                            "id": "postgres",
                            "source": "warehouse/postgres",
                            "enabled": True,
                            "config": {},
                        }
                    ],
                    "secrets": {"provider": {"type": "env"}, "values": {}},
                },
            }

            profile_file = profile_dir / "profile.yaml"
            profile_file.write_text(yaml.safe_dump(profile), encoding="utf-8")

            with patch.dict("os.environ", {"CDS_MODULE_PATH": str(modules_root)}, clear=False):
                diagnostics = validate_profile(str(profile_file))

            errors = [d for d in diagnostics if d.level == "error"]
            self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
