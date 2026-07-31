import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from cli import planner

class PlannerRegressionTest(unittest.TestCase):
    def test_build_plan_resolves_consumed_contracts(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            profile_dir = root / "profiles" / "local"
            producer_dir = profile_dir / "modules" / "producer"
            consumer_dir = profile_dir / "modules" / "consumer"
            producer_dir.mkdir(parents=True)
            consumer_dir.mkdir(parents=True)

            producer_module = {
                "apiVersion": "cds/v1alpha1",
                "kind": "Module",
                "metadata": {"name": "producer"},
                "spec": {
                    "configSchema": {
                        "type": "object",
                        "additionalProperties": False,
                    },
                    "provides": [
                        {
                            "name": "sql-database",
                            "contract": {
                                "kind": "sql-database",
                                "spec": {
                                    "connectionUri": "postgres://localhost:5432/test",
                                },
                            },
                        }
                    ],
                    "implementation": {
                        "kind": "docker-compose",
                        "compose": {"services": {}},
                    },
                },
            }

            consumer_module = {
                "apiVersion": "cds/v1alpha1",
                "kind": "Module",
                "metadata": {"name": "consumer"},
                "spec": {
                    "configSchema": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "database": {
                                "type": "object",
                                "additionalProperties": False,
                                "required": ["contractRef"],
                                "properties": {
                                    "contractRef": {"type": "string"},
                                },
                            }
                        },
                    },
                    "consumes": [
                        {
                            "name": "database",
                            "contract": {"kind": "sql-database"},
                            "required": True,
                            "mappedFrom": "spec.config.database",
                        }
                    ],
                    "implementation": {
                        "kind": "docker-compose",
                        "compose": {"services": {}},
                    },
                },
            }

            profile = {
                "apiVersion": "cds/v1alpha1",
                "kind": "Profile",
                "metadata": {"name": "local-test"},
                "spec": {
                    "runtime": {"type": "docker-compose"},
                    "modules": [
                        {
                            "id": "producer",
                            "source": "./modules/producer",
                            "enabled": True,
                            "config": {},
                        },
                        {
                            "id": "consumer",
                            "source": "./modules/consumer",
                            "enabled": True,
                            "dependsOn": ["producer"],
                            "config": {
                                "database": {
                                    "contractRef": "producer.sql-database",
                                }
                            },
                        },
                    ],
                    "secrets": {"provider": {"type": "env"}, "values": {}},
                },
            }

            import yaml

            producer_file = producer_dir / "module.yaml"
            producer_file.write_text(yaml.safe_dump(producer_module), encoding="utf-8")
            consumer_file = consumer_dir / "module.yaml"
            consumer_file.write_text(yaml.safe_dump(consumer_module), encoding="utf-8")

            profile_file = profile_dir / "profile.yaml"
            profile_file.write_text(yaml.safe_dump(profile), encoding="utf-8")

            plan, diagnostics = planner.build_plan(str(profile_file))

            self.assertIsNotNone(plan)
            self.assertEqual(len([d for d in diagnostics if d.level == "error"]), 0)

            consumer_entry = next(m for m in plan["modules"] if m["id"] == "consumer")
            self.assertIn("database", consumer_entry["consumes"])
            self.assertEqual(
                consumer_entry["consumes"]["database"]["contract"]["kind"],
                "sql-database",
            )
            self.assertEqual(
                consumer_entry["consumes"]["database"]["contract"]["spec"]["connectionUri"],
                "postgres://localhost:5432/test",
            )

    def test_build_plan_resolves_provider_contract_placeholders_for_consumers(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            profile_dir = root / "profiles" / "local"
            producer_dir = profile_dir / "modules" / "producer"
            consumer_dir = profile_dir / "modules" / "consumer"
            producer_dir.mkdir(parents=True)
            consumer_dir.mkdir(parents=True)

            producer_module = {
                "apiVersion": "cds/v1alpha1",
                "kind": "Module",
                "metadata": {"name": "producer"},
                "spec": {
                    "configSchema": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["database", "username", "passwordFrom", "port"],
                        "properties": {
                            "database": {"type": "string"},
                            "username": {"type": "string"},
                            "passwordFrom": {"type": "string"},
                            "port": {"type": "integer"},
                        },
                    },
                    "provides": [
                        {
                            "name": "sql-database",
                            "contract": {
                                "kind": "sql-database",
                                "spec": {
                                    "host": "${service.host}",
                                    "port": "${config.port}",
                                    "database": "${config.database}",
                                    "username": "${config.username}",
                                    "password": "${config.passwordFrom}",
                                    "connectionUri": "postgresql://${config.username}:${config.passwordFrom}@${service.host}:${config.port}/${config.database}",
                                },
                            },
                        }
                    ],
                    "implementation": {
                        "kind": "docker-compose",
                        "compose": {"services": {}},
                    },
                },
            }

            consumer_module = {
                "apiVersion": "cds/v1alpha1",
                "kind": "Module",
                "metadata": {"name": "consumer"},
                "spec": {
                    "configSchema": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "database": {
                                "type": "object",
                                "additionalProperties": False,
                                "required": ["contractRef"],
                                "properties": {
                                    "contractRef": {"type": "string"},
                                },
                            }
                        },
                    },
                    "consumes": [
                        {
                            "name": "database",
                            "contract": {"kind": "sql-database"},
                            "required": True,
                            "mappedFrom": "spec.config.database",
                        }
                    ],
                    "implementation": {
                        "kind": "docker-compose",
                        "compose": {"services": {}},
                    },
                },
            }

            profile = {
                "apiVersion": "cds/v1alpha1",
                "kind": "Profile",
                "metadata": {"name": "local-test"},
                "spec": {
                    "runtime": {"type": "docker-compose"},
                    "modules": [
                        {
                            "id": "producer",
                            "source": "./modules/producer",
                            "enabled": True,
                            "config": {
                                "database": "analytics",
                                "username": "analytics",
                                "passwordFrom": "secrets.db_password",
                                "port": 5432,
                            },
                        },
                        {
                            "id": "consumer",
                            "source": "./modules/consumer",
                            "enabled": True,
                            "dependsOn": ["producer"],
                            "config": {
                                "database": {
                                    "contractRef": "producer.sql-database",
                                }
                            },
                        },
                    ],
                    "secrets": {
                        "provider": {"type": "env"},
                        "values": {
                            "db_password": {"env": "CDS_DB_PASSWORD", "required": True}
                        },
                    },
                },
            }

            env_file = Path(root) / ".env"
            env_file.write_text("CDS_DB_PASSWORD=supersecret\n", encoding="utf-8")

            import yaml

            producer_file = producer_dir / "module.yaml"
            producer_file.write_text(yaml.safe_dump(producer_module), encoding="utf-8")
            consumer_file = consumer_dir / "module.yaml"
            consumer_file.write_text(yaml.safe_dump(consumer_module), encoding="utf-8")

            profile_file = profile_dir / "profile.yaml"
            profile_file.write_text(yaml.safe_dump(profile), encoding="utf-8")

            plan, diagnostics = planner.build_plan(str(profile_file), env_file=str(env_file))

            self.assertIsNotNone(plan)
            self.assertEqual(len([d for d in diagnostics if d.level == "error"]), 0)
            consumer_entry = next(m for m in plan["modules"] if m["id"] == "consumer")
            self.assertEqual(
                consumer_entry["consumes"]["database"]["contract"]["spec"]["connectionUri"],
                "postgresql://analytics:${CDS_DB_PASSWORD}@producer:5432/analytics",
            )
            self.assertEqual(
                consumer_entry["consumes"]["database"]["contract"]["spec"]["username"],
                "analytics",
            )
            self.assertEqual(
                consumer_entry["consumes"]["database"]["contract"]["spec"]["password"],
                "${CDS_DB_PASSWORD}",
            )
            self.assertEqual(
                consumer_entry["consumes"]["database"]["contract"]["spec"]["host"],
                "producer",
            )
            self.assertEqual(
                consumer_entry["consumes"]["database"]["contract"]["spec"]["port"],
                5432,
            )

    def test_build_plan_resolves_profile_secret_refs(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            profile_dir = root / "profiles" / "local"
            module_dir = profile_dir / "modules" / "database"
            module_dir.mkdir(parents=True)

            module = {
                "apiVersion": "cds/v1alpha1",
                "kind": "Module",
                "metadata": {"name": "database"},
                "spec": {
                    "configSchema": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "passwordFrom": {"type": "string"}
                        },
                    },
                    "implementation": {
                        "kind": "docker-compose",
                        "compose": {"services": {}},
                    },
                },
            }

            profile = {
                "apiVersion": "cds/v1alpha1",
                "kind": "Profile",
                "metadata": {"name": "local-test"},
                "spec": {
                    "runtime": {"type": "docker-compose"},
                    "modules": [
                        {
                            "id": "database",
                            "source": "./modules/database",
                            "enabled": True,
                            "config": {
                                "passwordFrom": "secrets.postgres_password"
                            },
                        }
                    ],
                    "secrets": {
                        "provider": {"type": "env"},
                        "values": {
                            "postgres_password": {
                                "env": "CDS_ANALYTICS_POSTGRES_PASSWORD",
                                "required": True,
                            }
                        }
                    },
                },
            }

            env_file = Path(root) / ".env"
            env_file.write_text("CDS_ANALYTICS_POSTGRES_PASSWORD=supersecret\n", encoding="utf-8")

            import yaml

            module_file = module_dir / "module.yaml"
            module_file.write_text(yaml.safe_dump(module), encoding="utf-8")

            profile_file = profile_dir / "profile.yaml"
            profile_file.write_text(yaml.safe_dump(profile), encoding="utf-8")

            plan, diagnostics = planner.build_plan(str(profile_file), env_file=str(env_file))

            self.assertIsNotNone(plan)
            self.assertEqual(len([d for d in diagnostics if d.level == "error"]), 0)
            self.assertEqual(plan["modules"][0]["config"]["passwordFrom"], "secrets.postgres_password")
            self.assertEqual(plan["secrets"]["postgres_password"], "CDS_ANALYTICS_POSTGRES_PASSWORD")

    def test_build_plan_rejects_module_source_traversal_outside_configured_module_root(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            profile_dir = root / "profiles" / "local"
            modules_root = root / "modules"
            outside_root = root / "outside_zone"
            profile_dir.mkdir(parents=True)
            modules_root.mkdir()
            outside_root.mkdir()

            import yaml

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
                            "source": "../../../outside_zone",
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
                plan, diagnostics = planner.build_plan(str(profile_file))

            self.assertIsNotNone(diagnostics)
            self.assertIsNotNone(plan)
            errors = [d for d in diagnostics if d.level == "error"]
            self.assertEqual(len(errors), 1)
            self.assertEqual(errors[0].code, "E022")
            self.assertIn("outside allowed module root", errors[0].message)
            self.assertEqual(plan["modules"], [])

    def test_build_plan_reports_diagnostic_instead_of_crashing_on_missing_module_id(self):
        """build_plan() is a public function that can be called directly
        (as this test does) without first running validate_profile(), so a
        module instance missing 'id' or 'source' must not raise a raw
        KeyError -- it should be reported as an E010 error diagnostic and
        skipped, mirroring the equivalent validator.py checks."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            profile_dir = root / "profiles" / "local"
            profile_dir.mkdir(parents=True)

            import yaml

            profile = {
                "apiVersion": "cds/v1alpha1",
                "kind": "Profile",
                "metadata": {"name": "local-test"},
                "spec": {
                    "runtime": {"type": "docker-compose"},
                    "modules": [
                        {"source": "../../modules/whatever", "enabled": True, "config": {}},
                        {"id": "missing-source", "enabled": True, "config": {}},
                    ],
                    "secrets": {"provider": {"type": "env"}, "values": {}},
                },
            }

            profile_file = profile_dir / "profile.yaml"
            profile_file.write_text(yaml.safe_dump(profile), encoding="utf-8")

            plan, diagnostics = planner.build_plan(str(profile_file))

            self.assertIsNotNone(plan)
            self.assertEqual(plan["modules"], [])
            errors = [d for d in diagnostics if d.level == "error" and d.code == "E010"]
            self.assertEqual(len(errors), 2)
            messages = {e.message for e in errors}
            self.assertIn("Module id is required.", messages)
            self.assertIn("Module source is required.", messages)

    def test_apply_defaults_recurses_into_object_typed_default_with_own_nested_defaults(self):
        """A nested object property with both its own top-level 'default'
        (e.g. 'default: {}') and nested properties that have defaults of
        their own must have those nested defaults filled in too, instead of
        stopping at the raw literal top-level default."""
        schema = {
            "type": "object",
            "properties": {
                "healthcheck": {
                    "type": "object",
                    "default": {},
                    "properties": {
                        "enabled": {"type": "boolean", "default": True},
                        "timeout": {
                            "type": "object",
                            "default": {},
                            "properties": {"seconds": {"type": "integer", "default": 30}},
                        },
                    },
                }
            },
        }

        result = planner.apply_defaults({}, schema)

        self.assertEqual(
            result,
            {"healthcheck": {"enabled": True, "timeout": {"seconds": 30}}},
        )


    def test_build_plan_reports_diagnostic_for_non_dict_module_entry(self):
        """build_plan() is a public entry point that may be called without
        prior validate_profile() shape-checking; a non-object module entry
        (e.g. a bare string or list in spec.modules) must produce a
        Diagnostic instead of raising AttributeError from `.get()`."""
        with tempfile.TemporaryDirectory() as tmpdir:
            profile_path = Path(tmpdir) / "profile.yaml"
            profile_path.write_text(
                "apiVersion: cds/v1alpha1\n"
                "kind: Profile\n"
                "spec:\n"
                "  modules:\n"
                "    - not-an-object\n"
            )

            plan, diagnostics = planner.build_plan(str(profile_path))

            self.assertIsNotNone(plan)
            self.assertEqual(plan["modules"], [])
            errors = [d for d in diagnostics if d.code == "E010"]
            self.assertEqual(len(errors), 1)
            self.assertIn("Module entry must be an object.", errors[0].message)

    def test_build_plan_reports_diagnostic_for_non_list_modules(self):
        """build_plan() is a public entry point that may be called without
        prior validate_profile() shape-checking; a non-list spec.modules
        (e.g. a scalar or null) must produce a Diagnostic instead of raising
        an unhandled TypeError from enumerate() on a non-iterable value."""
        with tempfile.TemporaryDirectory() as tmpdir:
            profile_path = Path(tmpdir) / "profile.yaml"
            profile_path.write_text(
                "apiVersion: cds/v1alpha1\n"
                "kind: Profile\n"
                "spec:\n"
                "  modules: 42\n"
            )

            plan, diagnostics = planner.build_plan(str(profile_path))

            self.assertIsNone(plan)
            errors = [d for d in diagnostics if d.code == "E010"]
            self.assertEqual(len(errors), 1)
            self.assertEqual(errors[0].path, "spec.modules")
            self.assertIn("spec.modules must be a list.", errors[0].message)

    def test_apply_defaults_raises_max_nesting_depth_exceeded_on_deep_schema(self):
        """A pathologically deeply nested configSchema must not overflow the
        Python call stack; _apply_schema_defaults() should raise a bounded,
        catchable error instead."""
        schema: dict = {"type": "object", "properties": {}}
        node = schema
        for _ in range(planner.MAX_NESTING_DEPTH + 10):
            child = {"type": "object", "properties": {}}
            node["properties"]["x"] = child
            node = child

        with self.assertRaises(planner.MaxNestingDepthExceeded):
            planner.apply_defaults({}, schema)

    def test_build_plan_converts_deep_config_nesting_into_diagnostic(self):
        """When apply_defaults() hits the nesting guard inside build_plan(),
        the module is skipped and an E094 diagnostic is reported instead of
        an unhandled exception propagating out of build_plan()."""
        schema: dict = {"type": "object", "properties": {}}
        node = schema
        for _ in range(planner.MAX_NESTING_DEPTH + 10):
            child = {"type": "object", "properties": {}}
            node["properties"]["x"] = child
            node = child

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            module_dir = root / "modules" / "deep"
            module_dir.mkdir(parents=True)
            module_file = module_dir / "module.yaml"
            import yaml as _yaml
            module_file.write_text(_yaml.safe_dump({
                "apiVersion": "cds/v1alpha1",
                "kind": "Module",
                "spec": {"configSchema": schema, "implementation": {"kind": "docker-compose"}},
            }))

            profile_dir = root / "profiles" / "local"
            profile_dir.mkdir(parents=True)
            profile_path = profile_dir / "profile.yaml"
            profile_path.write_text(
                "apiVersion: cds/v1alpha1\n"
                "kind: Profile\n"
                "spec:\n"
                "  modules:\n"
                "    - id: deep\n"
                "      source: ../../modules/deep\n"
                "      config: {}\n"
            )

            plan, diagnostics = planner.build_plan(str(profile_path))

            self.assertIsNotNone(plan)
            self.assertEqual(plan["modules"], [])
            errors = [d for d in diagnostics if d.code == "E094"]
            self.assertEqual(len(errors), 1)

    def test_substitute_values_raises_max_nesting_depth_exceeded_on_deep_dict(self):
        obj: dict = {}
        node = obj
        for _ in range(planner.MAX_NESTING_DEPTH + 10):
            node["child"] = {}
            node = node["child"]

        with self.assertRaises(planner.MaxNestingDepthExceeded):
            planner.substitute_values(obj, context={})


if __name__ == "__main__":
    unittest.main()
