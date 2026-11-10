"""Tests for the plan/render-time provided-contract port cross-check (E164,
issue #1245): a provided contract whose host is a rendered service must
advertise a numeric port equal to one of that service's declared container
ports (runtime.service.ports[].containerPort / compose containerPort /
Kubernetes Service targetPort), failing closed like E150 does for hosts."""

import unittest

from cli.k8s_renderer import render_helm
from cli.planner import build_plan
from cli.renderer import (
    _validate_provided_contract_ports,
    render_compose,
)


def _plan_with_provide(module_id, spec, kind="sql-database", provides_name="db"):
    return {
        "modules": [
            {"id": module_id, "provides": {provides_name: {"kind": kind, "spec": spec}}}
        ]
    }


class ProvidedContractPortValidationTest(unittest.TestCase):
    """Compose-side unit tests for `_validate_provided_contract_ports`."""

    _RENDERED = {"db": {"db-postgres": {5432}}}

    def test_matching_port_passes(self):
        plan = _plan_with_provide("db", {"host": "db-postgres", "port": 5432})
        self.assertEqual(_validate_provided_contract_ports(plan, self._RENDERED), [])

    def test_host_published_port_fails_closed(self):
        # The #1067 bug shape: advertising the host-published loopback port
        # (15432) instead of the in-network container port (5432) renders
        # green but refuses connections at runtime -> E164.
        plan = _plan_with_provide("db", {"host": "db-postgres", "port": 15432})
        diagnostics = _validate_provided_contract_ports(plan, self._RENDERED)
        self.assertEqual([d.code for d in diagnostics], ["E164"])
        self.assertEqual(diagnostics[0].level, "error")
        self.assertEqual(diagnostics[0].path, "module:db.provides.db.spec.port")
        self.assertIn("db-postgres", diagnostics[0].message)
        self.assertIn("5432", diagnostics[0].message)
        self.assertIn("no such container port", diagnostics[0].message)

    def test_string_port_is_normalized(self):
        plan = _plan_with_provide("db", {"host": "db-postgres", "port": "5432"})
        self.assertEqual(_validate_provided_contract_ports(plan, self._RENDERED), [])

    def test_non_numeric_or_absent_port_is_skipped(self):
        for port in (None, "${config.port}", True, 0, 70000):
            plan = _plan_with_provide("db", {"host": "db-postgres", "port": port})
            self.assertEqual(
                _validate_provided_contract_ports(plan, self._RENDERED),
                [],
                f"port={port!r} should be skipped",
            )

    def test_external_host_is_skipped(self):
        plan = _plan_with_provide(
            "db", {"host": "db.internal.example.com", "port": 1234}
        )
        self.assertEqual(_validate_provided_contract_ports(plan, self._RENDERED), [])

    def test_unrendered_host_is_left_to_e150(self):
        plan = _plan_with_provide("db", {"host": "nowhere", "port": 9999})
        self.assertEqual(_validate_provided_contract_ports(plan, self._RENDERED), [])

    def test_service_without_declared_ports_is_skipped(self):
        rendered = {"db": {"db-postgres": set()}}
        plan = _plan_with_provide("db", {"host": "db-postgres", "port": 9999})
        self.assertEqual(_validate_provided_contract_ports(plan, rendered), [])

    def test_module_without_rendered_services_is_skipped(self):
        plan = _plan_with_provide("broken", {"host": "nowhere", "port": 9999})
        self.assertEqual(_validate_provided_contract_ports(plan, {}), [])

    def test_service_name_maps_through_compose_naming_rule(self):
        plan = {
            "modules": [
                {
                    "id": "dagster",
                    "provides": {
                        "http-service": {
                            "kind": "http-service",
                            "spec": {
                                "host": "dagster",
                                "serviceName": "dagster-webserver",
                                "port": 3000,
                            },
                        }
                    },
                }
            ]
        }
        rendered = {"dagster": {"dagster-webserver": {3000}, "dagster-daemon": set()}}
        self.assertEqual(_validate_provided_contract_ports(plan, rendered), [])

        plan["modules"][0]["provides"]["http-service"]["spec"]["port"] = 8080
        diagnostics = _validate_provided_contract_ports(plan, rendered)
        self.assertEqual([d.code for d in diagnostics], ["E164"])
        self.assertEqual(
            diagnostics[0].path, "module:dagster.provides.http-service.spec.port"
        )
        self.assertIn("dagster-webserver", diagnostics[0].message)

    def test_object_storage_endpoint_port_is_checked(self):
        rendered = {"garage": {"garage": {3900}}}
        ok = _plan_with_provide(
            "garage",
            {"endpoint": "http://garage:3900"},
            kind="object-storage",
            provides_name="object-storage",
        )
        self.assertEqual(_validate_provided_contract_ports(ok, rendered), [])

        bad = _plan_with_provide(
            "garage",
            {"endpoint": "http://garage:13900"},
            kind="object-storage",
            provides_name="object-storage",
        )
        diagnostics = _validate_provided_contract_ports(bad, rendered)
        self.assertEqual([d.code for d in diagnostics], ["E164"])
        self.assertEqual(
            diagnostics[0].path, "module:garage.provides.object-storage.spec.endpoint"
        )

    def test_sink_connection_uri_port_is_checked_too(self):
        rendered = {"marquez": {"marquez-api": {5000}, "marquez-web": {5555}}}
        plan = {
            "modules": [
                {
                    "id": "marquez",
                    "provides": {
                        "lineage-sink": {
                            "kind": "lineage-sink",
                            "spec": {
                                "host": "marquez-api",
                                "port": 5000,
                                "connectionUri": "http://marquez-api:5050",
                                "serviceName": "marquez-api",
                            },
                        }
                    },
                }
            ]
        }
        diagnostics = _validate_provided_contract_ports(plan, rendered)
        self.assertEqual([d.code for d in diagnostics], ["E164"])
        self.assertEqual(
            diagnostics[0].path, "module:marquez.provides.lineage-sink.spec.connectionUri"
        )
        self.assertIn("5050", diagnostics[0].message)

        # Fixing the URI passes; a wrong `port` field fails on its own path.
        plan["modules"][0]["provides"]["lineage-sink"]["spec"][
            "connectionUri"
        ] = "http://marquez-api:5000"
        self.assertEqual(_validate_provided_contract_ports(plan, rendered), [])
        plan["modules"][0]["provides"]["lineage-sink"]["spec"]["port"] = 5001
        diagnostics = _validate_provided_contract_ports(plan, rendered)
        self.assertEqual([d.code for d in diagnostics], ["E164"])
        self.assertEqual(
            diagnostics[0].path, "module:marquez.provides.lineage-sink.spec.port"
        )

    def test_sink_external_connection_uri_is_skipped(self):
        rendered = {"marquez": {"marquez-api": {5000}}}
        plan = {
            "modules": [
                {
                    "id": "marquez",
                    "provides": {
                        "lineage-sink": {
                            "kind": "lineage-sink",
                            "spec": {
                                "host": "marquez-api",
                                "port": 5000,
                                "connectionUri": "https://lineage.example.com:5050",
                            },
                        }
                    },
                }
            ]
        }
        self.assertEqual(_validate_provided_contract_ports(plan, rendered), [])


