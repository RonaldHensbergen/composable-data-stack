import contextlib
import hashlib
import io
import os
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest import mock

import yaml

from cli.k8s_renderer import _configmap_checksum, render_helm
from cli.main import _render_helm_chart
from cli.planner import build_plan


class RealProfileHelmRendererTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.repo_root = Path(__file__).resolve().parent.parent
        cls.profile = (
            cls.repo_root
            / "profiles"
            / "local-dagster-postgres-superset"
            / "profile.yaml"
        )
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
            "\n".join(f"{key}={value}" for key, value in cls.secret_values.items())
            + "\n",
            encoding="utf-8",
        )
        with mock.patch.dict(os.environ, cls.secret_values, clear=False):
            plan, diagnostics = build_plan(str(cls.profile), env_file=str(env_file))
        errors = [
            diagnostic.format()
            for diagnostic in diagnostics
            if diagnostic.level == "error"
        ]
        if errors or plan is None:
            raise AssertionError(f"real profile did not produce a plan: {errors}")
        cls.plan = plan

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tempdir.cleanup()

    def render(self) -> dict[str, str]:
        files, diagnostics = render_helm(self.plan)
        errors = [
            diagnostic.format()
            for diagnostic in diagnostics
            if diagnostic.level == "error"
        ]
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
            values["modules"]["postgres"]["workloads"]["postgres"]["resources"][
                "postgres"
            ]["requests"]["memory"],
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
        self.assertIn(
            'required "secrets.CDS_SUPERSET_SECRET_KEY is required"', rendered
        )

    def test_workloads_services_storage_and_configmaps_render(self) -> None:
        files = self.render()
        self.assertIn(
            "kind: StatefulSet", files["templates/postgres-postgres-statefulset.yaml"]
        )
        self.assertIn(
            "storage: 5Gi", files["templates/postgres-postgres-statefulset.yaml"]
        )
        self.assertIn(
            "kind: Deployment", files["templates/dagster-webserver-deployment.yaml"]
        )
        dagster_service = yaml.safe_load(
            files["templates/dagster-webserver-service.yaml"]
        )
        superset_service = yaml.safe_load(
            files["templates/superset-superset-service.yaml"]
        )
        superset_deployment = files["templates/superset-superset-deployment.yaml"]
        self.assertEqual(dagster_service["spec"]["type"], "NodePort")
        self.assertEqual(dagster_service["spec"]["ports"][0]["nodePort"], 30300)
        self.assertEqual(superset_service["spec"]["type"], "NodePort")
        self.assertEqual(superset_service["spec"]["ports"][0]["nodePort"], 30808)
        self.assertIn("SUPERSET_ANALYTICS_DATABASE_URI", superset_deployment)
        self.assertIn(
            "tender_analytics.py",
            files["templates/dagster-configmap-tender-analytics.yaml"],
        )
        self.assertIn(
            "/app/workdirs/dagster/tender_analytics.py",
            files["templates/dagster-user-code-deployment.yaml"],
        )
        self.assertIn(
            "cds.dev/config-checksum:",
            files["templates/dagster-user-code-deployment.yaml"],
        )
        self.assertIn(
            "host: {{ .Release.Name }}-dagster-user-code",
            files["templates/dagster-configmap-workspace.yaml"],
        )
        postgres_service = yaml.safe_load(
            files["templates/postgres-postgres-service.yaml"]
        )
        superset_service = yaml.safe_load(
            files["templates/superset-superset-service.yaml"]
        )
        self.assertEqual(
            postgres_service["metadata"]["name"], "{{ .Release.Name }}-postgres"
        )
        self.assertEqual(
            superset_service["metadata"]["name"], "{{ .Release.Name }}-superset"
        )
        self.assertIn(
            "-v dagster_password", files["templates/postgres-configmap-init-db.yaml"]
        )
        self.assertIn(
            "defaultMode: 365", files["templates/postgres-postgres-statefulset.yaml"]
        )

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
        # The keydb probes authenticate when a password is configured (the
        # $$ escape in the compose healthcheck cannot cross into Helm
        # templates, so the kubernetes section overrides them with single-$
        # commands; see modules/cache/keydb/module.yaml, issue #206).
        self.assertIn('keydb-cli -a "$KEYDB_PASSWORD" ping', keydb)
        self.assertIn(
            'exec keydb-server --requirepass "$KEYDB_PASSWORD"', keydb
        )
        self.assertIn(
            "timed out waiting for {{ .Release.Name }}-postgres:5432", superset
        )
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
        self.assertIn("{{ .Release.Name }}-postgres:5432", superset)


