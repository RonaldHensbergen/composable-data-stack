"""Tests for reverse-proxy dynamic route generation from contract bindings (#581)."""
import tempfile
import unittest
from pathlib import Path

import yaml

from cli import planner
from cli.renderer import _generate_reverse_proxy_routes, render_compose
from cli.validator import validate_profile

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


def _http_target(target_id="superset", host="superset", port=8088):
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
                "spec": {"host": host, "port": port},
            }
        },
    }


def _plan(proxy, target, certificates=None):
    return {
        "modules": [proxy, target],
        "certificates": certificates or {},
    }


class RouteGenerationUnitTest(unittest.TestCase):
    def test_routes_generate_deterministic_traefik_document(self):
        proxy = _proxy_module()
        proxy["config"] = {
            "routes": [
                {"host": "app.example.local", "target": "superset"},
                {"host": "alt.example.local", "target": "superset", "entryPoints": ["web"], "tls": False},
            ]
        }
        documents, diagnostics = _generate_reverse_proxy_routes(_plan(proxy, _http_target()))
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
        documents2, _ = _generate_reverse_proxy_routes(_plan(proxy, _http_target()))
        self.assertEqual(documents, documents2)

    def test_unknown_target_reports_e136(self):
        proxy = _proxy_module()
        proxy["config"] = {"routes": [{"host": "x.example.local", "target": "ghost"}]}
        documents, diagnostics = _generate_reverse_proxy_routes(_plan(proxy, _http_target()))
        self.assertEqual([d.code for d in diagnostics], ["E136"])
        self.assertIn("unknown module", diagnostics[0].message)
        self.assertEqual(diagnostics[0].path, "module:proxy.config.routes[0].target")
        self.assertEqual(documents, {})

    def test_target_without_resolvable_endpoint_reports_e136(self):
        proxy = _proxy_module()
        proxy["config"] = {"routes": [{"host": "x.example.local", "target": "superset"}]}
        broken = _http_target()
        broken["provides"]["http-service"]["spec"] = {"protocol": "http", "portFrom": "spec.config.webPort"}
        _, diagnostics = _generate_reverse_proxy_routes(_plan(proxy, broken))
        self.assertEqual([d.code for d in diagnostics], ["E136"])
        self.assertIn("resolvable http-service endpoint", diagnostics[0].message)

    def test_unknown_certificate_alias_reports_e136(self):
        proxy = _proxy_module()
        proxy["config"] = {"routes": [
            {"host": "x.example.local", "target": "superset", "certificate": "missing"}
        ]}
        _, diagnostics = _generate_reverse_proxy_routes(_plan(proxy, _http_target()))
        self.assertEqual([d.code for d in diagnostics], ["E136"])
        self.assertIn("not declared in spec.certificates.values", diagnostics[0].message)
        self.assertEqual(diagnostics[0].path, "module:proxy.config.routes[0].certificate")

    def test_valid_certificate_alias_wires_certificate_files(self):
        proxy = _proxy_module()
        proxy["config"] = {"routes": [
            {"host": "x.example.local", "target": "superset", "certificate": "tls"}
        ]}
        documents, diagnostics = _generate_reverse_proxy_routes(
            _plan(proxy, _http_target(), certificates={"tls": {"cert": "CDS_TLS_CERT", "key": "CDS_TLS_KEY"}})
        )
        self.assertEqual(diagnostics, [])
        self.assertEqual(
            documents["proxy"]["tls"]["certificates"],
            [{"certFile": "/etc/traefik/certs/tls.crt", "keyFile": "/etc/traefik/certs/tls.key"}],
        )

    def test_non_proxy_modules_are_ignored(self):
        target = _http_target()
        target["module"] = {"spec": {"provides": []}}
        documents, diagnostics = _generate_reverse_proxy_routes(_plan(_proxy_module(), target))
        self.assertEqual(documents, {})
        self.assertEqual(diagnostics, [])


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

    def _profile(self, routes):
        return {
            "apiVersion": "cds/v1alpha1",
            "kind": "Profile",
            "metadata": {"name": "routes", "environment": "local"},
            "spec": {
                "runtime": {"type": "docker-compose"},
                "modules": [
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
                ],
            },
        }

    def test_render_writes_deterministic_routes_file_next_to_compose(self):
        self._write_profile([{"host": "web.example.local", "target": "web"}])
        errors = [d for d in validate_profile(str(self.profile_dir / "profile.yaml")) if d.level == "error"]
        self.assertEqual(errors, [])

        profile = self._profile([{"host": "web.example.local", "target": "web"}])
        plan, plan_diags = planner.build_plan_from_profile(profile, self.profile_dir)
        self.assertFalse([d for d in plan_diags if d.level == "error"], plan_diags)
        compose_yaml, render_diags = render_compose(plan, output_path=str(self.output))
        self.assertFalse([d for d in render_diags if d.level == "error"], render_diags)

        routes_file = self.output.parent / "traefik" / "dynamic" / "cds-routes-proxy.yml"
        self.assertTrue(routes_file.is_file(), f"missing {routes_file}")
        document = yaml.safe_load(routes_file.read_text(encoding="utf-8"))
        servers = document["http"]["services"]["cds-service-web"]["loadBalancer"]["servers"]
        self.assertEqual(servers, [{"url": "http://web:3000"}])
        self.assertEqual(
            document["http"]["routers"]["cds-route-0-web.example.local"]["rule"],
            "Host(`web.example.local`)",
        )

    def _write_profile(self, routes):
        path = self.profile_dir / "profile.yaml"
        path.write_text(yaml.safe_dump(self._profile(routes)), encoding="utf-8")
        return path

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
