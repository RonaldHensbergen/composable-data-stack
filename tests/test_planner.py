import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

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

    def _write_module_with_image_variant(self, module_dir: Path, supports_variant: bool) -> None:
        import yaml

        config_schema: dict = {
            "type": "object",
            "additionalProperties": False,
            "properties": {},
        }
        if supports_variant:
            config_schema["properties"]["image"] = {
                "type": "object",
                "additionalProperties": False,
                "default": {},
                "properties": {
                    "variant": {
                        "type": "string",
                        "enum": ["base", "hardened"],
                        "default": "base",
                    }
                },
            }
        module_def = {
            "apiVersion": "cds/v1alpha1",
            "kind": "Module",
            "metadata": {"name": module_dir.name},
            "spec": {
                "configSchema": config_schema,
                "implementation": {"kind": "docker-compose", "compose": {"services": {}}},
            },
        }
        module_dir.mkdir(parents=True)
        (module_dir / "module.yaml").write_text(yaml.safe_dump(module_def), encoding="utf-8")

    def test_build_plan_hardened_flag_overrides_image_variant_for_supporting_modules(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            profile_dir = root / "profiles" / "local"
            self._write_module_with_image_variant(profile_dir / "modules" / "dagster", supports_variant=True)
            self._write_module_with_image_variant(profile_dir / "modules" / "postgres", supports_variant=False)

            profile = {
                "apiVersion": "cds/v1alpha1",
                "kind": "Profile",
                "metadata": {"name": "local-test"},
                "spec": {
                    "runtime": {"type": "docker-compose"},
                    "modules": [
                        {"id": "dagster", "source": "./modules/dagster", "enabled": True, "config": {}},
                        {"id": "postgres", "source": "./modules/postgres", "enabled": True, "config": {}},
                    ],
                    "secrets": {"provider": {"type": "env"}, "values": {}},
                },
            }

            import yaml

            profile_file = profile_dir / "profile.yaml"
            profile_file.write_text(yaml.safe_dump(profile), encoding="utf-8")

            plan, diagnostics = planner.build_plan(str(profile_file), hardened=True)

            self.assertIsNotNone(plan)
            self.assertEqual(len([d for d in diagnostics if d.level == "error"]), 0)

            dagster_entry = next(m for m in plan["modules"] if m["id"] == "dagster")
            postgres_entry = next(m for m in plan["modules"] if m["id"] == "postgres")
            self.assertEqual(dagster_entry["config"]["image"]["variant"], "hardened")
            self.assertNotIn("image", postgres_entry["config"])

    def test_build_plan_without_hardened_flag_leaves_default_variant(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            profile_dir = root / "profiles" / "local"
            self._write_module_with_image_variant(profile_dir / "modules" / "dagster", supports_variant=True)

            profile = {
                "apiVersion": "cds/v1alpha1",
                "kind": "Profile",
                "metadata": {"name": "local-test"},
                "spec": {
                    "runtime": {"type": "docker-compose"},
                    "modules": [
                        {"id": "dagster", "source": "./modules/dagster", "enabled": True, "config": {}},
                    ],
                    "secrets": {"provider": {"type": "env"}, "values": {}},
                },
            }

            import yaml

            profile_file = profile_dir / "profile.yaml"
            profile_file.write_text(yaml.safe_dump(profile), encoding="utf-8")

            plan, diagnostics = planner.build_plan(str(profile_file))

            self.assertIsNotNone(plan)
            dagster_entry = next(m for m in plan["modules"] if m["id"] == "dagster")
            self.assertEqual(dagster_entry["config"]["image"]["variant"], "base")

    def _write_module_with_image_source(
        self,
        module_dir: Path,
        supports_source: bool,
        supports_variant: bool = False,
        default_tag: str | None = None,
    ) -> None:
        import yaml

        image_properties: dict = {}
        if supports_variant:
            image_properties["variant"] = {
                "type": "string",
                "enum": ["base", "hardened"],
                "default": "base",
            }
        if supports_source:
            image_properties["source"] = {
                "type": "string",
                "enum": ["build", "registry"],
                "default": "build",
            }
            tag_schema: dict = {"type": "string"}
            if default_tag is not None:
                tag_schema["default"] = default_tag
            image_properties["tag"] = tag_schema

        config_schema: dict = {
            "type": "object",
            "additionalProperties": False,
            "properties": {},
        }
        if image_properties:
            config_schema["properties"]["image"] = {
                "type": "object",
                "additionalProperties": False,
                "default": {},
                "properties": image_properties,
            }

        module_def = {
            "apiVersion": "cds/v1alpha1",
            "kind": "Module",
            "metadata": {"name": module_dir.name},
            "spec": {
                "configSchema": config_schema,
                "implementation": {"kind": "docker-compose", "compose": {"services": {}}},
            },
        }
        module_dir.mkdir(parents=True)
        (module_dir / "module.yaml").write_text(yaml.safe_dump(module_def), encoding="utf-8")

    def _build_single_module_plan(self, profile_dir: Path, module_id: str, config: dict, hardened: bool = False, image_source: str | None = None):
        import yaml

        profile = {
            "apiVersion": "cds/v1alpha1",
            "kind": "Profile",
            "metadata": {"name": "local-test"},
            "spec": {
                "runtime": {"type": "docker-compose"},
                "modules": [
                    {"id": module_id, "source": f"./modules/{module_id}", "enabled": True, "config": config},
                ],
                "secrets": {"provider": {"type": "env"}, "values": {}},
            },
        }
        profile_file = profile_dir / "profile.yaml"
        profile_file.write_text(yaml.safe_dump(profile), encoding="utf-8")
        return planner.build_plan(str(profile_file), hardened=hardened, image_source=image_source)

    def test_build_plan_image_source_flag_overrides_source_for_supporting_modules(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            profile_dir = root / "profiles" / "local"
            self._write_module_with_image_source(
                profile_dir / "modules" / "superset", supports_source=True, default_tag="6.1.0"
            )

            plan, diagnostics = self._build_single_module_plan(
                profile_dir, "superset", config={}, image_source="registry"
            )

            self.assertIsNotNone(plan)
            self.assertEqual(len([d for d in diagnostics if d.level == "error"]), 0)
            entry = next(m for m in plan["modules"] if m["id"] == "superset")
            self.assertEqual(entry["config"]["image"]["source"], "registry")
            self.assertEqual(entry["config"]["image"]["tag"], "6.1.0")

    def test_build_plan_image_source_flag_leaves_unsupported_modules_untouched(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            profile_dir = root / "profiles" / "local"
            self._write_module_with_image_source(profile_dir / "modules" / "postgres", supports_source=False)

            plan, diagnostics = self._build_single_module_plan(
                profile_dir, "postgres", config={}, image_source="registry"
            )

            self.assertIsNotNone(plan)
            entry = next(m for m in plan["modules"] if m["id"] == "postgres")
            self.assertNotIn("image", entry["config"])

    def test_build_plan_image_source_flag_does_not_overwrite_explicit_profile_tag(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            profile_dir = root / "profiles" / "local"
            self._write_module_with_image_source(
                profile_dir / "modules" / "superset", supports_source=True, default_tag="6.1.0"
            )

            plan, diagnostics = self._build_single_module_plan(
                profile_dir,
                "superset",
                config={"image": {"tag": "5.0.0-custom"}},
                image_source="registry",
            )

            self.assertIsNotNone(plan)
            entry = next(m for m in plan["modules"] if m["id"] == "superset")
            self.assertEqual(entry["config"]["image"]["source"], "registry")
            self.assertEqual(entry["config"]["image"]["tag"], "5.0.0-custom")

    def test_build_plan_image_source_registry_with_hardened_prefixes_default_tag(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            profile_dir = root / "profiles" / "local"
            self._write_module_with_image_source(
                profile_dir / "modules" / "dagster",
                supports_source=True,
                supports_variant=True,
                default_tag="1.13.20",
            )

            plan, diagnostics = self._build_single_module_plan(
                profile_dir, "dagster", config={}, hardened=True, image_source="registry"
            )

            self.assertIsNotNone(plan)
            entry = next(m for m in plan["modules"] if m["id"] == "dagster")
            self.assertEqual(entry["config"]["image"]["variant"], "hardened")
            self.assertEqual(entry["config"]["image"]["source"], "registry")
            self.assertEqual(entry["config"]["image"]["tag"], "hardened-1.13.20")

    def test_build_plan_image_source_build_leaves_tag_default_bare(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            profile_dir = root / "profiles" / "local"
            self._write_module_with_image_source(
                profile_dir / "modules" / "superset", supports_source=True, default_tag="6.1.0"
            )

            plan, diagnostics = self._build_single_module_plan(
                profile_dir, "superset", config={}, image_source="build"
            )

            self.assertIsNotNone(plan)
            entry = next(m for m in plan["modules"] if m["id"] == "superset")
            self.assertEqual(entry["config"]["image"]["source"], "build")
            self.assertEqual(entry["config"]["image"]["tag"], "6.1.0")

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

    def test_build_plan_reports_diagnostic_instead_of_crashing_on_missing_module_id(
        self,
    ):
        """A valid module source must still surface its missing instance id."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            profile_dir = root / "profiles" / "local"
            module_dir = profile_dir / "modules" / "valid"
            module_dir.mkdir(parents=True)

            import yaml

            module = {
                "apiVersion": "cds/v1alpha1",
                "kind": "Module",
                "metadata": {"name": "valid"},
                "spec": {
                    "configSchema": {"type": "object", "additionalProperties": False},
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
                        {"source": "./modules/valid", "enabled": True, "config": {}}
                    ],
                    "secrets": {"provider": {"type": "env"}, "values": {}},
                },
            }

            (module_dir / "module.yaml").write_text(
                yaml.safe_dump(module), encoding="utf-8"
            )
            profile_file = profile_dir / "profile.yaml"
            profile_file.write_text(yaml.safe_dump(profile), encoding="utf-8")

            plan, diagnostics = planner.build_plan(str(profile_file))

            self.assertIsNotNone(plan)
            self.assertEqual(plan["modules"], [])
            errors = [d for d in diagnostics if d.level == "error" and d.code == "E010"]
            self.assertEqual(len(errors), 1)
            self.assertEqual(errors[0].message, "Module id is required.")
            self.assertEqual(errors[0].path, "spec.modules[0].id")

    def test_build_plan_reports_diagnostic_instead_of_crashing_on_missing_module_source(
        self,
    ):
        """A module id without a source must produce its own E010 diagnostic."""
        with tempfile.TemporaryDirectory() as tmpdir:
            profile_dir = Path(tmpdir) / "profiles" / "local"
            profile_dir.mkdir(parents=True)

            import yaml

            profile = {
                "apiVersion": "cds/v1alpha1",
                "kind": "Profile",
                "metadata": {"name": "local-test"},
                "spec": {
                    "runtime": {"type": "docker-compose"},
                    "modules": [
                        {"id": "missing-source", "enabled": True, "config": {}}
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
            self.assertEqual(len(errors), 1)
            self.assertEqual(errors[0].message, "Module source is required.")
            self.assertEqual(errors[0].path, "spec.modules[0].source")

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

    def test_apply_defaults_materializes_string_nested_defaults(self):
        """A non-boolean (string) nested default inside an object-typed
        parent default must be materialized too. Boolean-true defaults are
        masked in the renderer by a None-vs-False quirk, so a regression
        here would be invisible in rendered output; string defaults are
        not."""
        schema = {
            "type": "object",
            "properties": {
                "sharedData": {
                    "type": "object",
                    "additionalProperties": False,
                    "default": {},
                    "properties": {
                        "hostPath": {
                            "type": "string",
                            "minLength": 1,
                            "default": "./workdirs/shared-data",
                        },
                        "containerPath": {
                            "type": "string",
                            "minLength": 1,
                            "default": "/app/data/cds",
                        },
                    },
                }
            },
        }

        result = planner.apply_defaults({}, schema)

        self.assertIsInstance(result.get("sharedData"), dict)
        self.assertIn("hostPath", result["sharedData"])
        self.assertIn("containerPath", result["sharedData"])

        self.assertEqual(
            result,
            {
                "sharedData": {
                    "hostPath": "./workdirs/shared-data",
                    "containerPath": "/app/data/cds",
                }
            },
        )

    def test_apply_defaults_real_postgres_schema_materializes_nested_defaults(self):
        """The real postgres module schema has several object-typed blocks
        with a top-level 'default: {}' and nested defaults (storage,
        dagsterDatabase, supersetDatabase, healthcheck). All of them must
        materialize their nested defaults when the profile omits the parent
        key entirely (regression guard for the planner recursion fix, #299)."""
        import yaml

        repo_root = Path(__file__).resolve().parent.parent
        module_path = repo_root / "modules" / "warehouse" / "postgres" / "module.yaml"
        self.assertTrue(module_path.exists(), f"Real postgres module not found at {module_path}")

        module_def = yaml.safe_load(module_path.read_text(encoding="utf-8"))
        config_schema = module_def["spec"]["configSchema"]

        result = planner.apply_defaults({}, config_schema)

        self.assertIsInstance(result.get("storage"), dict)
        self.assertIn("enabled", result["storage"])
        self.assertIn("size", result["storage"])
        self.assertIsInstance(result.get("dagsterDatabase"), dict)
        self.assertIn("name", result["dagsterDatabase"])
        self.assertIn("username", result["dagsterDatabase"])
        self.assertIsInstance(result.get("supersetDatabase"), dict)
        self.assertIn("name", result["supersetDatabase"])
        self.assertIn("username", result["supersetDatabase"])
        self.assertIsInstance(result.get("healthcheck"), dict)
        self.assertIn("enabled", result["healthcheck"])

        self.assertEqual(
            result.get("storage"),
            {"enabled": True, "size": "5Gi"},
        )
        self.assertEqual(
            result.get("dagsterDatabase"),
            {"name": "dagster", "username": "dagster"},
        )
        self.assertEqual(
            result.get("supersetDatabase"),
            {"name": "superset", "username": "superset"},
        )
        self.assertEqual(
            result.get("healthcheck"),
            {"enabled": True},
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

    def test_build_plan_honors_extends_without_environment_flag(self):
        """
        A profile's `extends` chain must be resolved even when build_plan()
        is called with no --environment, since `extends` is a property of
        the profile file itself, not something gated behind that flag.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            module_dir = root / "modules" / "deep"
            module_dir.mkdir(parents=True)
            (module_dir / "module.yaml").write_text(
                "apiVersion: cds/v1alpha1\n"
                "kind: Module\n"
                "metadata:\n"
                "  name: deep\n"
                "spec:\n"
                "  configSchema: {type: object, additionalProperties: true}\n"
                "  implementation: {kind: docker-compose, compose: {services: {}}}\n"
            )

            base_dir = root / "profiles" / "base"
            base_dir.mkdir(parents=True)
            (base_dir / "profile.yaml").write_text(
                "apiVersion: cds/v1alpha1\n"
                "kind: Profile\n"
                "metadata: {name: base, environment: local}\n"
                "spec:\n"
                "  runtime: {type: docker-compose}\n"
                "  modules:\n"
                "    - id: svc\n"
                "      source: ../../modules/deep\n"
                "      config: {replicas: 1}\n"
            )

            child_dir = root / "profiles" / "child"
            child_dir.mkdir(parents=True)
            profile_path = child_dir / "profile.yaml"
            profile_path.write_text(
                "apiVersion: cds/v1alpha1\n"
                "kind: Profile\n"
                "metadata: {name: child, environment: local}\n"
                "extends: [base]\n"
                "spec:\n"
                "  modules:\n"
                "    - id: svc\n"
                "      config: {replicas: 5}\n"
            )

            plan, diagnostics = planner.build_plan(str(profile_path))

            self.assertFalse(any(d.level == "error" for d in diagnostics), diagnostics)
            self.assertIsNotNone(plan)
            module = next(m for m in plan["modules"] if m["id"] == "svc")
            self.assertEqual(module["config"]["replicas"], 5)
    def test_substitute_string_if_nonempty_omits_affix_for_empty_password(self):
        result = planner.substitute_string(
            "redis://${ifNonempty:config.password,:,@}${service.host}:${config.port}",
            {
                "config": {"password": "", "port": 6379},
                "service": {"host": "keydb"},
            },
        )
        self.assertEqual(result, "redis://keydb:6379")

    def test_substitute_string_if_nonempty_includes_password_in_redis_uri(self):
        result = planner.substitute_string(
            "redis://${ifNonempty:config.password,:,@}${service.host}:${config.port}",
            {
                "config": {"password": "secret", "port": 6379},
                "service": {"host": "keydb"},
            },
        )
        self.assertEqual(result, "redis://:secret@keydb:6379")

    def test_substitute_values_raises_max_nesting_depth_exceeded_on_deep_dict(self):
        obj: dict = {}
        node = obj
        for _ in range(planner.MAX_NESTING_DEPTH + 10):
            node["child"] = {}
            node = node["child"]

        with self.assertRaises(planner.MaxNestingDepthExceeded):
            planner.substitute_values(obj, context={})


class RequiredIfConsumeTest(unittest.TestCase):
    """
    Covers the requiredIf gate (cli/resolver.py's evaluate_required_if, used
    by both cli/planner.py's resolve_consumed_contracts and
    cli/validator.py's validate_contract_bindings) with a dbt-warehouseType-
    shaped module: two optional consumes (a sql-database "target-database"
    and a file-database "target-warehouse-file"), each requiredIf-gated on
    config.warehouseType, mirroring modules-experimental/transformation/dbt.
    """

    def _write_profile(self, tmpdir: str, warehouse_type: str, database_ref: str | None, warehouse_file_ref: str | None) -> str:
        import yaml

        root = Path(tmpdir)
        profile_dir = root / "profiles" / "local"
        postgres_dir = profile_dir / "modules" / "postgres"
        duckdb_dir = profile_dir / "modules" / "duckdb"
        consumer_dir = profile_dir / "modules" / "consumer"
        for d in (postgres_dir, duckdb_dir, consumer_dir):
            d.mkdir(parents=True)

        postgres_module = {
            "apiVersion": "cds/v1alpha1",
            "kind": "Module",
            "metadata": {"name": "postgres"},
            "spec": {
                "configSchema": {"type": "object", "additionalProperties": False},
                "provides": [
                    {
                        "name": "sql-database",
                        "contract": {"kind": "sql-database", "spec": {"host": "postgres"}},
                    }
                ],
                "implementation": {"kind": "docker-compose", "compose": {"services": {}}},
            },
        }
        duckdb_module = {
            "apiVersion": "cds/v1alpha1",
            "kind": "Module",
            "metadata": {"name": "duckdb"},
            "spec": {
                "configSchema": {"type": "object", "additionalProperties": False},
                "provides": [
                    {
                        "name": "file-database",
                        "contract": {"kind": "file-database", "spec": {"hostDirectory": "./data/duckdb"}},
                    }
                ],
                "implementation": {"kind": "docker-compose", "compose": {"services": {}}},
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
                        "warehouseType": {"type": "string", "enum": ["postgres", "duckdb"], "default": "postgres"},
                        "targetDatabase": {"type": "object", "additionalProperties": False, "default": {}, "properties": {"contractRef": {"type": "string"}}},
                        "targetWarehouseFile": {"type": "object", "additionalProperties": False, "default": {}, "properties": {"contractRef": {"type": "string"}}},
                    },
                },
                "consumes": [
                    {
                        "name": "target-database",
                        "contract": {"kind": "sql-database"},
                        "required": False,
                        "requiredIf": "config.warehouseType==postgres",
                        "mappedFrom": "spec.config.targetDatabase",
                    },
                    {
                        "name": "target-warehouse-file",
                        "contract": {"kind": "file-database"},
                        "required": False,
                        "requiredIf": "config.warehouseType==duckdb",
                        "mappedFrom": "spec.config.targetWarehouseFile",
                    },
                ],
                "implementation": {"kind": "docker-compose", "compose": {"services": {}}},
            },
        }

        (postgres_dir / "module.yaml").write_text(yaml.safe_dump(postgres_module), encoding="utf-8")
        (duckdb_dir / "module.yaml").write_text(yaml.safe_dump(duckdb_module), encoding="utf-8")
        (consumer_dir / "module.yaml").write_text(yaml.safe_dump(consumer_module), encoding="utf-8")

        consumer_config: dict = {"warehouseType": warehouse_type}
        if database_ref is not None:
            consumer_config["targetDatabase"] = {"contractRef": database_ref}
        if warehouse_file_ref is not None:
            consumer_config["targetWarehouseFile"] = {"contractRef": warehouse_file_ref}

        profile = {
            "apiVersion": "cds/v1alpha1",
            "kind": "Profile",
            "metadata": {"name": "local-test"},
            "spec": {
                "runtime": {"type": "docker-compose"},
                "modules": [
                    {"id": "postgres", "source": "./modules/postgres", "enabled": True, "config": {}},
                    {"id": "duckdb", "source": "./modules/duckdb", "enabled": True, "config": {}},
                    {
                        "id": "consumer",
                        "source": "./modules/consumer",
                        "enabled": True,
                        "dependsOn": ["postgres", "duckdb"],
                        "config": consumer_config,
                    },
                ],
                "secrets": {"provider": {"type": "env"}, "values": {}},
            },
        }
        profile_file = profile_dir / "profile.yaml"
        profile_file.write_text(yaml.safe_dump(profile), encoding="utf-8")
        return str(profile_file)

    def test_build_plan_resolves_target_database_when_warehouse_type_is_postgres(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            profile_file = self._write_profile(tmpdir, "postgres", "postgres.sql-database", None)
            plan, diagnostics = planner.build_plan(profile_file)

            self.assertEqual([d for d in diagnostics if d.level == "error"], [])
            consumer_entry = next(m for m in plan["modules"] if m["id"] == "consumer")
            self.assertIn("target-database", consumer_entry["consumes"])
            self.assertNotIn("target-warehouse-file", consumer_entry["consumes"])

    def test_build_plan_resolves_target_warehouse_file_when_warehouse_type_is_duckdb(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            profile_file = self._write_profile(tmpdir, "duckdb", None, "duckdb.file-database")
            plan, diagnostics = planner.build_plan(profile_file)

            self.assertEqual([d for d in diagnostics if d.level == "error"], [])
            consumer_entry = next(m for m in plan["modules"] if m["id"] == "consumer")
            self.assertIn("target-warehouse-file", consumer_entry["consumes"])
            self.assertNotIn("target-database", consumer_entry["consumes"])

    def test_build_plan_reports_e041_when_warehouse_type_mismatches_bound_target(self):
        """warehouseType selects postgres, but only the duckdb-side
        targetWarehouseFile binding is set (e.g. a config typo/omission) --
        requiredIf must fail this as E041 rather than silently planning
        without a target-database binding at all."""
        with tempfile.TemporaryDirectory() as tmpdir:
            profile_file = self._write_profile(tmpdir, "postgres", None, "duckdb.file-database")
            plan, diagnostics = planner.build_plan(profile_file)

            errors = [d for d in diagnostics if d.level == "error"]
            self.assertTrue(errors)
            self.assertTrue(all(d.code == "E041" for d in errors))


class RequiredIfMalformedGatePlannerTest(unittest.TestCase):
    """
    Direct unit test for resolve_consumed_contracts against a malformed
    requiredIf gate, bypassing module.schema.json's requiredIf pattern (the
    schema stops a malformed expression earlier in practice), to prove the
    resolver-level defense-in-depth also fails loudly at plan time with an
    E021 diagnostic instead of silently disabling the requiredIf gate.
    """

    def test_missing_operator_reports_e021_instead_of_silently_skipping(self):
        inst = {
            "id": "consumer",
            "config": {"warehouseType": "duckdb"},
            "module": {
                "spec": {
                    "consumes": [
                        {
                            "name": "target-database",
                            "contract": {"kind": "sql-database"},
                            "required": False,
                            "requiredIf": "config.warehouseType",
                            "mappedFrom": "spec.config.targetDatabase",
                        }
                    ]
                }
            },
        }
        diagnostics: list = []
        planner.resolve_consumed_contracts(inst, {}, {}, diagnostics)

        errors = [d for d in diagnostics if d.level == "error"]
        self.assertEqual([d.code for d in errors], ["E021"])
        self.assertIn("malformed requiredIf", errors[0].message)


if __name__ == "__main__":
    unittest.main()
