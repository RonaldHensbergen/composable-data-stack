"""
Service-mesh contract + Istio reference module (issue #776).

Covers the acceptance criteria of #776 and the promotion in #1171:

- shared/contracts/service-mesh.yaml is schema-valid via
  cli.validator.validate_contract_file (the repo-wide contract test also
  globs it; this test pins the specific file and its fields).
- modules/security/istio/module.yaml is module-schema valid,
  provides the service-mesh contract, and is Kubernetes-only
  (`targets: [kubernetes]`, no compose block), so the compose target fails
  closed with E070.
- A profile binds existing modules (a warehouse and a cache module, used
  verbatim) to the mesh with no consumer-side changes: the enrollment lives
  entirely in the istio module's `config.services`, the plan resolves the
  service-mesh contract from it, `cds maturity` counts the mesh, and
  render_helm emits the PeerAuthentication (plus the gated
  AuthorizationPolicy) as chart templates with a W130 operator warning.
"""

import json
import tempfile
import unittest
import unittest.mock
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator

from cli.k8s_renderer import render_helm
from cli.maturity import build_maturity_report
from cli.planner import build_plan
from cli.renderer import render_compose
from cli.validator import validate_contract_file, validate_profile

REPO_ROOT = Path(__file__).resolve().parent.parent
CONTRACT_PATH = REPO_ROOT / "shared" / "contracts" / "service-mesh.yaml"
MODULE_PATH = REPO_ROOT / "modules" / "security" / "istio" / "module.yaml"
MODULE_SCHEMA = json.loads(
    (REPO_ROOT / "cli" / "resources" / "module.schema.json").read_text(encoding="utf-8")
)

ISTIO_CONFIG = {
    "mtlsMode": "STRICT",
    "services": [
        {"name": "warehouse", "port": 5432},
        {"name": "cache", "port": 6379},
    ],
}

ENV_VALUES = {
    "CDS_WAREHOUSE_DB_PASSWORD": "sentinel-warehouse-password",
    "CDS_WAREHOUSE_SUPERUSER_PASSWORD": "sentinel-superuser-password",
}


def _profile(istio_config: dict) -> dict:
    # warehouse (modules/warehouse/postgres) and cache (modules/cache/keydb)
    # stand in for "existing modules of a Helm-target profile": stable modules
    # used verbatim — the mesh enrollment happens only in the istio module's
    # config, with no consumer-side changes to these two.
    return {
        "apiVersion": "cds/v1alpha1",
        "kind": "Profile",
        "metadata": {"name": "mesh-test", "environment": "local"},
        "spec": {
            "runtime": {"type": "docker-compose", "namespace": "cds-local"},
            "secrets": {
                "provider": {"type": "env"},
                "values": {
                    "warehouse_db_password": {
                        "env": "CDS_WAREHOUSE_DB_PASSWORD",
                        "required": True,
                    },
                    "warehouse_superuser_password": {
                        "env": "CDS_WAREHOUSE_SUPERUSER_PASSWORD",
                        "required": True,
                    },
                },
            },
            "modules": [
                {
                    "id": "warehouse",
                    "source": "modules/warehouse/postgres",
                    "version": "0.1.0",
                    "enabled": True,
                    "config": {
                        "database": "analytics",
                        "username": "analytics",
                        "passwordFrom": "secrets.warehouse_db_password",
                        "superuserPasswordFrom": "secrets.warehouse_superuser_password",
                        "port": 5432,
                        "initDbScript": "init-db.sql",
                    },
                },
                {
                    "id": "cache",
                    "source": "modules/cache/keydb",
                    "version": "0.1.0",
                    "enabled": True,
                    "config": {},
                },
                {
                    "id": "mesh",
                    "source": "modules/security/istio",
                    "version": "0.1.0",
                    "enabled": True,
                    "config": istio_config,
                },
            ],
        },
    }


class _EnvFileMixin:
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


class ServiceMeshContractTest(unittest.TestCase):
    def test_contract_file_is_schema_valid(self) -> None:
        self.assertEqual(validate_contract_file(CONTRACT_PATH), [])

    def test_contract_declares_the_mesh_fields(self) -> None:
        contract = yaml.safe_load(CONTRACT_PATH.read_text(encoding="utf-8"))
        self.assertEqual(contract["kind"], "Contract")
        self.assertEqual(contract["metadata"]["name"], "service-mesh")
        fields = contract["spec"]["fields"]
        self.assertTrue(fields["mtlsMode"]["required"])
        self.assertTrue(fields["services"]["required"])
        # Per-service authorization is optional on top of mTLS.
        self.assertFalse(fields["authorizationPolicy"]["required"])
        self.assertTrue(contract["spec"]["examples"])


class IstioModuleShapeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.module = yaml.safe_load(MODULE_PATH.read_text(encoding="utf-8"))

    def test_module_is_schema_valid(self) -> None:
        errors = list(Draft202012Validator(MODULE_SCHEMA).iter_errors(self.module))
        self.assertEqual(errors, [])

    def test_module_provides_service_mesh_and_is_kubernetes_only(self) -> None:
        spec = self.module["spec"]
        provides = spec["provides"]
        self.assertEqual(len(provides), 1)
        self.assertEqual(provides[0]["name"], "service-mesh")
        self.assertEqual(provides[0]["contract"]["kind"], "service-mesh")
        impl = spec["implementation"]
        self.assertEqual(impl["kind"], "kubernetes")
        self.assertEqual(impl["targets"], ["kubernetes"])
        self.assertNotIn("compose", impl)
        self.assertEqual(
            sorted(impl["kubernetes"]["manifests"]),
            ["authorization-policy", "peer-authentication"],
        )
        # The AuthorizationPolicy fans out per enrolled service (issue #1170).
        authz = impl["kubernetes"]["manifests"]["authorization-policy"]
        self.assertEqual(authz["forEach"], "config.services")
        self.assertEqual(authz["enabledFrom"], "config.authorization.enabled")
        self.assertEqual(
            authz["spec"]["selector"]["matchLabels"], {"cds.dev/module": "${item.name}"}
        )
        self.assertEqual(authz["metadata"]["name"], "mesh-allow-${item.name}")
        operators = impl["kubernetes"]["operators"]
        self.assertEqual(operators[0]["crd"], "peerauthentications.security.istio.io")
        self.assertIn("istio", operators[0]["install"])
        self.assertTrue(self.module["metadata"]["productionSuitable"])
        self.assertEqual(spec["configSchema"]["required"], ["services"])


