import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from cli.validator import validate_observability_config, validate_profile


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
                "metadata": {"name": "local-test", "environment": "local"},
                "spec": {
                    "runtime": {"type": "docker-compose"},
                    "modules": [
                        {
                            "id": "outside",
                            "source": "modules/../../../outside_zone",
                            "version": "0.1.0",
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
                "metadata": {"name": "local-test", "environment": "local"},
                "spec": {
                    "runtime": {"type": "docker-compose"},
                    "modules": [
                        {
                            "id": "postgres",
                            "source": "warehouse/postgres",
                            "version": "0.1.0",
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


class ObservabilityConfigValidationTest(unittest.TestCase):
    def test_absent_observability_block_is_valid(self):
        profile = {"spec": {}}
        self.assertEqual(validate_observability_config(profile, []), [])

    def test_valid_log_shipping_without_sink_is_accepted(self):
        profile = {
            "spec": {
                "observability": {
                    "logShipping": {
                        "enabled": True,
                        "retention": {"rawDays": 7, "structuredDays": 90},
                    }
                }
            }
        }
        self.assertEqual(validate_observability_config(profile, []), [])

    def test_enabled_must_be_boolean(self):
        profile = {"spec": {"observability": {"logShipping": {"enabled": "yes"}}}}
        diagnostics = validate_observability_config(profile, [])
        self.assertEqual([d.code for d in diagnostics], ["E100"])
        self.assertEqual(diagnostics[0].path, "spec.observability.logShipping.enabled")

    def test_enabled_is_required_when_log_shipping_is_present(self):
        profile = {"spec": {"observability": {"logShipping": {}}}}
        diagnostics = validate_observability_config(profile, [])
        self.assertEqual([d.code for d in diagnostics], ["E100"])
        self.assertEqual(diagnostics[0].path, "spec.observability.logShipping.enabled")

    def test_retention_days_must_be_positive_integers(self):
        profile = {
            "spec": {
                "observability": {
                    "logShipping": {
                        "enabled": True,
                        "retention": {"rawDays": 0, "structuredDays": -1},
                    }
                }
            }
        }
        diagnostics = validate_observability_config(profile, [])
        self.assertEqual([d.code for d in diagnostics], ["E101", "E101"])

    def test_structured_days_must_be_at_least_raw_days(self):
        profile = {
            "spec": {
                "observability": {
                    "logShipping": {
                        "enabled": True,
                        "retention": {"rawDays": 30, "structuredDays": 7},
                    }
                }
            }
        }
        diagnostics = validate_observability_config(profile, [])
        self.assertEqual(len(diagnostics), 1)
        self.assertEqual(diagnostics[0].code, "E101")
        self.assertIn("structuredDays", diagnostics[0].message)

    def test_sink_contract_ref_must_be_well_formed(self):
        profile = {
            "spec": {
                "observability": {
                    "logShipping": {"enabled": True, "sink": {"contractRef": "not-a-valid-ref"}}
                }
            }
        }
        diagnostics = validate_observability_config(profile, [])
        self.assertEqual([d.code for d in diagnostics], ["E102"])

    def test_sink_contract_ref_must_point_to_known_module(self):
        profile = {
            "spec": {
                "observability": {
                    "logShipping": {"enabled": True, "sink": {"contractRef": "missing-module.log-sink"}}
                }
            }
        }
        diagnostics = validate_observability_config(profile, [])
        self.assertEqual([d.code for d in diagnostics], ["E102"])
        self.assertIn("unknown module", diagnostics[0].message)

    def test_sink_contract_ref_must_match_provided_contract_kind(self):
        profile = {
            "spec": {
                "observability": {
                    "logShipping": {"enabled": True, "sink": {"contractRef": "collector.log-sink"}}
                }
            }
        }
        module_instances = [
            {
                "id": "collector",
                "module": {
                    "spec": {
                        "provides": [
                            {"name": "log-sink", "contract": {"kind": "cache-service"}},
                        ]
                    }
                },
            }
        ]
        diagnostics = validate_observability_config(profile, module_instances)
        self.assertEqual([d.code for d in diagnostics], ["E102"])
        self.assertIn("expected", diagnostics[0].message)

    def test_sink_contract_ref_resolves_when_kind_matches(self):
        profile = {
            "spec": {
                "observability": {
                    "logShipping": {"enabled": True, "sink": {"contractRef": "collector.log-sink"}}
                }
            }
        }
        module_instances = [
            {
                "id": "collector",
                "module": {
                    "spec": {
                        "provides": [
                            {"name": "log-sink", "contract": {"kind": "log-sink"}},
                        ]
                    }
                },
            }
        ]
        self.assertEqual(validate_observability_config(profile, module_instances), [])


class ProfileSchemaValidationTest(unittest.TestCase):
    def _valid_profile(self) -> dict:
        return {
            "apiVersion": "cds/v1alpha1",
            "kind": "Profile",
            "metadata": {"name": "local-test", "environment": "local"},
            "spec": {
                "runtime": {"type": "docker-compose"},
                "modules": [
                    {
                        "id": "postgres",
                        "source": "warehouse/postgres",
                        "version": "0.1.0",
                        "enabled": True,
                        "config": {},
                    }
                ],
                "secrets": {"provider": {"type": "env"}, "values": {}},
            },
        }

    def test_valid_profile_passes_schema(self) -> None:
        from cli.validator import validate_profile_shape

        self.assertEqual(validate_profile_shape(self._valid_profile()), [])

    def test_wrong_api_version_is_rejected(self) -> None:
        from cli.validator import validate_profile_shape

        profile = self._valid_profile()
        profile["apiVersion"] = "cds/v2beta1"
        diagnostics = validate_profile_shape(profile)
        self.assertEqual([d.code for d in diagnostics], ["E010"])
        self.assertIn("cds/v1alpha1", diagnostics[0].message)

    def test_extra_top_level_key_is_rejected(self) -> None:
        from cli.validator import validate_profile_shape

        profile = self._valid_profile()
        profile["unexpectedKey"] = "value"
        diagnostics = validate_profile_shape(profile)
        self.assertEqual([d.code for d in diagnostics], ["E010"])
        self.assertIn("unexpectedKey", diagnostics[0].message)

    def test_missing_metadata_environment_is_rejected(self) -> None:
        from cli.validator import validate_profile_shape

        profile = self._valid_profile()
        del profile["metadata"]["environment"]
        diagnostics = validate_profile_shape(profile)
        self.assertEqual([d.code for d in diagnostics], ["E010"])
        self.assertIn("environment", diagnostics[0].message)

    def test_invalid_module_id_pattern_is_rejected(self) -> None:
        from cli.validator import validate_profile_shape

        profile = self._valid_profile()
        profile["spec"]["modules"][0]["id"] = "UPPERCASE"
        diagnostics = validate_profile_shape(profile)
        self.assertEqual([d.code for d in diagnostics], ["E010"])
        self.assertIn("UPPERCASE", diagnostics[0].message)

    def test_duplicate_module_ids_are_rejected(self) -> None:
        from cli.validator import validate_profile_shape

        profile = self._valid_profile()
        profile["spec"]["modules"].append(
            {
                "id": "postgres",
                "source": "warehouse/postgres",
                "version": "0.1.0",
                "enabled": True,
                "config": {},
            }
        )
        diagnostics = validate_profile_shape(profile)
        self.assertEqual([d.code for d in diagnostics], ["E011"])


if __name__ == "__main__":
    unittest.main()