class RenderComposePortCheckTest(unittest.TestCase):
    """End-to-end through render_compose: the rendered service's container
    port and the plan-carried runtime.service.ports[].containerPort both
    count as declared ports (issue #1245)."""

    def _plan(self, spec, runtime=None):
        module = {
            "id": "db",
            "provides": {"db": {"kind": "sql-database", "spec": spec}},
            "implementation": {
                "kind": "docker-compose",
                "compose": {
                    "services": {
                        "postgres": {
                            "image": "postgres:16",
                            "ports": ["127.0.0.1:15432:5432"],
                        }
                    }
                },
            },
        }
        if runtime is not None:
            module["runtime"] = runtime
        return {"metadata": {"name": "t"}, "modules": [module]}

    def test_wrong_port_fails_render_with_e164(self):
        plan = self._plan({"host": "db-postgres", "port": 15432})
        _, diagnostics = render_compose(plan)
        e164 = [d for d in diagnostics if d.code == "E164"]
        self.assertEqual(len(e164), 1)
        self.assertEqual(e164[0].path, "module:db.provides.db.spec.port")
        self.assertIn("5432", e164[0].message)

    def test_container_port_passes(self):
        plan = self._plan({"host": "db-postgres", "port": 5432})
        _, diagnostics = render_compose(plan)
        self.assertNotIn("E164", {d.code for d in diagnostics})

    def test_runtime_service_ports_count_as_declared(self):
        # The compose service publishes nothing; runtime.service.ports declares
        # the container port, and the plan carries the runtime block.
        plan = self._plan(
            {"host": "db-postgres", "port": 5433},
            runtime={
                "type": "container",
                "service": {
                    "name": "postgres",
                    "ports": [{"name": "postgres", "containerPort": 5433, "protocol": "TCP"}],
                },
            },
        )
        plan["modules"][0]["implementation"]["compose"]["services"]["postgres"][
            "ports"
        ] = []
        _, diagnostics = render_compose(plan)
        self.assertNotIn("E164", {d.code for d in diagnostics})

        plan["modules"][0]["provides"]["db"]["spec"]["port"] = 5432
        _, diagnostics = render_compose(plan)
        e164 = [d for d in diagnostics if d.code == "E164"]
        self.assertEqual(len(e164), 1)
        self.assertIn("5433", e164[0].message)


