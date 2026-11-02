import io
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from unittest.mock import patch

from cli.k8s_provision import (
    LocalImageBuild,
    _validate_build_args,
    _validate_image_ref,
    build_local_images,
    cluster_is_reachable,
    collect_local_builds,
    current_kube_context,
    ensure_k8s_cluster,
    import_local_images,
    k3d_cluster_name,
)
from cli.planner import build_plan
from cli.up_runner import _ALLOWED_EXECUTABLES

REPO_ROOT = Path(__file__).resolve().parent.parent

SECRET_VALUES = {
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


def _synthetic_plan(services: dict, config: dict | None = None) -> dict:
    return {
        "sourceProfile": str(
            REPO_ROOT / "profiles" / "local-dagster-postgres-superset" / "profile.yaml"
        ),
        "secrets": {},
        "modules": [
            {
                "id": "demo",
                "source": "../../modules/warehouse/postgres",
                "config": config or {},
                "implementation": {
                    "kind": "docker-compose",
                    "compose": {"services": services},
                },
            }
        ],
    }


class CollectLocalBuildsRealProfileTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tempdir = tempfile.TemporaryDirectory()
        env_file = Path(cls._tempdir.name) / ".env"
        env_file.write_text(
            "\n".join(f"{key}={value}" for key, value in SECRET_VALUES.items()) + "\n",
            encoding="utf-8",
        )
        with mock.patch.dict(os.environ, SECRET_VALUES, clear=False):
            plan, diagnostics = build_plan(
                str(
                    REPO_ROOT
                    / "profiles"
                    / "local-dagster-postgres-superset"
                    / "profile.yaml"
                ),
                env_file=str(env_file),
            )
        errors = [d for d in diagnostics if d.level == "error"]
        if errors or plan is None:
            raise AssertionError(f"real profile did not produce a plan: {errors}")
        cls.plan = plan

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tempdir.cleanup()

    def test_collects_exactly_the_harness_images(self) -> None:
        builds = {build.image: build for build in collect_local_builds(self.plan)}
        # Same three images scripts/k8s/build-images.sh builds, deduplicated
        # across the services that share them (dagster has three).
        self.assertEqual(
            set(builds),
            {"local/dagster:custom", "local/postgres:custom", "local/superset:custom"},
        )
        for build in builds.values():
            # The resolved context must actually contain the Dockerfile —
            # asserted via existence rather than equality with REPO_ROOT,
            # because the mutation kill-matrix runs pytest from a copied
            # mutants/ tree whose project-root resolution lands elsewhere.
            self.assertTrue(
                (Path(build.context) / build.dockerfile).is_file(),
                f"{build.dockerfile} not found under resolved context {build.context}",
            )
        self.assertEqual(
            builds["local/dagster:custom"].dockerfile, "images/dagster/base/Dockerfile"
        )
        self.assertEqual(
            builds["local/dagster:custom"].args, {"DB_BACKEND": "postgres"}
        )
        self.assertEqual(
            builds["local/postgres:custom"].dockerfile, "images/postgres/Dockerfile"
        )
        self.assertEqual(
            builds["local/superset:custom"].dockerfile,
            "images/superset/base/Dockerfile",
        )

    def test_production_gate_replaces_stock_image_with_build(self) -> None:
        # The postgres base service uses the pinned stock image; the Helm
        # target renders the production variant, which builds locally.
        builds = {build.image: build for build in collect_local_builds(self.plan)}
        self.assertIn("local/postgres:custom", builds)
        self.assertNotIn("postgres:18", {build.image for build in builds.values()})


class CollectLocalBuildsUnitTest(unittest.TestCase):
    def test_services_without_build_are_skipped(self) -> None:
        plan = _synthetic_plan({"web": {"image": "nginx:1.27"}})
        self.assertEqual(collect_local_builds(plan), [])

    def test_string_build_form_uses_context_only(self) -> None:
        # An absolute context passes through unchanged on every platform
        # (`Path("/abs").is_absolute()` is False on Windows, so build the
        # path from the cwd anchor instead of hardcoding "/abs/context").
        abs_context = str(Path(Path.cwd().anchor) / "abs" / "context")
        plan = _synthetic_plan(
            {"web": {"image": "local/web:custom", "build": abs_context}}
        )
        builds = collect_local_builds(plan)
        self.assertEqual(
            builds,
            [
                LocalImageBuild(
                    image="local/web:custom",
                    context=abs_context,
                    dockerfile=None,
                    args={},
                )
            ],
        )

    def test_shared_image_dedupes_and_conflicting_builds_fail(self) -> None:
        same = {
            "a": {"image": "local/x:1", "build": {"context": "/abs"}},
            "b": {"image": "local/x:1", "build": {"context": "/abs"}},
        }
        self.assertEqual(len(collect_local_builds(_synthetic_plan(same))), 1)
        conflicting = {
            "a": {"image": "local/x:1", "build": {"context": "/abs"}},
            "b": {"image": "local/x:1", "build": {"context": "/other"}},
        }
        with self.assertRaisesRegex(ValueError, "conflicting build blocks"):
            collect_local_builds(_synthetic_plan(conflicting))

    def test_disabled_service_is_skipped(self) -> None:
        plan = _synthetic_plan(
            {
                "sidecar": {
                    "enabledFrom": "config.flag",
                    "image": "local/x:1",
                    "build": {"context": "/abs"},
                }
            },
            config={"flag": False},
        )
        self.assertEqual(collect_local_builds(plan), [])

    def test_production_true_service_is_included_for_helm(self) -> None:
        plan = _synthetic_plan(
            {
                "backup": {
                    "production": True,
                    "image": "local/b:1",
                    "build": {"context": "/abs"},
                }
            }
        )
        builds = collect_local_builds(plan)
        self.assertEqual([b.image for b in builds], ["local/b:1"])

    def test_interpolated_build_fields_are_substituted(self) -> None:
        plan = _synthetic_plan(
            {
                "web": {
                    "image": "local/web:${config.tag}",
                    "build": {
                        "context": "/abs",
                        "dockerfile": "images/${config.variant}/Dockerfile",
                        "args": {"MODE": "${config.mode}"},
                    },
                }
            },
            config={"tag": "custom", "variant": "base", "mode": "pg"},
        )
        builds = collect_local_builds(plan)
        self.assertEqual(builds[0].image, "local/web:custom")
        self.assertEqual(builds[0].dockerfile, "images/base/Dockerfile")
        self.assertEqual(builds[0].args, {"MODE": "pg"})

    def test_remote_context_passes_through(self) -> None:
        plan = _synthetic_plan(
            {
                "web": {
                    "image": "local/web:1",
                    "build": {"context": "https://github.com/example/repo.git"},
                }
            }
        )
        builds = collect_local_builds(plan)
        self.assertEqual(builds[0].context, "https://github.com/example/repo.git")

    def test_invalid_image_ref_rejected(self) -> None:
        plan = _synthetic_plan(
            {"web": {"image": "--pull=always", "build": {"context": "/abs"}}}
        )
        with self.assertRaisesRegex(ValueError, "invalid image reference"):
            collect_local_builds(plan)

    def test_missing_image_rejected(self) -> None:
        plan = _synthetic_plan({"web": {"build": {"context": "/abs"}}})
        with self.assertRaisesRegex(ValueError, "image"):
            collect_local_builds(plan)

    def test_flag_like_dockerfile_rejected(self) -> None:
        plan = _synthetic_plan(
            {
                "web": {
                    "image": "local/web:1",
                    "build": {"context": "/abs", "dockerfile": "--evil"},
                }
            }
        )
        with self.assertRaisesRegex(ValueError, "dockerfile"):
            collect_local_builds(plan)

    def test_empty_plan_yields_no_builds(self) -> None:
        self.assertEqual(collect_local_builds({"modules": []}), [])
        self.assertEqual(collect_local_builds({}), [])

    def test_validate_build_args_rejects_flag_like_names(self) -> None:
        with self.assertRaisesRegex(ValueError, "build arg"):
            _validate_build_args({"--add-host": "x"})
        with self.assertRaisesRegex(ValueError, "build arg"):
            _validate_build_args({"OK": "a\x00b"})
        self.assertEqual(_validate_build_args({"A_B": "1"}), {"A_B": "1"})

    def test_validate_image_ref_allows_digest_form(self) -> None:
        self.assertEqual(
            _validate_image_ref("registry.example.com/team/app:1.2@sha256:abc"),
            "registry.example.com/team/app:1.2@sha256:abc",
        )


class BuildLocalImagesTest(unittest.TestCase):
    def _builds(self) -> list[LocalImageBuild]:
        return [
            LocalImageBuild(
                image="local/a:custom",
                context="/repo",
                dockerfile="images/a/Dockerfile",
                args={"DB_BACKEND": "postgres"},
            ),
            LocalImageBuild(image="local/b:custom", context="/repo", dockerfile=None),
        ]

    @patch("cli.k8s_provision.run_streamed", return_value=0)
    def test_docker_build_command_shape(self, mock_run) -> None:
        result = build_local_images(self._builds(), io.StringIO())
        self.assertEqual(result, 0)
        self.assertEqual(mock_run.call_count, 2)
        first_cmd = mock_run.call_args_list[0].args[0]
        self.assertEqual(
            first_cmd,
            [
                "docker",
                "build",
                "-t",
                "local/a:custom",
                "-f",
                "images/a/Dockerfile",
                "--build-arg",
                "DB_BACKEND=postgres",
                "/repo",
            ],
        )
        second_cmd = mock_run.call_args_list[1].args[0]
        self.assertEqual(
            second_cmd, ["docker", "build", "-t", "local/b:custom", "/repo"]
        )

    @patch("cli.k8s_provision.run_streamed", side_effect=[0, 7])
    def test_first_failure_stops_and_propagates(self, mock_run) -> None:
        result = build_local_images(self._builds(), io.StringIO())
        self.assertEqual(result, 7)
        self.assertEqual(mock_run.call_count, 2)

    def test_empty_build_list_is_noop(self) -> None:
        with patch("cli.k8s_provision.run_streamed") as mock_run:
            self.assertEqual(build_local_images([], io.StringIO()), 0)
            mock_run.assert_not_called()

    def test_secret_placeholder_in_build_arg_is_refused_before_any_build(self) -> None:
        # Code-scanning finding: a resolved ${CDS_*} in a --build-arg KEY=VALUE
        # argv entry is readable via `ps` while the build runs (compose expands
        # via the Docker API instead). Fail closed instead of expanding.
        builds = [
            LocalImageBuild(
                image="local/a:1", context="/repo", args={"TOKEN": "${CDS_API_TOKEN}"}
            ),
            LocalImageBuild(image="local/b:1", context="/repo"),
        ]
        with patch.dict(os.environ, {"CDS_API_TOKEN": "sentinel-value"}, clear=False):
            with patch("cli.k8s_provision.run_streamed") as mock_run:
                with self.assertRaisesRegex(RuntimeError, "build.args.TOKEN"):
                    build_local_images(builds, io.StringIO())
        mock_run.assert_not_called()

    def test_secret_placeholder_in_image_or_dockerfile_is_refused(self) -> None:
        for build in (
            LocalImageBuild(image="local/a:${CDS_TAG}", context="/repo"),
            LocalImageBuild(
                image="local/a:1",
                context="/repo",
                dockerfile="images/${CDS_X}/Dockerfile",
            ),
        ):
            with self.subTest(build=build):
                with patch("cli.k8s_provision.run_streamed") as mock_run:
                    with self.assertRaisesRegex(RuntimeError, "secret placeholder"):
                        build_local_images([build], io.StringIO())
                mock_run.assert_not_called()

    def test_k3d_is_an_allowed_executable(self) -> None:
        self.assertIn("k3d", _ALLOWED_EXECUTABLES)


class K3dClusterNameTest(unittest.TestCase):
    def test_derives_cluster_from_context(self) -> None:
        self.assertEqual(k3d_cluster_name("k3d-cds-local"), "cds-local")

    def test_non_k3d_contexts(self) -> None:
        self.assertIsNone(k3d_cluster_name(None))
        self.assertIsNone(k3d_cluster_name("minikube"))
        self.assertIsNone(k3d_cluster_name("k3d-"))

    def test_flag_like_cluster_name_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "k3d cluster name"):
            k3d_cluster_name("k3d---evil")


