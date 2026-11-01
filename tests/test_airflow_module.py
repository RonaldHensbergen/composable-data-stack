"""Experimental airflow module: modules-experimental/orchestration/airflow (#1080).

Names the module so contract coverage counts it: module.yaml validates
against cli/resources/module.schema.json, its configSchema is itself a valid
JSON Schema, and the provided contracts (workflow-api / scheduler /
metadata-db-client) plus the consumed bindings (sql-database / object-store /
secrets-provider) are pinned. The module is a placeholder: it declares its
runtime service but renders no compose services yet.
"""
import json
import unittest
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator

REPO_ROOT = Path(__file__).resolve().parent.parent
MODULE_PATH = (
    REPO_ROOT / "modules-experimental" / "orchestration" / "airflow" / "module.yaml"
)
MODULE_SCHEMA = json.loads(
    (REPO_ROOT / "cli" / "resources" / "module.schema.json").read_text(encoding="utf-8")
)


class AirflowModuleTest(unittest.TestCase):
    def setUp(self) -> None:
        self.module = yaml.safe_load(MODULE_PATH.read_text(encoding="utf-8"))

    def test_module_is_schema_valid(self) -> None:
        errors = list(Draft202012Validator(MODULE_SCHEMA).iter_errors(self.module))
        self.assertEqual(errors, [])

    def test_metadata_and_config_schema(self) -> None:
        metadata = self.module["metadata"]
        self.assertEqual(metadata["name"], "airflow")
        self.assertEqual(metadata["category"], "orchestration")
        config_schema = self.module["spec"]["configSchema"]
        Draft202012Validator.check_schema(config_schema)
        properties = config_schema["properties"]
        self.assertEqual(
            sorted(properties), ["metadataDatabase", "objectStore", "secretsProvider"]
        )
        for name in properties.values():
            self.assertEqual(name["required"], ["contractRef"])

    def test_provides_workflow_api_scheduler_and_metadata_db_client(self) -> None:
        provides = {p["name"]: p["contract"] for p in self.module["spec"]["provides"]}
        self.assertEqual(
            sorted(provides), ["metadata-db-client", "scheduler", "workflow-api"]
        )
        workflow_api = provides["workflow-api"]
        self.assertEqual(workflow_api["kind"], "http-service")
        self.assertEqual(
            workflow_api["spec"],
            {"port": 8080, "healthPath": "/health", "protocol": "http"},
        )
        self.assertEqual(provides["scheduler"]["kind"], "scheduler")
        self.assertEqual(provides["metadata-db-client"]["kind"], "metadata-db-client")

    def test_consumes_required_and_optional_contracts(self) -> None:
        consumes = {c["name"]: c for c in self.module["spec"]["consumes"]}
        self.assertEqual(
            sorted(consumes), ["metadata-database", "object-store", "secrets-provider"]
        )
        metadata_db = consumes["metadata-database"]
        self.assertEqual(metadata_db["contract"]["kind"], "sql-database")
        self.assertTrue(metadata_db["required"])
        self.assertEqual(metadata_db["mappedFrom"], "spec.config.metadataDatabase")
        object_store = consumes["object-store"]
        self.assertEqual(object_store["contract"]["kind"], "object-store")
        self.assertFalse(object_store["required"])
        self.assertEqual(object_store["mappedFrom"], "spec.config.objectStore")
        secrets = consumes["secrets-provider"]
        self.assertEqual(secrets["contract"]["kind"], "secrets-provider")
        self.assertTrue(secrets["required"])
        self.assertEqual(secrets["mappedFrom"], "spec.config.secretsProvider")

    def test_runtime_service_matches_workflow_api_port(self) -> None:
        service = self.module["spec"]["runtime"]["service"]
        self.assertEqual(service["name"], "airflow")
        self.assertEqual(
            service["ports"],
            [{"name": "http", "containerPort": 8080, "protocol": "TCP"}],
        )

    def test_implementation_is_an_empty_placeholder(self) -> None:
        implementation = self.module["spec"]["implementation"]
        self.assertEqual(implementation["kind"], "docker-compose")
        self.assertEqual(implementation["compose"], {})


if __name__ == "__main__":
    unittest.main()
