"""
CloudNativePG operator module + declared kubernetes.services + manifest
secret references (issue #768).

Covers the acceptance criteria of #768:

- modules-experimental/warehouse/postgres-operator/module.yaml is
  module-schema valid, provides the existing `sql-database` contract, and is
  Kubernetes-only (`targets: [kubernetes]`, no compose block), so the compose
  target fails closed (E070).
- profiles/local-kubernetes-postgres-operator validates, plans, and renders
  under `--target helm` (`render_helm`), and a consumer (Superset) binds to
  `postgres.sql-database` with no consumer-side changes: no E084/E150, and
  the binding host is release-scoped to the CloudNativePG `<cluster>-rw`
  Service in the consumer's rendered env.
- The rendered chart contains the CloudNativePG Cluster CR and the
  app-credentials Secret; the password is a Helm `required` lookup of
  .Values.secrets (never a literal or a compose-style ${CDS_*}), and the key
  lands in the chart's secrets Secret.
- Renderer mechanics added for this module: `implementation.kubernetes.services`
  joins the service registry used by E084/E150 host checks and binding
  release-scoping, and whole-value `${CDS_*}` manifest values are rewritten
  to Helm lookups while embedded ones still fail closed with E078.
"""
import json
import tempfile
import unittest
import unittest.mock
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator

from cli.k8s_renderer import render_helm
from cli.planner import build_plan
from cli.renderer import render_compose
from cli.validator import validate_profile

REPO_ROOT = Path(__file__).resolve().parent.parent
MODULE_PATH = REPO_ROOT / "modules-experimental" / "warehouse" / "postgres-operator" / "module.yaml"
PROFILE_PATH = REPO_ROOT / "profiles" / "local-kubernetes-postgres-operator" / "profile.yaml"
MODULE_SCHEMA = json.loads(
    (REPO_ROOT / "cli" / "resources" / "module.schema.json").read_text(encoding="utf-8")
)

ENV_VALUES = {
    "CDS_ANALYTICS_DB_NAME": "analytics",
    "CDS_ANALYTICS_DB_PASSWORD": "sentinel-analytics-password",
    "CDS_ANALYTICS_DB_USER": "analytics",
    "CDS_SUPERSET_ADMIN_PASSWORD": "sentinel-admin-password",
    "CDS_SUPERSET_SECRET_KEY": "sentinel-superset-secret-key",
}

OPERATOR_CONFIG = {
    "database": "${CDS_ANALYTICS_DB_NAME}",
    "username": "${CDS_ANALYTICS_DB_USER}",
    "passwordFrom": "secrets.analytics_db_password",
    "port": 5432,
    "instances": 3,
    "storage": {"size": "10Gi"},
    "authentication": {"hostAuth": "scram-sha-256"},
}


def _profile(operator_config: dict) -> dict:
    # keydb stands in for "an existing Helm-target profile's" workload module:
    # a stable module with a kubernetes implementation and no required config.
    return {
        "apiVersion": "cds/v1alpha1",
        "kind": "Profile",
        "metadata": {"name": "operator-test", "environment": "local"},
        "spec": {
            "runtime": {"type": "docker-compose", "namespace": "cds-local"},
            "secrets": {
                "provider": {"type": "env"},
                "values": {
                    "analytics_db_password": {
                        "env": "CDS_ANALYTICS_DB_PASSWORD",
                        "required": True,
                    }
                },
            },
            "modules": [
                {
                    "id": "warehouse",
                    "source": "modules-experimental/warehouse/postgres-operator",
                    "version": "0.1.0",
                    "enabled": True,
                    "config": operator_config,
                },
                {
                    "id": "cache",
                    "source": "modules/cache/keydb",
                    "version": "0.1.0",
                    "enabled": True,
                    "config": {},
                },
            ],
        },
    }


