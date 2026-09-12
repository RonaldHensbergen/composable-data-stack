import importlib.util
import sys
import unittest
import unittest.mock
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = REPO_ROOT / "scripts" / "compose_to_module.py"

_spec = importlib.util.spec_from_file_location("compose_to_module", SCRIPT_PATH)
assert _spec is not None and _spec.loader is not None
compose_to_module = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = compose_to_module
_spec.loader.exec_module(compose_to_module)


class BuildScaffoldTest(unittest.TestCase):
    def test_literal_port_and_env_become_config_properties(self) -> None:
        compose = {
            "services": {
                "postgres": {
                    "image": "postgres:16",
                    "ports": ["5432:5432"],
                    "environment": {
                        "POSTGRES_DB": "analytics",
                        "POSTGRES_PASSWORD": "${CDS_ANALYTICS_POSTGRES_PASSWORD}",
                    },
                }
            }
        }
        scaffold = compose_to_module.build_scaffold(compose, ["postgres"], "postgres", "warehouse")
        module = scaffold.to_module_dict()

        props = module["spec"]["configSchema"]["properties"]
        self.assertEqual(props["postgresPort"]["default"], 5432)
        self.assertEqual(props["postgresDb"]["default"], "analytics")
        self.assertIn("postgresPasswordFrom", module["spec"]["configSchema"]["required"])
        self.assertEqual(props["postgresPasswordFrom"]["pattern"], "^secrets\\.[a-zA-Z0-9_-]+$")

        service = module["spec"]["implementation"]["compose"]["services"]["postgres"]
        self.assertEqual(service["ports"], ["${config.postgresPort}:5432"])
        self.assertEqual(service["environment"]["POSTGRES_PASSWORD"], "${config.postgresPasswordFrom}")
        self.assertFalse(scaffold.todos)

    def test_bare_var_referencing_another_service_is_flagged_as_todo(self) -> None:
        compose = {
            "services": {
                "app": {
                    "image": "example/app:1.0",
                    "environment": {"DATABASE_HOST": "${POSTGRES_HOST}"},
                }
            }
        }
        scaffold = compose_to_module.build_scaffold(compose, ["app"], "app", "bi")
        module = scaffold.to_module_dict()

        service = module["spec"]["implementation"]["compose"]["services"]["app"]
        # Left untouched (not turned into a config property) and flagged.
        self.assertEqual(service["environment"]["DATABASE_HOST"], "${POSTGRES_HOST}")
        self.assertTrue(any("consumes contract binding" in todo for todo in scaffold.todos))

    def test_hardcoded_connection_string_is_flagged_not_defaulted(self) -> None:
        compose = {
            "services": {
                "superset": {
                    "image": "apache/superset:6.1.0",
                    "environment": {
                        "SUPERSET_DATABASE_URI": (
                            "postgresql://superset:${CDS_SUPERSET_POSTGRES_PASSWORD}@postgres:5432/superset"
                        ),
                    },
                }
            }
        }
        scaffold = compose_to_module.build_scaffold(compose, ["superset"], "superset", "bi")
        module = scaffold.to_module_dict()

        props = module["spec"]["configSchema"]["properties"]
        self.assertNotIn("supersetDatabaseUri", props)
        service = module["spec"]["implementation"]["compose"]["services"]["superset"]
        self.assertEqual(
            service["environment"]["SUPERSET_DATABASE_URI"],
            "postgresql://superset:${CDS_SUPERSET_POSTGRES_PASSWORD}@postgres:5432/superset",
        )
        self.assertTrue(any("hardcoded connection string" in todo for todo in scaffold.todos))

    def test_external_depends_on_is_flagged_as_todo(self) -> None:
        compose = {
            "services": {
                "worker": {"image": "example/worker:1.0", "depends_on": {"postgres": {"condition": "service_started"}}},
                "postgres": {"image": "example/custom-thing:1.0"},
            }
        }
        scaffold = compose_to_module.build_scaffold(compose, ["worker"], "worker", "orchestration")
        module = scaffold.to_module_dict()

        self.assertTrue(any("depends_on external service" in todo for todo in scaffold.todos))
        # The flagged target isn't defined anywhere in this module's own
        # compose.services, so leaving it in depends_on would be a dangling
        # reference -- it must be stripped, not just flagged.
        service = module["spec"]["implementation"]["compose"]["services"]["worker"]
        self.assertNotIn("depends_on", service)

    def test_external_depends_on_list_form_is_stripped(self) -> None:
        compose = {
            "services": {
                "app": {"image": "example/app:1.0", "depends_on": ["db"]},
                "db": {"image": "example/custom-thing:1.0"},
            }
        }
        scaffold = compose_to_module.build_scaffold(compose, ["app"], "app", "bi")
        module = scaffold.to_module_dict()

        service = module["spec"]["implementation"]["compose"]["services"]["app"]
        self.assertNotIn("depends_on", service)

    def test_known_infra_dependency_points_at_existing_contract_provider(self) -> None:
        compose = {
            "services": {
                "app": {"image": "example/app:1.0", "depends_on": ["db"]},
                "db": {"image": "postgres:14"},
            }
        }
        scaffold = compose_to_module.build_scaffold(compose, ["app"], "app", "bi")
        scaffold.to_module_dict()

        todo = next(t for t in scaffold.todos if "'db'" in t)
        self.assertIn("sql-database", todo)
        self.assertIn("Don't scaffold this as a new module", todo)
        self.assertIn("warehouse/postgres", todo)

    def test_known_infra_dependency_without_existing_provider_names_contract_file(self) -> None:
        compose = {
            "services": {
                "app": {"image": "example/app:1.0", "depends_on": ["cache"]},
                "cache": {"image": "valkey/valkey:8"},
            }
        }
        with unittest.mock.patch.object(compose_to_module, "_find_existing_contract_providers", return_value=[]):
            scaffold = compose_to_module.build_scaffold(compose, ["app"], "app", "bi")
            scaffold.to_module_dict()

        todo = next(t for t in scaffold.todos if "'cache'" in t)
        self.assertIn("cache-service", todo)
        self.assertIn("shared/contracts/cache-service.yaml", todo)

    def test_multi_service_merge_keeps_internal_depends_on_untouched(self) -> None:
        compose = {
            "services": {
                "daemon": {"image": "dagster:1.0", "depends_on": {"user-code": {"condition": "service_healthy"}}},
                "user-code": {"image": "dagster-user-code:1.0"},
            }
        }
        scaffold = compose_to_module.build_scaffold(compose, ["daemon", "user-code"], "dagster", "orchestration")
        module = scaffold.to_module_dict()

        services = module["spec"]["implementation"]["compose"]["services"]
        self.assertIn("user-code", services)
        self.assertIn("daemon", services)
        self.assertEqual(services["daemon"]["depends_on"], {"user-code": {"condition": "service_healthy"}})
        self.assertFalse(scaffold.todos)

    def test_mixed_internal_and_external_depends_on_keeps_only_internal(self) -> None:
        compose = {
            "services": {
                "daemon": {
                    "image": "dagster:1.0",
                    "depends_on": {
                        "user-code": {"condition": "service_healthy"},
                        "db": {"condition": "service_healthy"},
                    },
                },
                "user-code": {"image": "dagster-user-code:1.0"},
                "db": {"image": "postgres:16"},
            }
        }
        scaffold = compose_to_module.build_scaffold(compose, ["daemon", "user-code"], "dagster", "orchestration")
        module = scaffold.to_module_dict()

        service = module["spec"]["implementation"]["compose"]["services"]["daemon"]
        self.assertEqual(service["depends_on"], {"user-code": {"condition": "service_healthy"}})
        self.assertTrue(any("'db'" in todo for todo in scaffold.todos))

    def test_missing_service_raises_scaffold_error(self) -> None:
        compose = {"services": {"postgres": {"image": "postgres:16"}}}
        with self.assertRaises(SystemExit):
            compose_to_module.build_scaffold(compose, ["nope"], "x", "warehouse")

    def test_generated_module_passes_schema_self_check(self) -> None:
        compose = {
            "services": {
                "postgres": {
                    "image": "postgres:16",
                    "ports": ["5432:5432"],
                    "environment": {"POSTGRES_PASSWORD": "${CDS_ANALYTICS_POSTGRES_PASSWORD}"},
                }
            }
        }
        scaffold = compose_to_module.build_scaffold(compose, ["postgres"], "postgres", "warehouse")
        module = scaffold.to_module_dict()
        self.assertEqual(compose_to_module._self_check(module), [])


