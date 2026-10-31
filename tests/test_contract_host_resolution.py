"""Tests for ${service.host} resolution and provided-contract host checks (#1010)."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from cli.planner import build_plan, resolve_provided_contracts
from cli.renderer import (
    _validate_provided_contract_hosts,
    module_dns_host,
    render_compose,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
GARAGE_MODULE_PATH = REPO_ROOT / "modules" / "storage" / "garage" / "module.yaml"

_TEST_SECRETS = {
    "object_storage_access_key": "CDS_OBJECT_STORAGE_ACCESS_KEY",
    "object_storage_secret_key": "CDS_OBJECT_STORAGE_SECRET_KEY",
    "garage_rpc_secret": "CDS_GARAGE_RPC_SECRET",
}


def _instance(inst_id, services, provides, module_name=None):
    module_doc = {
        "spec": {
            "provides": provides,
            "implementation": {
                "kind": "docker-compose",
                "compose": {"services": services},
            },
        }
    }
    inst = {"id": inst_id, "config": {}, "module": module_doc}
    if module_name:
        inst["source"] = f"category/{module_name}"
        module_doc["metadata"] = {"name": module_name}
    return inst


def _object_storage_provide(endpoint):
    return [
        {
            "name": "object-storage",
            "contract": {
                "kind": "object-storage",
                "spec": {"endpoint": endpoint},
            },
        }
    ]


def _garage_profile(inst_id):
    return {
        "apiVersion": "cds/v1alpha1",
        "kind": "Profile",
        "metadata": {"name": "garage-id-test", "environment": "local"},
        "spec": {
            "runtime": {"type": "docker-compose"},
            "modules": [
                {
                    "id": inst_id,
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
                    alias: {"env": env, "required": True}
                    for alias, env in _TEST_SECRETS.items()
                },
            },
        },
    }


class ModuleDnsHostTest(unittest.TestCase):
    def test_single_service_matching_id_stays_the_id(self):
        self.assertEqual(module_dns_host("postgres", {"postgres": {}}), "postgres")

    def test_single_service_with_other_name_is_prefixed(self):
        self.assertEqual(module_dns_host("object-storage", {"garage": {}}), "object-storage-garage")

    def test_single_service_already_prefixed_is_not_duplicated(self):
        self.assertEqual(module_dns_host("api", {"api-gateway": {}}), "api-gateway")

    def test_multi_service_module_keeps_the_instance_id(self):
        self.assertEqual(
            module_dns_host("dagster", {"user-code": {}, "dagster-webserver": {}, "dagster-daemon": {}}),
            "dagster",
        )

    def test_namesake_service_among_auxiliary_services_is_prefixed(self):
        # postgres ships job containers next to the serving service.
        self.assertEqual(
            module_dns_host(
                "analytics-db",
                {"postgres": {}, "postgres-backup": {}, "postgres-restore": {}},
                "postgres",
            ),
            "analytics-db-postgres",
        )

    def test_namesake_service_matching_id_stays_the_id(self):
        self.assertEqual(
            module_dns_host("postgres", {"postgres": {}, "postgres-backup": {}}, "postgres"),
            "postgres",
        )

    def test_multi_service_without_namesake_keeps_the_instance_id(self):
        self.assertEqual(
            module_dns_host(
                "dagster",
                {"user-code": {}, "dagster-webserver": {}, "dagster-daemon": {}},
                "dagster",
            ),
            "dagster",
        )

    def test_missing_or_non_dict_services_keep_the_instance_id(self):
        self.assertEqual(module_dns_host("mod", {}), "mod")
        self.assertEqual(module_dns_host("mod", None), "mod")
        self.assertEqual(module_dns_host("mod", ["svc"]), "mod")


class ResolveProvidedContractsHostTest(unittest.TestCase):
    def test_single_service_instance_resolves_service_dns_name(self):
        inst = _instance(
            "object-storage",
            {"garage": {"image": "dxflrs/garage"}},
            _object_storage_provide("http://${service.dns}:3900"),
        )
        resolved = resolve_provided_contracts(inst)
        self.assertEqual(resolved["object-storage"]["spec"]["endpoint"], "http://object-storage-garage:3900")

    def test_service_host_stays_the_instance_id(self):
        # ${service.host} keeps the id semantics name prefixes rely on
        # (e.g. backup volume names rendered as "<id>-<volume>").
        inst = _instance(
            "object-storage",
            {"garage": {"image": "dxflrs/garage"}},
            [
                {
                    "name": "object-storage",
                    "contract": {
                        "kind": "object-storage",
                        "spec": {"endpoint": "http://${service.dns}:3900", "region": "${service.host}"},
                    },
                }
            ],
        )
        resolved = resolve_provided_contracts(inst)
        self.assertEqual(resolved["object-storage"]["spec"]["region"], "object-storage")

    def test_matching_id_resolves_to_the_id(self):
        inst = _instance(
            "garage",
            {"garage": {"image": "dxflrs/garage"}},
            _object_storage_provide("http://${service.dns}:3900"),
        )
        resolved = resolve_provided_contracts(inst)
        self.assertEqual(resolved["object-storage"]["spec"]["endpoint"], "http://garage:3900")

    def test_namesake_module_instance_resolves_the_serving_service(self):
        # postgres-style module: serving service matches the module name,
        # auxiliary job services do not affect the DNS answer.
        inst = _instance(
            "analytics-db",
            {"postgres": {}, "postgres-backup": {}, "postgres-restore": {}},
            [
                {
                    "name": "sql-database",
                    "contract": {
                        "kind": "sql-database",
                        "spec": {
                            "host": "${service.dns}",
                            "connectionUri": "postgresql://u:p@${service.dns}:5432/db",
                        },
                    },
                }
            ],
            module_name="postgres",
        )
        resolved = resolve_provided_contracts(inst)
        self.assertEqual(resolved["sql-database"]["spec"]["host"], "analytics-db-postgres")
        self.assertEqual(
            resolved["sql-database"]["spec"]["connectionUri"],
            "postgresql://u:p@analytics-db-postgres:5432/db",
        )

    def test_multi_service_instance_keeps_the_instance_id_for_both(self):
        inst = _instance(
            "orchestration",
            {"user-code": {}, "webserver": {}},
            [
                {
                    "name": "http-service",
                    "contract": {
                        "kind": "http-service",
                        "spec": {
                            "host": "${service.host}",
                            "dns": "${service.dns}",
                            "serviceName": "webserver",
                        },
                    },
                }
            ],
        )
        resolved = resolve_provided_contracts(inst)
        self.assertEqual(resolved["http-service"]["spec"]["host"], "orchestration")
        self.assertEqual(resolved["http-service"]["spec"]["dns"], "orchestration")


class ProvidedContractHostValidationTest(unittest.TestCase):
    def test_advertised_host_without_rendered_service_reports_e147(self):
        plan = {
            "modules": [
                {
                    "id": "superset",
                    "provides": {
                        "http-service": {
                            "kind": "http-service",
                            "spec": {"host": "superset", "port": 8088},
                        }
                    },
                }
            ]
        }
        diagnostics = _validate_provided_contract_hosts(plan, {"superset": ["superset-init"]})
        self.assertEqual([d.code for d in diagnostics], ["E150"])
        self.assertEqual(diagnostics[0].level, "error")
        self.assertEqual(diagnostics[0].path, "module:superset.provides.http-service.spec.host")
        self.assertIn("not a compose service", diagnostics[0].message)

    def test_matching_host_passes(self):
        plan = {
            "modules": [
                {
                    "id": "superset",
                    "provides": {
                        "http-service": {
                            "kind": "http-service",
                            "spec": {"host": "superset", "port": 8088},
                        }
                    },
                }
            ]
        }
        self.assertEqual(_validate_provided_contract_hosts(plan, {"superset": ["superset"]}), [])

    def test_service_name_maps_through_compose_naming_rule(self):
        plan = {
            "modules": [
                {
                    "id": "dagster",
                    "provides": {
                        "http-service": {
                            "kind": "http-service",
                            "spec": {"host": "dagster", "serviceName": "dagster-webserver"},
                        }
                    },
                }
            ]
        }
        rendered = {"dagster": ["dagster-user-code", "dagster-webserver", "dagster-daemon"]}
        self.assertEqual(_validate_provided_contract_hosts(plan, rendered), [])

        broken = {"dagster": ["dagster-user-code", "dagster-daemon"]}
        diagnostics = _validate_provided_contract_hosts(plan, broken)
        self.assertEqual([d.code for d in diagnostics], ["E150"])
        self.assertEqual(diagnostics[0].path, "module:dagster.provides.http-service.spec.serviceName")

    def test_sql_database_and_object_storage_hosts_are_checked(self):
        plan = {
            "modules": [
                {
                    "id": "analytics-db",
                    "provides": {
                        "sql-database": {
                            "kind": "sql-database",
                            "spec": {"host": "analytics-db"},
                        },
                        "object-storage": {
                            "kind": "object-storage",
                            "spec": {"endpoint": "http://analytics-db:3900"},
                        },
                    },
                }
            ]
        }
        diagnostics = _validate_provided_contract_hosts(plan, {"analytics-db": ["analytics-db-postgres"]})
        self.assertEqual([d.code for d in diagnostics], ["E150", "E150"])
        self.assertEqual(
            [d.path for d in diagnostics],
            [
                "module:analytics-db.provides.sql-database.spec.host",
                "module:analytics-db.provides.object-storage.spec.endpoint",
            ],
        )

    def test_external_hosts_and_empty_values_are_skipped(self):
        plan = {
            "modules": [
                {
                    "id": "analytics-db",
                    "provides": {
                        "sql-database": {
                            "kind": "sql-database",
                            "spec": {"host": "db.internal.example.com"},
                        },
                        "object-storage": {
                            "kind": "object-storage",
                            "spec": {"endpoint": "https://s3.example.com:443"},
                        },
                        "http-service": {"kind": "http-service", "spec": {}},
                    },
                }
            ]
        }
        self.assertEqual(
            _validate_provided_contract_hosts(plan, {"analytics-db": ["analytics-db-postgres"]}),
            [],
        )

    def test_scheme_less_host_port_is_checked_by_its_host_part(self):
        # Issue #1036: a "garage:3900" endpoint contains a colon and used
        # to be skipped by the _DNS_LABEL_RE gate; its host part is
        # cross-checkable just like a URL endpoint's hostname part.
        plan = {
            "modules": [
                {
                    "id": "garage",
                    "provides": {
                        "object-storage": {
                            "kind": "object-storage",
                            "spec": {"endpoint": "garage:3900"},
                        },
                        "sql-database": {
                            "kind": "sql-database",
                            "spec": {"host": "garage:5432"},
                        },
                    },
                }
            ]
        }
        self.assertEqual(_validate_provided_contract_hosts(plan, {"garage": ["garage"]}), [])
        diagnostics = _validate_provided_contract_hosts(plan, {"garage": ["other"]})
        self.assertEqual([d.code for d in diagnostics], ["E150", "E150"])
        self.assertEqual(
            [d.path for d in diagnostics],
            [
                "module:garage.provides.object-storage.spec.endpoint",
                "module:garage.provides.sql-database.spec.host",
            ],
        )

    def test_scheme_less_host_port_with_non_numeric_port_is_skipped(self):
        plan = {
            "modules": [
                {
                    "id": "garage",
                    "provides": {
                        "sql-database": {
                            "kind": "sql-database",
                            "spec": {"host": "garage:abc"},
                        },
                        "cache-service": {
                            "kind": "cache-service",
                            "spec": {"host": "[::1]:6379"},
                        },
                    },
                }
            ]
        }
        self.assertEqual(_validate_provided_contract_hosts(plan, {"garage": ["other"]}), [])

    def test_module_without_rendered_services_is_skipped(self):
        plan = {
            "modules": [
                {
                    "id": "broken",
                    "provides": {
                        "http-service": {
                            "kind": "http-service",
                            "spec": {"host": "nowhere"},
                        }
                    },
                }
            ]
        }
        self.assertEqual(_validate_provided_contract_hosts(plan, {}), [])


class GarageCustomInstanceIdTest(unittest.TestCase):
    """The issue #1010 reproduction: garage instantiated as id \"object-storage\"."""

    def test_plan_and_render_resolve_the_prefixed_service_name(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            profile_path = Path(tmpdir) / "profile.yaml"
            profile_path.write_text(
                yaml.safe_dump(_garage_profile("object-storage")), encoding="utf-8"
            )
            with patch.dict(
                os.environ,
                {
                    "CDS_MODULE_PATH": str(REPO_ROOT / "modules"),
                    "CDS_OBJECT_STORAGE_ACCESS_KEY": "GK0123456789abcdef0123456789abcdef",
                    "CDS_OBJECT_STORAGE_SECRET_KEY": "a" * 64,
                    "CDS_GARAGE_RPC_SECRET": "b" * 64,
                },
            ):
                plan, plan_diagnostics = build_plan(str(profile_path))

            self.assertIsNotNone(plan)
            self.assertEqual(
                [d.format() for d in plan_diagnostics if d.level == "error"],
                [],
            )
            assert plan is not None
            provided = plan["modules"][0]["provides"]["object-storage"]
            self.assertEqual(
                provided["spec"]["endpoint"],
                "http://object-storage-garage:3900",
            )

            compose_text, render_diagnostics = render_compose(plan)

        self.assertEqual(
            [d.format() for d in render_diagnostics if d.level == "error"],
            [],
        )
        compose = yaml.safe_load(compose_text)
        self.assertIn("object-storage-garage", compose["services"])


_CHECKED_KINDS = {"http-service", "sql-database", "cache-service", "object-storage"}
_HOST_FIELDS = ("host", "endpoint", "connectionUri")


class ModuleYamlDnsMigrationTest(unittest.TestCase):
    """Checked-in modules with a resolvable serving service must advertise
    checked contract kinds via ${service.dns}, not ${service.host} (#1010)."""

    def test_checked_contracts_use_service_dns(self):
        offenders = []
        for root in ("modules", "modules-experimental"):
            for path in sorted((REPO_ROOT / root).rglob("module.yaml")):
                doc = yaml.safe_load(path.read_text(encoding="utf-8"))
                spec = doc.get("spec") or {}
                module_name = str((doc.get("metadata") or {}).get("name") or "")
                services = ((spec.get("implementation") or {}).get("compose") or {}).get("services") or {}
                if not isinstance(services, dict):
                    continue
                if len(services) == 1:
                    serving = next(iter(services))
                elif module_name and module_name in services:
                    serving = module_name
                else:
                    continue  # multi-service without namesake: serviceName mechanism
                for provided in spec.get("provides") or []:
                    contract = provided.get("contract") or {}
                    if contract.get("kind") not in _CHECKED_KINDS:
                        continue
                    fields = contract.get("spec") or {}
                    for field in _HOST_FIELDS:
                        value = fields.get(field)
                        if not isinstance(value, str) or "${service." not in value:
                            continue
                        if "${service.host}" in value:
                            offenders.append(f"{path}:{provided.get('name')}.{field} uses ${{service.host}}")
                        elif serving and "${service.dns}" not in value:
                            offenders.append(f"{path}:{provided.get('name')}.{field} uses neither placeholder")
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