def _build_plan(profile: dict, env_file: Path | None = None):
    with tempfile.TemporaryDirectory() as tmp:
        profile_path = Path(tmp) / "profile.yaml"
        profile_path.write_text(yaml.safe_dump(profile), encoding="utf-8")
        with unittest.mock.patch.dict(
            "os.environ", {"CDS_MODULE_PATH": str(REPO_ROOT)}, clear=False
        ):
            diagnostics = validate_profile(str(profile_path))
            errors = [d.format() for d in diagnostics if d.level == "error"]
            assert errors == [], errors
            plan, plan_diagnostics = build_plan(
                str(profile_path), env_file=str(env_file) if env_file else None
            )
    errors = [d.format() for d in plan_diagnostics if d.level == "error"]
    assert errors == [], errors
    assert plan is not None
    return plan


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


class PostgresOperatorModuleShapeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.module = yaml.safe_load(MODULE_PATH.read_text(encoding="utf-8"))

    def test_module_is_schema_valid(self) -> None:
        errors = list(Draft202012Validator(MODULE_SCHEMA).iter_errors(self.module))
        self.assertEqual(errors, [])

    def test_module_provides_sql_database_and_is_kubernetes_only(self) -> None:
        spec = self.module["spec"]
        provides = spec["provides"]
        self.assertEqual(len(provides), 1)
        self.assertEqual(provides[0]["name"], "sql-database")
        self.assertEqual(provides[0]["contract"]["kind"], "sql-database")
        impl = spec["implementation"]
        self.assertEqual(impl["kind"], "kubernetes")
        self.assertEqual(impl["targets"], ["kubernetes"])
        self.assertNotIn("compose", impl)
        k8s = impl["kubernetes"]
        self.assertEqual(sorted(k8s["manifests"]), ["app-credentials", "cluster"])
        self.assertEqual(sorted(k8s["services"]), ["rw"])
        self.assertFalse(self.module["metadata"]["productionSuitable"])

    def test_contract_host_points_at_the_declared_rw_service(self) -> None:
        spec = self.module["spec"]
        contract_spec = spec["provides"][0]["contract"]["spec"]
        # ${service.host} is the module instance id; the declared service key
        # "rw" resolves to <id>-rw under the same naming rule.
        self.assertEqual(contract_spec["host"], "${service.host}-rw")
        self.assertIn("@${service.host}-rw:", contract_spec["connectionUri"])
        self.assertEqual(contract_spec["engine"], "postgres")

    def test_cluster_manifest_is_the_cloudnativepg_shape(self) -> None:
        cluster = self.module["spec"]["implementation"]["kubernetes"]["manifests"]["cluster"]
        self.assertEqual(cluster["apiVersion"], "postgresql.cnpg.io/v1")
        self.assertEqual(cluster["kind"], "Cluster")
        self.assertEqual(cluster["spec"]["instances"], "${config.instances}")
        self.assertEqual(cluster["spec"]["imageName"], "${config.image}")
        self.assertIn(
            "host all all all ${config.authentication.hostAuth}",
            cluster["spec"]["postgresql"]["pg_hba"],
        )
        labels = cluster["spec"]["inheritedMetadata"]["labels"]
        self.assertEqual(labels["cds.dev/module"], "${service.host}")


