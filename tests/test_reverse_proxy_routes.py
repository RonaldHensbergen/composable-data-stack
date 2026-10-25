"""Tests for reverse-proxy dynamic route generation from contract bindings (#581)."""
import tempfile
import unittest
from pathlib import Path

import yaml

from cli import planner
from cli.renderer import _generate_reverse_proxy_routes, render_compose
from cli.validator import validate_module_configs

_REPO_ROOT = Path(__file__).resolve().parent.parent


def _proxy_module(proxy_id="proxy"):
    return {
        "index": 0,
        "id": proxy_id,
        "config": {},
        "module": {
            "spec": {
                "provides": [
                    {"name": "reverse-proxy", "contract": {"kind": "reverse-proxy", "spec": {}}}
                ]
            }
        },
        "provides": {"reverse-proxy": {"kind": "reverse-proxy", "spec": {}}},
    }


def _http_target(target_id="superset", host="superset", port=8088, service_name=None):
    spec = {"host": host, "port": port}
    if service_name is not None:
        spec["serviceName"] = service_name
    return {
        "index": 1,
        "id": target_id,
        "config": {},
        "module": {
            "spec": {
                "provides": [
                    {
                        "name": "http-service",
                        "contract": {
                            "kind": "http-service",
                            "spec": {"protocol": "http", "portFrom": "spec.config.webPort"},
                        },
                    }
                ]
            }
        },
        "provides": {
            "http-service": {
                "kind": "http-service",
                "spec": spec,
            }
        },
    }


def _plan(proxy, target, certificates=None):
    return {
        "modules": [proxy, target],
        "certificates": certificates or {},
    }


def _rendered_services(target_id="superset", names=None):
    """The compose service names a render produced for the target module."""
    if names is None:
        names = [target_id]
    return {target_id: names}


