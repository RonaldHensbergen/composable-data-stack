import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from cli.validator import (
    validate_contract_bindings,
    validate_contract_document,
    validate_contract_file,
    validate_image_source_config,
    validate_observability_config,
    validate_profile,
)

_REPO_ROOT = Path(__file__).resolve().parent.parent
_CONTRACTS_ROOT = _REPO_ROOT / "shared" / "contracts"


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


class ImageSourceConfigValidationTest(unittest.TestCase):
    def _instance(self, config, index=0):
        return {"index": index, "id": "under-test", "config": config}

    def test_source_build_default_is_unaffected(self):
        instances = [self._instance({"image": {"source": "build"}})]
        self.assertEqual(validate_image_source_config(instances), [])

    def test_absent_image_config_is_unaffected(self):
        instances = [self._instance({})]
        self.assertEqual(validate_image_source_config(instances), [])

    def test_registry_source_without_tag_is_rejected(self):
        instances = [self._instance({"image": {"source": "registry"}})]
        diagnostics = validate_image_source_config(instances)
        self.assertEqual([d.code for d in diagnostics], ["E103"])
        self.assertEqual(diagnostics[0].path, "spec.modules[0].config.image.tag")

    def test_registry_source_with_non_string_tag_is_rejected(self):
        instances = [self._instance({"image": {"source": "registry", "tag": 123}})]
        diagnostics = validate_image_source_config(instances)
        self.assertEqual([d.code for d in diagnostics], ["E103"])
    def test_registry_source_with_empty_tag_is_rejected(self):
        instances = [self._instance({"image": {"source": "registry", "tag": ""}})]
        diagnostics = validate_image_source_config(instances)
        self.assertEqual([d.code for d in diagnostics], ["E103"])

    def test_registry_source_with_pinned_tag_is_valid(self):
        instances = [self._instance({"image": {"source": "registry", "tag": "1.8.0"}})]
        self.assertEqual(validate_image_source_config(instances), [])

    def test_registry_source_with_latest_tag_is_a_warning_not_an_error(self):
        instances = [self._instance({"image": {"source": "registry", "tag": "latest"}})]
        diagnostics = validate_image_source_config(instances)
        self.assertEqual([d.code for d in diagnostics], ["W097"])
        self.assertEqual(diagnostics[0].level, "warning")
        self.assertEqual(diagnostics[0].path, "spec.modules[0].config.image.tag")

    def test_full_profile_with_latest_tag_still_validates(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            profile_dir = root / "profiles" / "local"
            modules_root = root / "modules"
            module_dir = modules_root / "orchestration" / "dagster"
            profile_dir.mkdir(parents=True)
            module_dir.mkdir(parents=True)

            (module_dir / "module.yaml").write_text(
                yaml.safe_dump(
                    {
                        "apiVersion": "cds/v1alpha1",
                        "kind": "Module",
                        "metadata": {"name": "dagster", "category": "orchestration", "version": "0.1.0"},
                        "spec": {
                            "runtime": {
                                "type": "container",
                                "service": {
                                    "name": "dagster",
                                    "ports": [{"name": "http", "containerPort": 3000, "protocol": "TCP"}],
                                },
                            },
                            "configSchema": {
                                "type": "object",
                                "additionalProperties": True,
                            },
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
                            "id": "dagster",
                            "source": "orchestration/dagster",
                            "version": "0.1.0",
                            "enabled": True,
                            "config": {"image": {"source": "registry", "tag": "latest"}},
                        }
                    ],
                    "secrets": {"provider": {"type": "env"}, "values": {}},
                },
            }

            profile_file = profile_dir / "profile.yaml"
            profile_file.write_text(yaml.safe_dump(profile), encoding="utf-8")

            with patch.dict("os.environ", {"CDS_MODULE_PATH": str(modules_root)}, clear=False):
                diagnostics = validate_profile(str(profile_file))

            self.assertEqual([d for d in diagnostics if d.level == "error"], [])
            self.assertEqual([d.code for d in diagnostics if d.level == "warning"], ["W097"])


class ContractSchemaValidationTest(unittest.TestCase):
    def test_all_repo_contract_files_are_schema_valid(self):
        contract_files = sorted(_CONTRACTS_ROOT.glob("*.yaml"))
        self.assertGreaterEqual(len(contract_files), 1)
        for contract_file in contract_files:
            with self.subTest(contract_file=contract_file.name):
                self.assertEqual(validate_contract_file(contract_file), [])

    def test_valid_contract_document_passes(self):
        contract = {
            "apiVersion": "cds/v1alpha1",
            "kind": "Contract",
            "metadata": {"name": "example", "version": "0.1.0"},
            "spec": {
                "fields": {
                    "host": {"type": "string", "required": True},
                }
            },
        }
        self.assertEqual(validate_contract_document(contract), [])

    def test_wrong_kind_is_rejected(self):
        contract = {
            "apiVersion": "cds/v1alpha1",
            "kind": "Module",
            "metadata": {"name": "example", "version": "0.1.0"},
            "spec": {"fields": {}},
        }
        diagnostics = validate_contract_document(contract)
        self.assertEqual([d.code for d in diagnostics], ["E023"])
        self.assertIn("Contract", diagnostics[0].message)

    def test_missing_spec_fields_is_rejected(self):
        contract = {
            "apiVersion": "cds/v1alpha1",
            "kind": "Contract",
            "metadata": {"name": "example", "version": "0.1.0"},
            "spec": {},
        }
        diagnostics = validate_contract_document(contract)
        self.assertEqual([d.code for d in diagnostics], ["E023"])
        self.assertIn("fields", diagnostics[0].message)

    def test_field_entry_missing_required_flag_is_rejected(self):
        contract = {
            "apiVersion": "cds/v1alpha1",
            "kind": "Contract",
            "metadata": {"name": "example", "version": "0.1.0"},
            "spec": {"fields": {"host": {"type": "string"}}},
        }
        diagnostics = validate_contract_document(contract)
        self.assertEqual([d.code for d in diagnostics], ["E023"])
        self.assertIn("required", diagnostics[0].message)

    def test_unknown_top_level_key_is_rejected(self):
        contract = {
            "apiVersion": "cds/v1alpha1",
            "kind": "Contract",
            "metadata": {"name": "example", "version": "0.1.0"},
            "spec": {"fields": {}},
            "unexpectedKey": "value",
        }
        diagnostics = validate_contract_document(contract)
        self.assertEqual([d.code for d in diagnostics], ["E023"])
        self.assertIn("unexpectedKey", diagnostics[0].message)


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


class ModuleSchemaValidationTest(unittest.TestCase):
    def _write_module(self, root: Path, module_yaml: dict) -> Path:
        module_dir = root / "modules" / "warehouse" / "postgres"
        module_dir.mkdir(parents=True)
        module_file = module_dir / "module.yaml"
        module_file.write_text(yaml.safe_dump(module_yaml), encoding="utf-8")
        return module_file

    def _valid_module(self) -> dict:
        return {
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

    def _validate(self, root: Path, module_yaml: dict) -> list:
        self._write_module(root, module_yaml)
        profile_file = root / "profiles" / "local" / "profile.yaml"
        profile_file.parent.mkdir(parents=True)
        profile_file.write_text(yaml.safe_dump(self._valid_profile()), encoding="utf-8")
        with patch.dict("os.environ", {"CDS_MODULE_PATH": str(root / "modules")}, clear=False):
            return validate_profile(str(profile_file))

    def test_valid_module_passes_schema(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            diagnostics = self._validate(Path(tmpdir), self._valid_module())
            self.assertEqual([d for d in diagnostics if d.level == "error"], [])

    def test_module_missing_runtime_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            module = self._valid_module()
            del module["spec"]["runtime"]
            errors = [d for d in self._validate(Path(tmpdir), module) if d.level == "error"]
            self.assertEqual([d.code for d in errors], ["E021"])
            self.assertIn("runtime", errors[0].message)
            self.assertIn("spec.modules[0]", errors[0].path)

    def test_module_with_unknown_top_level_key_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            module = self._valid_module()
            module["unexpectedKey"] = "value"
            errors = [d for d in self._validate(Path(tmpdir), module) if d.level == "error"]
            self.assertEqual([d.code for d in errors], ["E021"])
            self.assertIn("unexpectedKey", errors[0].message)

    def test_module_schema_validation_rejects_invalid_contract_provide(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            module = self._valid_module()
            module["spec"]["provides"] = [
                {"name": "sql-database", "contract": {"kind": 42}},
            ]
            errors = [d for d in self._validate(Path(tmpdir), module) if d.level == "error"]
            self.assertEqual([d.code for d in errors], ["E021"])
            self.assertIn("not of type", errors[0].message)

    def test_module_schema_rejects_malformed_required_if(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            module = self._valid_module()
            module["spec"]["consumes"] = [
                {
                    "name": "target-database",
                    "contract": {"kind": "sql-database"},
                    "required": False,
                    "requiredIf": "config.warehouseType",
                    "mappedFrom": "spec.config.targetDatabase",
                }
            ]
            errors = [d for d in self._validate(Path(tmpdir), module) if d.level == "error"]
            self.assertEqual([d.code for d in errors], ["E021"])


class RequiredIfMalformedGateTest(unittest.TestCase):
    """
    Direct unit tests for validate_contract_bindings against a malformed
    requiredIf gate, bypassing module.schema.json's requiredIf pattern (the
    schema stops a malformed expression earlier in practice, via
    load_module_instances's E021 short-circuit -- see
    ModuleSchemaValidationTest.test_module_schema_rejects_malformed_required_if),
    to prove the resolver-level defense-in-depth (cli/resolver.py's
    RequiredIfSyntaxError) also fails loudly with an E021 diagnostic
    instead of resolver.evaluate_required_if's old behavior of silently
    returning False and disabling enforcement.
    """

    def _module_instance(self, required_if: str) -> dict:
        return {
            "index": 0,
            "id": "consumer",
            "config": {"warehouseType": "duckdb"},
            "module": {
                "spec": {
                    "consumes": [
                        {
                            "name": "target-database",
                            "contract": {"kind": "sql-database"},
                            "required": False,
                            "requiredIf": required_if,
                            "mappedFrom": "spec.config.targetDatabase",
                        }
                    ]
                }
            },
        }

    def test_missing_operator_reports_e021_instead_of_silently_skipping(self):
        diagnostics = validate_contract_bindings([self._module_instance("config.warehouseType")])
        errors = [d for d in diagnostics if d.level == "error"]
        self.assertEqual([d.code for d in errors], ["E021"])
        self.assertIn("malformed requiredIf", errors[0].message)

    def test_path_not_rooted_at_config_reports_e021(self):
        diagnostics = validate_contract_bindings([self._module_instance("cofnig.warehouseType==duckdb")])
        errors = [d for d in diagnostics if d.level == "error"]
        self.assertEqual([d.code for d in errors], ["E021"])


class CompatibilityRegistryTest(unittest.TestCase):
    """
    Direct unit tests for validate_contract_bindings's compatibility-registry
    check (issue #350): a consumer/provider pairing can match structurally
    (same contract kind, E042-clean) yet still be recorded as "unsupported"
    in cli/resources/compatibility-registry.json, which is a stronger signal
    than kind matching alone. Absence of a registry entry must never block a
    profile -- only an explicit "unsupported" status does.
    """

    def _instances(self, *, provider_category: str, provider_name: str, consumer_category: str, consumer_name: str) -> list[dict]:
        provider = {
            "index": 0,
            "id": "provider",
            "config": {},
            "module": {
                "metadata": {"category": provider_category, "name": provider_name},
                "spec": {"provides": [{"name": "svc", "contract": {"kind": "sql-database"}}]},
            },
        }
        consumer = {
            "index": 1,
            "id": "consumer",
            "config": {"db": {"contractRef": "provider.svc"}},
            "module": {
                "metadata": {"category": consumer_category, "name": consumer_name},
                "spec": {
                    "consumes": [
                        {
                            "name": "db",
                            "contract": {"kind": "sql-database"},
                            "required": True,
                            "mappedFrom": "spec.config.db",
                        }
                    ]
                },
            },
        }
        return [provider, consumer]

    def test_pairing_with_no_registry_entry_is_not_blocked(self):
        instances = self._instances(
            provider_category="warehouse", provider_name="not-a-real-module",
            consumer_category="orchestration", consumer_name="also-not-real",
        )
        with patch("cli.validator._load_compatibility_registry", return_value={}):
            diagnostics = validate_contract_bindings(instances)
        self.assertEqual([d for d in diagnostics if d.level == "error"], [])

    def test_unsupported_pairing_reports_e043(self):
        instances = self._instances(
            provider_category="warehouse", provider_name="postgres",
            consumer_category="orchestration", consumer_name="dagster",
        )
        registry = {("sql-database", "warehouse/postgres", "orchestration/dagster"): "unsupported"}
        with patch("cli.validator._load_compatibility_registry", return_value=registry):
            diagnostics = validate_contract_bindings(instances)
        errors = [d for d in diagnostics if d.level == "error"]
        self.assertEqual([d.code for d in errors], ["E043"])
        self.assertIn("orchestration/dagster", errors[0].message)
        self.assertIn("warehouse/postgres", errors[0].message)
        self.assertIn("unsupported", errors[0].message)

    def test_tested_pairing_is_not_blocked(self):
        instances = self._instances(
            provider_category="warehouse", provider_name="postgres",
            consumer_category="orchestration", consumer_name="dagster",
        )
        registry = {("sql-database", "warehouse/postgres", "orchestration/dagster"): "tested"}
        with patch("cli.validator._load_compatibility_registry", return_value=registry):
            diagnostics = validate_contract_bindings(instances)
        self.assertEqual([d for d in diagnostics if d.level == "error"], [])

    def test_module_missing_metadata_skips_registry_lookup_without_crashing(self):
        instances = self._instances(
            provider_category="warehouse", provider_name="postgres",
            consumer_category="orchestration", consumer_name="dagster",
        )
        del instances[0]["module"]["metadata"]
        registry = {("sql-database", "warehouse/postgres", "orchestration/dagster"): "unsupported"}
        with patch("cli.validator._load_compatibility_registry", return_value=registry):
            diagnostics = validate_contract_bindings(instances)
        self.assertEqual([d for d in diagnostics if d.level == "error"], [])

    def test_bundled_registry_loads_and_validates_against_its_own_schema(self):
        # No mocking: exercises the real cli/resources/compatibility-registry.json
        # and cli/resources/compatibility-registry.schema.json.
        from cli.validator import _load_compatibility_registry

        registry = _load_compatibility_registry()
        self.assertEqual(
            registry[("sql-database", "warehouse/postgres", "orchestration/dagster")],
            "tested",
        )
        self.assertEqual(
            registry[("cache-service", "cache/keydb", "bi/superset")],
            "tested",
        )

    def test_real_tested_pairing_from_bundled_registry_is_not_blocked(self):
        # No mocking: postgres -> dagster (sql-database) is a real "tested"
        # entry in cli/resources/compatibility-registry.json.
        instances = self._instances(
            provider_category="warehouse", provider_name="postgres",
            consumer_category="orchestration", consumer_name="dagster",
        )
        diagnostics = validate_contract_bindings(instances)
        self.assertEqual([d for d in diagnostics if d.level == "error"], [])


if __name__ == "__main__":
    unittest.main()