class PostgresOperatorPlanRenderTest(_EnvFileMixin, unittest.TestCase):
    def _render(self, operator_config: dict) -> dict:
        plan = _build_plan(_profile(operator_config), env_file=self.env_file)
        files, diagnostics = render_helm(plan, env_file=str(self.env_file))
        errors = [d.format() for d in diagnostics if d.level == "error"]
        self.assertEqual(errors, [])
        return files

    def test_plan_resolves_the_sql_database_contract(self) -> None:
        plan = _build_plan(_profile(dict(OPERATOR_CONFIG)), env_file=self.env_file)
        warehouse = next(m for m in plan["modules"] if m["id"] == "warehouse")
        provided = warehouse["provides"]["sql-database"]["spec"]
        self.assertEqual(provided["host"], "warehouse-rw")
        self.assertEqual(provided["port"], 5432)
        self.assertEqual(provided["authentication"], "scram-sha-256")
        self.assertEqual(provided["engine"], "postgres")
        self.assertIn("@warehouse-rw:5432/", provided["connectionUri"])

    def test_render_emits_cluster_and_credentials_without_workload_files(self) -> None:
        files = self._render(dict(OPERATOR_CONFIG))
        self.assertIn("templates/warehouse-manifest-cluster.yaml", files)
        self.assertIn("templates/warehouse-manifest-app-credentials.yaml", files)
        # Manifest-only module: no pods of its own, so no workload/Service/
        # ServiceAccount/NetworkPolicy templates for it.
        for name in files:
            if name.startswith("templates/warehouse-"):
                self.assertIn("-manifest-", name)

        cluster = yaml.safe_load(files["templates/warehouse-manifest-cluster.yaml"])
        self.assertEqual(cluster["kind"], "Cluster")
        self.assertEqual(cluster["metadata"]["name"], "{{ .Release.Name }}-warehouse")
        self.assertEqual(cluster["spec"]["instances"], 3)
        self.assertEqual(cluster["spec"]["storage"]["size"], "10Gi")
        self.assertEqual(
            cluster["spec"]["postgresql"]["pg_hba"], ["host all all all scram-sha-256"]
        )
        self.assertEqual(
            cluster["spec"]["bootstrap"]["initdb"]["secret"]["name"],
            "{{ .Release.Name }}-warehouse-app",
        )
        self.assertEqual(
            cluster["spec"]["inheritedMetadata"]["labels"]["cds.dev/module"], "warehouse"
        )
        self.assertIn("sha256:", cluster["spec"]["imageName"])

    def test_app_credentials_secret_uses_helm_lookup_not_secret_material(self) -> None:
        files = self._render(dict(OPERATOR_CONFIG))
        content = files["templates/warehouse-manifest-app-credentials.yaml"]
        self.assertNotIn("${CDS_", content)
        secret = yaml.safe_load(content)
        self.assertEqual(secret["kind"], "Secret")
        self.assertEqual(secret["metadata"]["name"], "{{ .Release.Name }}-warehouse-app")
        password = secret["stringData"]["password"]
        self.assertIn('index .Values.secrets "CDS_ANALYTICS_DB_PASSWORD"', password)
        self.assertIn("required", password)
        # The key is registered in the chart's secrets Secret.
        self.assertIn("CDS_ANALYTICS_DB_PASSWORD", files["templates/secret.yaml"])

    def test_compose_target_fails_closed(self) -> None:
        plan = _build_plan(_profile(dict(OPERATOR_CONFIG)), env_file=self.env_file)
        _, diagnostics = render_compose(plan, env_file=str(self.env_file))
        codes = [d.code for d in diagnostics if d.level == "error"]
        self.assertIn("E070", codes)


class ShippedDemoProfileTest(_EnvFileMixin, unittest.TestCase):
    """profiles/local-kubernetes-postgres-operator: the acceptance criterion
    `cds test --target helm` passes for a profile using the operator module,
    with a real consumer (Superset) binding via sql-database unchanged."""

    def test_profile_validates_without_errors(self) -> None:
        diagnostics = validate_profile(str(PROFILE_PATH))
        errors = [d for d in diagnostics if d.level == "error"]
        self.assertEqual(errors, [])

    def test_profile_plans_renders_and_binds_consumer(self) -> None:
        plan, plan_diags = build_plan(str(PROFILE_PATH), env_file=str(self.env_file))
        errors = [d for d in plan_diags if d.level == "error"]
        self.assertEqual(errors, [])
        self.assertIsNotNone(plan)

        module_ids = {m["id"] for m in plan["modules"]}
        self.assertIn("postgres", module_ids)
        self.assertIn("superset", module_ids)

        # The consumer module binds to the operator's contract unchanged.
        superset = next(m for m in plan["modules"] if m["id"] == "superset")
        consumes = superset["consumes"]
        self.assertEqual(consumes["metadata-database"]["contractRef"], "postgres.sql-database")
        self.assertEqual(consumes["analytics-database"]["contractRef"], "postgres.sql-database")
        self.assertEqual(
            consumes["metadata-database"]["contract"]["spec"]["host"], "postgres-rw"
        )

        files, render_diags = render_helm(plan, env_file=str(self.env_file))
        errors = [d.format() for d in render_diags if d.level == "error"]
        self.assertEqual(errors, [])
        codes = [d.code for d in render_diags]
        self.assertNotIn("E084", codes)
        self.assertNotIn("E150", codes)

        # The binding host is release-scoped to the CloudNativePG -rw Service
        # in the consumer's rendered env and wait gate.
        deployment = files["templates/superset-superset-deployment.yaml"]
        self.assertIn("{{ .Release.Name }}-postgres-rw:5432", deployment)
        self.assertNotIn("@postgres-rw:", deployment)
        # No compose-style secret reference survives in any chart file.
        for name, content in files.items():
            self.assertNotIn("${CDS_", content, name)