def _completed(returncode: int = 0, stdout: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(
        args=[], returncode=returncode, stdout=stdout, stderr=""
    )


class ClusterReachabilityTest(unittest.TestCase):
    @patch("cli.k8s_provision.subprocess.run", return_value=_completed(0, "ok"))
    def test_reachable(self, mock_run) -> None:
        self.assertTrue(cluster_is_reachable("k3d-x"))
        cmd = mock_run.call_args.args[0]
        self.assertEqual(cmd[:3], ["kubectl", "--context", "k3d-x"])
        self.assertIn("/healthz", cmd)

    @patch("cli.k8s_provision.subprocess.run", return_value=_completed(1, ""))
    def test_unreachable(self, _mock_run) -> None:
        self.assertFalse(cluster_is_reachable("k3d-x"))

    @patch(
        "cli.k8s_provision.subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd="kubectl", timeout=15),
    )
    def test_timeout_counts_as_unreachable(self, _mock_run) -> None:
        self.assertFalse(cluster_is_reachable(None))

    @patch("cli.k8s_provision.subprocess.run", side_effect=FileNotFoundError("kubectl"))
    def test_missing_kubectl_counts_as_unreachable(self, _mock_run) -> None:
        self.assertFalse(cluster_is_reachable(None))

    @patch(
        "cli.k8s_provision.subprocess.run", return_value=_completed(0, "k3d-cds-mvp\n")
    )
    def test_current_kube_context(self, mock_run) -> None:
        self.assertEqual(current_kube_context(), "k3d-cds-mvp")
        self.assertEqual(
            mock_run.call_args.args[0], ["kubectl", "config", "current-context"]
        )

    @patch("cli.k8s_provision.subprocess.run", return_value=_completed(1, ""))
    def test_current_kube_context_failure_is_none(self, _mock_run) -> None:
        self.assertIsNone(current_kube_context())


class EnsureK8sClusterTest(unittest.TestCase):
    @patch("cli.k8s_provision.cluster_is_reachable", return_value=True)
    def test_reachable_cluster_needs_no_create(self, _mock_reachable) -> None:
        with patch("cli.k8s_provision.run_streamed") as mock_run:
            self.assertEqual(
                ensure_k8s_cluster("k3d-x", create=True, log_file=io.StringIO()), 0
            )
            mock_run.assert_not_called()

    @patch("cli.k8s_provision.cluster_is_reachable", return_value=False)
    def test_unreachable_without_create_raises(self, _mock_reachable) -> None:
        with self.assertRaisesRegex(RuntimeError, "not reachable"):
            ensure_k8s_cluster("k3d-x", create=False, log_file=io.StringIO())

    @patch("cli.k8s_provision.cluster_is_reachable", return_value=False)
    def test_create_requires_k3d_context(self, _mock_reachable) -> None:
        with self.assertRaisesRegex(RuntimeError, "k3d-<name>"):
            ensure_k8s_cluster("minikube", create=True, log_file=io.StringIO())

    @patch("cli.k8s_provision.cluster_is_reachable", side_effect=[False, True])
    @patch("cli.k8s_provision.run_streamed", return_value=0)
    def test_create_runs_k3d_create_and_node_wait(
        self, mock_run, _mock_reachable
    ) -> None:
        result = ensure_k8s_cluster(
            "k3d-cds-x", create=True, log_file=io.StringIO(), timeout=120
        )
        self.assertEqual(result, 0)
        self.assertEqual(mock_run.call_count, 2)
        create_cmd = mock_run.call_args_list[0].args[0]
        self.assertEqual(
            create_cmd,
            [
                "k3d",
                "cluster",
                "create",
                "cds-x",
                "--wait",
                "--k3s-arg",
                "--disable=traefik@server:0",
            ],
        )
        wait_cmd = mock_run.call_args_list[1].args[0]
        self.assertEqual(
            wait_cmd,
            [
                "kubectl",
                "--context",
                "k3d-cds-x",
                "wait",
                "--for=condition=Ready",
                "node",
                "--all",
                "--timeout=120s",
            ],
        )

    @patch("cli.k8s_provision.cluster_is_reachable", return_value=False)
    @patch("cli.k8s_provision.run_streamed", return_value=3)
    def test_create_failure_propagates(self, mock_run, _mock_reachable) -> None:
        result = ensure_k8s_cluster("k3d-cds-x", create=True, log_file=io.StringIO())
        self.assertEqual(result, 3)
        self.assertEqual(mock_run.call_count, 1)

    @patch("cli.k8s_provision.cluster_is_reachable", side_effect=[False, False, False])
    @patch("cli.k8s_provision.run_streamed", return_value=0)
    def test_still_unreachable_after_create_raises(
        self, _mock_run, _mock_reachable
    ) -> None:
        with self.assertRaisesRegex(RuntimeError, "still not reachable"):
            ensure_k8s_cluster("k3d-cds-x", create=True, log_file=io.StringIO())

    def test_flag_like_context_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "kube context"):
            ensure_k8s_cluster("--evil", create=False, log_file=io.StringIO())


