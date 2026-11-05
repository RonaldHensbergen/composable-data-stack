"""Tests for reverse-proxy dynamic route generation from contract bindings (#581, #883)."""
import os
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
                {"host": "app.example.local", "contractRef": "superset.http-service"},
                {"host": "alt.example.local", "contractRef": "superset.http-service", "entryPoints": ["web"], "tls": False},
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
        proxy["config"] = {"routes": [{"host": "orch.example.local", "contractRef": "dagster.http-service"}]}
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
        proxy["config"] = {"routes": [{"host": "api.example.local", "contractRef": "api.http-service"}]}
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
        proxy["config"] = {"routes": [{"host": "x.example.local", "contractRef": "superset.http-service"}]}
        # The advertised host matches no rendered service (e.g. the module
        # has several compose services and none is named after the id).
        _, diagnostics = _generate_reverse_proxy_routes(
            _plan(proxy, _http_target()), _rendered_services("superset", ["superset-init"])
        )
        self.assertEqual([d.code for d in diagnostics], ["E136"])
        self.assertIn("not a rendered compose service", diagnostics[0].message)
        self.assertIn("superset-init", diagnostics[0].message)

    def test_unknown_contract_ref_reports_e136(self):
        proxy = _proxy_module()
        proxy["config"] = {"routes": [{"host": "x.example.local", "contractRef": "ghost.http-service"}]}
        documents, diagnostics = _generate_reverse_proxy_routes(
            _plan(proxy, _http_target()), _rendered_services()
        )
        self.assertEqual([d.code for d in diagnostics], ["E136"])
        self.assertIn("unknown module", diagnostics[0].message)
        self.assertEqual(diagnostics[0].path, "module:proxy.config.routes[0].contractRef")
        self.assertEqual(documents, {})

    def test_contract_ref_without_port_reports_e136(self):
        proxy = _proxy_module()
        proxy["config"] = {"routes": [{"host": "x.example.local", "contractRef": "superset.http-service"}]}
        broken = _http_target()
        broken["provides"]["http-service"]["spec"] = {"host": "superset"}
        _, diagnostics = _generate_reverse_proxy_routes(_plan(proxy, broken), _rendered_services())
        self.assertEqual([d.code for d in diagnostics], ["E136"])
        self.assertIn("resolvable http-service endpoint", diagnostics[0].message)

    def test_contract_ref_without_resolvable_endpoint_reports_e136(self):
        proxy = _proxy_module()
        proxy["config"] = {"routes": [{"host": "x.example.local", "contractRef": "superset.http-service"}]}
        broken = _http_target()
        broken["provides"]["http-service"]["spec"] = {"protocol": "http", "portFrom": "spec.config.webPort"}
        _, diagnostics = _generate_reverse_proxy_routes(_plan(proxy, broken), _rendered_services())
        self.assertEqual([d.code for d in diagnostics], ["E136"])
        self.assertIn("resolvable http-service endpoint", diagnostics[0].message)

    def test_backend_port_outside_valid_range_reports_e136(self):
        proxy = _proxy_module()
        proxy["config"] = {"routes": [{"host": "x.example.local", "contractRef": "superset.http-service"}]}
        for bad_port in (0, -1, 99999):
            broken = _http_target()
            broken["provides"]["http-service"]["spec"] = {"host": "superset", "port": bad_port}
            _, diagnostics = _generate_reverse_proxy_routes(_plan(proxy, broken), _rendered_services())
            self.assertEqual([d.code for d in diagnostics], ["E136"], f"port {bad_port} must be rejected")
            self.assertIn("resolvable http-service endpoint", diagnostics[0].message)

    def test_string_backend_port_still_resolves(self):
        proxy = _proxy_module()
        proxy["config"] = {"routes": [{"host": "x.example.local", "contractRef": "superset.http-service"}]}
        target = _http_target()
        target["provides"]["http-service"]["spec"] = {"host": "superset", "port": "8088"}
        documents, diagnostics = _generate_reverse_proxy_routes(_plan(proxy, target), _rendered_services())
        self.assertEqual(diagnostics, [])
        self.assertEqual(
            documents["proxy"]["http"]["services"]["cds-service-superset"]["loadBalancer"]["servers"],
            [{"url": "http://superset:8088"}],
        )

    def _proxy_with_entry_points(self):
        proxy = _proxy_module()
        proxy["config"] = {
            "entryPoints": {"web": {"address": ":80"}, "websecure": {"address": ":443"}},
            "routes": [],
        }
        return proxy

    def test_unknown_entrypoint_reports_e136(self):
        # A router no entrypoint serves would drop its traffic silently;
        # route entrypoints must match the proxy's declared set.
        proxy = self._proxy_with_entry_points()
        proxy["config"]["routes"] = [
            {"host": "x.example.local", "contractRef": "superset.http-service", "entryPoints": ["websecre"]}
        ]
        documents, diagnostics = _generate_reverse_proxy_routes(
            _plan(proxy, _http_target()), _rendered_services()
        )
        self.assertEqual([d.code for d in diagnostics], ["E136"])
        self.assertIn('not declared by proxy "proxy"', diagnostics[0].message)
        self.assertIn("declared: web, websecure", diagnostics[0].message)
        self.assertEqual(diagnostics[0].path, "module:proxy.config.routes[0].entryPoints")
        self.assertEqual(documents, {})

    def test_declared_entrypoints_pass(self):
        proxy = self._proxy_with_entry_points()
        proxy["config"]["routes"] = [
            {"host": "x.example.local", "contractRef": "superset.http-service", "entryPoints": ["web"]},
            {"host": "y.example.local", "contractRef": "superset.http-service"},
        ]
        documents, diagnostics = _generate_reverse_proxy_routes(
            _plan(proxy, _http_target()), _rendered_services()
        )
        self.assertEqual(diagnostics, [])
        routers = documents["proxy"]["http"]["routers"]
        # Explicit entrypoint is used as-is; the default stays websecure.
        self.assertEqual(
            [r["entryPoints"] for r in routers.values()],
            [["web"], ["websecure"]],
        )

    def test_entrypoints_unchecked_without_proxy_entrypoint_config(self):
        # Providers that declare no entryPoints config have no known set;
        # their routes are not entrypoint-checked.
        proxy = _proxy_module()
        proxy["config"] = {"routes": [
            {"host": "x.example.local", "contractRef": "superset.http-service", "entryPoints": ["custom"]}
        ]}
        documents, diagnostics = _generate_reverse_proxy_routes(
            _plan(proxy, _http_target()), _rendered_services()
        )
        self.assertEqual(diagnostics, [])
        self.assertEqual(
            documents["proxy"]["http"]["routers"]["cds-route-0-x.example.local"]["entryPoints"],
            ["custom"],
        )

    def test_diagnostic_path_keeps_profile_route_index(self):
        proxy = _proxy_module()
        # Written in reverse-sorted order so the sorted position differs
        # from the profile index of the failing route.
        proxy["config"] = {
            "routes": [
                {"host": "zzz.example.local", "contractRef": "superset.http-service"},
                {"host": "aaa.example.local", "contractRef": "ghost.http-service"},
            ]
        }
        _, diagnostics = _generate_reverse_proxy_routes(_plan(proxy, _http_target()), _rendered_services())
        self.assertEqual([d.code for d in diagnostics], ["E136"])
        # The failing route is routes[1] in the profile, no matter where the
        # deterministic output ordering places it.
        self.assertEqual(diagnostics[0].path, "module:proxy.config.routes[1].contractRef")

    def test_unknown_certificate_alias_reports_e136(self):
        proxy = _proxy_module()
        proxy["config"] = {"routes": [
            {"host": "x.example.local", "contractRef": "superset.http-service", "certificate": "missing"}
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
            {"host": "x.example.local", "contractRef": "superset.http-service", "certificate": "tls"}
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
            {"host": "x.example.local", "contractRef": "superset.http-service", "tls": False, "certificate": "tls"}
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
            {"host": "x.example.local", "contractRef": "superset.http-service", "tls": False, "certificate": "missing"}
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
        """A module exposing two http-service contracts (named contractRef case)."""
        target = _http_target(target_id="app", host="app", port=3000, service_name="gateway")
        target["provides"]["admin"] = {
            "kind": "http-service",
            "spec": {"host": "app", "port": 9090, "serviceName": "admin"},
        }
        return target

    def test_named_contract_ref_selects_named_contract(self):
        proxy = _proxy_module()
        proxy["config"] = {"routes": [
            {"host": "api.example.local", "contractRef": "app.http-service"},
            {"host": "admin.example.local", "contractRef": "app.admin"},
        ]}
        documents, diagnostics = _generate_reverse_proxy_routes(
            _plan(proxy, self._multi_http_target()),
            _rendered_services("app", ["app-gateway", "app-admin"]),
        )
        self.assertEqual(diagnostics, [])
        services = documents["proxy"]["http"]["services"]
        self.assertEqual(
            services["cds-service-app"]["loadBalancer"]["servers"],
            [{"url": "http://app-gateway:3000"}],
        )
        self.assertEqual(
            services["cds-service-app-admin"]["loadBalancer"]["servers"],
            [{"url": "http://app-admin:9090"}],
        )

    def test_contract_ref_with_unknown_contract_reports_e136(self):
        proxy = _proxy_module()
        proxy["config"] = {"routes": [{"host": "x.example.local", "contractRef": "app.ghost"}]}
        _, diagnostics = _generate_reverse_proxy_routes(
            _plan(proxy, self._multi_http_target()), _rendered_services("app", ["app-gateway"])
        )
        self.assertEqual([d.code for d in diagnostics], ["E136"])
        self.assertIn('does not provide an http-service contract named "ghost"', diagnostics[0].message)
        self.assertEqual(diagnostics[0].path, "module:proxy.config.routes[0].contractRef")

    def test_contract_ref_with_non_http_contract_reports_e136(self):
        proxy = _proxy_module()
        proxy["config"] = {"routes": [{"host": "x.example.local", "contractRef": "app.db"}]}
        target = self._multi_http_target()
        target["provides"]["db"] = {"kind": "sql-database", "spec": {}}
        _, diagnostics = _generate_reverse_proxy_routes(
            _plan(proxy, target), _rendered_services("app", ["app-gateway"])
        )
        self.assertEqual([d.code for d in diagnostics], ["E136"])
        self.assertIn('does not provide an http-service contract named "db"', diagnostics[0].message)

    def test_bare_module_id_ref_is_rejected(self):
        # issue #883: contractRef must be the full <module-id>.<contract-name>
        # binding; a bare module id no longer guesses the first http-service.
        proxy = _proxy_module()
        proxy["config"] = {"routes": [{"host": "x.example.local", "contractRef": "app"}]}
        documents, diagnostics = _generate_reverse_proxy_routes(
            _plan(proxy, self._multi_http_target()), _rendered_services("app", ["app-gateway", "app-admin"])
        )
        self.assertEqual([d.code for d in diagnostics], ["E136"])
        self.assertIn("not a valid", diagnostics[0].message)
        self.assertEqual(diagnostics[0].path, "module:proxy.config.routes[0].contractRef")
        self.assertEqual(documents, {})

    def test_route_is_generated_from_the_resolved_consume_binding(self):
        # issue #883: the planner-resolved binding (module.consumes) is the
        # primary backend source -- the contract graph, not a config lookup.
        proxy = _proxy_module()
        proxy["config"] = {"routes": [{"host": "x.example.local", "contractRef": "superset.http-service"}]}
        proxy["consumes"] = {
            "routes": [
                {
                    "contractRef": "superset.http-service",
                    "contract": {
                        "kind": "http-service",
                        "spec": {"host": "superset", "port": 8088},
                    },
                }
            ]
        }
        target = _http_target()
        documents, diagnostics = _generate_reverse_proxy_routes(_plan(proxy, target), _rendered_services())
        self.assertEqual(diagnostics, [])
        self.assertEqual(
            documents["proxy"]["http"]["services"]["cds-service-superset"]["loadBalancer"]["servers"],
            [{"url": "http://superset:8088"}],
        )

    def test_path_prefix_only_route_composes_pathprefix_rule(self):
        proxy = _proxy_module()
        proxy["config"] = {"routes": [{"pathPrefix": "/", "contractRef": "superset.http-service"}]}
        documents, diagnostics = _generate_reverse_proxy_routes(
            _plan(proxy, _http_target()), _rendered_services()
        )
        self.assertEqual(diagnostics, [])
        router = documents["proxy"]["http"]["routers"]["cds-route-0-default"]
        self.assertEqual(router["rule"], "PathPrefix(`/`)")
        self.assertEqual(router["entryPoints"], ["websecure"])
        self.assertEqual(router["tls"], {})

    def test_host_and_path_prefix_compose_combined_rule(self):
        proxy = _proxy_module()
        proxy["config"] = {"routes": [
            {"host": "app.example.local", "pathPrefix": "/api", "contractRef": "superset.http-service"}
        ]}
        documents, diagnostics = _generate_reverse_proxy_routes(
            _plan(proxy, _http_target()), _rendered_services()
        )
        self.assertEqual(diagnostics, [])
        router = documents["proxy"]["http"]["routers"]["cds-route-0-app.example.local"]
        self.assertEqual(router["rule"], "Host(`app.example.local`) && PathPrefix(`/api`)")

    def test_provider_advertised_path_prefix_is_the_default(self):
        # The bound http-service contract may advertise a pathPrefix; a
        # route with only a host uses it as the rule's path part.
        proxy = _proxy_module()
        proxy["config"] = {"routes": [{"host": "auth.example.local", "contractRef": "superset.http-service"}]}
        target = _http_target()
        target["provides"]["http-service"]["spec"]["pathPrefix"] = "/auth"
        documents, diagnostics = _generate_reverse_proxy_routes(_plan(proxy, target), _rendered_services())
        self.assertEqual(diagnostics, [])
        router = documents["proxy"]["http"]["routers"]["cds-route-0-auth.example.local"]
        self.assertEqual(router["rule"], "Host(`auth.example.local`) && PathPrefix(`/auth`)")

    def test_route_path_prefix_overrides_provider_advertisement(self):
        proxy = _proxy_module()
        proxy["config"] = {"routes": [
            {"host": "auth.example.local", "pathPrefix": "/sso", "contractRef": "superset.http-service"}
        ]}
        target = _http_target()
        target["provides"]["http-service"]["spec"]["pathPrefix"] = "/auth"
        documents, diagnostics = _generate_reverse_proxy_routes(_plan(proxy, target), _rendered_services())
        self.assertEqual(diagnostics, [])
        router = documents["proxy"]["http"]["routers"]["cds-route-0-auth.example.local"]
        self.assertEqual(router["rule"], "Host(`auth.example.local`) && PathPrefix(`/sso`)")

    def test_route_without_host_or_path_prefix_reports_e136(self):
        proxy = _proxy_module()
        proxy["config"] = {"routes": [{"contractRef": "superset.http-service"}]}
        documents, diagnostics = _generate_reverse_proxy_routes(
            _plan(proxy, _http_target()), _rendered_services()
        )
        self.assertEqual([d.code for d in diagnostics], ["E136"])
        self.assertIn("neither a host nor a pathPrefix", diagnostics[0].message)
        self.assertEqual(documents, {})

    def test_invalid_path_prefix_reports_e136(self):
        proxy = _proxy_module()
        proxy["config"] = {"routes": [
            {"pathPrefix": "no-leading-slash", "contractRef": "superset.http-service"}
        ]}
        _, diagnostics = _generate_reverse_proxy_routes(
            _plan(proxy, _http_target()), _rendered_services()
        )
        self.assertEqual([d.code for d in diagnostics], ["E136"])
        self.assertIn("must start with", diagnostics[0].message)
        self.assertEqual(diagnostics[0].path, "module:proxy.config.routes[0].pathPrefix")

    def test_invalid_host_reports_e136(self):
        # Defense in depth for plans loaded from files that bypass the
        # module configSchema's host pattern: a rule-injecting host must
        # fail closed here too.
        proxy = _proxy_module()
        proxy["config"] = {"routes": [
            {"host": "evil`) || Host(`x", "contractRef": "superset.http-service"}
        ]}
        _, diagnostics = _generate_reverse_proxy_routes(
            _plan(proxy, _http_target()), _rendered_services()
        )
        self.assertEqual([d.code for d in diagnostics], ["E136"])
        self.assertIn("not a DNS-safe hostname", diagnostics[0].message)
        self.assertEqual(diagnostics[0].path, "module:proxy.config.routes[0].host")

    def test_duplicate_rule_with_different_path_prefixes_is_allowed(self):
        # Same host, different path prefixes: Traefik disambiguates by
        # rule length/priority, so this is not a duplicate.
        proxy = _proxy_module()
        proxy["config"] = {"routes": [
            {"host": "app.example.local", "pathPrefix": "/a", "contractRef": "superset.http-service"},
            {"host": "app.example.local", "pathPrefix": "/b", "contractRef": "superset.http-service"},
        ]}
        documents, diagnostics = _generate_reverse_proxy_routes(
            _plan(proxy, _http_target()), _rendered_services()
        )
        self.assertEqual(diagnostics, [])
        self.assertEqual(len(documents["proxy"]["http"]["routers"]), 2)

    def test_duplicate_path_prefix_only_rule_reports_e136(self):
        proxy = _proxy_module()
        proxy["config"] = {"routes": [
            {"pathPrefix": "/", "contractRef": "superset.http-service"},
            {"pathPrefix": "/", "contractRef": "superset.http-service"},
        ]}
        documents, diagnostics = _generate_reverse_proxy_routes(
            _plan(proxy, _http_target()), _rendered_services()
        )
        self.assertEqual([d.code for d in diagnostics], ["E136"])
        self.assertIn("duplicates an earlier route", diagnostics[0].message)
        self.assertEqual(diagnostics[0].path, "module:proxy.config.routes[1]")
        self.assertEqual(sorted(documents["proxy"]["http"]["routers"]), ["cds-route-0-default"])

    def test_duplicate_host_per_proxy_reports_e136(self):
        proxy = _proxy_module()
        proxy["config"] = {"routes": [
            {"host": "dup.example.local", "contractRef": "superset.http-service"},
            {"host": "dup.example.local", "contractRef": "superset.http-service"},
        ]}
        documents, diagnostics = _generate_reverse_proxy_routes(
            _plan(proxy, _http_target()), _rendered_services()
        )
        self.assertEqual([d.code for d in diagnostics], ["E136"])
        self.assertIn("duplicates an earlier route", diagnostics[0].message)
        # The later profile occurrence is flagged; the first route stays valid.
        self.assertEqual(diagnostics[0].path, "module:proxy.config.routes[1]")
        self.assertEqual(
            sorted(documents["proxy"]["http"]["routers"]),
            ["cds-route-0-dup.example.local"],
        )

    def test_same_host_on_different_proxies_is_allowed(self):
        proxy_a = _proxy_module("proxy-a")
        proxy_a["config"] = {"routes": [{"host": "dup.example.local", "contractRef": "superset.http-service"}]}
        proxy_b = _proxy_module("proxy-b")
        proxy_b["config"] = {"routes": [{"host": "dup.example.local", "contractRef": "superset.http-service"}]}
        target = _http_target()
        plan = {"modules": [proxy_a, proxy_b, target], "certificates": {}}
        documents, diagnostics = _generate_reverse_proxy_routes(plan, _rendered_services())
        self.assertEqual(diagnostics, [])
        self.assertEqual(sorted(documents), ["proxy-a", "proxy-b"])


class TraefikRoutesSchemaTest(unittest.TestCase):
    """The real traefik module's routes schema (review follow-ups on #581/#883)."""

    def _traefik_module(self):
        return yaml.safe_load(
            (_REPO_ROOT / "modules" / "integration" / "traefik" / "module.yaml").read_text(
                encoding="utf-8"
            )
        )

    def test_route_without_host_or_path_prefix_is_rejected_by_the_schema(self):
        # issue #883: a route must compose at least one matcher; an entry
        # with only contractRef would render an empty-rule router.
        inst = {
            "index": 0,
            "id": "proxy",
            "config": {"routes": [{"contractRef": "web.http-service"}]},
            "module": self._traefik_module(),
        }
        diagnostics = validate_module_configs([inst])
        self.assertTrue(
            any(d.code == "E030" and "routes" in str(d.path) for d in diagnostics),
            diagnostics,
        )

    def test_bare_module_id_contract_ref_is_rejected_by_the_schema(self):
        # contractRef must be the full <module-id>.<contract-name> binding
        # (issue #883), so a bare module id fails the pattern with E030.
        inst = {
            "index": 0,
            "id": "proxy",
            "config": {"routes": [{"host": "x.example.local", "contractRef": "web"}]},
            "module": self._traefik_module(),
        }
        diagnostics = validate_module_configs([inst])
        self.assertTrue(
            any(d.code == "E030" and str(d.path).endswith("contractRef") for d in diagnostics),
            diagnostics,
        )

    def test_path_prefix_must_be_url_path_shaped(self):
        inst = {
            "index": 0,
            "id": "proxy",
            "config": {"routes": [
                {"pathPrefix": "no-slash", "contractRef": "web.http-service"}
            ]},
            "module": self._traefik_module(),
        }
        diagnostics = validate_module_configs([inst])
        self.assertTrue(
            any(d.code == "E030" and str(d.path).endswith("pathPrefix") for d in diagnostics),
            diagnostics,
        )

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
                {"host": "x.example.local", "contractRef": "web.http-service", "entryPoints": []}
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
                {"host": "x.example.local", "contractRef": "web.http-service", "certificate": "../evil"}
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
                # Routes bind http-service contracts through this consume
                # entry (issue #883), exactly like the real traefik module.
                "consumes": [{"name": "routes", "contract": {"kind": "http-service"},
                              "required": False, "mappedFrom": "spec.config.routes"}],
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
        # so named contractRef bindings (<module>.<contract>) can be exercised end to
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
            str(route.get("contractRef", "")).startswith("api.")
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
        self._render([{"host": "web.example.local", "contractRef": "web.http-service"}])
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
            {"host": "web.example.local", "contractRef": "web.http-service"},
            {"host": "api.example.local", "contractRef": "api.http-service"},
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
            {"host": "web.example.local", "contractRef": "web.http-service"},
            {"host": "admin.example.local", "contractRef": "api.admin-service"},
        ])
        routes_file = self.output.parent / "traefik" / "dynamic" / "cds-routes-proxy.yml"
        self.assertTrue(routes_file.is_file(), f"missing {routes_file}")
        document = yaml.safe_load(routes_file.read_text(encoding="utf-8"))
        # The named contractRef picks the api module's second http-service
        # contract, whose "admin" compose service renders as "api-admin".
        self.assertEqual(
            document["http"]["services"]["cds-service-api-admin-service"]["loadBalancer"]["servers"],
            [{"url": "http://api-admin:9090"}],
        )

    def test_render_removes_stale_routes_file_when_routes_are_removed(self):
        self._render([{"host": "web.example.local", "contractRef": "web.http-service"}])
        routes_file = self.output.parent / "traefik" / "dynamic" / "cds-routes-proxy.yml"
        self.assertTrue(routes_file.is_file(), f"missing {routes_file}")

        # Re-render the same profile with no routes: the file provider
        # watches this directory, so the stale file must not survive.
        self._render([])
        self.assertFalse(routes_file.is_file(), f"stale routes file survived: {routes_file}")

    def test_invalid_contract_ref_surfaces_e136_at_render(self):
        profile = self._profile([{"host": "web.example.local", "contractRef": "ghost.http-service"}])
        plan, _ = planner.build_plan_from_profile(profile, self.profile_dir)
        compose_yaml, render_diags = render_compose(plan, output_path=str(self.output))
        errors = [d for d in render_diags if d.level == "error"]
        self.assertEqual([d.code for d in errors], ["E136"])
        # No routes file written when routing inputs are invalid.
        routes_file = self.output.parent / "traefik" / "dynamic" / "cds-routes-proxy.yml"
        self.assertFalse(routes_file.is_file())