def _plan_with_declared_services(provider_host: str, declared: dict) -> dict:
    """In-memory plan: manifests-only provider + a workload consumer that
    binds to the provider's sql-database contract."""
    provider = {
        "id": "warehouse",
        "source": "modules-experimental/warehouse/postgres-operator",
        "version": "0.1.0",
        "dependsOn": [],
        "config": {},
        "security": {},
        "consumes": {},
        "provides": {
            "sql-database": {
                "kind": "sql-database",
                "spec": {
                    "host": provider_host,
                    "port": 5432,
                    "database": "analytics",
                    "username": "analytics",
                    "password": "${CDS_ANALYTICS_DB_PASSWORD}",
                    "engine": "postgres",
                    "connectionUri": f"postgresql://analytics:${{CDS_ANALYTICS_DB_PASSWORD}}@{provider_host}:5432/analytics",
                },
            }
        },
        "implementation": {
            "kind": "kubernetes",
            "targets": ["kubernetes"],
            "kubernetes": {
                "services": declared,
                "manifests": {
                    "cluster": {
                        "apiVersion": "postgresql.cnpg.io/v1",
                        "kind": "Cluster",
                        "metadata": {"name": "${service.host}"},
                        "spec": {"instances": 1},
                    }
                },
            },
        },
    }
    consumer = {
        "id": "app",
        "source": "modules/cache/keydb",
        "version": "0.1.0",
        "dependsOn": ["warehouse"],
        "config": {},
        "security": {},
        "consumes": {
            "db": {
                "contractRef": "warehouse.sql-database",
                "contract": {
                    "kind": "sql-database",
                    "spec": {
                        "host": provider_host,
                        "port": 5432,
                        "database": "analytics",
                        "username": "analytics",
                        "password": "${CDS_ANALYTICS_DB_PASSWORD}",
                        "engine": "postgres",
                        "connectionUri": f"postgresql://analytics:${{CDS_ANALYTICS_DB_PASSWORD}}@{provider_host}:5432/analytics",
                    },
                },
            }
        },
        "provides": {},
        "implementation": {
            "kind": "docker-compose",
            "targets": ["docker-compose", "kubernetes"],
            "kubernetes": {
                "workloads": {
                    "app": {
                        "kind": "Deployment",
                        "containers": ["app"],
                        "service": {
                            "type": "ClusterIP",
                            "ports": [{"name": "http", "port": 8080, "targetPort": 8080}],
                        },
                        "resources": {"app": {"requests": {"cpu": "50m", "memory": "64Mi"}}},
                    }
                }
            },
            "compose": {
                "services": {
                    "app": {
                        "image": "example/app:1.0",
                        "environment": {
                            "DB_HOST": "${bindings.db.host}",
                            "DB_URI": "${bindings.db.connectionUri}",
                        },
                    }
                }
            },
        },
    }
    return {
        "apiVersion": "cds/v1alpha1",
        "kind": "Plan",
        "metadata": {"name": "demo", "environment": "local"},
        "runtime": {},
        "secrets": {"CDS_ANALYTICS_DB_PASSWORD": "CDS_ANALYTICS_DB_PASSWORD"},
        "certificates": {},
        "outputs": {},
        "modules": [provider, consumer],
    }


