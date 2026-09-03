import contextlib
import hashlib
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import yaml

from cli.k8s_renderer import render_helm
from cli.main import _render_helm_chart
from cli.planner import build_plan


class RealProfileHelmRendererTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.repo_root = Path(__file__).resolve().parent.parent
        cls.profile = cls.repo_root / "profiles" / "local-dagster-postgres-superset" / "profile.yaml"
        cls.secret_values = {
            "CDS_ANALYTICS_DB_NAME": "analytics",
            "CDS_ANALYTICS_DB_PASSWORD": "sentinel-analytics-password",
            "CDS_ANALYTICS_DB_USER": "analytics",
            "CDS_DAGSTER_DB_NAME": "dagster",
            "CDS_DAGSTER_DB_PASSWORD": "sentinel-dagster-password",
            "CDS_DAGSTER_DB_USER": "dagster",
            "CDS_POSTGRES_SUPERUSER_PASSWORD": "sentinel-postgres-password",
            "CDS_SUPERSET_ADMIN_PASSWORD": "sentinel-admin-password",
            "CDS_SUPERSET_DB_NAME": "superset",
            "CDS_SUPERSET_DB_PASSWORD": "sentinel-superset-password",
            "CDS_SUPERSET_DB_USER": "superset",
            "CDS_SUPERSET_SECRET_KEY": "sentinel-superset-secret-key",
        }
        cls._tempdir = tempfile.TemporaryDirectory()
        env_file = Path(cls._tempdir.name) / ".env"
        env_file.write_text(
            "\n".join(f"{key}={value}" for key, value in cls.secret_values.items()) + "\n",
            encoding="utf-8",
        )
        with mock.patch.dict(os.environ, cls.secret_values, clear=False):
            plan, diagnostics = build_plan(str(cls.profile), env_file=str(env_file))
        errors = [diagnostic.format() for diagnostic in diagnostics if diagnostic.level == "error"]
        if errors or plan is None:
            raise AssertionError(f"real profile did not produce a plan: {errors}")
        cls.plan = plan

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tempdir.cleanup()

    def render(self) -> dict[str, str]:
        files, diagnostics = render_helm(self.plan)
        errors = [diagnostic.format() for diagnostic in diagnostics if diagnostic.level == "error"]
        self.assertEqual(errors, [])
        return files

    def test_chart_is_deterministic_and_matches_golden_hashes(self) -> None:
        first = self.render()
        second = self.render()
        self.assertEqual(first, second)

        golden_path = self.repo_root / "tests" / "golden" / "k8s-chart.sha256"
        expected = {}
        for line in golden_path.read_text(encoding="utf-8").splitlines():
            digest, relative_path = line.split("  ", 1)
            expected[relative_path] = digest
        actual = {
            relative_path: hashlib.sha256(content.encode("utf-8")).hexdigest()
            for relative_path, content in sorted(first.items())
        }
        self.assertEqual(actual, expected)

    def test_chart_scaffolding_and_values_are_complete(self) -> None:
        files = self.render()
        chart = yaml.safe_load(files["Chart.yaml"])
        values = yaml.safe_load(files["values.yaml"])

        self.assertEqual(chart["apiVersion"], "v2")
        self.assertEqual(chart["name"], "local-dagster-postgres-superset")
        self.assertEqual(chart["type"], "application")
        self.assertEqual(
            set(values["modules"]),
            {"postgres", "dagster", "keydb", "superset"},
        )
        self.assertEqual(
            values["modules"]["postgres"]["workloads"]["postgres"]["resources"]
            ["postgres"]["requests"]["memory"],
            "256Mi",
        )
        notes = files["templates/NOTES.txt"]
        self.assertEqual(notes.count("dagster-user-code:"), 1)
        self.assertNotIn("dagster-daemon:", notes)

    def test_secret_values_never_reach_chart_files(self) -> None:
        rendered = "\n".join(self.render().values())
        for value in self.secret_values.values():
            if not value.startswith("sentinel-"):
                continue
            self.assertNotIn(value, rendered)
        self.assertNotIn("${CDS_", rendered)
        self.assertIn("secretKeyRef:", rendered)
        self.assertIn('required "secrets.CDS_SUPERSET_SECRET_KEY is required"', rendered)

    def test_workloads_services_storage_and_configmaps_render(self) -> None:
        files = self.render()
        self.assertIn("kind: StatefulSet", files["templates/postgres-postgres-statefulset.yaml"])
        self.assertIn("storage: 5Gi", files["templates/postgres-postgres-statefulset.yaml"])
        self.assertIn("kind: Deployment", files["templates/dagster-webserver-deployment.yaml"])
        self.assertIn("host: dagster-user-code", files["templates/dagster-configmap-workspace.yaml"])
        self.assertIn("name: postgres", files["templates/postgres-postgres-service.yaml"])
        self.assertIn("name: superset", files["templates/superset-superset-service.yaml"])
        self.assertIn("-v dagster_password", files["templates/postgres-configmap-init-db.yaml"])
        self.assertIn("defaultMode: 365", files["templates/postgres-postgres-statefulset.yaml"])

    def test_postgres_init_environment_is_available_on_first_boot(self) -> None:
        manifest = self.render()["templates/postgres-postgres-statefulset.yaml"]
        for name in (
            "ANALYTICS_DB_NAME",
            "ANALYTICS_DB_USER",
            "ANALYTICS_DB_PASSWORD",
            "DAGSTER_DB_NAME",
            "DAGSTER_DB_USER",
            "DAGSTER_DB_PASSWORD",
            "SUPERSET_DB_NAME",
            "SUPERSET_DB_USER",
            "SUPERSET_DB_PASSWORD",
        ):
            self.assertIn(f"- name: {name}\n", manifest)

    def test_probes_security_contexts_and_resource_overrides_render(self) -> None:
        files = self.render()
        postgres = files["templates/postgres-postgres-statefulset.yaml"]
        keydb = files["templates/keydb-keydb-deployment.yaml"]
        superset = files["templates/superset-superset-deployment.yaml"]

        self.assertIn("readinessProbe:", postgres)
        self.assertIn("livenessProbe:", postgres)
        self.assertIn("startupProbe:", postgres)
        self.assertIn("- keydb-cli", keydb)
        self.assertIn("timed out waiting for postgres:5432", superset)
        self.assertIn('if [ "$attempt" -ge 150 ]', superset)
        for manifest in (postgres, keydb, superset):
            self.assertIn("readOnlyRootFilesystem: true", manifest)
            self.assertIn("allowPrivilegeEscalation: false", manifest)
            self.assertIn("runAsNonRoot: true", manifest)
            self.assertIn("- ALL", manifest)
        self.assertIn(
            '{{ toYaml (index .Values "modules" "superset" "workloads" "superset" "resources" "superset")',
            superset,
        )