class RouteGenerationUnitTest(unittest.TestCase):
    def test_routes_generate_deterministic_traefik_document(self):
        proxy = _proxy_module()
        proxy["config"] = {
            "routes": [
                {"host": "app.example.local", "target": "superset"},
                {"host": "alt.example.local", "target": "superset", "entryPoints": ["web"], "tls": False},
            ]
        }
        target = _http_target()
        services = _rendered_services()
        documents, diagnostics = _generate_reverse_proxy_routes(_plan(proxy, target), services)
        self.assertEqual(diagnostics, [])
        doc = documents["proxy"]
        self.assertEqual(
            sorted(doc["http"]["routers"]),
            ["cds-route-0-alt.example.local", "cds-route-1-app.example.local"],
        )
        self.assertEqual(doc["http"]["services"]["cds-service-superset"]["loadBalancer"]["servers"],
                         [{"url": "http://superset:8088"}])
        # Sorted order: alt.example.local (index 0, explicit entrypoint, no tls)
        # before app.example.local (index 1, defaults).
        self.assertEqual(doc["http"]["routers"]["cds-route-0-alt.example.local"]["entryPoints"], ["web"])
        self.assertNotIn("tls", doc["http"]["routers"]["cds-route-0-alt.example.local"])
        self.assertEqual(doc["http"]["routers"]["cds-route-1-app.example.local"]["entryPoints"], ["websecure"])
        self.assertEqual(doc["http"]["routers"]["cds-route-1-app.example.local"]["tls"], {})
        self.assertNotIn("tls", doc)  # no certificate alias -> no tls section

        # Deterministic: identical input -> identical document.
        documents2, _ = _generate_reverse_proxy_routes(_plan(proxy, target), services)
        self.assertEqual(documents, documents2)

    def test_service_name_maps_through_compose_naming_rule(self):
        # Multi-service target shaped like dagster: the instance id does not
        # resolve, the serving compose service does once prefixed.
        proxy = _proxy_module()
        proxy["config"] = {"routes": [{"host": "orch.example.local", "target": "dagster"}]}
        target = _http_target(
            target_id="dagster", host="dagster", port=3000, service_name="dagster-webserver"
        )
        documents, diagnostics = _generate_reverse_proxy_routes(
            _plan(proxy, target),
            _rendered_services("dagster", ["dagster-user-code", "dagster-webserver", "dagster-daemon"]),
        )
        self.assertEqual(diagnostics, [])
        self.assertEqual(
            documents["proxy"]["http"]["services"]["cds-service-dagster"]["loadBalancer"]["servers"],
            [{"url": "http://dagster-webserver:3000"}],
        )

    def test_service_name_prefixes_for_unprefixed_instance(self):
        # An instance id that is not a prefix of the service name gets the
        # renderer's `<instance-id>-<service>` mapping.
        proxy = _proxy_module()
        proxy["config"] = {"routes": [{"host": "api.example.local", "target": "api"}]}
        target = _http_target(target_id="api", host="api", port=3000, service_name="gateway")
        documents, diagnostics = _generate_reverse_proxy_routes(
            _plan(proxy, target), _rendered_services("api", ["api-gateway"])
        )
        self.assertEqual(diagnostics, [])
        self.assertEqual(
            documents["proxy"]["http"]["services"]["cds-service-api"]["loadBalancer"]["servers"],
            [{"url": "http://api-gateway:3000"}],
        )

    def test_backend_address_without_rendered_service_reports_e136(self):
        proxy = _proxy_module()
        proxy["config"] = {"routes": [{"host": "x.example.local", "target": "superset"}]}
        # The advertised host matches no rendered service (e.g. the module
        # has several compose services and none is named after the id).
        _, diagnostics = _generate_reverse_proxy_routes(
            _plan(proxy, _http_target()), _rendered_services("superset", ["superset-init"])
        )
        self.assertEqual([d.code for d in diagnostics], ["E136"])
        self.assertIn("not a rendered compose service", diagnostics[0].message)
        self.assertIn("superset-init", diagnostics[0].message)

    def test_unknown_target_reports_e136(self):
        proxy = _proxy_module()
        proxy["config"] = {"routes": [{"host": "x.example.local", "target": "ghost"}]}
        documents, diagnostics = _generate_reverse_proxy_routes(
            _plan(proxy, _http_target()), _rendered_services()
        )
        self.assertEqual([d.code for d in diagnostics], ["E136"])
        self.assertIn("unknown module", diagnostics[0].message)
        self.assertEqual(diagnostics[0].path, "module:proxy.config.routes[0].target")
        self.assertEqual(documents, {})

    def test_target_without_port_reports_e136(self):
        proxy = _proxy_module()
        proxy["config"] = {"routes": [{"host": "x.example.local", "target": "superset"}]}
        broken = _http_target()
        broken["provides"]["http-service"]["spec"] = {"host": "superset"}
        _, diagnostics = _generate_reverse_proxy_routes(_plan(proxy, broken), _rendered_services())
        self.assertEqual([d.code for d in diagnostics], ["E136"])
        self.assertIn("resolvable http-service endpoint", diagnostics[0].message)

    def test_target_without_resolvable_endpoint_reports_e136(self):
        proxy = _proxy_module()
        proxy["config"] = {"routes": [{"host": "x.example.local", "target": "superset"}]}
        broken = _http_target()
        broken["provides"]["http-service"]["spec"] = {"protocol": "http", "portFrom": "spec.config.webPort"}
        _, diagnostics = _generate_reverse_proxy_routes(_plan(proxy, broken), _rendered_services())
        self.assertEqual([d.code for d in diagnostics], ["E136"])
        self.assertIn("resolvable http-service endpoint", diagnostics[0].message)

    def test_diagnostic_path_keeps_profile_route_index(self):
        proxy = _proxy_module()
        # Written in reverse-sorted order so the sorted position differs
        # from the profile index of the failing route.
        proxy["config"] = {
            "routes": [
                {"host": "zzz.example.local", "target": "superset"},
                {"host": "aaa.example.local", "target": "ghost"},
            ]
        }
        _, diagnostics = _generate_reverse_proxy_routes(_plan(proxy, _http_target()), _rendered_services())
        self.assertEqual([d.code for d in diagnostics], ["E136"])
        # The failing route is routes[1] in the profile, no matter where the
        # deterministic output ordering places it.
        self.assertEqual(diagnostics[0].path, "module:proxy.config.routes[1].target")

    def test_unknown_certificate_alias_reports_e136(self):
        proxy = _proxy_module()
        proxy["config"] = {"routes": [
            {"host": "x.example.local", "target": "superset", "certificate": "missing"}
        ]}
        _, diagnostics = _generate_reverse_proxy_routes(
            _plan(proxy, _http_target()), _rendered_services()
        )
        self.assertEqual([d.code for d in diagnostics], ["E136"])
        self.assertIn("not declared in spec.certificates.values", diagnostics[0].message)
        self.assertEqual(diagnostics[0].path, "module:proxy.config.routes[0].certificate")

    def test_valid_certificate_alias_wires_certificate_files(self):
        proxy = _proxy_module()
        proxy["config"] = {"routes": [
            {"host": "x.example.local", "target": "superset", "certificate": "tls"}
        ]}
        documents, diagnostics = _generate_reverse_proxy_routes(
            _plan(proxy, _http_target(), certificates={"tls": {"cert": "CDS_TLS_CERT", "key": "CDS_TLS_KEY"}}),
            _rendered_services(),
        )
        self.assertEqual(diagnostics, [])
        self.assertEqual(
            documents["proxy"]["tls"]["certificates"],
            [{"certFile": "/etc/traefik/certs/tls.crt", "keyFile": "/etc/traefik/certs/tls.key"}],
        )

    def test_certificate_on_plaintext_router_is_validated_but_not_wired(self):
        # A plaintext router never presents a certificate, so its alias must
        # not contribute to the document's tls.certificates section; the
        # alias itself is still validated like any other.
        proxy = _proxy_module()
        proxy["config"] = {"routes": [
            {"host": "x.example.local", "target": "superset", "tls": False, "certificate": "tls"}
        ]}
        documents, diagnostics = _generate_reverse_proxy_routes(
            _plan(proxy, _http_target(), certificates={"tls": {"cert": "CDS_TLS_CERT", "key": "CDS_TLS_KEY"}}),
            _rendered_services(),
        )
        self.assertEqual(diagnostics, [])
        self.assertNotIn("tls", documents["proxy"])

    def test_unknown_certificate_alias_on_plaintext_router_still_reports_e136(self):
        proxy = _proxy_module()
        proxy["config"] = {"routes": [
            {"host": "x.example.local", "target": "superset", "tls": False, "certificate": "missing"}
        ]}
        _, diagnostics = _generate_reverse_proxy_routes(
            _plan(proxy, _http_target()), _rendered_services()
        )
        self.assertEqual([d.code for d in diagnostics], ["E136"])
        self.assertIn("not declared in spec.certificates.values", diagnostics[0].message)
        self.assertEqual(diagnostics[0].path, "module:proxy.config.routes[0].certificate")

    def test_non_proxy_modules_are_ignored(self):
        target = _http_target()
        target["module"] = {"spec": {"provides": []}}
        documents, diagnostics = _generate_reverse_proxy_routes(
            _plan(_proxy_module(), target), _rendered_services()
        )
        self.assertEqual(documents, {})
        self.assertEqual(diagnostics, [])

    def _multi_http_target(self):
        """A module exposing two http-service contracts (dotted-target case)."""
        target = _http_target(target_id="app", host="app", port=3000, service_name="gateway")
        target["provides"]["admin"] = {
            "kind": "http-service",
            "spec": {"host": "app", "port": 9090, "serviceName": "admin"},
        }
        return target

    def test_dotted_target_selects_named_contract(self):
        proxy = _proxy_module()
        proxy["config"] = {"routes": [
            {"host": "api.example.local", "target": "app.http-service"},
            {"host": "admin.example.local", "target": "app.admin"},
        ]}
        documents, diagnostics = _generate_reverse_proxy_routes(
            _plan(proxy, self._multi_http_target()),
            _rendered_services("app", ["app-gateway", "app-admin"]),
        )
        self.assertEqual(diagnostics, [])
        services = documents["proxy"]["http"]["services"]
        self.assertEqual(
            services["cds-service-app.http-service"]["loadBalancer"]["servers"],
            [{"url": "http://app-gateway:3000"}],
        )
        self.assertEqual(
            services["cds-service-app.admin"]["loadBalancer"]["servers"],
            [{"url": "http://app-admin:9090"}],
        )

    def test_dotted_target_with_unknown_contract_reports_e136(self):
        proxy = _proxy_module()
        proxy["config"] = {"routes": [{"host": "x.example.local", "target": "app.ghost"}]}
        _, diagnostics = _generate_reverse_proxy_routes(
            _plan(proxy, self._multi_http_target()), _rendered_services("app", ["app-gateway"])
        )
        self.assertEqual([d.code for d in diagnostics], ["E136"])
        self.assertIn('does not provide an http-service contract named "ghost"', diagnostics[0].message)
        self.assertEqual(diagnostics[0].path, "module:proxy.config.routes[0].target")

    def test_dotted_target_with_non_http_contract_reports_e136(self):
        proxy = _proxy_module()
        proxy["config"] = {"routes": [{"host": "x.example.local", "target": "app.db"}]}
        target = self._multi_http_target()
        target["provides"]["db"] = {"kind": "sql-database", "spec": {}}
        _, diagnostics = _generate_reverse_proxy_routes(
            _plan(proxy, target), _rendered_services("app", ["app-gateway"])
        )
        self.assertEqual([d.code for d in diagnostics], ["E136"])
        self.assertIn('does not provide an http-service contract named "db"', diagnostics[0].message)

    def test_bare_target_still_routes_to_first_http_service(self):
        proxy = _proxy_module()
        proxy["config"] = {"routes": [{"host": "x.example.local", "target": "app"}]}
        documents, diagnostics = _generate_reverse_proxy_routes(
            _plan(proxy, self._multi_http_target()), _rendered_services("app", ["app-gateway", "app-admin"])
        )
        self.assertEqual(diagnostics, [])
        self.assertEqual(
            documents["proxy"]["http"]["services"]["cds-service-app"]["loadBalancer"]["servers"],
            [{"url": "http://app-gateway:3000"}],
        )

    def test_duplicate_host_per_proxy_reports_e136(self):
        proxy = _proxy_module()
        proxy["config"] = {"routes": [
            {"host": "dup.example.local", "target": "superset"},
            {"host": "dup.example.local", "target": "superset"},
        ]}
        documents, diagnostics = _generate_reverse_proxy_routes(
            _plan(proxy, _http_target()), _rendered_services()
        )
        self.assertEqual([d.code for d in diagnostics], ["E136"])
        self.assertIn("duplicates an earlier route", diagnostics[0].message)
        # The later profile occurrence is flagged; the first route stays valid.
        self.assertEqual(diagnostics[0].path, "module:proxy.config.routes[1].host")
        self.assertEqual(
            sorted(documents["proxy"]["http"]["routers"]),
            ["cds-route-0-dup.example.local"],
        )

    def test_same_host_on_different_proxies_is_allowed(self):
        proxy_a = _proxy_module("proxy-a")
        proxy_a["config"] = {"routes": [{"host": "dup.example.local", "target": "superset"}]}
        proxy_b = _proxy_module("proxy-b")
        proxy_b["config"] = {"routes": [{"host": "dup.example.local", "target": "superset"}]}
        target = _http_target()
        plan = {"modules": [proxy_a, proxy_b, target], "certificates": {}}
        documents, diagnostics = _generate_reverse_proxy_routes(plan, _rendered_services())
        self.assertEqual(diagnostics, [])
        self.assertEqual(sorted(documents), ["proxy-a", "proxy-b"])


