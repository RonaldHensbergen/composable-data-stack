import contextlib
import hashlib
import io
import json
import os
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest import mock

import yaml
from jsonschema import Draft202012Validator

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

    def test_configmap_bind_resolves_with_cds_dir_as_only_project_marker(self) -> None:
        # A `cds get`-scaffolded project has a `.cds/` state directory but no
        # `.git`/`pyproject.toml`; `_resolve_project_root` must still pick that
        # directory as the project root, otherwise every relative fromBind
        # fails closed with E085 (regression: bare fetched projects could not
        # render the local-kubernetes-example profile at all).
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            project = workspace / "project"
            profile = project / "profiles" / "demo" / "profile.yaml"
            profile.parent.mkdir(parents=True)
            (project / ".cds").mkdir()
            bind = project / "workdirs" / "demo" / "init.sh"
            bind.parent.mkdir(parents=True)
            bind.write_text("echo hello\n", encoding="utf-8")

            plan = self.minimal_plan()
            plan["sourceProfile"] = str(profile)
            plan["modules"][0]["implementation"]["kubernetes"]["configMaps"] = {
                "init": {
                    "key": "init.sh",
                    "fromBind": "./workdirs/demo/init.sh",
                    "mountPath": "/etc/init.sh",
                }
            }

            files, diagnostics = render_helm(plan)

            self.assertNotIn("E085", {diag.code for diag in diagnostics})
            self.assertNotIn("E076", {diag.code for diag in diagnostics})
            self.assertIn("echo hello", "\n".join(files.values()))

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

    def test_provided_contract_host_without_matching_service_is_rejected(self) -> None:
        # Provide-side counterpart of the E084 test above (issue #1035): a
        # provided contract advertising a host no rendered k8s Service
        # matches must fail closed with E150, like the compose renderer does.
        plan = self.minimal_plan()
        plan["modules"][0]["provides"] = {
            "http": {
                "kind": "http-service",
                "spec": {"host": "nowhere", "port": 8080},
            }
        }

        files, diagnostics = render_helm(plan)

        self.assertEqual(files, {})
        matching = [d for d in diagnostics if d.code == "E150"]
        self.assertEqual(len(matching), 1)
        self.assertEqual(matching[0].level, "error")
        self.assertEqual(
            matching[0].path, "module:demo.provides.http.spec.host"
        )
        self.assertIn("not a Kubernetes Service", matching[0].message)

    def test_provided_contract_matching_a_rendered_service_passes(self) -> None:
        plan = self.minimal_plan()
        plan["modules"][0]["provides"] = {
            "http": {
                "kind": "http-service",
                "spec": {"host": "demo-app", "port": 8080},
            },
            "sql": {
                "kind": "sql-database",
                "spec": {"host": "demo-app", "port": 5432},
            },
        }

        files, diagnostics = render_helm(plan)

        self.assertFalse(any(d.level == "error" for d in diagnostics))
        self.assertTrue(files)

    def test_provided_contract_service_name_maps_through_naming_rule(self) -> None:
        plan = self.minimal_plan()
        plan["modules"][0]["provides"] = {
            "http": {
                "kind": "http-service",
                "spec": {"host": "demo", "serviceName": "app", "port": 8080},
            }
        }

        # The workload "app" renders Service "demo-app", so serviceName
        # "app" resolves even though the bare host "demo" does not.
        files, diagnostics = render_helm(plan)
        self.assertFalse(any(d.level == "error" for d in diagnostics))
        self.assertTrue(files)

        broken = self.minimal_plan()
        broken["modules"][0]["provides"] = {
            "http": {
                "kind": "http-service",
                "spec": {"host": "demo", "serviceName": "missing", "port": 8080},
            }
        }
        files, diagnostics = render_helm(broken)
        self.assertEqual(files, {})
        matching = [d for d in diagnostics if d.code == "E150"]
        self.assertEqual(len(matching), 1)
        self.assertEqual(
            matching[0].path, "module:demo.provides.http.spec.serviceName"
        )

    def test_provided_sql_database_and_object_storage_hosts_are_checked(self) -> None:
        plan = self.minimal_plan()
        plan["modules"][0]["provides"] = {
            "sql": {"kind": "sql-database", "spec": {"host": "no-db"}},
            "storage": {
                "kind": "object-storage",
                "spec": {"endpoint": "http://no-store:3900"},
            },
        }

        files, diagnostics = render_helm(plan)

        self.assertEqual(files, {})
        matching = [d for d in diagnostics if d.code == "E150"]
        self.assertEqual(
            [d.path for d in matching],
            [
                "module:demo.provides.sql.spec.host",
                "module:demo.provides.storage.spec.endpoint",
            ],
        )

    def test_provided_contract_external_hosts_are_skipped(self) -> None:
        plan = self.minimal_plan()
        plan["modules"][0]["provides"] = {
            "sql": {
                "kind": "sql-database",
                "spec": {"host": "db.internal.example.com"},
            },
            "storage": {
                "kind": "object-storage",
                "spec": {"endpoint": "https://s3.example.com:443"},
            },
            "http": {"kind": "http-service", "spec": {}},
        }

        files, diagnostics = render_helm(plan)

        self.assertFalse(any(d.level == "error" for d in diagnostics))
        self.assertTrue(files)

    def test_provided_contract_module_without_rendered_services_is_skipped(self) -> None:
        # Mirrors the compose check: a module whose workloads render no
        # Service at all is left to its own diagnostics.
        plan = self.minimal_plan()
        workload = plan["modules"][0]["implementation"]["kubernetes"]["workloads"][
            "app"
        ]
        workload.pop("service")
        workload["kind"] = "Deployment"
        plan["modules"][0]["provides"] = {
            "http": {"kind": "http-service", "spec": {"host": "nowhere"}}
        }

        files, diagnostics = render_helm(plan)

        self.assertNotIn("E150", {diagnostic.code for diagnostic in diagnostics})
        self.assertFalse(any(d.level == "error" for d in diagnostics))

    def test_provided_contract_host_resolves_workload_service_name_override(self) -> None:
        # A workload-level serviceName renames the rendered Service, so the
        # advertised host must be the Service name, and a serviceName
        # advertisement resolves through the override.
        plan = self.minimal_plan()
        workload = plan["modules"][0]["implementation"]["kubernetes"]["workloads"][
            "app"
        ]
        workload["serviceName"] = "custom-svc"
        plan["modules"][0]["provides"] = {
            "http": {
                "kind": "http-service",
                "spec": {"host": "custom-svc", "port": 8080},
            }
        }

        files, diagnostics = render_helm(plan)
        self.assertFalse(any(d.level == "error" for d in diagnostics))
        self.assertIn(
            "name: '{{ .Release.Name }}-custom-svc'",
            files["templates/demo-app-service.yaml"],
        )

        plan["modules"][0]["provides"] = {
            "http": {
                "kind": "http-service",
                "spec": {"host": "demo-app", "port": 8080},
            }
        }
        files, diagnostics = render_helm(plan)
        self.assertEqual(files, {})
        self.assertIn("E150", {d.code for d in diagnostics})

        plan["modules"][0]["provides"] = {
            "http": {
                "kind": "http-service",
                "spec": {"serviceName": "app", "port": 8080},
            }
        }
        files, diagnostics = render_helm(plan)
        self.assertFalse(any(d.level == "error" for d in diagnostics))

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
        e148 = [d for d in diagnostics if d.code == "E148"]
        self.assertEqual(len(e148), 1)
        self.assertEqual(
            e148[0].path,
            "module:demo.implementation.kubernetes.serviceAccount.rbac[0]",
        )

    def test_malformed_service_account_block_fails_closed(self) -> None:
        # Same bypass class as the RBAC rules: a non-mapping
        # `serviceAccount` block must fail closed as E147, not crash the
        # renderer with an AttributeError from `.get()`.
        plan = self.bound_plan()
        plan["modules"][0]["implementation"]["kubernetes"]["serviceAccount"] = "default"

        files, diagnostics = render_helm(plan)

        self.assertEqual(files, {})
        e148 = [d for d in diagnostics if d.code == "E148"]
        self.assertEqual(len(e148), 1)
        self.assertEqual(
            e148[0].path,
            "module:demo.implementation.kubernetes.serviceAccount",
        )

    def test_module_schema_accepts_the_core_api_group(self) -> None:
        # The Kubernetes core API group is spelled "" (the schema's own
        # description uses it as the example); `minLength` on the items
        # would reject exactly that spelling.
        schema = json.loads(
            (Path(__file__).resolve().parent.parent / "cli" / "resources"
             / "module.schema.json").read_text(encoding="utf-8")
        )
        validator = Draft202012Validator(schema["$defs"]["kubernetesRbacRule"])
        rule = {
            "apiGroups": [""],
            "resources": ["pods"],
            "verbs": ["get", "list"],
        }
        self.assertEqual(list(validator.iter_errors(rule)), [])

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

    def test_network_policy_passthrough_renders_verbatim(self) -> None:
        # Bindings cannot name external endpoints or cross-namespace
        # clients; the passthrough rules are appended after the derived
        # rules so both can coexist.
        plan = self.bound_plan()
        plan["modules"][1]["implementation"]["kubernetes"]["networkPolicy"] = {
            "egress": [{
                "to": [{"ipBlock": {"cidr": "10.8.0.0/16"}}],
                "ports": [{"protocol": "TCP", "port": 443}],
            }],
            "ingress": [{
                "from": [{
                    "namespaceSelector": {
                        "matchLabels": {"kubernetes.io/metadata.name": "ingress"}
                    }
                }],
                "ports": [{"protocol": "TCP", "port": 8080}],
            }],
        }

        files, _ = self.render(plan)

        consumer = self.policy(files, "consumer")
        self.assertEqual(
            consumer["spec"]["egress"][-1],
            {
                "to": [{"ipBlock": {"cidr": "10.8.0.0/16"}}],
                "ports": [{"protocol": "TCP", "port": 443}],
            },
        )
        self.assertEqual(
            consumer["spec"]["ingress"][-1],
            {
                "from": [{
                    "namespaceSelector": {
                        "matchLabels": {"kubernetes.io/metadata.name": "ingress"}
                    }
                }],
                "ports": [{"protocol": "TCP", "port": 8080}],
            },
        )

    def test_malformed_network_policy_block_fails_closed(self) -> None:
        # A plan loaded from a file bypasses the module schema: a malformed
        # passthrough block must fail closed as E149, not crash or render a
        # broken NetworkPolicy.
        plan = self.bound_plan()
        plan["modules"][0]["implementation"]["kubernetes"]["networkPolicy"] = "allow-all"

        files, diagnostics = render_helm(plan)

        self.assertEqual(files, {})
        e149 = [d for d in diagnostics if d.code == "E149"]
        self.assertEqual(len(e149), 1)
        self.assertEqual(
            e149[0].path,
            "module:demo.implementation.kubernetes.networkPolicy",
        )

    def test_malformed_passthrough_rule_fails_closed(self) -> None:
        plan = self.bound_plan()
        plan["modules"][0]["implementation"]["kubernetes"]["networkPolicy"] = {
            "egress": [{"to": []}, "anywhere"]
        }

        files, diagnostics = render_helm(plan)

        self.assertEqual(files, {})
        e149 = [d for d in diagnostics if d.code == "E149"]
        self.assertEqual(len(e149), 1)
        self.assertEqual(
            e149[0].path,
            "module:demo.implementation.kubernetes.networkPolicy.egress",
        )

    def test_contract_port_maps_through_service_target_port(self) -> None:
        # The contract's port is Service-facing while NetworkPolicy ports
        # match the destination pod port: a `port: 80, targetPort: 8080`
        # remap must generate rules for 8080, not 80.
        plan = self.bound_plan()
        plan["modules"][0]["implementation"]["kubernetes"]["workloads"]["app"]["service"] = {
            "type": "ClusterIP",
            "ports": [{"name": "http", "port": 80, "targetPort": 8080}],
        }
        plan["modules"][1]["consumes"]["backend"]["contract"]["spec"]["port"] = 80

        files, _ = self.render(plan)

        consumer = self.policy(files, "consumer")
        demo = self.policy(files, "demo")
        self.assertEqual(
            consumer["spec"]["egress"][2]["ports"],
            [{"protocol": "TCP", "port": 8080}],
        )
        self.assertEqual(
            demo["spec"]["ingress"][1]["ports"],
            [{"protocol": "TCP", "port": 8080}],
        )

    def test_wait_gates_on_bound_provider_and_same_module_service_render(
        self,
    ) -> None:
        # Gates resolving to a bound provider's endpoint or a same-module
        # Service are allowed by the default-deny NetworkPolicy baseline, so
        # they render unchanged (issue #1031).
        plan = self.bound_plan()
        workload = plan["modules"][1]["implementation"]["kubernetes"]["workloads"][
            "app"
        ]
        workload["waitFor"] = [
            {"host": "${bindings.backend.host}", "port": "${bindings.backend.port}"},
            {"host": "${k8s.service.app}", "port": 8080},
        ]

        files, diagnostics = render_helm(plan)

        self.assertFalse(any(d.level == "error" for d in diagnostics))
        self.assertNotIn("E152", {d.code for d in diagnostics})
        deployment = files["templates/consumer-app-deployment.yaml"]
        self.assertIn("until nc -z {{ .Release.Name }}-demo-app 8080", deployment)
        self.assertIn("until nc -z {{ .Release.Name }}-consumer-app 8080", deployment)

    def test_wait_gate_on_non_bound_cross_module_host_fails_closed(self) -> None:
        # A gate naming another module's Service without a binding for it can
        # only hang until its timeout under the default-deny policy: fail
        # closed with E152 naming the gate and the unreachable host (#1031).
        plan = self.bound_plan()
        workload = plan["modules"][1]["implementation"]["kubernetes"]["workloads"][
            "app"
        ]
        workload["waitFor"] = [{"host": "${k8s.service.isolated-app}", "port": 8080}]

        files, diagnostics = render_helm(plan)

        self.assertEqual(files, {})
        e152 = [d for d in diagnostics if d.code == "E152"]
        self.assertEqual(len(e152), 1)
        self.assertEqual(
            e152[0].path,
            "module:consumer.implementation.kubernetes.workloads.app.waitFor",
        )
        self.assertIn("consumer/app", e152[0].message)
        self.assertIn("{{ .Release.Name }}-isolated-app", e152[0].message)

    def test_wait_gate_on_external_host_fails_closed(self) -> None:
        # Same silent-failure class for a literal external host: no binding
        # and no same-module Service, so no egress rule can cover it.
        plan = self.bound_plan()
        workload = plan["modules"][1]["implementation"]["kubernetes"]["workloads"][
            "app"
        ]
        workload["waitFor"] = [{"host": "api.example.com", "port": 443}]

        files, diagnostics = render_helm(plan)

        self.assertEqual(files, {})
        e152 = [d for d in diagnostics if d.code == "E152"]
        self.assertEqual(len(e152), 1)
        self.assertIn("api.example.com", e152[0].message)

    def test_wait_gate_on_unconsumed_provider_service_fails_closed(self) -> None:
        # The demo module's Service is in the chart, but the consumer binds
        # nothing from it in this variant: even a resolvable cross-module
        # Service DNS name is unreachable without the egress edge.
        plan = self.bound_plan()
        consumer = plan["modules"][1]
        consumer["consumes"] = {}
        workload = consumer["implementation"]["kubernetes"]["workloads"]["app"]
        workload["waitFor"] = [{"host": "${k8s.service.demo-app}", "port": 8080}]

        files, diagnostics = render_helm(plan)

        self.assertEqual(files, {})
        e152 = [d for d in diagnostics if d.code == "E152"]
        self.assertEqual(len(e152), 1)
        self.assertIn("{{ .Release.Name }}-demo-app", e152[0].message)

    def test_wait_gate_on_disabled_same_module_service_fails_closed(self) -> None:
        # A same-module Service whose workload is disabled (enabledFrom
        # resolves to False) renders no Service, so a gate on it would hang
        # at runtime exactly like a non-bound host: fail closed with E152
        # (#1031). When the workload is enabled, the same gate renders.
        plan = self.bound_plan()
        consumer = plan["modules"][1]
        consumer["config"] = {"optional": {"enabled": False}}
        workloads = consumer["implementation"]["kubernetes"]["workloads"]
        workloads["sidecar"] = {
            "kind": "Deployment",
            "containers": ["app"],
            "enabledFrom": "config.optional.enabled",
            "service": {
                "type": "ClusterIP",
                "ports": [{"name": "http", "port": 9090}],
            },
        }
        workloads["app"]["waitFor"] = [
            {"host": "${k8s.service.sidecar}", "port": 9090}
        ]

        files, diagnostics = render_helm(plan)

        self.assertEqual(files, {})
        e152 = [d for d in diagnostics if d.code == "E152"]
        self.assertEqual(len(e152), 1)
        self.assertIn("{{ .Release.Name }}-consumer-sidecar", e152[0].message)

        consumer["config"] = {"optional": {"enabled": True}}
        files, diagnostics = render_helm(plan)
        self.assertFalse(any(d.level == "error" for d in diagnostics))
        self.assertNotIn("E152", {d.code for d in diagnostics})

    def test_wait_gate_covered_by_passthrough_ip_block_renders(self) -> None:
        # The custom networkPolicy.egress passthrough is the escape hatch
        # for destinations no binding can name; a gate whose host:port an
        # ipBlock rule covers is allowed, so E152 must not fire (#1031).
        plan = self.bound_plan()
        consumer = plan["modules"][1]
        consumer["implementation"]["kubernetes"]["networkPolicy"] = {
            "egress": [{
                "to": [{"ipBlock": {"cidr": "10.8.0.0/16"}}],
                "ports": [{"protocol": "TCP", "port": 443}],
            }],
        }
        workload = consumer["implementation"]["kubernetes"]["workloads"]["app"]
        workload["waitFor"] = [{"host": "10.8.1.5", "port": 443}]

        files, diagnostics = render_helm(plan)

        self.assertFalse(any(d.level == "error" for d in diagnostics))
        self.assertNotIn("E152", {d.code for d in diagnostics})
        self.assertIn(
            "until nc -z 10.8.1.5 443",
            files["templates/consumer-app-deployment.yaml"],
        )

    def test_wait_gate_outside_passthrough_ip_block_fails_closed(self) -> None:
        # Same passthrough rule, but the gate names an address outside the
        # CIDR or a port the rule does not open: still unreachable, still
        # E152.
        plan = self.bound_plan()
        consumer = plan["modules"][1]
        consumer["implementation"]["kubernetes"]["networkPolicy"] = {
            "egress": [{
                "to": [{"ipBlock": {"cidr": "10.8.0.0/16"}}],
                "ports": [{"protocol": "TCP", "port": 443}],
            }],
        }
        workload = consumer["implementation"]["kubernetes"]["workloads"]["app"]

        for host, port in (("10.9.0.1", 443), ("10.8.1.5", 5432)):
            with self.subTest(host=host, port=port):
                workload["waitFor"] = [{"host": host, "port": port}]

                files, diagnostics = render_helm(plan)

                self.assertEqual(files, {})
                e152 = [d for d in diagnostics if d.code == "E152"]
                self.assertEqual(len(e152), 1)
                self.assertIn(f'"{host}"', e152[0].message)


    def test_wait_gate_with_excepted_passthrough_address_fails_closed(self) -> None:
        # An `except` CIDR inside the ipBlock stays denied, so a gate on an
        # excepted address is not lifted by the passthrough rule.
        plan = self.bound_plan()
        consumer = plan["modules"][1]
        consumer["implementation"]["kubernetes"]["networkPolicy"] = {
            "egress": [{
                "to": [{"ipBlock": {"cidr": "10.8.0.0/16", "except": ["10.8.1.0/24"]}}],
                "ports": [{"protocol": "TCP", "port": 443}],
            }],
        }
        workload = consumer["implementation"]["kubernetes"]["workloads"]["app"]
        workload["waitFor"] = [{"host": "10.8.1.5", "port": 443}]

        files, diagnostics = render_helm(plan)

        self.assertEqual(files, {})
        e152 = [d for d in diagnostics if d.code == "E152"]
        self.assertEqual(len(e152), 1)
        self.assertIn("10.8.1.5", e152[0].message)

    def test_wait_gate_with_malformed_passthrough_except_does_not_crash(self) -> None:
        # E149 shape-checks a passthrough rule only as a mapping, and a plan
        # loaded from a file bypasses the module schema: a garbage `except`
        # entry must not crash the renderer with a ValueError from the
        # ipaddress parser -- the entry is skipped, the gate stays covered.
        plan = self.bound_plan()
        consumer = plan["modules"][1]
        consumer["implementation"]["kubernetes"]["networkPolicy"] = {
            "egress": [{
                "to": [{"ipBlock": {"cidr": "10.8.0.0/16", "except": ["not-a-cidr"]}}],
                "ports": [{"protocol": "TCP", "port": 443}],
            }],
        }
        workload = consumer["implementation"]["kubernetes"]["workloads"]["app"]
        workload["waitFor"] = [{"host": "10.8.1.5", "port": 443}]

        files, diagnostics = render_helm(plan)

        self.assertFalse(any(d.level == "error" for d in diagnostics))
        self.assertNotIn("E152", {d.code for d in diagnostics})
        self.assertIn(
            "until nc -z 10.8.1.5 443",
            files["templates/consumer-app-deployment.yaml"],
        )

    def proxy_plan(self) -> dict:
        """A reverse-proxy module with routes, two route targets, and an unrouted module."""
        proxy = self.module("proxy")
        proxy["provides"] = {
            "reverse-proxy": {
                "kind": "reverse-proxy",
                "spec": {"host": "proxy-app", "port": 443},
            }
        }
        proxy["config"] = {
            "routes": [
                {"host": "app.example.local", "target": "demo"},
                {"host": "alt.example.local", "target": "other.http"},
            ]
        }
        demo = self.module("demo")
        demo["provides"] = {
            "http": {"kind": "http-service", "spec": {"host": "demo-app", "port": 8080}}
        }
        other = self.module("other")
        other["implementation"]["kubernetes"]["workloads"]["app"]["service"] = {
            "type": "ClusterIP",
            "ports": [{"name": "http", "port": 9090}],
        }
        other["provides"] = {
            "http": {"kind": "http-service", "spec": {"host": "other-app", "port": 9090}}
        }
        isolated = self.module("isolated")
        return {
            "metadata": {"name": "test-chart", "version": "1.2.3"},
            "runtime": {"namespace": "test"},
            "secrets": {},
            "modules": [proxy, demo, other, isolated],
        }

    def proxy_egress_edges(self, files: dict, module_id: str) -> dict:
        """Map peer module -> egress rule for the binding/route-derived rules."""
        policy = self.policy(files, module_id)
        edges = {}
        for rule in policy["spec"]["egress"]:
            to = rule.get("to")
            if not to:
                continue
            peer = to[0].get("podSelector", {}).get("matchLabels", {}).get(
                "cds.dev/module"
            )
            if peer and peer != module_id:
                edges[peer] = rule
        return edges

    def proxy_ingress_edges(self, files: dict, module_id: str) -> dict:
        """Map peer module -> ingress rule for the binding/route-derived rules."""
        policy = self.policy(files, module_id)
        edges = {}
        for rule in policy["spec"]["ingress"]:
            frm = rule.get("from")
            if not frm:
                continue
            peer = frm[0].get("podSelector", {}).get("matchLabels", {}).get(
                "cds.dev/module"
            )
            if peer and peer != module_id:
                edges[peer] = rule
        return edges

    def test_reverse_proxy_routes_render_port_scoped_edges_without_passthrough(
        self,
    ) -> None:
        # Issue #1033: a route's target module becomes an egress edge on the
        # proxy and a matching ingress edge on the target, scoped to the
        # route's backend port -- no passthrough rules involved.
        files, _ = self.render(self.proxy_plan())

        proxy_edges = self.proxy_egress_edges(files, "proxy")
        self.assertEqual(set(proxy_edges), {"demo", "other"})
        self.assertEqual(
            proxy_edges["demo"],
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
        self.assertEqual(
            proxy_edges["other"]["ports"],
            [{"protocol": "TCP", "port": 9090}],
        )
        # The proxy's egress is exactly same-module + DNS + the two routes.
        self.assertEqual(len(self.policy(files, "proxy")["spec"]["egress"]), 4)

        demo_ingress = self.proxy_ingress_edges(files, "demo")
        self.assertEqual(set(demo_ingress), {"proxy"})
        self.assertEqual(
            demo_ingress["proxy"]["ports"],
            [{"protocol": "TCP", "port": 8080}],
        )
        other_ingress = self.proxy_ingress_edges(files, "other")
        self.assertEqual(set(other_ingress), {"proxy"})
        self.assertEqual(
            other_ingress["proxy"]["ports"],
            [{"protocol": "TCP", "port": 9090}],
        )

    def test_route_targets_not_named_by_any_route_stay_unreachable(self) -> None:
        # The inverse direction of #1033: "isolated" provides an http-service
        # but no route names it, so the proxy gets no egress edge to it and
        # it gets no ingress edge from the proxy.
        files, _ = self.render(self.proxy_plan())

        self.assertNotIn("isolated", yaml.safe_dump(self.proxy_egress_edges(files, "proxy")))
        isolated = self.policy(files, "isolated")
        self.assertNotIn("proxy", yaml.safe_dump(isolated["spec"]))
        self.assertEqual(len(isolated["spec"]["ingress"]), 1)
        self.assertEqual(len(isolated["spec"]["egress"]), 2)

    def test_route_backend_port_maps_through_target_service_target_port(self) -> None:
        # The route's backend port is the contract's Service-facing port:
        # a `port: 80, targetPort: 8080` remap on the target's Service must
        # generate the rule for the pod port 8080, like a binding's port.
        plan = self.proxy_plan()
        demo = plan["modules"][1]
        demo["implementation"]["kubernetes"]["workloads"]["app"]["service"] = {
            "type": "ClusterIP",
            "ports": [{"name": "http", "port": 80, "targetPort": 8080}],
        }
        demo["provides"]["http"]["spec"]["port"] = 80

        files, _ = self.render(plan)

        self.assertEqual(
            self.proxy_egress_edges(files, "proxy")["demo"]["ports"],
            [{"protocol": "TCP", "port": 8080}],
        )
        self.assertEqual(
            self.proxy_ingress_edges(files, "demo")["proxy"]["ports"],
            [{"protocol": "TCP", "port": 8080}],
        )

    def test_route_to_unknown_target_warns_and_adds_no_edge(self) -> None:
        # A route naming a module the plan does not contain cannot become an
        # edge: W129 says so and the default-deny baseline keeps holding.
        plan = self.proxy_plan()
        plan["modules"][0]["config"]["routes"].append(
            {"host": "ghost.example.local", "target": "ghost"}
        )

        files, diagnostics = render_helm(plan)

        self.assertFalse(any(d.level == "error" for d in diagnostics))
        w129 = [d for d in diagnostics if d.code == "W129"]
        self.assertEqual(len(w129), 1)
        self.assertEqual(w129[0].path, "module:proxy.config.routes[2]")
        self.assertIn("unknown module", w129[0].message)
        self.assertNotIn("ghost", yaml.safe_dump(self.policy(files, "proxy")["spec"]))
        # The valid routes still produced their edges.
        self.assertEqual(set(self.proxy_egress_edges(files, "proxy")), {"demo", "other"})

    def test_route_target_without_port_evidence_warns_and_stays_denied(self) -> None:
        # No numeric port on the target's http-service and no advertised
        # service ports: no port-scoped edge can be derived, so the target
        # stays unreachable and W129 points at the route.
        plan = self.proxy_plan()
        demo = plan["modules"][1]
        demo["implementation"]["kubernetes"]["workloads"]["app"].pop("service")
        demo["provides"]["http"]["spec"] = {"host": "demo-app"}

        files, diagnostics = render_helm(plan)

        self.assertFalse(any(d.level == "error" for d in diagnostics))
        w129 = [d for d in diagnostics if d.code == "W129"]
        self.assertEqual(len(w129), 1)
        self.assertEqual(w129[0].path, "module:proxy.config.routes[0]")
        self.assertIn("no numeric port", w129[0].message)
        self.assertEqual(set(self.proxy_egress_edges(files, "proxy")), {"other"})
        self.assertEqual(self.proxy_ingress_edges(files, "demo"), {})

    def test_wait_gate_on_route_target_service_renders(self) -> None:
        # The route-derived egress edge covers a waitFor gate on the route
        # target's Service, so E152 must not fire for it (#1033); a gate on
        # a Service no route names still fails closed.
        plan = self.proxy_plan()
        workload = plan["modules"][0]["implementation"]["kubernetes"]["workloads"]["app"]
        workload["waitFor"] = [
            {"host": "${k8s.service.demo-app}", "port": 8080},
            {"host": "${k8s.service.isolated-app}", "port": 8080},
        ]

        files, diagnostics = render_helm(plan)

        self.assertEqual(files, {})
        e152 = [d for d in diagnostics if d.code == "E152"]
        self.assertEqual(len(e152), 1)
        self.assertIn("{{ .Release.Name }}-isolated-app", e152[0].message)

        workload["waitFor"] = [{"host": "${k8s.service.demo-app}", "port": 8080}]
        files, diagnostics = render_helm(plan)
        self.assertFalse(any(d.level == "error" for d in diagnostics))
        self.assertNotIn("E152", {d.code for d in diagnostics})
        deployment = files["templates/proxy-app-deployment.yaml"]
        self.assertIn("until nc -z {{ .Release.Name }}-demo-app 8080", deployment)

    def test_route_with_out_of_range_backend_port_falls_back_to_advertised(self) -> None:
        # The compose route check (E136) rejects backend ports outside
        # 1-65535, but a plan loaded from a file can bypass it; such a port
        # must not render into the NetworkPolicy. The rule falls back to the
        # target's advertised service ports instead.
        plan = self.proxy_plan()
        demo = plan["modules"][1]
        demo["provides"]["http"]["spec"]["port"] = 70000

        files, diagnostics = render_helm(plan)

        self.assertFalse(any(d.level == "error" for d in diagnostics))
        self.assertNotIn("W129", {d.code for d in diagnostics})
        self.assertEqual(
            self.proxy_egress_edges(files, "proxy")["demo"]["ports"],
            [{"protocol": "TCP", "port": 8080}],
        )
        self.assertEqual(
            self.proxy_ingress_edges(files, "demo")["proxy"]["ports"],
            [{"protocol": "TCP", "port": 8080}],
        )