class PortParsingTest(unittest.TestCase):
    def test_short_form_host_and_container(self) -> None:
        self.assertEqual(compose_to_module._parse_port_entry("5432:5432"), (5432, 5432, "TCP"))

    def test_container_only(self) -> None:
        self.assertEqual(compose_to_module._parse_port_entry("5432"), (None, 5432, "TCP"))

    def test_host_ip_host_port_container_port(self) -> None:
        self.assertEqual(compose_to_module._parse_port_entry("127.0.0.1:5432:5432"), (5432, 5432, "TCP"))

    def test_udp_suffix(self) -> None:
        self.assertEqual(compose_to_module._parse_port_entry("53:53/udp"), (53, 53, "UDP"))

    def test_long_form_mapping(self) -> None:
        entry = {"target": 5432, "published": 5433, "protocol": "tcp"}
        self.assertEqual(compose_to_module._parse_port_entry(entry), (5433, 5432, "TCP"))


class PortFieldNameDedupTest(unittest.TestCase):
    """Regression coverage for the _port_field_name() suffix loop: it must
    return the first *free* candidate name, not hang looping over free
    names waiting to land on one that's already taken."""

    def test_no_collision_returns_base_name(self) -> None:
        self.assertEqual(compose_to_module._port_field_name(set(), 8080), "httpPort")

    def test_single_collision_returns_suffixed_name(self) -> None:
        self.assertEqual(compose_to_module._port_field_name({"httpPort"}, 3000), "httpPort2")

    def test_multiple_collisions_returns_next_free_suffix(self) -> None:
        existing = {"httpPort", "httpPort2", "httpPort3"}
        self.assertEqual(compose_to_module._port_field_name(existing, 3000), "httpPort4")

    def test_merging_two_services_with_same_known_port_name_terminates(self) -> None:
        # 3000 and 8080 both map to "http" in _KNOWN_PORT_NAMES; merging two
        # services exposing them (e.g. a webserver + an admin UI) must not
        # hang -- this previously looped forever due to an inverted `while`
        # condition in _port_field_name().
        compose = {
            "services": {
                "webserver": {"image": "example/webserver:1.0", "ports": ["3000:3000"]},
                "admin": {"image": "example/admin:1.0", "ports": ["8080:8080"]},
            }
        }
        scaffold = compose_to_module.build_scaffold(compose, ["webserver", "admin"], "app", "bi")
        module = scaffold.to_module_dict()

        props = module["spec"]["configSchema"]["properties"]
        self.assertEqual(set(props), {"httpPort", "httpPort2"})
        self.assertEqual(props["httpPort"]["default"], 3000)
        self.assertEqual(props["httpPort2"]["default"], 8080)


if __name__ == "__main__":
    unittest.main()
