"""lineage-sink provider module: modules-experimental/observability/marquez (#1080).

Names the module so contract coverage counts it: module.yaml validates
against cli/resources/module.schema.json, its configSchema is itself a valid
JSON Schema, and the provided `lineage-sink` contract plus the consumed
`sql-database` binding are pinned field by field. Plan/render behavior of
the multi-service host wiring is covered by
tests/test_marquez_lineage_sink_host.py (#1029).
"""
import json
import unittest
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator

REPO_ROOT = Path(__file__).resolve().parent.parent
MODULE_PATH = (
    REPO_ROOT / "modules-experimental" / "observability" / "marquez" / "module.yaml"
)
MODULE_SCHEMA = json.loads(
    (REPO_ROOT / "cli" / "resources" / "module.schema.json").read_text(encoding="utf-8")
)


class MarquezModuleTest(unittest.TestCase):
    def setUp(self) -> None:
        self.module = yaml.safe_load(MODULE_PATH.read_text(encoding="utf-8"))

    def test_module_is_schema_valid(self) -> None:
        errors = list(Draft202012Validator(MODULE_SCHEMA).iter_errors(self.module))
        self.assertEqual(errors, [])

    def test_metadata_and_config_schema(self) -> None:
        metadata = self.module["metadata"]
        self.assertEqual(metadata["name"], "marquez")
        self.assertEqual(metadata["category"], "observability")
        self.assertFalse(metadata["productionSuitable"])
        config_schema = self.module["spec"]["configSchema"]
        Draft202012Validator.check_schema(config_schema)
        self.assertEqual(config_schema["required"], ["database"])

    def test_provides_lineage_sink_contract(self) -> None:
        provides = self.module["spec"]["provides"]
        self.assertEqual(len(provides), 1)
        self.assertEqual(provides[0]["name"], "lineage-sink")
        contract = provides[0]["contract"]
        self.assertEqual(contract["kind"], "lineage-sink")
        spec = contract["spec"]
        # Multi-service module (marquez-api + marquez-web): consumers must be
        # pointed at the API service's rendered DNS name, not the instance id
        # (#1029); serviceName advertises the same serving service for the
        # E150 host cross-check.
        self.assertEqual(spec["host"], "${service.services.marquez-api}")
        self.assertEqual(spec["port"], "${config.apiPort}")
        self.assertEqual(spec["protocol"], "http")
        self.assertEqual(spec["ingestPath"], "/api/v1/lineage")
        self.assertEqual(
            spec["connectionUri"],
            "http://${service.services.marquez-api}:${config.apiPort}",
        )
        self.assertEqual(spec["authToken"], "")
        self.assertEqual(spec["serviceName"], "marquez-api")

    def test_consumes_sql_database(self) -> None:
        consumes = self.module["spec"]["consumes"]
        self.assertEqual(len(consumes), 1)
        self.assertEqual(consumes[0]["name"], "database")
        self.assertEqual(consumes[0]["contract"]["kind"], "sql-database")
        self.assertTrue(consumes[0]["required"])
        self.assertEqual(consumes[0]["mappedFrom"], "spec.config.database")

    def test_runtime_service_ports_match_contract_defaults(self) -> None:
        service = self.module["spec"]["runtime"]["service"]
        self.assertEqual(service["name"], "marquez-api")
        self.assertEqual(
            service["ports"],
            [
                {"name": "http", "containerPort": 5000, "protocol": "TCP"},
                {"name": "admin", "containerPort": 5001, "protocol": "TCP"},
            ],
        )
        defaults = self.module["spec"]["configSchema"]["properties"]
        self.assertEqual(defaults["apiPort"]["default"], 5000)
        self.assertEqual(defaults["apiAdminPort"]["default"], 5001)

    def test_compose_services_are_hardened_and_pinned(self) -> None:
        services = self.module["spec"]["implementation"]["compose"]["services"]
        self.assertEqual(sorted(services), ["marquez-api", "marquez-web"])
        self.assertRegex(
            services["marquez-api"]["image"],
            r"^marquezproject/marquez:0\.51\.1@sha256:[0-9a-f]{64}$",
        )
        self.assertRegex(
            services["marquez-web"]["image"],
            r"^marquezproject/marquez-web:0\.51\.1@sha256:[0-9a-f]{64}$",
        )
        for service in services.values():
            self.assertEqual(service["cap_drop"], ["ALL"])
            self.assertTrue(service["read_only"])
            self.assertIn("no-new-privileges:true", service["security_opt"])


if __name__ == "__main__":
    unittest.main()
