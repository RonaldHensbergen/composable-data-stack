import tempfile
import unittest
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

if __name__ == "__main__":
    unittest.main()