class RealTraefikModuleRoutesTest(unittest.TestCase):
    """Route generation against the real traefik and keycloak module files.

    The other classes use synthetic modules; these tests pin that the real
    traefik module's configSchema defaults (config.entryPoints
    web/websecure) flow through the planner into route generation, so a
    schema regression in the module itself fails here instead of silently
    breaking route generation for real profiles.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        # Symlink the repo's modules tree so the profile's `source` paths
        # resolve under an allowed modules/ root without copying files.
        os.symlink(_REPO_ROOT / "modules", root / "modules")
        self.profile_dir = root / "profiles" / "routes"
        self.profile_dir.mkdir(parents=True)

    def _plan(self, routes):
        profile = {
            "apiVersion": "cds/v1alpha1",
            "kind": "Profile",
            "metadata": {"name": "routes", "environment": "local"},
            "spec": {
                "runtime": {"type": "docker-compose"},
                "modules": [
                    {
                        "id": "proxy",
                        "source": "../../modules/integration/traefik",
                        "version": "0.1.0",
                        "enabled": True,
                        "config": {"routes": routes},
                    },
                    {
                        "id": "keycloak",
                        "source": "../../modules/identity/keycloak",
                        "version": "0.1.0",
                        "enabled": True,
                        "config": {},
                    },
                ],
            },
        }
        # The partial profile (no warehouse bound for keycloak) plans with
        # binding diagnostics unrelated to routes; route generation over
        # the built plan is what is under test here.
        plan, _ = planner.build_plan_from_profile(profile, self.profile_dir)
        return plan

    def test_real_traefik_defaults_validate_route_entrypoints(self):
        plan = self._plan([
            {"host": "k.example.local", "contractRef": "keycloak.http-service", "entryPoints": ["websecre"]}
        ])
        documents, diagnostics = _generate_reverse_proxy_routes(
            plan, {"keycloak": ["keycloak"]}
        )
        self.assertEqual([d.code for d in diagnostics], ["E136"])
        self.assertIn('not declared by proxy "proxy"', diagnostics[0].message)
        self.assertEqual(diagnostics[0].path, "module:proxy.config.routes[0].entryPoints")
        self.assertEqual(documents, {})

    def test_real_traefik_renders_route_to_keycloak(self):
        plan = self._plan([{"host": "k.example.local", "contractRef": "keycloak.http-service"}])
        documents, diagnostics = _generate_reverse_proxy_routes(
            plan, {"keycloak": ["keycloak"]}
        )
        self.assertEqual(diagnostics, [])
        router = documents["proxy"]["http"]["routers"]["cds-route-0-k.example.local"]
        # Entrypoint default and TLS default come from the real module's
        # configSchema; the backend comes from keycloak's resolved
        # http-service contract (host ${service.host}, port ${config.httpPort}).
        self.assertEqual(router["entryPoints"], ["websecure"])
        self.assertEqual(router["tls"], {})
        self.assertEqual(router["rule"], "Host(`k.example.local`)")
        self.assertEqual(
            documents["proxy"]["http"]["services"]["cds-service-keycloak"]["loadBalancer"]["servers"],
            [{"url": "http://keycloak:8080"}],
        )


if __name__ == "__main__":
    unittest.main()
