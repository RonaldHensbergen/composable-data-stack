"""
Committed grafana module tests (issue #369).

Proves, via the real validate/plan/render pipeline, that the experimental
`modules-experimental/observability/grafana` module:

- provides an `http-service` contract whose host/port resolve through the
  renderer's service-naming rule for any instance id (issue #1010),
- provisions a Postgres "Warehouse" datasource only when a `sql-database`
  binding advertising engine "postgres" is bound (enabledFrom equality gate),
  with the connection values resolved from the binding and the password left
  as a `${CDS_*}` runtime placeholder (secrets never render),
- gates the Prometheus "Metrics" datasource on a `metrics-sink` binding whose
  protocol is "prometheus" (an OTLP collector gets no datasource mount),
- fails validation with E154 when the warehouse binding advertises a
  non-Postgres engine (issue #682 engineFrom guard),
- and keeps the container-hardening baseline (read-only rootfs, dropped
  capabilities, no-new-privileges, tmpfs, non-root uid, loopback-only host
  publish, no debug logging, no docker socket).
"""

import shutil
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
    REPO_ROOT / "modules-experimental" / "observability" / "grafana" / "module.yaml"
)
PROVISIONING_DIR = MODULE_PATH.parent / "provisioning" / "datasources"


class GrafanaModuleTest(unittest.TestCase):
    def setUp(self) -> None:
        self.module = yaml.safe_load(MODULE_PATH.read_text(encoding="utf-8"))
        self.config_schema = self.module["spec"]["configSchema"]

    def test_module_config_schema_and_image_are_valid(self) -> None:
        Draft202012Validator.check_schema(self.config_schema)
        self.assertFalse(self.module["metadata"]["productionSuitable"])
        self.assertEqual(self.module["metadata"]["category"], "observability")
        service = self.module["spec"]["implementation"]["compose"]["services"][
            "grafana"
        ]
        self.assertRegex(
            service["image"],
            r"^grafana/grafana:12\.4\.14@sha256:[0-9a-f]{64}$",
        )

    def test_provides_http_service_and_optional_consumes(self) -> None:
        provides = self.module["spec"]["provides"]
        self.assertEqual(len(provides), 1)
        provided = provides[0]
        self.assertEqual(provided["name"], "http-service")
        self.assertEqual(provided["contract"]["kind"], "http-service")
        contract = provided["contract"]["spec"]
        self.assertEqual(contract["portFrom"], "spec.config.webPort")
        self.assertEqual(contract["host"], "${service.dns}")
        self.assertEqual(contract["port"], "${config.webPort}")
        self.assertEqual(contract["healthPath"], "/api/health")
        self.assertEqual(contract["protocol"], "http")

        consumes = {c["name"]: c for c in self.module["spec"]["consumes"]}
        warehouse = consumes["warehouse-database"]
        self.assertEqual(warehouse["contract"]["kind"], "sql-database")
        self.assertFalse(warehouse["required"])
        self.assertEqual(warehouse["mappedFrom"], "spec.config.warehouseDatabase")
        self.assertEqual(warehouse["engineFrom"], "spec.config.warehouseEngine")
        metrics = consumes["metrics-sink"]
        self.assertEqual(metrics["contract"]["kind"], "metrics-sink")
        self.assertFalse(metrics["required"])
        self.assertEqual(metrics["mappedFrom"], "spec.config.metricsSink")

    def test_container_hardening_baseline(self) -> None:
        service = self.module["spec"]["implementation"]["compose"]["services"][
            "grafana"
        ]
        self.assertEqual(service["user"], "472:472")
        self.assertTrue(service["read_only"])
        self.assertEqual(service["cap_drop"], ["ALL"])
        self.assertIn("no-new-privileges:true", service["security_opt"])
        self.assertTrue(
            any(str(t).startswith("/tmp:") for t in service["tmpfs"]),
            "read-only rootfs needs a writable /tmp tmpfs",
        )
        # Loopback-only host publish (CDS-SEC-020) and no debug logging
        # (CDS-SEC-072).
        for port in service["ports"]:
            self.assertTrue(str(port).startswith("127.0.0.1:"), port)
        env = service["environment"]
        self.assertEqual(env["GF_LOG_LEVEL"], "info")
        self.assertEqual(env["GF_SERVER_PROTOCOL"], "http")
        for key in (
            "GF_ANALYTICS_REPORTING_ENABLED",
            "GF_ANALYTICS_CHECK_FOR_UPDATES",
            "GF_ANALYTICS_CHECK_FOR_PLUGIN_UPDATES",
        ):
            self.assertEqual(env[key], "false", key)
        # No host docker socket access.
        for volume in service.get("volumes", []):
            source = volume.get("source") if isinstance(volume, dict) else volume
            self.assertNotEqual(source, "/var/run/docker.sock")

    def test_provisioning_files_exist_for_the_gated_mounts(self) -> None:
        service = self.module["spec"]["implementation"]["compose"]["services"][
            "grafana"
        ]
        bind_targets = {}
        for volume in service["volumes"]:
            if isinstance(volume, dict) and volume.get("type") == "bind":
                bind_targets[volume["target"]] = volume
        warehouse = bind_targets["/etc/grafana/provisioning/datasources/warehouse.yaml"]
        self.assertEqual(warehouse["source"], "provisioning/datasources/warehouse.yaml")
        self.assertEqual(
            warehouse["enabledFrom"], "bindings.warehouse-database.engine==postgres"
        )
        self.assertTrue((PROVISIONING_DIR / "warehouse.yaml").is_file())
        metrics = bind_targets["/etc/grafana/provisioning/datasources/metrics.yaml"]
        self.assertEqual(
            metrics["enabledFrom"], "bindings.metrics-sink.protocol==prometheus"
        )
        self.assertTrue((PROVISIONING_DIR / "metrics.yaml").is_file())


