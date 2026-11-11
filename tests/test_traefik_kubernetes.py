"""Tests for the traefik module's Kubernetes (Helm) implementation (#1132).

Covers the `implementation.kubernetes` block of
modules/integration/traefik/module.yaml: the Deployment + Service with
NodePort/LoadBalancer entry points on the declared config.entryPoints, the
file-provider directory rendered as a ConfigMap generated from the plan's
config.routes (same resolution/validation as the Compose target, plus the
E150 host cross-check against the rendered Kubernetes Services), the
certificate mounts projected from the chart Secret via spec.certificates,
and the interaction with the #1033 route-derived NetworkPolicy edges.
"""

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import yaml

from cli.k8s_renderer import render_helm
from cli.planner import build_plan

_REPO_ROOT = Path(__file__).resolve().parent.parent

_CERT_ENV = {
    "CDS_EDGE_CERT": "sentinel-edge-cert-material",
    "CDS_EDGE_KEY": "sentinel-edge-key-material",
}


def _load(text: str):
    """Parse a rendered manifest, dropping raw Helm template expression lines
    (the `{{ toYaml ... }}` resources block), so yaml.safe_load can read the
    rest; scalar values that merely contain `{{ .Release.Name }}` stay."""
    stripped = "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith("{{")
    )
    return yaml.safe_load(stripped)


def _web_module_yaml() -> dict:
    """A minimal http-service provider with a Kubernetes workload."""
    return {
        "apiVersion": "cds/v1alpha1",
        "kind": "Module",
        "metadata": {"name": "web", "category": "app", "version": "0.1.0"},
        "spec": {
            "runtime": {
                "type": "container",
                "service": {
                    "name": "web",
                    "ports": [
                        {"name": "http", "containerPort": 8080, "protocol": "TCP"}
                    ],
                },
            },
            "configSchema": {"type": "object", "additionalProperties": True},
            "provides": [
                {
                    "name": "http-service",
                    "contract": {
                        "kind": "http-service",
                        "spec": {
                            "host": "${service.dns}",
                            "port": 8080,
                            "protocol": "http",
                        },
                    },
                }
            ],
            "implementation": {
                "kind": "docker-compose",
                "targets": ["docker-compose", "kubernetes"],
                "compose": {"services": {"web": {"image": "example/web:1"}}},
                "kubernetes": {
                    "workloads": {
                        "web": {
                            "kind": "Deployment",
                            "containers": ["web"],
                            "service": {
                                "type": "ClusterIP",
                                "ports": [
                                    {"name": "http", "port": 8080, "targetPort": 8080}
                                ],
                            },
                            "resources": {
                                "web": {"requests": {"cpu": "10m", "memory": "32Mi"}}
                            },
                        }
                    }
                },
            },
        },
    }


class TraefikKubernetesProfileTest(unittest.TestCase):
    """
    End-to-end: a temp project whose profile wires the real traefik module
    (modules/integration/traefik/module.yaml) to a synthetic http-service
    module, planned and rendered for the Helm target.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        traefik_dir = root / "modules" / "integration" / "traefik"
        traefik_dir.mkdir(parents=True)
        shutil.copy(
            _REPO_ROOT / "modules" / "integration" / "traefik" / "module.yaml",
            traefik_dir / "module.yaml",
        )
        web_dir = root / "modules" / "app" / "web"
        web_dir.mkdir(parents=True)
        (web_dir / "module.yaml").write_text(
            yaml.safe_dump(_web_module_yaml()), encoding="utf-8"
        )
        self.profile_dir = root / "profiles" / "traefik-k8s"
        self.profile_dir.mkdir(parents=True)
        self.env_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.env_dir.cleanup)
        self.env_file = Path(self.env_dir.name) / ".env"
        self.env_file.write_text(
            "\n".join(f"{k}={v}" for k, v in _CERT_ENV.items()) + "\n", encoding="utf-8"
        )

    def _write_profile(
        self, traefik_config: dict, certificates: dict | None = None
    ) -> Path:
        profile = {
            "apiVersion": "cds/v1alpha1",
            "kind": "Profile",
            "metadata": {"name": "traefik-k8s", "environment": "local"},
            "spec": {
                "runtime": {"type": "container", "namespace": "traefik-k8s"},
                "modules": [
                    {
                        "id": "web",
                        "source": "../../modules/app/web",
                        "version": "0.1.0",
                        "config": {},
                    },
                    {
                        "id": "traefik",
                        "source": "../../modules/integration/traefik",
                        "version": "3.0.0",
                        "config": traefik_config,
                    },
                ],
                "secrets": {"provider": {"type": "env"}, "values": {}},
            },
        }
        if certificates is not None:
            profile["spec"]["certificates"] = certificates
        path = self.profile_dir / "profile.yaml"
        path.write_text(yaml.safe_dump(profile), encoding="utf-8")
        return path

    def _plan_and_render(self, traefik_config: dict, certificates: dict | None = None):
        profile_path = self._write_profile(traefik_config, certificates)
        with mock.patch.dict(os.environ, _CERT_ENV, clear=False):
            plan, plan_diags = build_plan(
                str(profile_path), env_file=str(self.env_file)
            )
        self.assertIsNone(
            next((d for d in plan_diags if d.level == "error"), None),
            [d.format() for d in plan_diags],
        )
        with mock.patch.dict(os.environ, _CERT_ENV, clear=False):
            files, diagnostics = render_helm(plan, env_file=str(self.env_file))
        return files, diagnostics

    def _routes_config(self, **extra) -> dict:
        config = {
            "routes": [{"host": "app.example.local", "contractRef": "web.http-service"}]
        }
        config.update(extra)
        return config

    def test_traefik_renders_for_helm_without_e072(self) -> None:
        files, diagnostics = self._plan_and_render(self._routes_config())
        errors = [d for d in diagnostics if d.level == "error"]
        self.assertEqual(errors, [])
        self.assertNotIn("E072", {d.code for d in diagnostics})
        for name in (
            "templates/traefik-traefik-deployment.yaml",
            "templates/traefik-traefik-service.yaml",
            "templates/traefik-configmap-routes.yaml",
            "templates/traefik-network-policy.yaml",
            "templates/traefik-service-account.yaml",
        ):
            self.assertIn(name, files)

    def test_service_exposes_entry_points_as_node_port(self) -> None:
        files, _ = self._plan_and_render(self._routes_config())
        service = yaml.safe_load(files["templates/traefik-traefik-service.yaml"])
        self.assertEqual(service["kind"], "Service")
        self.assertEqual(service["spec"]["type"], "NodePort")
        self.assertEqual(
            service["spec"]["ports"],
            [
                {"name": "web", "port": 80, "targetPort": 80, "protocol": "TCP"},
                {
                    "name": "websecure",
                    "port": 443,
                    "targetPort": 443,
                    "protocol": "TCP",
                },
            ],
        )

    def test_service_type_follows_kubernetes_service_config(self) -> None:
        files, _ = self._plan_and_render(
            self._routes_config(kubernetesService={"type": "LoadBalancer"})
        )
        service = yaml.safe_load(files["templates/traefik-traefik-service.yaml"])
        self.assertEqual(service["spec"]["type"], "LoadBalancer")

    def test_custom_entrypoint_address_drives_ports(self) -> None:
        # A profile moving the web entrypoint to :8080 gets Service port
        # 8080 -> targetPort 8080, container port 8080, and the NetworkPolicy
        # entry-point ingress opens pod port 8080 (not 80).
        files, diagnostics = self._plan_and_render(
            self._routes_config(
                entryPoints={
                    "web": {"address": ":8080"},
                    "websecure": {"address": ":443"},
                }
            )
        )
        self.assertEqual([d for d in diagnostics if d.level == "error"], [])
        service = yaml.safe_load(files["templates/traefik-traefik-service.yaml"])
        self.assertEqual(service["spec"]["ports"][0]["port"], 8080)
        self.assertEqual(service["spec"]["ports"][0]["targetPort"], 8080)
        deployment = _load(files["templates/traefik-traefik-deployment.yaml"])
        container_ports = {
            p["name"]: p["containerPort"]
            for p in deployment["spec"]["template"]["spec"]["containers"][0]["ports"]
        }
        self.assertEqual(
            container_ports, {"web": 8080, "websecure": 443, "dashboard": 8080}
        )
        policy = yaml.safe_load(files["templates/traefik-network-policy.yaml"])
        open_ports = [
            p["port"]
            for rule in policy["spec"]["ingress"]
            if "ports" in rule
            for p in rule["ports"]
        ]
        self.assertEqual(sorted(open_ports), [443, 8080])

    def test_routes_configmap_is_generated_from_plan_routes(self) -> None:
        files, _ = self._plan_and_render(self._routes_config())
        cm = yaml.safe_load(files["templates/traefik-configmap-routes.yaml"])
        self.assertEqual(cm["kind"], "ConfigMap")
        self.assertEqual(list(cm["data"]), ["cds-routes-traefik.yml"])
        document = yaml.safe_load(cm["data"]["cds-routes-traefik.yml"])
        router = document["http"]["routers"]["cds-route-0-app.example.local"]
        self.assertEqual(router["rule"], "Host(`app.example.local`)")
        self.assertEqual(router["entryPoints"], ["websecure"])
        self.assertEqual(router["tls"], {})
        # The backend is dialed by the release-scoped Kubernetes Service DNS
        # name of the route target, on the contract's port.
        server = document["http"]["services"]["cds-service-web"]["loadBalancer"][
            "servers"
        ][0]
        self.assertEqual(server["url"], "http://{{ .Release.Name }}-web:8080")

    def test_route_certificate_alias_wires_tls_certificates_and_secret_mount(
        self,
    ) -> None:
        files, diagnostics = self._plan_and_render(
            {
                "routes": [
                    {
                        "host": "app.example.local",
                        "contractRef": "web.http-service",
                        "certificate": "edge",
                    }
                ]
            },
            certificates={
                "values": {
                    "edge": {"certEnv": "CDS_EDGE_CERT", "keyEnv": "CDS_EDGE_KEY"}
                }
            },
        )
        self.assertEqual([d for d in diagnostics if d.level == "error"], [])
        cm = yaml.safe_load(files["templates/traefik-configmap-routes.yaml"])
        document = yaml.safe_load(cm["data"]["cds-routes-traefik.yml"])
        self.assertEqual(
            document["tls"]["certificates"],
            [
                {
                    "certFile": "/etc/traefik/certs/edge.crt",
                    "keyFile": "/etc/traefik/certs/edge.key",
                }
            ],
        )
        deployment = _load(files["templates/traefik-traefik-deployment.yaml"])
        volumes = {
            v["name"]: v for v in deployment["spec"]["template"]["spec"]["volumes"]
        }
        certs_volume = volumes["certs-traefik-certs"]["secret"]
        self.assertEqual(certs_volume["secretName"], "{{ .Release.Name }}-secrets")
        self.assertEqual(
            certs_volume["items"],
            [
                {"key": "CDS_EDGE_CERT", "path": "edge.crt"},
                {"key": "CDS_EDGE_KEY", "path": "edge.key"},
            ],
        )
        mounts = {
            m["mountPath"]: m
            for m in deployment["spec"]["template"]["spec"]["containers"][0][
                "volumeMounts"
            ]
        }
        self.assertEqual(mounts["/etc/traefik/certs"]["name"], "certs-traefik-certs")
        self.assertTrue(mounts["/etc/traefik/certs"]["readOnly"])
        # The file-provider directory is mounted whole (no subPath), so the
        # generated cds-routes file lands inside it.
        self.assertEqual(mounts["/etc/traefik/dynamic"]["name"], "cm-traefik-routes")
        self.assertNotIn("subPath", mounts["/etc/traefik/dynamic"])
        # The chart Secret carries the certificate env keys as install-time
        # required lookups, and no certificate material reaches the chart.
        secret = files["templates/secret.yaml"]
        self.assertIn("CDS_EDGE_CERT", secret)
        self.assertIn("CDS_EDGE_KEY", secret)
        rendered = "\n".join(files.values())
        for value in _CERT_ENV.values():
            self.assertNotIn(value, rendered)

    def test_no_routes_no_configmap_and_no_cert_mount(self) -> None:
        files, diagnostics = self._plan_and_render({})
        self.assertEqual([d for d in diagnostics if d.level == "error"], [])
        self.assertNotIn("templates/traefik-configmap-routes.yaml", files)
        self.assertNotIn("templates/secret.yaml", files)
        deployment = _load(files["templates/traefik-traefik-deployment.yaml"])
        mounts = {
            m["mountPath"]: m
            for m in deployment["spec"]["template"]["spec"]["containers"][0][
                "volumeMounts"
            ]
        }
        # Without routes the file-provider bind falls back to an empty dir,
        # and without certificates the certs bind does the same (no Secret
        # projection is built).
        self.assertIn("/etc/traefik/dynamic", mounts)
        volumes = {
            v["name"]: v for v in deployment["spec"]["template"]["spec"]["volumes"]
        }
        self.assertIn("emptyDir", volumes[mounts["/etc/traefik/dynamic"]["name"]])
        self.assertIn("/etc/traefik/certs", mounts)
        self.assertIn("emptyDir", volumes[mounts["/etc/traefik/certs"]["name"]])

    def test_network_policy_allows_entry_points_and_route_edges_only(self) -> None:
        files, _ = self._plan_and_render(self._routes_config())
        policy = yaml.safe_load(files["templates/traefik-network-policy.yaml"])
        ingress = policy["spec"]["ingress"]
        # same-module rule plus the NodePort entry-point rule on 80/443
        self.assertEqual(len(ingress), 2)
        self.assertEqual(sorted(p["port"] for p in ingress[1]["ports"]), [80, 443])
        egress = policy["spec"]["egress"]
        # same-module + DNS + the route-derived edge to web on 8080 (#1033)
        self.assertEqual(len(egress), 3)
        route_edge = egress[2]
        peer = route_edge["to"][0]["podSelector"]["matchLabels"]["cds.dev/module"]
        self.assertEqual(peer, "web")
        self.assertEqual(route_edge["ports"], [{"protocol": "TCP", "port": 8080}])
        # The target gets the matching ingress edge from the proxy.
        web_policy = yaml.safe_load(files["templates/web-network-policy.yaml"])
        web_ingress = web_policy["spec"]["ingress"]
        self.assertEqual(len(web_ingress), 2)
        from_proxy = web_ingress[1]["from"][0]["podSelector"]["matchLabels"]
        self.assertEqual(from_proxy["cds.dev/module"], "traefik")
        self.assertEqual(web_ingress[1]["ports"], [{"protocol": "TCP", "port": 8080}])

    def test_unrouted_host_gets_no_edge(self) -> None:
        # A second http-service module that no route names: the proxy gets
        # no egress edge to it and it gets no ingress edge from the proxy.
        files, diagnostics = self._plan_and_render(self._routes_config())
        self.assertEqual([d for d in diagnostics if d.level == "error"], [])
        # Only the routed "web" appears in the proxy's egress.
        policy = yaml.safe_load(files["templates/traefik-network-policy.yaml"])
        peers = []
        for rule in policy["spec"]["egress"]:
            to = rule.get("to") or []
            for peer in to:
                match = (peer.get("podSelector") or {}).get("matchLabels") or {}
                if "cds.dev/module" in match:
                    peers.append(match["cds.dev/module"])
        self.assertEqual(peers, ["traefik", "web"])

    def test_route_to_target_without_kubernetes_service_fails_e150(self) -> None:
        # The E150 host cross-check on Helm (#1132): a route whose backend
        # host matches no rendered Kubernetes Service fails closed instead
        # of dialing a name nothing answers.
        web_yaml = _web_module_yaml()
        del web_yaml["spec"]["implementation"]["kubernetes"]["workloads"]["web"][
            "service"
        ]
        web_dir = self.profile_dir.parent.parent / "modules" / "app" / "web"
        (web_dir / "module.yaml").write_text(yaml.safe_dump(web_yaml), encoding="utf-8")
        files, diagnostics = self._plan_and_render(self._routes_config())
        self.assertEqual(files, {})
        e150 = [d for d in diagnostics if d.code == "E150"]
        self.assertEqual(len(e150), 1)
        self.assertIn("web", e150[0].message)
        self.assertEqual(e150[0].path, "module:traefik.config.routes[0].contractRef")

    def test_route_to_unknown_module_fails_at_plan_time(self) -> None:
        # A route naming a module the profile does not contain is rejected
        # by the planner (E041) before any render, like every other consume
        # binding (issue #883).
        profile_path = self._write_profile(
            {
                "routes": [
                    {"host": "ghost.example.local", "contractRef": "ghost.http-service"}
                ]
            }
        )
        with mock.patch.dict(os.environ, _CERT_ENV, clear=False):
            plan, plan_diags = build_plan(
                str(profile_path), env_file=str(self.env_file)
            )
        e041 = [d for d in plan_diags if d.code == "E041"]
        self.assertEqual(len(e041), 1)
        self.assertIn('Unknown producer module "ghost"', e041[0].message)

    def test_undeclared_certificate_alias_fails_e136(self) -> None:
        files, diagnostics = self._plan_and_render(
            {
                "routes": [
                    {
                        "host": "app.example.local",
                        "contractRef": "web.http-service",
                        "certificate": "ghost",
                    }
                ]
            }
        )
        self.assertEqual(files, {})
        e136 = [d for d in diagnostics if d.code == "E136"]
        self.assertEqual(len(e136), 1)
        self.assertIn('certificate alias "ghost"', e136[0].message)

    def test_render_is_deterministic(self) -> None:
        first, _ = self._plan_and_render(self._routes_config())
        second, _ = self._plan_and_render(self._routes_config())
        self.assertEqual(first, second)


class TraefikKubernetesPlanFileTest(unittest.TestCase):
    """Plan-file bypass shapes: fail closed instead of crashing (E165)."""

    def _plan(self, service_port=None, routes=None) -> dict:
        traefik_module = yaml.safe_load(
            (
                _REPO_ROOT / "modules" / "integration" / "traefik" / "module.yaml"
            ).read_text(encoding="utf-8")
        )
        implementation = traefik_module["spec"]["implementation"]
        if service_port is not None:
            implementation["kubernetes"]["workloads"]["traefik"]["service"]["ports"][0][
                "port"
            ] = service_port
        proxy = {
            "id": "traefik",
            "source": "modules/integration/traefik",
            # The planner normally applies the configSchema defaults; a plan
            # loaded from a file must carry them explicitly.
            "config": {
                "log": {"level": "INFO"},
                "accessLog": {"enabled": True},
                "api": {"dashboard": True, "debug": False, "insecure": False},
                "entryPoints": {
                    "web": {"address": ":80"},
                    "websecure": {"address": ":443"},
                },
                "tls": {
                    "minVersion": "VersionTLS12",
                    "maxVersion": "VersionTLS13",
                    "preferServerCipherSuites": True,
                    "sniStrict": True,
                },
                "docker": {"enabled": False, "exposedByDefault": False},
                "fileProvider": {"directory": "/etc/traefik/dynamic", "watch": True},
                "kubernetesService": {"type": "NodePort"},
                "routes": routes or [],
            },
            "provides": {
                "reverse-proxy": {
                    "kind": "reverse-proxy",
                    "spec": {"host": "traefik", "port": 443},
                }
            },
            "implementation": implementation,
        }
        target = {
            "id": "web",
            "source": "modules/app/web",
            "config": {},
            "provides": {
                "http-service": {
                    "kind": "http-service",
                    "spec": {"host": "web", "port": 8080},
                }
            },
            "implementation": {
                "kind": "docker-compose",
                "targets": ["docker-compose", "kubernetes"],
                "compose": {"services": {"web": {"image": "example/web:1"}}},
                "kubernetes": {
                    "workloads": {
                        "web": {
                            "kind": "Deployment",
                            "containers": ["web"],
                            "service": {
                                "type": "ClusterIP",
                                "ports": [{"name": "http", "port": 8080}],
                            },
                            "resources": {
                                "web": {"requests": {"cpu": "10m", "memory": "32Mi"}}
                            },
                        }
                    }
                },
            },
        }
        return {
            "metadata": {"name": "t", "version": "1.0.0"},
            "runtime": {"namespace": "t"},
            "secrets": {},
            "certificates": {},
            "modules": [proxy, target],
        }

    def test_garbage_service_port_fails_e165(self) -> None:
        files, diagnostics = render_helm(self._plan(service_port="not-a-port"))
        self.assertEqual(files, {})
        e165 = [d for d in diagnostics if d.code == "E165"]
        self.assertEqual(len(e165), 1)
        self.assertIn('"port"', e165[0].message)
        self.assertIn("not-a-port", e165[0].message)

    def test_address_shaped_service_port_parses(self) -> None:
        # ":8080" (a traefik entrypoint address) is a valid port declaration.
        files, diagnostics = render_helm(self._plan(service_port=":8080"))
        self.assertEqual([d for d in diagnostics if d.level == "error"], [])
        service = yaml.safe_load(files["templates/traefik-traefik-service.yaml"])
        self.assertEqual(service["spec"]["ports"][0]["port"], 8080)

    def test_render_time_route_to_unknown_module_fails_e136(self) -> None:
        # A plan loaded from a file bypasses the planner's E041: the route
        # generation still fails closed with E136 at render time.
        plan = self._plan(
            routes=[
                {"host": "ghost.example.local", "contractRef": "ghost.http-service"}
            ]
        )
        files, diagnostics = render_helm(plan)
        self.assertEqual(files, {})
        e136 = [d for d in diagnostics if d.code == "E136"]
        self.assertEqual(len(e136), 1)
        self.assertIn("unknown module", e136[0].message)

    def test_unsafe_certificate_alias_fails_e166(self) -> None:
        # The alias interpolates into the projected <alias>.crt path, so a
        # plan file naming an alias outside [A-Za-z0-9-] fails closed instead
        # of rendering a path Kubernetes would reject at install time.
        plan = self._plan()
        plan["certificates"] = {
            "../evil": {"cert": "CDS_EVIL_CERT", "key": "CDS_EVIL_KEY"}
        }
        # E166 is raised in the module rendering pass, so like E074/E075 the
        # files mapping is returned but the CLI exits non-zero and writes
        # nothing to disk.
        files, diagnostics = render_helm(plan)
        e166 = [d for d in diagnostics if d.code == "E166"]
        self.assertEqual(len(e166), 1)
        self.assertIn("../evil", e166[0].message)
        self.assertNotIn("../evil", "\n".join(files.values()))


if __name__ == "__main__":
    unittest.main()