class DeclaredServicesTest(unittest.TestCase):
    """implementation.kubernetes.services joins the renderer's service
    registry (issue #768)."""

    def test_binding_to_a_declared_service_passes_e084_and_release_scopes(self) -> None:
        plan = _plan_with_declared_services("warehouse-rw", {"rw": {"description": "cnpg -rw"}})
        files, diagnostics = render_helm(plan)
        errors = [d for d in diagnostics if d.level == "error"]
        self.assertEqual([d.format() for d in errors], [])
        deployment = files["templates/app-app-deployment.yaml"]
        self.assertIn("{{ .Release.Name }}-warehouse-rw", deployment)

    def test_undeclared_host_still_fails_closed(self) -> None:
        # Declared services make the provider checkable, so a typo'd host is
        # now caught by both E084 (consume side) and E150 (provide side).
        plan = _plan_with_declared_services("warehouse-typo", {"rw": {}})
        _, diagnostics = render_helm(plan)
        codes = [d.code for d in diagnostics if d.level == "error"]
        self.assertIn("E084", codes)
        self.assertIn("E150", codes)

    def test_no_declared_services_keeps_e084_fail_closed(self) -> None:
        plan = _plan_with_declared_services("warehouse-rw", {})
        _, diagnostics = render_helm(plan)
        codes = [d.code for d in diagnostics if d.level == "error"]
        self.assertIn("E084", codes)

    def test_declared_service_service_name_override(self) -> None:
        plan = _plan_with_declared_services(
            "warehouse-primary", {"rw": {"serviceName": "warehouse-primary"}}
        )
        files, diagnostics = render_helm(plan)
        errors = [d for d in diagnostics if d.level == "error"]
        self.assertEqual([d.format() for d in errors], [])
        deployment = files["templates/app-app-deployment.yaml"]
        self.assertIn("{{ .Release.Name }}-warehouse-primary", deployment)


class ManifestSecretReferenceTest(unittest.TestCase):
    """Whole-value ${CDS_*} manifest values become Helm required lookups;
    embedded ones still fail closed with E078 (issue #768)."""

    def _plan(self, string_data: dict) -> dict:
        return {
            "apiVersion": "cds/v1alpha1",
            "kind": "Plan",
            "metadata": {"name": "demo", "environment": "local"},
            "runtime": {},
            "secrets": {"CDS_DB_PASSWORD": "CDS_DB_PASSWORD"},
            "certificates": {},
            "outputs": {},
            "modules": [
                {
                    "id": "warehouse",
                    "source": "modules-experimental/warehouse/postgres-operator",
                    "version": "0.1.0",
                    "dependsOn": [],
                    "config": {},
                    "security": {},
                    "consumes": {},
                    "provides": {},
                    "implementation": {
                        "kind": "kubernetes",
                        "targets": ["kubernetes"],
                        "kubernetes": {
                            "manifests": {
                                "creds": {
                                    "apiVersion": "v1",
                                    "kind": "Secret",
                                    "metadata": {"name": "creds"},
                                    "stringData": string_data,
                                }
                            }
                        },
                    },
                }
            ],
        }

    def test_exact_secret_ref_becomes_helm_lookup_and_registers_key(self) -> None:
        files, diagnostics = render_helm(
            self._plan({"password": "${CDS_DB_PASSWORD}", "username": "app"})
        )
        errors = [d.format() for d in diagnostics if d.level == "error"]
        self.assertEqual(errors, [])
        content = files["templates/warehouse-manifest-creds.yaml"]
        self.assertNotIn("${CDS_", content)
        secret = yaml.safe_load(content)
        self.assertEqual(
            secret["stringData"]["password"],
            '{{ required "secrets.CDS_DB_PASSWORD is required" '
            '(index .Values.secrets "CDS_DB_PASSWORD") }}',
        )
        self.assertEqual(secret["stringData"]["username"], "app")
        self.assertIn("CDS_DB_PASSWORD", files["templates/secret.yaml"])

    def test_embedded_secret_ref_still_fails_closed_with_e078(self) -> None:
        _, diagnostics = render_helm(
            self._plan({"uri": "postgresql://app:${CDS_DB_PASSWORD}@db:5432/app"})
        )
        codes = [d.code for d in diagnostics if d.level == "error"]
        self.assertIn("E078", codes)


if __name__ == "__main__":
    unittest.main()