class HelmPortCheckTest(unittest.TestCase):
    """Helm-target E164 coverage via render_helm (issue #1245)."""

    def _plan(self, spec, kind="http-service", provides_name="http"):
        return {
            "metadata": {"name": "test-chart", "version": "1.2.3"},
            "runtime": {"namespace": "test"},
            "secrets": {},
            "modules": [
                {
                    "id": "demo",
                    "provides": {provides_name: {"kind": kind, "spec": spec}},
                    "implementation": {
                        "kind": "docker-compose",
                        "targets": ["docker-compose", "kubernetes"],
                        "compose": {
                            "services": {
                                "app": {"image": "example/app:1", "ports": ["8080:8080"]}
                            }
                        },
                        "kubernetes": {
                            "workloads": {
                                "app": {
                                    "kind": "StatefulSet",
                                    "containers": ["app"],
                                    "service": {
                                        "type": "ClusterIP",
                                        "ports": [
                                            {"name": "http", "port": 8080, "targetPort": 8080}
                                        ],
                                    },
                                    "resources": {
                                        "app": {
                                            "requests": {"cpu": "10m", "memory": "16Mi"},
                                            "limits": {"memory": "32Mi"},
                                        }
                                    },
                                }
                            }
                        },
                    },
                }
            ],
        }

    def test_matching_port_passes(self):
        files, diagnostics = render_helm(self._plan({"host": "demo-app", "port": 8080}))
        self.assertNotIn("E164", {d.code for d in diagnostics})
        self.assertTrue(files)

    def test_wrong_port_fails_with_e164(self):
        files, diagnostics = render_helm(self._plan({"host": "demo-app", "port": 9999}))
        self.assertEqual(files, {})
        matching = [d for d in diagnostics if d.code == "E164"]
        self.assertEqual(len(matching), 1)
        self.assertEqual(matching[0].level, "error")
        self.assertEqual(matching[0].path, "module:demo.provides.http.spec.port")
        self.assertIn("Kubernetes Service", matching[0].message)
        self.assertIn("8080", matching[0].message)

    def test_service_name_advertisement_resolves_for_port_check(self):
        # serviceName "app" maps to the rendered Service "demo-app"; the port
        # is checked against that Service's targetPort.
        plan = self._plan({"host": "demo", "serviceName": "app", "port": 9999})
        _, diagnostics = render_helm(plan)
        matching = [d for d in diagnostics if d.code == "E164"]
        self.assertEqual(len(matching), 1)
        self.assertIn("demo-app", matching[0].message)

    def test_declared_kubernetes_service_ports_are_used(self):
        # A declared (not rendered) implementation.kubernetes.services entry
        # contributes its targetPort, like postgres's operator-adjacent block.
        plan = self._plan(
            {"host": "demo-postgres", "port": 5433},
            kind="sql-database",
            provides_name="sql",
        )
        plan["modules"][0]["implementation"]["kubernetes"]["services"] = {
            "postgres": {
                "ports": [{"name": "postgres", "port": 5432, "targetPort": 5432}]
            }
        }
        _, diagnostics = render_helm(plan)
        matching = [d for d in diagnostics if d.code == "E164"]
        self.assertEqual(len(matching), 1)
        self.assertIn("5432", matching[0].message)

        plan["modules"][0]["provides"]["sql"]["spec"]["port"] = 5432
        _, diagnostics = render_helm(plan)
        self.assertNotIn("E164", {d.code for d in diagnostics})


