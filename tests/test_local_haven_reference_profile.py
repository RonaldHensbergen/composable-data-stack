"""Contract coverage for the local-haven-reference profile.

Names `profiles/local-haven-reference` (see docs/contract-coverage.md,
issue #1079) and exercises its contract behavior: the profile validates,
plans, and renders to docker-compose without errors, publishing the
identity provider (OIDC), edge (reverse proxy), and database output
contracts of the Haven-aligned reference shape. Since #1140 (MVP Gate 3
G3.3) it also renders to `--target helm` without E072: keycloak and traefik
ship kubernetes implementations, and the profile's keycloak route becomes
a generated ConfigMap plus a NetworkPolicy edge on the helm target.
"""

import hashlib
import tempfile
import unittest
from pathlib import Path

import yaml

from cli.k8s_renderer import render_helm
from cli.planner import build_plan
from cli.renderer import render_compose
from cli.validator import validate_profile

REPO_ROOT = Path(__file__).resolve().parent.parent
PROFILE_PATH = REPO_ROOT / "profiles" / "local-haven-reference" / "profile.yaml"

# Non-secret config values and required secrets the profile resolves from
# the environment (see the profile README).
ENV_VALUES = {
    "CDS_IDENTITY_DB_NAME": "identity",
    "CDS_IDENTITY_DB_PASSWORD": "sentinel-identity-password",
    "CDS_IDENTITY_DB_USER": "identity",
    "CDS_KEYCLOAK_ADMIN_PASSWORD": "sentinel-keycloak-password",
    "CDS_POSTGRES_SUPERUSER_PASSWORD": "sentinel-postgres-password",
}


class LocalHavenReferenceProfileTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tempdir = tempfile.TemporaryDirectory()
        cls.env_file = Path(cls._tempdir.name) / ".env"
        cls.env_file.write_text(
            "\n".join(f"{key}={value}" for key, value in ENV_VALUES.items()) + "\n",
            encoding="utf-8",
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tempdir.cleanup()

    def test_profile_validates_without_errors(self) -> None:
        self.assertTrue(PROFILE_PATH.exists(), f"profile not found at {PROFILE_PATH}")
        diagnostics = validate_profile(str(PROFILE_PATH))
        errors = [d for d in diagnostics if d.level == "error"]
        self.assertEqual(errors, [])

    def test_profile_plans_and_renders_compose(self) -> None:
        plan, plan_diags = build_plan(str(PROFILE_PATH), env_file=str(self.env_file))
        self.assertIsNotNone(plan)
        errors = [d for d in plan_diags if d.level == "error"]
        self.assertEqual(errors, [])

        # Haven-aligned output contracts: identity database, identity API,
        # OIDC provider, and edge reverse proxy.
        outputs = plan.get("outputs", {}).get("contracts", {})
        for expected in ("identityDatabase", "identityApi", "identityProvider", "edge"):
            self.assertIn(expected, outputs)

        output, render_diags = render_compose(plan, env_file=str(self.env_file))
        errors = [d for d in render_diags if d.level == "error"]
        self.assertEqual(errors, [])

        compose = yaml.safe_load(output)
        services = compose["services"]
        for expected in ("postgres", "keycloak", "traefik"):
            self.assertIn(expected, services)
        # Keycloak consumes the generic sql-database contract from postgres.
        keycloak_env = services["keycloak"]["environment"]
        self.assertEqual(keycloak_env["KC_DB"], "postgres")
        self.assertEqual(keycloak_env["KC_DB_URL_HOST"], "postgres")
        # Compare stringified: the contract is the port value, not the YAML
        # scalar type the renderer happens to emit.
        self.assertEqual(str(keycloak_env["KC_DB_URL_PORT"]), "5432")

    def test_traefik_route_is_generated_from_the_keycloak_binding(self) -> None:
        # issue #883: the profile binds keycloak.http-service through the
        # traefik module's routes consume entry; the planner resolves it and
        # the renderer generates the file-provider document from the bound
        # contract -- the profile ships no hand-written routing file.
        plan, plan_diags = build_plan(str(PROFILE_PATH), env_file=str(self.env_file))
        self.assertIsNotNone(plan)
        self.assertEqual([d for d in plan_diags if d.level == "error"], [])

        traefik = next(m for m in plan["modules"] if m["id"] == "traefik")
        bound = traefik["consumes"]["routes"]
        self.assertEqual([b["contractRef"] for b in bound], ["keycloak.http-service"])
        self.assertEqual(bound[0]["contract"]["spec"]["host"], "keycloak")
        # The backend port is keycloak's in-network container port, not the
        # localhost host publish (config.httpPort: 8081 in this profile).
        self.assertEqual(str(bound[0]["contract"]["spec"]["port"]), "8080")

        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / "docker-compose.yml"
            _, render_diags = render_compose(
                plan, output_path=str(output), env_file=str(self.env_file)
            )
            self.assertEqual([d for d in render_diags if d.level == "error"], [])
            routes_file = (
                Path(tmpdir) / "traefik" / "dynamic" / "cds-routes-traefik.yml"
            )
            self.assertTrue(
                routes_file.is_file(), f"missing generated routes file {routes_file}"
            )
            document = yaml.safe_load(routes_file.read_text(encoding="utf-8"))
            router = document["http"]["routers"]["cds-route-0-default"]
            self.assertEqual(router["rule"], "PathPrefix(`/`)")
            self.assertEqual(router["entryPoints"], ["websecure"])
            self.assertEqual(router["tls"], {})
            self.assertEqual(
                document["http"]["services"]["cds-service-keycloak"]["loadBalancer"][
                    "servers"
                ],
                [{"url": "http://keycloak:8080"}],
            )

        # No hand-written routing file remains in the repository.
        self.assertFalse((REPO_ROOT / "traefik" / "dynamic" / "keycloak.yml").exists())

    def test_profile_renders_on_helm_target_without_e072(self) -> None:
        # Issue #1140 (MVP Gate 3, task G3.3): keycloak and traefik have
        # kubernetes implementations, so the Haven reference shape renders
        # `--target helm` instead of failing closed with E072.
        plan, plan_diags = build_plan(str(PROFILE_PATH), env_file=str(self.env_file))
        self.assertIsNotNone(plan)
        self.assertEqual([d for d in plan_diags if d.level == "error"], [])

        files, render_diags = render_helm(plan, env_file=str(self.env_file))
        errors = [d for d in render_diags if d.level == "error"]
        self.assertEqual(errors, [], msg=f"unexpected helm render errors: {render_diags}")
        self.assertNotIn("E072", {d.code for d in render_diags})

        # Every module renders a workload and a Service on the helm target.
        for module, workload in (
            ("postgres", "postgres"),
            ("keycloak", "keycloak"),
            ("traefik", "traefik"),
        ):
            self.assertIn(f"templates/{module}-{workload}-service.yaml", files)
            self.assertTrue(
                any(
                    name.startswith(f"templates/{module}-{workload}-")
                    and name.endswith(("deployment.yaml", "statefulset.yaml"))
                    for name in files
                ),
                f"no workload manifest for {module}",
            )

        # Keycloak sits behind the TLS ingress: the traefik route renders as
        # a generated ConfigMap dialing the release-scoped keycloak Service,
        # and the keycloak NetworkPolicy admits the proxy on the contract
        # port (route-derived edges, issue #1033).
        routes_cm = yaml.safe_load(files["templates/traefik-configmap-routes.yaml"])
        route_document = yaml.safe_load(routes_cm["data"]["cds-routes-traefik.yml"])
        router = route_document["http"]["routers"]["cds-route-0-default"]
        self.assertEqual(router["entryPoints"], ["websecure"])
        self.assertEqual(router["tls"], {})
        servers = route_document["http"]["services"]["cds-service-keycloak"][
            "loadBalancer"
        ]["servers"]
        self.assertEqual(
            servers, [{"url": "http://{{ .Release.Name }}-keycloak:8080"}]
        )
        keycloak_np = files["templates/keycloak-network-policy.yaml"]
        self.assertIn("cds.dev/module: traefik", keycloak_np)
        self.assertIn("port: 8080", keycloak_np)

        # Keycloak consumes the sql-database binding on helm as well: the
        # wait gate targets the release-scoped postgres Service and the DB
        # host env is release-scoped.
        keycloak_deployment = files["templates/keycloak-keycloak-deployment.yaml"]
        self.assertIn("{{ .Release.Name }}-postgres", keycloak_deployment)
        self.assertIn("CDS_OIDC_REALM_IMPORT", keycloak_deployment)

    def test_helm_chart_is_deterministic_and_matches_golden_hashes(self) -> None:
        # Pins the whole Haven-shaped helm chart (issue #1140), mirroring
        # tests/golden/k8s-chart.sha256 and k8s-chart-oidc.sha256.
        def render() -> dict[str, str]:
            plan, plan_diags = build_plan(
                str(PROFILE_PATH), env_file=str(self.env_file)
            )
            self.assertIsNotNone(plan)
            self.assertEqual([d for d in plan_diags if d.level == "error"], [])
            files, render_diags = render_helm(plan, env_file=str(self.env_file))
            self.assertEqual(
                [d for d in render_diags if d.level == "error"], [],
                msg=f"unexpected helm render errors: {render_diags}",
            )
            return files

        first = render()
        second = render()
        self.assertEqual(first, second)

        golden_path = REPO_ROOT / "tests" / "golden" / "k8s-chart-haven.sha256"
        expected = {}
        for line in golden_path.read_text(encoding="utf-8").splitlines():
            digest, relative_path = line.split("  ", 1)
            expected[relative_path] = digest
        actual = {
            relative_path: hashlib.sha256(content.encode("utf-8")).hexdigest()
            for relative_path, content in sorted(first.items())
        }
        self.assertEqual(actual, expected)


if __name__ == "__main__":
    unittest.main()