class HelmRendererDiagnosticTest(unittest.TestCase):
    def test_configmap_content_changes_the_workload_checksum(self) -> None:
        volumes = {"code": {"configMap": {"name": "release-code", "defaultMode": 292}}}

        def configmaps(content: str) -> dict:
            return {
                "code": {
                    "manifest": {
                        "metadata": {"name": "release-code"},
                        "data": {"app.py": content},
                    }
                }
            }

        first = _configmap_checksum(volumes, configmaps("print('first')"))
        second = _configmap_checksum(volumes, configmaps("print('second')"))

        self.assertRegex(first or "", r"^[0-9a-f]{64}$")
        self.assertNotEqual(first, second)

    def minimal_plan(
        self, *, storage_enabled: bool = True, health_enabled: bool = True
    ) -> dict:
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
                                    "podSecurityContext": {
                                        "runAsNonRoot": True,
                                        "runAsUser": 1000,
                                    },
                                    "service": {
                                        "type": "ClusterIP",
                                        "ports": [{"name": "http", "port": 8080}],
                                    },
                                    "resources": {
                                        "app": {
                                            "requests": {
                                                "cpu": "10m",
                                                "memory": "16Mi",
                                            },
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

    def test_backup_capability_is_rejected_for_helm_target(self) -> None:
        # The built-in backup capability (issue #665) has no Kubernetes
        # sidecar workload yet; enabling it must fail closed instead of
        # rendering a chart whose database is silently unprotected.
        plan = self.minimal_plan()
        plan["modules"][0]["config"]["backup"] = {"enabled": True}

        files, diagnostics = render_helm(plan)

        self.assertEqual(files, {})
        matching = [d for d in diagnostics if d.code == "E142"]
        self.assertEqual(len(matching), 1)
        self.assertEqual(matching[0].path, "module:demo.config.backup.enabled")

    def test_disabled_backup_capability_renders_helm(self) -> None:
        plan = self.minimal_plan()
        plan["modules"][0]["config"]["backup"] = {"enabled": False}

        files, diagnostics = render_helm(plan)

        self.assertFalse(any(d.level == "error" for d in diagnostics))
        self.assertIn("templates/demo-app-statefulset.yaml", files)

    def test_disabled_storage_and_healthcheck_emit_neither_claim_nor_probes(
        self,
    ) -> None:
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

    def test_configmap_bind_rejects_absolute_and_traversal_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            project = workspace / "project"
            profile = project / "profiles" / "demo" / "profile.yaml"
            profile.parent.mkdir(parents=True)
            (project / "pyproject.toml").write_text("[project]\nname='demo'\n")
            outside = workspace / "secret.txt"
            outside.write_text("do not render\n", encoding="utf-8")

            for from_bind in (str(outside), "../secret.txt"):
                with self.subTest(from_bind=from_bind):
                    plan = self.minimal_plan()
                    plan["sourceProfile"] = str(profile)
                    plan["modules"][0]["implementation"]["kubernetes"]["configMaps"] = {
                        "unsafe": {
                            "key": "unsafe.txt",
                            "fromBind": from_bind,
                            "mountPath": "/etc/unsafe.txt",
                        }
                    }

                    files, diagnostics = render_helm(plan)

                    self.assertIn("E085", {diag.code for diag in diagnostics})
                    self.assertNotIn("do not render", "\n".join(files.values()))

    def test_configmap_bind_rejects_symlink_escape(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            project = workspace / "project"
            profile = project / "profiles" / "demo" / "profile.yaml"
            profile.parent.mkdir(parents=True)
            (project / "pyproject.toml").write_text("[project]\nname='demo'\n")
            outside = workspace / "secret.txt"
            outside.write_text("do not render\n", encoding="utf-8")
            link = project / "linked-secret.txt"
            try:
                link.symlink_to(outside)
            except OSError as exc:
                self.skipTest(f"symlinks unavailable on this platform: {exc}")

            plan = self.minimal_plan()
            plan["sourceProfile"] = str(profile)
            plan["modules"][0]["implementation"]["kubernetes"]["configMaps"] = {
                "unsafe": {
                    "key": "unsafe.txt",
                    "fromBind": "linked-secret.txt",
                    "mountPath": "/etc/unsafe.txt",
                }
            }

            files, diagnostics = render_helm(plan)

            self.assertIn("E085", {diag.code for diag in diagnostics})
            self.assertNotIn("do not render", "\n".join(files.values()))

    def test_module_local_service_aliases_do_not_collide(self) -> None:
        plan = self.minimal_plan(storage_enabled=False)
        template = plan["modules"][0]
        modules = []
        for module_id in ("alpha", "beta"):
            module = deepcopy(template)
            module["id"] = module_id
            workload = module["implementation"]["kubernetes"]["workloads"]["app"]
            workload["kind"] = "Deployment"
            workload["waitFor"] = [
                {"host": "${k8s.service.app}", "port": 8080, "timeoutSeconds": 2}
            ]
            modules.append(module)
        plan["modules"] = modules

        files, diagnostics = render_helm(plan)

        self.assertFalse(any(diag.level == "error" for diag in diagnostics))
        self.assertIn(
            "{{ .Release.Name }}-alpha-app:8080",
            files["templates/alpha-app-deployment.yaml"],
        )
        self.assertIn(
            "{{ .Release.Name }}-beta-app:8080",
            files["templates/beta-app-deployment.yaml"],
        )

    def test_service_and_statefulset_names_are_release_scoped(self) -> None:
        files, diagnostics = render_helm(self.minimal_plan())

        self.assertFalse(any(diag.level == "error" for diag in diagnostics))
        service = yaml.safe_load(files["templates/demo-app-service.yaml"])
        self.assertEqual(
            service["metadata"]["name"], "{{ .Release.Name }}-demo-app"
        )
        self.assertIn(
            "serviceName: '{{ .Release.Name }}-demo-app'",
            files["templates/demo-app-statefulset.yaml"],
        )

    def test_failed_render_does_not_replace_last_valid_chart(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / "chart"
            files, diagnostics = render_helm(
                self.minimal_plan(), output_dir=str(output)
            )
            self.assertTrue(files)
            self.assertFalse(any(d.level == "error" for d in diagnostics))
            original = (output / "Chart.yaml").read_text(encoding="utf-8")

            invalid = self.minimal_plan()
            invalid["modules"][0]["implementation"]["kubernetes"]["configMaps"] = {
                "large": {"key": "large.txt", "content": "x" * 1_000_001}
            }
            _, diagnostics = render_helm(invalid, output_dir=str(output))

            self.assertIn("E075", {diagnostic.code for diagnostic in diagnostics})
            self.assertEqual(
                (output / "Chart.yaml").read_text(encoding="utf-8"), original
            )

    def test_contract_provider_without_a_service_is_rejected(self) -> None:
        plan = self.minimal_plan()
        workload = plan["modules"][0]["implementation"]["kubernetes"]["workloads"][
            "app"
        ]
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
        workload = plan["modules"][0]["implementation"]["kubernetes"]["workloads"][
            "app"
        ]
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
            code, diagnostics = _render_helm_chart(
                self.minimal_plan(), output, force=False
            )
            self.assertEqual(code, 0)
            self.assertFalse(
                any(diagnostic.level == "error" for diagnostic in diagnostics)
            )
            stale = output / "templates" / "stale.yaml"
            stale.write_text("stale\n", encoding="utf-8")

            code, diagnostics = _render_helm_chart(
                self.minimal_plan(), output, force=False
            )

            self.assertEqual(code, 0)
            self.assertFalse(
                any(diagnostic.level == "error" for diagnostic in diagnostics)
            )
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
            code, diagnostics = _render_helm_chart(
                self.minimal_plan(), file_output, force=True
            )
            self.assertEqual(code, 0)
            self.assertFalse(
                any(diagnostic.level == "error" for diagnostic in diagnostics)
            )
            self.assertTrue((file_output / "Chart.yaml").is_file())

    def test_render_helm_chart_canonicalizes_a_relative_output_dir(self) -> None:
        """
        `--chart-dir`/`--output` is user-controlled; `_render_helm_chart()`
        must resolve it to an absolute path up front so the chart always
        lands at one unambiguous location relative to the current working
        directory, rather than being reinterpreted if the CWD changes
        later in the same process.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            previous_cwd = os.getcwd()
            os.chdir(tmpdir)
            try:
                code, diagnostics = _render_helm_chart(
                    self.minimal_plan(), "relative-chart", force=False
                )
            finally:
                os.chdir(previous_cwd)
            self.assertEqual(code, 0)
            self.assertFalse(
                any(diagnostic.level == "error" for diagnostic in diagnostics)
            )
            self.assertTrue(
                (Path(tmpdir) / "relative-chart" / "Chart.yaml").is_file()
            )

    def test_plan_file_with_marker_shape_violation_fails_closed(self) -> None:
        # Plans can be loaded from files, so the helm renderer re-checks
        # marker shapes: an unhonorable marker would leak on the compose
        # target and silently diverge here.
        plan = self.minimal_plan()
        plan["modules"][0]["implementation"]["compose"]["services"]["app"]["volumes"] = [
            {
                "type": "volume",
                "source": "data",
                "target": "/data",
                "production": {"read_only": True},
            }
        ]
        files, diagnostics = render_helm(plan)
        e144 = [d for d in diagnostics if d.code == "E144"]
        self.assertEqual(len(e144), 1)
        self.assertEqual(
            e144[0].path,
            "module:demo.implementation.compose.services.app.volumes[0].production",
        )
        self.assertEqual(files, {})

    def test_plan_file_with_bool_enabled_from_fails_closed(self) -> None:
        # A boolean `enabledFrom` crashed the compose renderer with an
        # untyped TypeError and is not honored here either; the plan
        # re-check fails closed on both targets.
        plan = self.minimal_plan()
        plan["modules"][0]["implementation"]["compose"]["services"]["app"]["enabledFrom"] = True
        files, diagnostics = render_helm(plan)
        e145 = [d for d in diagnostics if d.code == "E145"]
        self.assertEqual(len(e145), 1)
        self.assertEqual(
            e145[0].path,
            "module:demo.implementation.compose.services.app.enabledFrom",
        )
        self.assertEqual(files, {})

    def test_plan_file_with_bool_enabled_from_on_workload_fails_closed(self) -> None:
        # The workload-level enabledFrom guard is unguarded: a boolean
        # shape crashed the helm renderer with an untyped TypeError; the
        # plan re-check fails closed instead.
        plan = self.minimal_plan()
        plan["modules"][0]["implementation"]["kubernetes"]["workloads"]["app"]["enabledFrom"] = True
        files, diagnostics = render_helm(plan)
        e145 = [d for d in diagnostics if d.code == "E145"]
        self.assertEqual(len(e145), 1)
        self.assertEqual(
            e145[0].path,
            "module:demo.implementation.kubernetes.workloads.app.enabledFrom",
        )
        self.assertEqual(files, {})


class NetworkPolicyAndServiceAccountTest(unittest.TestCase):
    """The issue #75 resources: per-module ServiceAccounts and contract-derived NetworkPolicies."""

    def module(
        self,
        module_id: str,
        *,
        service_type: str = "ClusterIP",
        with_service: bool = True,
    ) -> dict:
        module: dict = {
            "id": module_id,
            "implementation": {
                "kind": "docker-compose",
                "targets": ["docker-compose", "kubernetes"],
                "compose": {"services": {"app": {"image": "example/app:1"}}},
                "kubernetes": {
                    "workloads": {
                        "app": {
                            "kind": "Deployment",
                            "containers": ["app"],
                        }
                    }
                },
            },
        }
        if with_service:
            module["implementation"]["kubernetes"]["workloads"]["app"]["service"] = {
                "type": service_type,
                "ports": [{"name": "http", "port": 8080}],
            }
        return module

    def bound_plan(self) -> dict:
        provider = self.module("demo")
        consumer = self.module("consumer")
        consumer["consumes"] = {
            "backend": {
                "contractRef": "demo.http",
                "contract": {
                    "kind": "http",
                    "spec": {"host": "demo-app", "port": 8080},
                },
            }
        }
        isolated = self.module("isolated")
        return {
            "metadata": {"name": "test-chart", "version": "1.2.3"},
            "runtime": {"namespace": "test"},
            "secrets": {},
            "modules": [provider, consumer, isolated],
        }

    def render(self, plan: dict) -> tuple[dict, list]:
        files, diagnostics = render_helm(plan)
        errors = [d.format() for d in diagnostics if d.level == "error"]
        self.assertEqual(errors, [])
        return files, diagnostics

    def policy(self, files: dict, module_id: str) -> dict:
        return yaml.safe_load(files[f"templates/{module_id}-network-policy.yaml"])

    def test_policies_default_deny_and_allow_only_contract_bindings(self) -> None:
        files, _ = self.render(self.bound_plan())

        for module_id in ("demo", "consumer", "isolated"):
            with self.subTest(module=module_id):
                policy = self.policy(files, module_id)
                self.assertEqual(policy["kind"], "NetworkPolicy")
                self.assertEqual(policy["spec"]["policyTypes"], ["Ingress", "Egress"])
                self.assertEqual(
                    policy["spec"]["podSelector"]["matchLabels"]["cds.dev/module"],
                    module_id,
                )

        demo = self.policy(files, "demo")
        consumer_rules = [
            rule
            for rule in demo["spec"]["ingress"]
            if rule.get("from")
            and rule["from"][0]["podSelector"]["matchLabels"].get("cds.dev/module")
            == "consumer"
        ]
        self.assertEqual(
            consumer_rules,
            [{
                "from": [{
                    "podSelector": {
                        "matchLabels": {
                            "cds.dev/module": "consumer",
                            "app.kubernetes.io/instance": "{{ .Release.Name }}",
                        }
                    }
                }],
                "ports": [{"protocol": "TCP", "port": 8080}],
            }],
        )

        consumer = self.policy(files, "consumer")
        egress = consumer["spec"]["egress"]
        # Same-module egress, DNS egress, and exactly one binding-derived rule.
        self.assertEqual(len(egress), 3)
        self.assertEqual(
            egress[1]["to"][0]["namespaceSelector"]["matchLabels"],
            {"kubernetes.io/metadata.name": "kube-system"},
        )
        self.assertEqual(
            egress[1]["to"][0]["podSelector"]["matchLabels"],
            {"k8s-app": "kube-dns"},
        )
        self.assertEqual(
            egress[2],
            {
                "to": [{
                    "podSelector": {
                        "matchLabels": {
                            "cds.dev/module": "demo",
                            "app.kubernetes.io/instance": "{{ .Release.Name }}",
                        }
                    }
                }],
                "ports": [{"protocol": "TCP", "port": 8080}],
            },
        )

        isolated = self.policy(files, "isolated")
        # No policy allows traffic a binding does not justify: the isolated
        # module talks to nobody but itself and the cluster DNS.
        self.assertEqual(len(isolated["spec"]["ingress"]), 1)
        self.assertEqual(len(isolated["spec"]["egress"]), 2)
        self.assertNotIn("demo", yaml.safe_dump(isolated["spec"]))
        self.assertNotIn("consumer", yaml.safe_dump(isolated["spec"]))

    def test_policies_allow_same_module_traffic_and_dns_on_every_module(self) -> None:
        files, _ = self.render(self.bound_plan())

        for module_id in ("demo", "consumer", "isolated"):
            policy = self.policy(files, module_id)
            with self.subTest(module=module_id):
                self.assertEqual(
                    policy["spec"]["ingress"][0]["from"][0]["podSelector"][
                        "matchLabels"
                    ]["cds.dev/module"],
                    module_id,
                )
                dns = policy["spec"]["egress"][1]
                self.assertEqual(
                    dns["ports"],
                    [{"protocol": "UDP", "port": 53}, {"protocol": "TCP", "port": 53}],
                )

    def nodeport_plan(self) -> dict:
        plan = self.bound_plan()
        plan["modules"][0] = self.module("demo", service_type="NodePort")
        return plan

    def test_nodeport_service_opens_ingress_on_its_ports_only(self) -> None:
        # The NodePort Service is an intended entry point: ingress is
        # opened to all sources, but only on the exposed ports.
        files, _ = self.render(self.nodeport_plan())

        demo = self.policy(files, "demo")
        open_rules = [
            rule for rule in demo["spec"]["ingress"] if "from" not in rule
        ]
        self.assertEqual(
            open_rules,
            [{"ports": [{"protocol": "TCP", "port": 8080}]}],
        )

    def test_workloads_run_under_module_service_accounts(self) -> None:
        files, _ = self.render(self.bound_plan())

        for module_id in ("demo", "consumer", "isolated"):
            with self.subTest(module=module_id):
                documents = list(yaml.safe_load_all(
                    files[f"templates/{module_id}-service-account.yaml"]
                ))
                self.assertEqual(len(documents), 1)
                service_account = documents[0]
                self.assertEqual(service_account["kind"], "ServiceAccount")
                self.assertEqual(
                    service_account["metadata"]["name"],
                    f"{{{{ .Release.Name }}}}-{module_id}",
                )
                # Least privilege without RBAC rules: no API token at all.
                self.assertFalse(
                    service_account["automountServiceAccountToken"]
                )
                # The deployment carries Helm's `toYaml` expansion for
                # resources, so it is checked textually rather than parsed.
                workload = files[f"templates/{module_id}-app-deployment.yaml"]
                self.assertIn(
                    f"serviceAccountName: '{{{{ .Release.Name }}}}-{module_id}'\n",
                    workload,
                )

    def test_declared_rbac_rules_render_role_and_binding(self) -> None:
        plan = self.bound_plan()
        rule = {
            "apiGroups": [""],
            "resources": ["pods"],
            "verbs": ["get", "list"],
        }
        plan["modules"][0]["implementation"]["kubernetes"]["serviceAccount"] = {
            "rbac": [rule]
        }

        files, _ = self.render(plan)

        documents = list(yaml.safe_load_all(
            files["templates/demo-service-account.yaml"]
        ))
        self.assertEqual(
            [document["kind"] for document in documents],
            ["ServiceAccount", "Role", "RoleBinding"],
        )
        service_account, role, binding = documents
        self.assertNotIn("automountServiceAccountToken", service_account)
        self.assertEqual(role["rules"], [rule])
        self.assertEqual(
            binding["subjects"],
            [{"kind": "ServiceAccount", "name": "{{ .Release.Name }}-demo"}],
        )
        self.assertEqual(
            binding["roleRef"],
            {
                "apiGroup": "rbac.authorization.k8s.io",
                "kind": "Role",
                "name": "{{ .Release.Name }}-demo-role",
            },
        )

    def test_malformed_rbac_rule_fails_closed(self) -> None:
        # Plans can be loaded from files and bypass the module schema, so
        # the renderer re-checks the RBAC rule shape: anything Kubernetes
        # would reject at install time is an error, not a broken Role.
        plan = self.bound_plan()
        plan["modules"][0]["implementation"]["kubernetes"]["serviceAccount"] = {
            "rbac": [{"apiGroups": [""], "resources": ["pods"]}]
        }

        files, diagnostics = render_helm(plan)

        self.assertEqual(files, {})
        e147 = [d for d in diagnostics if d.code == "E147"]
        self.assertEqual(len(e147), 1)
        self.assertEqual(
            e147[0].path,
            "module:demo.implementation.kubernetes.serviceAccount.rbac[0]",
        )

    def test_binding_without_port_falls_back_to_provider_service_ports(self) -> None:
        plan = self.bound_plan()
        contract = plan["modules"][1]["consumes"]["backend"]["contract"]
        contract["spec"].pop("port")

        files, diagnostics = self.render(plan)

        self.assertNotIn("W128", {d.code for d in diagnostics})
        consumer = self.policy(files, "consumer")
        self.assertEqual(
            consumer["spec"]["egress"][2]["ports"],
            [{"protocol": "TCP", "port": 8080}],
        )

    def test_binding_without_any_port_evidence_warns_and_stays_module_scoped(self) -> None:
        # E084 requires the binding host to be an exposed Service, but that
        # Service can belong to a different module than the contractRef
        # names; when the provider itself advertises no ports, the egress
        # rule cannot be port-scoped and W128 says so.
        plan = self.bound_plan()
        plan["modules"][0] = self.module("demo", with_service=False)
        contract = plan["modules"][1]["consumes"]["backend"]["contract"]
        contract["spec"].pop("port")
        contract["spec"]["host"] = "isolated-app"

        files, diagnostics = render_helm(plan)

        self.assertFalse(any(d.level == "error" for d in diagnostics))
        w128 = [d for d in diagnostics if d.code == "W128"]
        self.assertEqual(len(w128), 1)
        self.assertEqual(w128[0].path, "module:consumer.consumes.backend")
        consumer = self.policy(files, "consumer")
        self.assertEqual(
            consumer["spec"]["egress"][2],
            {
                "to": [{
                    "podSelector": {
                        "matchLabels": {
                            "cds.dev/module": "demo",
                            "app.kubernetes.io/instance": "{{ .Release.Name }}",
                        }
                    }
                }]
            },
        )