class GrafanaWiringTest(unittest.TestCase):
    """Runs the real pipeline against temp profiles wired to real modules."""

    def _write_profile(
        self, profile_dir: Path, grafana_config: dict, extra_modules: list | None = None
    ) -> Path:
        modules = [
            {
                "id": "postgres",
                "source": "modules/warehouse/postgres",
                "version": "0.1.0",
                "enabled": True,
                "config": {
                    "database": "analytics",
                    "username": "analytics",
                    "passwordFrom": "secrets.analytics_db_password",
                    "superuserPasswordFrom": "secrets.postgres_superuser_password",
                    "port": 5432,
                },
            },
            {
                "id": "grafana",
                "source": "modules-experimental/observability/grafana",
                "version": "0.1.0",
                "enabled": True,
                "dependsOn": ["postgres"],
                "config": grafana_config,
            },
        ]
        if extra_modules:
            modules = extra_modules + modules
        profile = {
            "apiVersion": "cds/v1alpha1",
            "kind": "Profile",
            "metadata": {"name": "grafana-wiring", "environment": "local"},
            "spec": {
                "runtime": {"type": "docker-compose"},
                "modules": modules,
                "secrets": {
                    "provider": {"type": "env"},
                    "values": {
                        "postgres_superuser_password": {
                            "env": "CDS_POSTGRES_SUPERUSER_PASSWORD",
                            "required": True,
                        },
                        "analytics_db_password": {
                            "env": "CDS_ANALYTICS_DB_PASSWORD",
                            "required": True,
                        },
                        "grafana_admin_password": {
                            "env": "CDS_GRAFANA_ADMIN_PASSWORD",
                            "required": True,
                        },
                    },
                },
            },
        }
        profile_file = profile_dir / "profile.yaml"
        profile_file.write_text(yaml.safe_dump(profile), encoding="utf-8")
        env_file = profile_dir / ".env"
        env_file.write_text(
            "CDS_POSTGRES_SUPERUSER_PASSWORD=superuser_testpass\n"
            "CDS_ANALYTICS_DB_PASSWORD=analytics_testpass\n"
            "CDS_GRAFANA_ADMIN_PASSWORD=admin_testpass\n",
            encoding="utf-8",
        )
        return profile_file

    def _pipeline(self, profile_file: Path, profile_dir: Path):
        with mock.patch.dict(
            "os.environ", {"CDS_MODULE_PATH": str(REPO_ROOT)}, clear=False
        ):
            diagnostics = validate_profile(str(profile_file))
            errors = [d for d in diagnostics if d.level == "error"]
            self.assertEqual(errors, [], msg=f"validation errors: {diagnostics}")

            plan, plan_diagnostics = build_plan(
                str(profile_file), env_file=str(profile_dir / ".env")
            )
            self.assertIsNotNone(plan)
            self.assertEqual(
                [d for d in plan_diagnostics if d.level == "error"],
                [],
                msg=f"plan errors: {plan_diagnostics}",
            )
            output, render_diagnostics = render_compose(
                plan, env_file=str(profile_dir / ".env")
            )
            self.assertEqual(
                [d for d in render_diagnostics if d.level == "error"],
                [],
                msg=f"render errors: {render_diagnostics}",
            )
        return plan, yaml.safe_load(output)

    def test_warehouse_binding_provisions_postgres_datasource(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            profile_dir = Path(root)
            profile_file = self._write_profile(
                profile_dir,
                {
                    "webPort": 13000,
                    "adminUser": {
                        "username": "admin",
                        "passwordFrom": "secrets.grafana_admin_password",
                    },
                    "warehouseDatabase": {"contractRef": "postgres.sql-database"},
                    "healthcheck": {"enabled": True},
                },
            )
            plan, compose = self._pipeline(profile_file, profile_dir)

            grafana_plan = next(m for m in plan["modules"] if m["id"] == "grafana")
            provided = grafana_plan["provides"]["http-service"]["spec"]
            self.assertEqual(provided["host"], "grafana")
            self.assertEqual(provided["port"], 13000)
            self.assertEqual(provided["healthPath"], "/api/health")
            self.assertEqual(provided["protocol"], "http")

            service = compose["services"]["grafana"]
            self.assertEqual(service["ports"], ["127.0.0.1:13000:3000"])

            bind_targets = {
                v["target"]: v
                for v in service["volumes"]
                if isinstance(v, dict) and v.get("type") == "bind"
            }
            # Bound to postgres (engine "postgres"): warehouse datasource
            # mounted; no metrics-sink bound: metrics datasource not mounted.
            self.assertIn(
                "/etc/grafana/provisioning/datasources/warehouse.yaml", bind_targets
            )
            self.assertNotIn(
                "/etc/grafana/provisioning/datasources/metrics.yaml", bind_targets
            )

            env = service["environment"]
            self.assertEqual(env["GF_SECURITY_ADMIN_USER"], "admin")
            # Secrets stay `${CDS_*}` runtime placeholders in rendered output.
            self.assertEqual(
                env["GF_SECURITY_ADMIN_PASSWORD"], "${CDS_GRAFANA_ADMIN_PASSWORD}"
            )
            self.assertEqual(env["CDS_GF_DS_WAREHOUSE_HOST"], "postgres")
            self.assertEqual(str(env["CDS_GF_DS_WAREHOUSE_PORT"]), "5432")
            self.assertEqual(env["CDS_GF_DS_WAREHOUSE_DATABASE"], "analytics")
            self.assertEqual(env["CDS_GF_DS_WAREHOUSE_USERNAME"], "analytics")
            self.assertEqual(
                env["CDS_GF_DS_WAREHOUSE_PASSWORD"], "${CDS_ANALYTICS_DB_PASSWORD}"
            )
            self.assertEqual(env["CDS_GF_DS_METRICS_URL"], "")

    def test_unbound_optional_contracts_render_no_datasource_mounts(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            profile_dir = Path(root)
            profile_file = self._write_profile(
                profile_dir,
                {
                    "adminUser": {"passwordFrom": "secrets.grafana_admin_password"},
                },
            )
            _plan, compose = self._pipeline(profile_file, profile_dir)
            service = compose["services"]["grafana"]
            bind_targets = {
                v["target"]
                for v in service["volumes"]
                if isinstance(v, dict) and v.get("type") == "bind"
            }
            self.assertEqual(bind_targets, set())
            env = service["environment"]
            self.assertEqual(env["CDS_GF_DS_WAREHOUSE_HOST"], "")
            self.assertEqual(env["CDS_GF_DS_WAREHOUSE_PASSWORD"], "")
            self.assertEqual(env["CDS_GF_DS_METRICS_URL"], "")
            # Default webPort applies.
            self.assertEqual(service["ports"], ["127.0.0.1:3000:3000"])

    def test_otlp_metrics_sink_gets_no_prometheus_datasource_mount(self) -> None:
        otel_module = {
            "id": "otel",
            "source": "modules-experimental/observability/otel-collector",
            "version": "0.1.0",
            "enabled": True,
            "config": {},
        }
        with tempfile.TemporaryDirectory() as root:
            profile_dir = Path(root)
            profile_file = self._write_profile(
                profile_dir,
                {
                    "adminUser": {"passwordFrom": "secrets.grafana_admin_password"},
                    "metricsSink": {"contractRef": "otel.metrics-sink"},
                },
                extra_modules=[otel_module],
            )
            _plan, compose = self._pipeline(profile_file, profile_dir)
            service = compose["services"]["grafana"]
            bind_targets = {
                v["target"]
                for v in service["volumes"]
                if isinstance(v, dict) and v.get("type") == "bind"
            }
            # otel-collector advertises protocol "otlp-http": Grafana cannot
            # query that directly, so no datasource file is mounted...
            self.assertEqual(bind_targets, set())
            # ...but the binding values are still resolved into the env for
            # provisioning files should a future gate mount one.
            self.assertEqual(
                service["environment"]["CDS_GF_DS_METRICS_URL"],
                "http://otel-collector:4318",
            )


class GrafanaEngineGuardTest(unittest.TestCase):
    """Issue #682 guard: a warehouse binding advertising a non-Postgres engine
    must fail validation with E154 (the provisioned datasource speaks Postgres)."""

    _FAKE_MYSQL_MODULE = {
        "apiVersion": "cds/v1alpha1",
        "kind": "Module",
        "metadata": {"name": "fake-mysql", "category": "warehouse", "version": "0.1.0"},
        "spec": {
            "runtime": {
                "type": "container",
                "service": {
                    "name": "fake-mysql",
                    "ports": [
                        {"name": "mysql", "containerPort": 3306, "protocol": "TCP"}
                    ],
                },
            },
            "configSchema": {"type": "object", "additionalProperties": False},
            "provides": [
                {
                    "name": "sql-database",
                    "contract": {
                        "kind": "sql-database",
                        "spec": {
                            "host": "fake-mysql",
                            "port": 3306,
                            "database": "analytics",
                            "username": "analytics",
                            "password": "secrets.analytics_db_password",
                            "engine": "mysql",
                        },
                    },
                }
            ],
            "implementation": {"kind": "docker-compose", "compose": {"services": {}}},
        },
    }

    def test_non_postgres_warehouse_binding_fails_e154(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            scratch = Path(root)
            # Scratch modules root: a copy of the real grafana module plus a
            # fake sql-database provider advertising engine "mysql".
            shutil.copytree(
                REPO_ROOT / "modules-experimental" / "observability" / "grafana",
                scratch / "modules-experimental" / "observability" / "grafana",
            )
            fake_dir = scratch / "modules" / "warehouse" / "fake-mysql"
            fake_dir.mkdir(parents=True)
            (fake_dir / "module.yaml").write_text(
                yaml.safe_dump(self._FAKE_MYSQL_MODULE), encoding="utf-8"
            )
            profile_dir = scratch / "profiles" / "local"
            profile_dir.mkdir(parents=True)
            profile = {
                "apiVersion": "cds/v1alpha1",
                "kind": "Profile",
                "metadata": {"name": "grafana-engine-guard", "environment": "local"},
                "spec": {
                    "runtime": {"type": "docker-compose"},
                    "modules": [
                        {
                            "id": "fake-mysql",
                            "source": "modules/warehouse/fake-mysql",
                            "version": "0.1.0",
                            "enabled": True,
                            "config": {},
                        },
                        {
                            "id": "grafana",
                            "source": "modules-experimental/observability/grafana",
                            "version": "0.1.0",
                            "enabled": True,
                            "dependsOn": ["fake-mysql"],
                            "config": {
                                "adminUser": {
                                    "passwordFrom": "secrets.grafana_admin_password"
                                },
                                "warehouseDatabase": {
                                    "contractRef": "fake-mysql.sql-database"
                                },
                            },
                        },
                    ],
                    "secrets": {
                        "provider": {"type": "env"},
                        "values": {
                            "analytics_db_password": {
                                "env": "CDS_ANALYTICS_DB_PASSWORD",
                                "required": True,
                            },
                            "grafana_admin_password": {
                                "env": "CDS_GRAFANA_ADMIN_PASSWORD",
                                "required": True,
                            },
                        },
                    },
                },
            }
            profile_file = profile_dir / "profile.yaml"
            profile_file.write_text(yaml.safe_dump(profile), encoding="utf-8")

            with mock.patch.dict(
                "os.environ", {"CDS_MODULE_PATH": str(scratch)}, clear=False
            ):
                diagnostics = validate_profile(str(profile_file))
            errors = [d for d in diagnostics if d.level == "error"]
            e154 = [d for d in errors if d.code == "E154"]
            self.assertEqual(len(e154), 1, msg=f"diagnostics: {diagnostics}")
            self.assertIn('"postgres"', e154[0].message)
            self.assertIn('"mysql"', e154[0].message)
            self.assertIn("fake-mysql.sql-database", e154[0].message)


if __name__ == "__main__":
    unittest.main()