class TraefikRoutesSchemaTest(unittest.TestCase):
    """The real traefik module's routes schema (review follow-ups on #581)."""

    def test_empty_entrypoints_is_rejected_by_the_schema(self):
        # `entryPoints: []` must fail validation (E030) instead of being
        # silently coerced to the websecure default by the renderer.
        module = yaml.safe_load(
            (_REPO_ROOT / "modules" / "integration" / "traefik" / "module.yaml").read_text(
                encoding="utf-8"
            )
        )
        inst = {
            "index": 0,
            "id": "proxy",
            "config": {"routes": [
                {"host": "x.example.local", "target": "web", "entryPoints": []}
            ]},
            "module": module,
        }
        diagnostics = validate_module_configs([inst])
        # jsonschema reports minItems as "<instance> is too short";
        # match on the code and path instead of a specific wording.
        self.assertTrue(
            any(
                d.code == "E030"
                and str(d.path).endswith("entryPoints")
                for d in diagnostics
            ),
            diagnostics,
        )

    def test_certificate_alias_is_restricted_by_the_schema(self):
        # The alias interpolates into the router's certificate file paths,
        # so path-shaped values must fail validation (E030) up front.
        module = yaml.safe_load(
            (_REPO_ROOT / "modules" / "integration" / "traefik" / "module.yaml").read_text(
                encoding="utf-8"
            )
        )
        inst = {
            "index": 0,
            "id": "proxy",
            "config": {"routes": [
                {"host": "x.example.local", "target": "web", "certificate": "../evil"}
            ]},
            "module": module,
        }
        diagnostics = validate_module_configs([inst])
        self.assertTrue(
            any(
                d.code == "E030"
                and str(d.path).endswith("certificate")
                for d in diagnostics
            ),
            diagnostics,
        )


