import tempfile
import unittest
from pathlib import Path
from unittest import mock

import yaml
from jsonschema import Draft202012Validator

from cli.planner import build_plan
from cli.renderer import render_compose
from cli.validator import validate_profile

REPO_ROOT = Path(__file__).resolve().parent.parent
MODULE_PATH = (
    REPO_ROOT
    / "modules-experimental"
    / "observability"
    / "loki"
    / "module.yaml"
)


class LokiModuleTest(unittest.TestCase):
    def setUp(self) -> None:
        self.module = yaml.safe_load(MODULE_PATH.read_text(encoding="utf-8"))
        self.config_schema = self.module["spec"]["configSchema"]

    def test_module_config_schema_and_images_are_valid(self) -> None:
        Draft202012Validator.check_schema(self.config_schema)
        self.assertFalse(self.module["metadata"]["productionSuitable"])
        services = self.module["spec"]["implementation"]["compose"]["services"]
        self.assertRegex(
            services["loki"]["image"],
            r"^timberio/vector:0\.51\.0-alpine@sha256:[0-9a-f]{64}$",
        )
        self.assertRegex(
            services["loki-storage"]["image"],
            r"^grafana/loki:3\.6\.1@sha256:[0-9a-f]{64}$",
        )

    def test_log_sink_contract_exposes_internal_ingest_and_retention(self) -> None:
        provided = self.module["spec"]["provides"][0]
        self.assertEqual(provided["name"], "log-sink")
        self.assertEqual(provided["contract"]["kind"], "log-sink")
        contract = provided["contract"]["spec"]
        self.assertEqual(contract["host"], "${service.dns}")
        self.assertEqual(contract["port"], 8686)
        self.assertEqual(contract["protocol"], "http")
        self.assertEqual(contract["ingestPath"], "/logs")
        self.assertEqual(contract["rawRetentionDays"], "${config.rawRetentionDays}")
        self.assertEqual(
            contract["structuredRetentionDays"],
            "${config.structuredRetentionDays}",
        )
        self.assertEqual(contract["serviceName"], "loki")

    def test_profile_validation_and_render_publish_the_contract_endpoint(self) -> None:
        profile = {
            "apiVersion": "cds/v1alpha1",
            "kind": "Profile",
            "metadata": {"name": "loki-test", "environment": "local"},
            "spec": {
                "runtime": {"type": "docker-compose"},
                "modules": [
                    {
                        "id": "logs",
                        "source": "observability/loki",
                        "version": "0.1.0",
                        "enabled": True,
                        "config": {"ingestPort": 18686},
                    }
                ],
                "observability": {
                    "logShipping": {
                        "enabled": True,
                        "sink": {"contractRef": "logs.log-sink"},
                    }
                },
            },
        }

        with tempfile.TemporaryDirectory() as tmp:
            profile_path = Path(tmp) / "profile.yaml"
            profile_path.write_text(yaml.safe_dump(profile), encoding="utf-8")

            with mock.patch.dict(
                "os.environ",
                {"CDS_MODULE_PATH": str(REPO_ROOT / "modules-experimental")},
                clear=False,
            ):
                diagnostics = validate_profile(str(profile_path))
                errors = [d.format() for d in diagnostics if d.level == "error"]
                self.assertEqual(errors, [])

                plan, plan_diagnostics = build_plan(str(profile_path))
                self.assertIsNotNone(plan)
                self.assertEqual(
                    [d.format() for d in plan_diagnostics if d.level == "error"],
                    [],
                )
                assert plan is not None
                provided = plan["modules"][0]["provides"]["log-sink"]["spec"]
                self.assertEqual(provided["host"], "logs-loki")
                self.assertEqual(provided["port"], 8686)
                self.assertEqual(provided["ingestPath"], "/logs")
                self.assertEqual(provided["rawRetentionDays"], 7)
                self.assertEqual(provided["structuredRetentionDays"], 90)
                self.assertEqual(provided["serviceName"], "loki")

                rendered, render_diagnostics = render_compose(plan)

        self.assertEqual(
            [d.format() for d in render_diagnostics if d.level == "error"],
            [],
        )
        compose = yaml.safe_load(rendered)
        self.assertIn("logs-loki", compose["services"])
        self.assertIn("logs-loki-storage", compose["services"])
        self.assertEqual(
            compose["services"]["logs-loki"]["ports"],
            ["127.0.0.1:18686:8686"],
        )
        self.assertEqual(
            compose["services"]["logs-loki-storage"]["environment"],
            {"RAW_RETENTION_DAYS": "7d", "STRUCTURED_RETENTION_DAYS": "90d"},
        )
        self.assertEqual(
            compose["services"]["logs-loki"]["environment"]["LOKI_ENDPOINT"],
            "http://logs-loki-storage:3100",
        )

    def test_ingestion_is_not_granted_host_docker_socket_access(self) -> None:
        services = self.module["spec"]["implementation"]["compose"]["services"]
        for service in services.values():
            mounts = service.get("volumes", [])
            self.assertFalse(
                any(
                    isinstance(mount, dict)
                    and mount.get("source") == "/var/run/docker.sock"
                    for mount in mounts
                )
            )

        vector_config = (MODULE_PATH.parent / "vector.yaml").read_text(
            encoding="utf-8"
        )
        self.assertIn('path: /logs', vector_config)
        self.assertIn('.tier = "structured"', vector_config)
        self.assertIn('.tier = "raw"', vector_config)


if __name__ == "__main__":
    unittest.main()