class PlannerCarriesRuntimeTest(unittest.TestCase):
    """The plan carries each module's runtime block so render-time checks can
    consult runtime.service.ports[].containerPort (issue #1245)."""

    def test_plan_module_has_runtime(self):
        import os
        import tempfile
        from pathlib import Path
        from unittest.mock import patch

        import yaml

        repo_root = Path(__file__).resolve().parent.parent
        profile = {
            "apiVersion": "cds/v1alpha1",
            "kind": "Profile",
            "metadata": {"name": "runtime-carry-test", "environment": "local"},
            "spec": {
                "runtime": {"type": "docker-compose"},
                "modules": [
                    {
                        "id": "object-storage",
                        "source": "storage/garage",
                        "version": "0.1.0",
                        "enabled": True,
                        "config": {
                            "bucket": "cds-data",
                            "accessKeyFrom": "secrets.object_storage_access_key",
                            "secretKeyFrom": "secrets.object_storage_secret_key",
                            "rpcSecretFrom": "secrets.garage_rpc_secret",
                        },
                    }
                ],
                "secrets": {
                    "provider": {"type": "env"},
                    "values": {
                        "object_storage_access_key": {
                            "env": "CDS_OBJECT_STORAGE_ACCESS_KEY",
                            "required": True,
                        },
                        "object_storage_secret_key": {
                            "env": "CDS_OBJECT_STORAGE_SECRET_KEY",
                            "required": True,
                        },
                        "garage_rpc_secret": {
                            "env": "CDS_GARAGE_RPC_SECRET",
                            "required": True,
                        },
                    },
                },
            },
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            profile_path = Path(tmpdir) / "profile.yaml"
            profile_path.write_text(yaml.safe_dump(profile), encoding="utf-8")
            with patch.dict(
                os.environ,
                {
                    "CDS_MODULE_PATH": str(repo_root / "modules"),
                    "CDS_OBJECT_STORAGE_ACCESS_KEY": "GK0123456789abcdef0123456789abcdef",
                    "CDS_OBJECT_STORAGE_SECRET_KEY": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
                    "CDS_GARAGE_RPC_SECRET": "0123456789abcdef0123456789abcdef",
                },
            ):
                plan, diags = build_plan(str(profile_path))
        self.assertFalse([d for d in diags if d.level == "error"])
        module = plan["modules"][0]
        self.assertEqual(
            module["runtime"]["service"]["ports"][0]["containerPort"], 3900
        )
        # And the compose render of that plan passes the port cross-check.
        _, render_diags = render_compose(plan)
        self.assertNotIn("E164", {d.code for d in render_diags})


if __name__ == "__main__":
    unittest.main()