class RouteGenerationEndToEndTest(unittest.TestCase):
    """Plan + render with real module files: the routes file lands in the proxy's file-provider mount."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        modules_dir = root / "modules" / "test"
        modules_dir.mkdir(parents=True)
        self.profile_dir = root / "profiles" / "routes"
        self.profile_dir.mkdir(parents=True)
        self.output = root / "out" / "docker-compose.yml"

        (modules_dir / "proxy" ).mkdir()
        (modules_dir / "web").mkdir()
        (modules_dir / "api").mkdir()
        (modules_dir / "proxy" / "module.yaml").write_text(yaml.safe_dump({
            "apiVersion": "cds/v1alpha1",
            "kind": "Module",
            "metadata": {"name": "proxy", "category": "integration", "version": "0.1.0"},
            "spec": {
                "runtime": {"type": "container", "service": {
                    "name": "proxy", "ports": [{"name": "https", "containerPort": 443, "protocol": "TCP"}]}},
                "configSchema": {"type": "object", "additionalProperties": True},
                "provides": [{"name": "reverse-proxy", "contract": {
                    "kind": "reverse-proxy",
                    "spec": {"host": "${service.host}", "port": 443, "protocol": "https", "tls": True},
                }}],
                "implementation": {"kind": "docker-compose", "compose": {"services": {
                    "proxy": {"image": "local/proxy:1", "ports": ["8080:443"]}
                }}},
            },
        }), encoding="utf-8")
        (modules_dir / "web" / "module.yaml").write_text(yaml.safe_dump({
            "apiVersion": "cds/v1alpha1",
            "kind": "Module",
            "metadata": {"name": "web", "category": "apps", "version": "0.1.0"},
            "spec": {
                "runtime": {"type": "container", "service": {
                    "name": "web", "ports": [{"name": "http", "containerPort": 3000, "protocol": "TCP"}]}},
                "configSchema": {"type": "object", "additionalProperties": True,
                                 "properties": {"webPort": {"type": "integer", "default": 3000}}},
                "provides": [{"name": "http-service", "contract": {
                    "kind": "http-service",
                    "spec": {
                        "protocol": "http",
                        "portFrom": "spec.config.webPort",
                        "host": "${service.host}",
                        "port": "${config.webPort}",
                    },
                }}],
                "implementation": {"kind": "docker-compose", "compose": {"services": {
                    "web": {"image": "local/web:1", "ports": ["3000:3000"]}
                }}},
            },
        }), encoding="utf-8")
        # Multi-service module shaped like dagster/dbt: the compose service
        # is NOT named after the instance id, so routing only works through
        # the advertised serviceName. It exposes two http-service contracts
        # so dotted targeting (<module>.<contract>) can be exercised end to
        # end.
        (modules_dir / "api" / "module.yaml").write_text(yaml.safe_dump({
            "apiVersion": "cds/v1alpha1",
            "kind": "Module",
            "metadata": {"name": "api", "category": "apps", "version": "0.1.0"},
            "spec": {
                "runtime": {"type": "container", "service": {
                    "name": "api", "ports": [{"name": "http", "containerPort": 3000, "protocol": "TCP"}]}},
                "configSchema": {"type": "object", "additionalProperties": True,
                                 "properties": {
                                     "webPort": {"type": "integer", "default": 3000},
                                     "adminPort": {"type": "integer", "default": 9090},
                                 }},
                "provides": [
                    {"name": "http-service", "contract": {
                        "kind": "http-service",
                        "spec": {
                            "protocol": "http",
                            "portFrom": "spec.config.webPort",
                            "host": "${service.host}",
                            "port": "${config.webPort}",
                            "serviceName": "gateway",
                        },
                    }},
                    {"name": "admin-service", "contract": {
                        "kind": "http-service",
                        "spec": {
                            "protocol": "http",
                            "portFrom": "spec.config.adminPort",
                            "host": "${service.host}",
                            "port": "${config.adminPort}",
                            "serviceName": "admin",
                        },
                    }},
                ],
                "implementation": {"kind": "docker-compose", "compose": {"services": {
                    "gateway": {"image": "local/api:1", "ports": ["3001:3000"]},
                    "admin": {"image": "local/api:1", "ports": ["3002:9090"]},
                }}},
            },
        }), encoding="utf-8")

    def _profile(self, routes):
        modules = [
            {
                "id": "proxy",
                "source": "../../modules/test/proxy",
                "version": "0.1.0",
                "enabled": True,
                "config": {"routes": routes},
            },
            {
                "id": "web",
                "source": "../../modules/test/web",
                "version": "0.1.0",
                "enabled": True,
                "config": {},
            },
        ]
        if any(
            route.get("target") == "api" or str(route.get("target", "")).startswith("api.")
            for route in routes
            if isinstance(route, dict)
        ):
            modules.append({
                "id": "api",
                "source": "../../modules/test/api",
                "version": "0.1.0",
                "enabled": True,
                "config": {},
            })
        return {
            "apiVersion": "cds/v1alpha1",
            "kind": "Profile",
            "metadata": {"name": "routes", "environment": "local"},
            "spec": {
                "runtime": {"type": "docker-compose"},
                "modules": modules,
            },
        }

    def _render(self, routes):
        profile = self._profile(routes)
        plan, plan_diags = planner.build_plan_from_profile(profile, self.profile_dir)
        self.assertFalse([d for d in plan_diags if d.level == "error"], plan_diags)
        _, render_diags = render_compose(plan, output_path=str(self.output))
        self.assertFalse([d for d in render_diags if d.level == "error"], render_diags)
        return plan

    def test_render_writes_deterministic_routes_file_next_to_compose(self):
        self._render([{"host": "web.example.local", "target": "web"}])
        routes_file = self.output.parent / "traefik" / "dynamic" / "cds-routes-proxy.yml"
        self.assertTrue(routes_file.is_file(), f"missing {routes_file}")
        document = yaml.safe_load(routes_file.read_text(encoding="utf-8"))
        servers = document["http"]["services"]["cds-service-web"]["loadBalancer"]["servers"]
        self.assertEqual(servers, [{"url": "http://web:3000"}])
        self.assertEqual(
            document["http"]["routers"]["cds-route-0-web.example.local"]["rule"],
            "Host(`web.example.local`)",
        )

    def test_render_routes_multi_service_target_via_service_name(self):
        self._render([
            {"host": "web.example.local", "target": "web"},
            {"host": "api.example.local", "target": "api"},
        ])
        routes_file = self.output.parent / "traefik" / "dynamic" / "cds-routes-proxy.yml"
        self.assertTrue(routes_file.is_file(), f"missing {routes_file}")
        document = yaml.safe_load(routes_file.read_text(encoding="utf-8"))
        # The api module's compose service "gateway" renders as
        # "api-gateway"; the serviceName advertisement maps to it.
        self.assertEqual(
            document["http"]["services"]["cds-service-api"]["loadBalancer"]["servers"],
            [{"url": "http://api-gateway:3000"}],
        )

    def test_render_routes_named_contract_of_multi_endpoint_target(self):
        self._render([
            {"host": "web.example.local", "target": "web"},
            {"host": "admin.example.local", "target": "api.admin-service"},
        ])
        routes_file = self.output.parent / "traefik" / "dynamic" / "cds-routes-proxy.yml"
        self.assertTrue(routes_file.is_file(), f"missing {routes_file}")
        document = yaml.safe_load(routes_file.read_text(encoding="utf-8"))
        # The dotted target picks the api module's second http-service
        # contract, whose "admin" compose service renders as "api-admin".
        self.assertEqual(
            document["http"]["services"]["cds-service-api.admin-service"]["loadBalancer"]["servers"],
            [{"url": "http://api-admin:9090"}],
        )

    def test_render_removes_stale_routes_file_when_routes_are_removed(self):
        self._render([{"host": "web.example.local", "target": "web"}])
        routes_file = self.output.parent / "traefik" / "dynamic" / "cds-routes-proxy.yml"
        self.assertTrue(routes_file.is_file(), f"missing {routes_file}")

        # Re-render the same profile with no routes: the file provider
        # watches this directory, so the stale file must not survive.
        self._render([])
        self.assertFalse(routes_file.is_file(), f"stale routes file survived: {routes_file}")

    def test_invalid_target_surfaces_e136_at_render(self):
        profile = self._profile([{"host": "web.example.local", "target": "ghost"}])
        plan, _ = planner.build_plan_from_profile(profile, self.profile_dir)
        compose_yaml, render_diags = render_compose(plan, output_path=str(self.output))
        errors = [d for d in render_diags if d.level == "error"]
        self.assertEqual([d.code for d in errors], ["E136"])
        # No routes file written when routing inputs are invalid.
        routes_file = self.output.parent / "traefik" / "dynamic" / "cds-routes-proxy.yml"
        self.assertFalse(routes_file.is_file())


if __name__ == "__main__":
    unittest.main()