class HelmRendererDiagnosticTest(unittest.TestCase):
    def minimal_plan(self, *, storage_enabled: bool = True, health_enabled: bool = True) -> dict:
        return {
            "metadata": {"name": "test-chart", "version": "1.2.3"},
            "runtime": {"namespace": "test"},
            "secrets": {"password": "CDS_TEST_PASSWORD"},
            "modules": [
                {
                    "id": "demo",
                    "config": {
                        "storage": {"enabled": storage_enabled},
                        "healthcheck": {"enabled": health_enabled},
                    },
                    "implementation": {
                        "kind": "docker-compose",
                        "targets": ["docker-compose", "kubernetes"],
                        "compose": {
                            "services": {
                                "app": {
                                    "image": "example/app:1",
                                    "read_only": True,
                                    "cap_drop": ["ALL"],
                                    "security_opt": ["no-new-privileges:true"],
                                    "tmpfs": ["/tmp:rw"],
                                    "volumes": ["data:/data"],
                                    "environment": {"PASSWORD": "${secrets.password}"},
                                    "healthcheck": {
                                        "conditionallyEnabledFrom": "config.healthcheck.enabled",
                                        "test": ["CMD-SHELL", "test -f /tmp/ready"],
                                        "interval": "5s",
                                        "timeout": "2s",
                                        "retries": 4,
                                        "start_period": "10s",
                                    },
                                }
                            }
                        },
                        "kubernetes": {
                            "workloads": {
                                "app": {
                                    "kind": "StatefulSet",
                                    "containers": ["app"],
                                    "podSecurityContext": {"runAsNonRoot": True, "runAsUser": 1000},
                                    "service": {
                                        "type": "ClusterIP",
                                        "ports": [{"name": "http", "port": 8080}],
                                    },
                                    "resources": {
                                        "app": {
                                            "requests": {"cpu": "10m", "memory": "16Mi"},
                                            "limits": {"memory": "32Mi"},
                                        }
                                    },
                                }
                            },
                            "volumes": {
                                "data": {
                                    "type": "persistentVolumeClaim",
                                    "size": "2Gi",
                                    "enabledFrom": "config.storage.enabled",
                                }
                            },
                        },
                    },
                }
            ],
        }

    def test_missing_kubernetes_implementation_is_an_error(self) -> None:
        plan = self.minimal_plan()
        plan["modules"][0]["implementation"].pop("kubernetes")
        plan["modules"][0]["implementation"]["targets"] = ["docker-compose"]

        files, diagnostics = render_helm(plan)

        self.assertEqual(files, {})
        self.assertIn("E072", {diagnostic.code for diagnostic in diagnostics})

    def test_disabled_storage_and_healthcheck_emit_neither_claim_nor_probes(self) -> None:
        files, diagnostics = render_helm(
            self.minimal_plan(storage_enabled=False, health_enabled=False)
        )

        self.assertFalse(any(diagnostic.level == "error" for diagnostic in diagnostics))
        workload = files["templates/demo-app-statefulset.yaml"]
        self.assertNotIn("volumeClaimTemplates:", workload)
        self.assertNotIn("readinessProbe:", workload)
        self.assertNotIn("livenessProbe:", workload)
        self.assertNotIn("startupProbe:", workload)

    def test_oversized_configmap_is_rejected(self) -> None:
        plan = self.minimal_plan()
        plan["modules"][0]["implementation"]["kubernetes"]["configMaps"] = {
            "large": {
                "key": "large.txt",
                "content": "x" * 1_000_001,
                "mountPath": "/etc/large.txt",
            }
        }

        _, diagnostics = render_helm(plan)

        self.assertIn("E075", {diagnostic.code for diagnostic in diagnostics})

    def test_failed_render_does_not_replace_last_valid_chart(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / "chart"
            files, diagnostics = render_helm(self.minimal_plan(), output_dir=str(output))
            self.assertTrue(files)
            self.assertFalse(any(d.level == "error" for d in diagnostics))
            original = (output / "Chart.yaml").read_text(encoding="utf-8")

            invalid = self.minimal_plan()
            invalid["modules"][0]["implementation"]["kubernetes"]["configMaps"] = {
                "large": {"key": "large.txt", "content": "x" * 1_000_001}
            }
            _, diagnostics = render_helm(invalid, output_dir=str(output))

            self.assertIn("E075", {diagnostic.code for diagnostic in diagnostics})
            self.assertEqual((output / "Chart.yaml").read_text(encoding="utf-8"), original)

    def test_contract_provider_without_a_service_is_rejected(self) -> None:
        plan = self.minimal_plan()
        workload = plan["modules"][0]["implementation"]["kubernetes"]["workloads"]["app"]
        workload.pop("service")
        workload["kind"] = "Deployment"
        plan["modules"].append(
            {
                "id": "consumer",
                "consumes": {
                    "backend": {
                        "contractRef": "demo.http",
                        "contract": {
                            "kind": "http",
                            "spec": {"host": "demo-app", "port": 8080},
                        },
                    }
                },
                "implementation": {
                    "kind": "docker-compose",
                    "targets": ["docker-compose", "kubernetes"],
                    "compose": {"services": {}},
                    "kubernetes": {"workloads": {}},
                },
            }
        )

        files, diagnostics = render_helm(plan)

        self.assertEqual(files, {})
        self.assertIn("E084", {diagnostic.code for diagnostic in diagnostics})

    def test_missing_resources_emit_warning(self) -> None:
        plan = self.minimal_plan()
        plan["modules"][0]["implementation"]["kubernetes"]["workloads"]["app"].pop(
            "resources"
        )

        _, diagnostics = render_helm(plan)

        self.assertIn("W072", {diagnostic.code for diagnostic in diagnostics})

    def test_job_uses_batch_api_and_restart_policy(self) -> None:
        plan = self.minimal_plan(storage_enabled=False)
        workload = plan["modules"][0]["implementation"]["kubernetes"]["workloads"]["app"]
        workload["kind"] = "Job"
        workload.pop("service")

        files, diagnostics = render_helm(plan)

        self.assertFalse(any(d.level == "error" for d in diagnostics))
        manifest = files["templates/demo-app-job.yaml"]
        self.assertIn("apiVersion: batch/v1", manifest)
        self.assertIn("restartPolicy: OnFailure", manifest)
        self.assertNotIn("matchLabels:", manifest)

    def test_chart_directory_refresh_is_atomic_and_removes_stale_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / "chart"
            code, diagnostics = _render_helm_chart(self.minimal_plan(), output, force=False)
            self.assertEqual(code, 0)
            self.assertFalse(any(diagnostic.level == "error" for diagnostic in diagnostics))
            stale = output / "templates" / "stale.yaml"
            stale.write_text("stale\n", encoding="utf-8")

            code, diagnostics = _render_helm_chart(self.minimal_plan(), output, force=False)

            self.assertEqual(code, 0)
            self.assertFalse(any(diagnostic.level == "error" for diagnostic in diagnostics))
            self.assertFalse(stale.exists())

    def test_unrelated_directory_requires_force_and_file_can_be_replaced(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / "chart"
            output.mkdir()
            (output / "unrelated.txt").write_text("keep\n", encoding="utf-8")
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                code, _ = _render_helm_chart(self.minimal_plan(), output, force=False)
            self.assertEqual(code, 1)
            self.assertIn("does not look like a CDS-generated chart", stdout.getvalue())
            self.assertTrue((output / "unrelated.txt").exists())

            file_output = Path(tmpdir) / "chart-file"
            file_output.write_text("replace me\n", encoding="utf-8")
            code, diagnostics = _render_helm_chart(self.minimal_plan(), file_output, force=True)
            self.assertEqual(code, 0)
            self.assertFalse(any(diagnostic.level == "error" for diagnostic in diagnostics))
            self.assertTrue((file_output / "Chart.yaml").is_file())