class ImportLocalImagesTest(unittest.TestCase):
    def test_empty_list_is_noop(self) -> None:
        with patch("cli.k8s_provision.run_streamed") as mock_run:
            self.assertEqual(import_local_images([], "k3d-x", io.StringIO()), 0)
            mock_run.assert_not_called()

    @patch("cli.k8s_provision.run_streamed", return_value=0)
    def test_imports_all_images_into_named_cluster(self, mock_run) -> None:
        result = import_local_images(
            ["local/a:1", "local/b:1"], "k3d-cds-x", io.StringIO()
        )
        self.assertEqual(result, 0)
        cmd = mock_run.call_args.args[0]
        self.assertEqual(
            cmd,
            ["k3d", "image", "import", "local/a:1", "local/b:1", "--cluster", "cds-x"],
        )

    @patch("cli.k8s_provision.run_streamed", return_value=4)
    def test_import_failure_propagates(self, mock_run) -> None:
        self.assertEqual(
            import_local_images(["local/a:1"], "k3d-cds-x", io.StringIO()), 4
        )
        self.assertEqual(mock_run.call_count, 1)

    def test_non_k3d_cluster_fails_closed(self) -> None:
        with patch("cli.k8s_provision.current_kube_context", return_value="minikube"):
            with self.assertRaisesRegex(RuntimeError, "non-k3d cluster"):
                import_local_images(["local/a:1"], None, io.StringIO())

    @patch("cli.k8s_provision.current_kube_context", return_value="k3d-default")
    @patch("cli.k8s_provision.run_streamed", return_value=0)
    def test_default_context_is_resolved_when_no_kube_context(
        self, mock_run, _mock_current
    ) -> None:
        result = import_local_images(["local/a:1"], None, io.StringIO())
        self.assertEqual(result, 0)
        cmd = mock_run.call_args.args[0]
        self.assertEqual(cmd[-2:], ["--cluster", "default"])

    def test_flag_like_image_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "invalid image reference"):
            import_local_images(["--cluster"], "k3d-x", io.StringIO())


if __name__ == "__main__":
    unittest.main()