class IstioRenderTest(_EnvFileMixin, unittest.TestCase):
    def _build_plan(self, istio_config: dict, render: bool = False):
        profile = _profile(istio_config)
        # The profile lives in a temp dir under the repo root so the
        # warehouse module's init-db.sql bind resolves like in a real
        # profile directory (the renderer resolves binds against the
        # project root). The dir must stay alive until rendering is done.
        self._tmp = tempfile.TemporaryDirectory(dir=REPO_ROOT)
        tmp = self._tmp.name
        profile_path = Path(tmp) / "profile.yaml"
        profile_path.write_text(yaml.safe_dump(profile), encoding="utf-8")
        (Path(tmp) / "init-db.sql").write_text("-- none\n", encoding="utf-8")
        with unittest.mock.patch.dict(
            "os.environ", {"CDS_MODULE_PATH": str(REPO_ROOT)}, clear=False
        ):
            diagnostics = validate_profile(str(profile_path))
            errors = [d.format() for d in diagnostics if d.level == "error"]
            self.assertEqual(errors, [])
            plan, plan_diagnostics = build_plan(
                str(profile_path), env_file=str(self.env_file)
            )
        errors = [d.format() for d in plan_diagnostics if d.level == "error"]
        self.assertEqual(errors, [])
        self.assertIsNotNone(plan)
        if render:
            files, render_diagnostics = render_helm(plan)
            return plan, files, render_diagnostics
        return plan

    def tearDown(self) -> None:
        tmp = getattr(self, "_tmp", None)
        if tmp is not None:
            tmp.cleanup()
            self._tmp = None

    def test_plan_resolves_the_service_mesh_contract(self) -> None:
        plan = self._build_plan(dict(ISTIO_CONFIG))
        mesh = next(m for m in plan["modules"] if m["id"] == "mesh")
        provided = mesh["provides"]["service-mesh"]
        self.assertEqual(provided["kind"], "service-mesh")
        spec = provided["spec"]
        self.assertEqual(spec["mtlsMode"], "STRICT")
        self.assertEqual(
            spec["services"],
            [{"name": "warehouse", "port": 5432}, {"name": "cache", "port": 6379}],
        )
        # Defaults applied at plan time: authorization policy present but off.
        self.assertEqual(spec["authorizationPolicy"], {"enabled": False, "rules": []})

    def test_enrollment_needs_no_consumer_side_changes(self) -> None:
        plan = self._build_plan(dict(ISTIO_CONFIG))
        by_id = {m["id"]: m for m in plan["modules"]}
        # The two enrolled modules are the stock modules, with no consume
        # entry pointing at the mesh: the binding lives only in the istio
        # module's config.services, naming their instance ids.
        for module_id in ("warehouse", "cache"):
            self.assertNotIn(
                "service-mesh", yaml.safe_dump(by_id[module_id].get("consumes", {}))
            )
        mesh_config = by_id["mesh"]["config"]
        self.assertEqual(
            [s["name"] for s in mesh_config["services"]], ["warehouse", "cache"]
        )

    def test_maturity_counts_the_service_mesh(self) -> None:
        plan = self._build_plan(dict(ISTIO_CONFIG))
        report = build_maturity_report(plan)
        mesh_cap = next(c for c in report["capabilities"] if c["id"] == "service-mesh")
        self.assertTrue(mesh_cap["bound"])
        self.assertEqual(mesh_cap["providerInstances"], ["mesh"])

    def _render(self, istio_config: dict):
        _, files, diagnostics = self._build_plan(istio_config, render=True)
        errors = [d.format() for d in diagnostics if d.level == "error"]
        self.assertEqual(errors, [])
        return files, diagnostics

    def test_render_emits_peer_authentication_and_warns_w130(self) -> None:
        files, diagnostics = self._render(dict(ISTIO_CONFIG))
        self.assertIn("templates/mesh-manifest-peer-authentication.yaml", files)
        # AuthorizationPolicy is gated off by default: no fanned-out
        # per-service copies either.
        self.assertNotIn("templates/mesh-manifest-authorization-policy.yaml", files)
        self.assertEqual([name for name in files if "authorization-policy" in name], [])

        peer_auth = yaml.safe_load(
            files["templates/mesh-manifest-peer-authentication.yaml"]
        )
        self.assertEqual(peer_auth["apiVersion"], "security.istio.io/v1")
        self.assertEqual(peer_auth["kind"], "PeerAuthentication")
        self.assertEqual(peer_auth["metadata"]["name"], "{{ .Release.Name }}-mesh-mtls")
        self.assertEqual(peer_auth["metadata"]["labels"]["cds.dev/module"], "mesh")
        self.assertEqual(peer_auth["spec"]["mtls"]["mode"], "STRICT")

        warnings = [d for d in diagnostics if d.level == "warning"]
        self.assertIn("W130", [d.code for d in warnings])
        self.assertIn(
            "peerauthentications.security.istio.io",
            next(d for d in warnings if d.code == "W130").message,
        )

    def test_authorization_policy_renders_per_service_only_when_enabled(self) -> None:
        rules = [
            {
                "to": [{"operation": {"ports": ["5432"]}}],
                "from": [
                    {
                        "source": {
                            "principals": [
                                "cluster.local/ns/cds-local/sa/orchestration"
                            ]
                        }
                    }
                ],
            }
        ]
        config = dict(ISTIO_CONFIG, authorization={"enabled": True, "rules": rules})
        files, _ = self._render(config)
        # One AuthorizationPolicy per enrolled service (issue #1170), no
        # namespace-wide policy file.
        self.assertNotIn("templates/mesh-manifest-authorization-policy.yaml", files)
        for service in ("warehouse", "cache"):
            path = (
                "templates/mesh-manifest-authorization-policy-"
                f"mesh-allow-{service}.yaml"
            )
            self.assertIn(path, files)
            policy = yaml.safe_load(files[path])
            self.assertEqual(policy["apiVersion"], "security.istio.io/v1")
            self.assertEqual(policy["kind"], "AuthorizationPolicy")
            self.assertEqual(
                policy["metadata"]["name"],
                f"{{{{ .Release.Name }}}}-mesh-allow-{service}",
            )
            self.assertEqual(policy["metadata"]["labels"]["cds.dev/module"], "mesh")
            self.assertEqual(policy["spec"]["action"], "ALLOW")
            # The policy is selected onto exactly that service's pods.
            self.assertEqual(
                policy["spec"]["selector"]["matchLabels"], {"cds.dev/module": service}
            )
            self.assertEqual(policy["spec"]["rules"], rules)
            # The enabledFrom gate and the forEach marker are stripped.
            self.assertNotIn("enabledFrom", policy)
            self.assertNotIn("forEach", policy)

    def test_manifest_only_module_gets_no_service_account_or_network_policy(
        self,
    ) -> None:
        files, _ = self._render(dict(ISTIO_CONFIG))
        self.assertNotIn("templates/mesh-service-account.yaml", files)
        self.assertNotIn("templates/mesh-network-policy.yaml", files)
        # Workload modules in the same chart keep theirs.
        self.assertIn("templates/cache-service-account.yaml", files)
        self.assertIn("templates/cache-network-policy.yaml", files)

    def test_istio_module_fails_closed_on_the_compose_target(self) -> None:
        plan = self._build_plan(dict(ISTIO_CONFIG))
        _, diagnostics = render_compose(plan)
        codes = [d.code for d in diagnostics if d.level == "error"]
        self.assertIn("E070", codes)


if __name__ == "__main__":
    unittest.main()
