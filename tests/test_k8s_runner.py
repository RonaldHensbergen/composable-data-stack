import contextlib
import io
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from cli.k8s_runner import (
    HELM_COLD_START_SECONDS_PER_IMAGE,
    _secret_values,
    _settle_failure_message,
    _validate_k8s_name,
    _validate_kube_context,
    _validate_timeout,
    _write_secret_values,
    get_k8s_workloads,
    helm_down,
    helm_settle_timeout,
    helm_up,
)
from cli.up_runner import DEFAULT_TIMEOUT_SECONDS


class KubernetesRunnerTest(unittest.TestCase):
    def test_secret_values_require_every_planned_environment_key(self) -> None:
        plan = {"secrets": {"logical": "CDS_REQUIRED", "CDS_REQUIRED": "CDS_REQUIRED"}}
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ValueError, "CDS_REQUIRED"):
                _secret_values(plan)

    @unittest.skipIf(
        sys.platform == "win32",
        "Windows does not enforce POSIX permission bits",
    )
    def test_secret_values_file_is_private_and_yaml_safe(self) -> None:
        path = _write_secret_values({"CDS_PASSWORD": "colon: quote' newline\nvalue"})
        try:
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(
                yaml.safe_load(path.read_text(encoding="utf-8")),
                {"secrets": {"CDS_PASSWORD": "colon: quote' newline\nvalue"}},
            )
        finally:
            path.unlink(missing_ok=True)

    def test_secret_values_file_is_removed_when_serialization_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "secrets.yaml"
            descriptor = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
            with (
                patch(
                    "cli.k8s_runner.tempfile.mkstemp",
                    return_value=(descriptor, str(path)),
                ),
                patch(
                    "cli.k8s_runner.yaml.safe_dump", side_effect=ValueError("bad yaml")
                ),
            ):
                with self.assertRaisesRegex(ValueError, "bad yaml"):
                    _write_secret_values({"CDS_PASSWORD": "secret"})
            self.assertFalse(path.exists())

    @patch("cli.k8s_runner.get_k8s_workloads", return_value=[])
    @patch("cli.k8s_runner.run_streamed", return_value=0)
    def test_helm_up_removes_secret_file(self, mock_run, _mock_workloads) -> None:
        plan = {"secrets": {"password": "CDS_PASSWORD"}}
        with patch.dict(os.environ, {"CDS_PASSWORD": "sentinel"}, clear=True):
            result = helm_up(
                plan,
                Path("chart"),
                namespace="test",
                release="cds",
                kube_context="k3d-test",
                timeout=30,
                detach=False,
                log_file=io.StringIO(),
            )

        self.assertEqual(result, 0)
        command = mock_run.call_args.args[0]
        values_path = Path(command[command.index("--values") + 1])
        self.assertFalse(values_path.exists())
        self.assertNotIn("sentinel", " ".join(command))
        # Readiness is no longer decided by Helm's own --wait: it's decided by
        # polling get_k8s_state()/group_services_by_health() below (the same
        # pair `cds state` uses), so the apply step itself doesn't block on
        # workload rollout.
        self.assertNotIn("--wait", command)

    @patch("cli.k8s_runner.run_streamed", return_value=0)
    def test_helm_up_forwards_use_color_to_apply_output(self, mock_run) -> None:
        for use_color, expected in ((True, True), (False, False)):
            with self.subTest(use_color=use_color):
                result = helm_up(
                    {"secrets": {}},
                    Path("chart"),
                    namespace="test",
                    release="cds",
                    kube_context="k3d-test",
                    timeout=30,
                    detach=True,
                    log_file=io.StringIO(),
                    use_color=use_color,
                )
                self.assertEqual(result, 0)
                self.assertEqual(mock_run.call_args.kwargs["use_color"], expected)

    @patch("cli.k8s_runner.get_k8s_state", return_value=[])
    @patch("cli.k8s_runner.get_k8s_workloads", return_value=[])
    @patch("cli.k8s_runner.run_streamed", return_value=0)
    def test_helm_up_reports_failure_when_workload_never_settles(
        self, mock_run, mock_workloads, _mock_state
    ) -> None:
        plan: dict = {"secrets": {}}
        mock_workloads.return_value = [
            {
                "kind": "Deployment",
                "metadata": {"name": "cds-web"},
                "spec": {"replicas": 1},
            }
        ]

        result = helm_up(
            plan,
            Path("chart"),
            namespace="test",
            release="cds",
            kube_context=None,
            timeout=0,
            detach=False,
            log_file=io.StringIO(),
            sleep_fn=lambda _seconds: None,
        )

        self.assertEqual(result, 1)
        self.assertEqual(mock_run.call_count, 1)

    @patch("cli.k8s_runner.get_k8s_state")
    @patch("cli.k8s_runner.get_k8s_workloads")
    @patch("cli.k8s_runner.run_streamed", return_value=0)
    def test_helm_up_settle_failure_distinguishes_initializing_from_unhealthy(
        self, _mock_run, mock_workloads, mock_state
    ) -> None:
        # Issue #1116: a cold start whose pods are all still initializing
        # (STARTING, not UNHEALTHY) must be reported as "still initializing"
        # with a re-run/--timeout hint, in the log and on the terminal.
        mock_workloads.return_value = [
            {
                "kind": "Deployment",
                "metadata": {"name": "cds-web"},
                "spec": {"replicas": 1},
            }
        ]
        mock_state.return_value = [
            {"Service": "cds-web", "Health": "STARTING", "State": "running"}
        ]
        log_file = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()) as stdout:
            result = helm_up(
                {"secrets": {}},
                Path("chart"),
                namespace="test",
                release="cds",
                kube_context=None,
                timeout=0,  # clamped to 1s by _validate_timeout
                detach=False,
                log_file=log_file,
                sleep_fn=lambda _seconds: None,
            )

        self.assertEqual(result, 1)
        for sink in (log_file.getvalue(), stdout.getvalue()):
            self.assertIn("did not settle within 1s", sink)
            self.assertIn("still initializing: cds-web", sink)
            self.assertNotIn("unhealthy:", sink)
            self.assertIn("re-run `cds up` or pass --timeout", sink)

    @patch("cli.k8s_runner.get_k8s_state")
    @patch("cli.k8s_runner.get_k8s_workloads")
    @patch("cli.k8s_runner.run_streamed", return_value=0)
    def test_helm_up_ready_state_matches_cds_state_health_grouping(
        self, _mock_run, mock_workloads, mock_state
    ) -> None:
        # A release with one healthy Deployment settles as ready via the same
        # get_k8s_state()/group_services_by_health() pair `cds state` uses.
        workload = {
            "kind": "Deployment",
            "metadata": {"name": "cds-web"},
            "spec": {"replicas": 1},
            "status": {"readyReplicas": 1},
        }
        mock_workloads.return_value = [workload]
        mock_state.return_value = [
            {"Service": "cds-web", "Health": "HEALTHY", "State": "running"}
        ]

        result = helm_up(
            {"secrets": {}},
            Path("chart"),
            namespace="test",
            release="cds",
            kube_context=None,
            timeout=30,
            detach=False,
            log_file=io.StringIO(),
        )

        self.assertEqual(result, 0)

    @patch("cli.k8s_runner.get_k8s_workloads")
    @patch("cli.k8s_runner.run_streamed", return_value=0)
    def test_helm_down_retains_pvcs_by_default(self, mock_run, mock_workloads) -> None:
        result = helm_down(
            namespace="test",
            release="cds",
            kube_context=None,
            timeout=30,
            delete_pvcs=False,
            log_file=io.StringIO(),
        )

        self.assertEqual(result, 0)
        self.assertEqual(mock_run.call_count, 1)
        mock_workloads.assert_not_called()
        self.assertEqual(mock_run.call_args.args[0][1], "uninstall")

    @patch("cli.k8s_runner.get_k8s_workloads")
    @patch("cli.k8s_runner.run_streamed", return_value=0)
    def test_helm_down_deletes_only_exact_statefulset_claims(
        self, mock_run, mock_workloads
    ) -> None:
        mock_workloads.return_value = [
            {
                "kind": "StatefulSet",
                "metadata": {"name": "cds-postgres"},
                "spec": {
                    "replicas": 1,
                    "volumeClaimTemplates": [{"metadata": {"name": "postgres-data"}}],
                },
            }
        ]

        result = helm_down(
            namespace="test",
            release="cds",
            kube_context="k3d-test",
            timeout=30,
            delete_pvcs=True,
            log_file=io.StringIO(),
        )

        self.assertEqual(result, 0)
        self.assertEqual(mock_run.call_count, 2)
        delete_command = mock_run.call_args_list[1].args[0]
        self.assertIn("postgres-data-cds-postgres-0", delete_command)
        self.assertNotIn("-l", delete_command)


class K8sNameValidationTest(unittest.TestCase):
    """
    A `namespace`/`release`/`kube_context` value that starts with `-` (or
    otherwise doesn't look like a plain Kubernetes name) must never reach
    `helm`/`kubectl` as a command argument: a downstream binary could parse
    it as an extra flag instead of a plain positional value (CWE-88
    argument injection).
    """

    def test_validate_k8s_name_accepts_valid_dns_label(self) -> None:
        self.assertEqual(_validate_k8s_name("cds-local", "namespace"), "cds-local")

    def test_validate_k8s_name_rejects_leading_dash(self) -> None:
        with self.assertRaisesRegex(ValueError, "namespace"):
            _validate_k8s_name("--kubeconfig=/tmp/evil", "namespace")

    def test_validate_k8s_name_rejects_uppercase_and_empty(self) -> None:
        with self.assertRaises(ValueError):
            _validate_k8s_name("Invalid_Name", "release")
        with self.assertRaises(ValueError):
            _validate_k8s_name("", "release")

    def test_validate_kube_context_accepts_plain_value(self) -> None:
        self.assertEqual(_validate_kube_context("k3d-test"), "k3d-test")
        self.assertIsNone(_validate_kube_context(None))

    def test_validate_kube_context_rejects_flag_like_value(self) -> None:
        with self.assertRaises(ValueError):
            _validate_kube_context("--kubeconfig=/tmp/evil")

    @patch("cli.k8s_runner.subprocess.run")
    def test_get_k8s_workloads_rejects_flag_like_release(self, mock_run) -> None:
        with self.assertRaises(ValueError):
            get_k8s_workloads("test", "--namespace=kube-system", None)
        mock_run.assert_not_called()

    @patch("cli.k8s_runner.run_streamed")
    def test_helm_up_rejects_flag_like_namespace(self, mock_run_streamed) -> None:
        with self.assertRaises(ValueError):
            helm_up(
                {"secrets": {}},
                Path("chart"),
                namespace="--kubeconfig=/tmp/evil",
                release="cds",
                kube_context=None,
                timeout=30,
                detach=True,
                log_file=io.StringIO(),
            )
        mock_run_streamed.assert_not_called()

    @patch("cli.k8s_runner.run_streamed")
    def test_helm_down_rejects_flag_like_release(self, mock_run_streamed) -> None:
        with self.assertRaises(ValueError):
            helm_down(
                namespace="test",
                release="--kubeconfig=/tmp/evil",
                kube_context=None,
                timeout=30,
                delete_pvcs=False,
                log_file=io.StringIO(),
            )
        mock_run_streamed.assert_not_called()

    def test_validate_timeout_clamps_and_truncates(self) -> None:
        self.assertEqual(_validate_timeout(30.0), 30)
        self.assertEqual(_validate_timeout(0.4), 1)
        self.assertEqual(_validate_timeout(-5), 1)

    def test_validate_timeout_rejects_non_finite_values(self) -> None:
        with self.assertRaises(ValueError):
            _validate_timeout(float("nan"))
        with self.assertRaises(ValueError):
            _validate_timeout(float("inf"))
        with self.assertRaises(ValueError):
            _validate_timeout("30s")  # type: ignore[arg-type]

    @patch("cli.k8s_runner.run_streamed")
    def test_helm_up_rejects_non_finite_timeout(self, mock_run_streamed) -> None:
        with self.assertRaises(ValueError):
            helm_up(
                {"secrets": {}},
                Path("chart"),
                namespace="cds",
                release="cds",
                kube_context=None,
                timeout=float("nan"),
                detach=True,
                log_file=io.StringIO(),
            )
        mock_run_streamed.assert_not_called()

    @patch("cli.k8s_runner.run_streamed")
    def test_helm_down_rejects_non_finite_timeout(self, mock_run_streamed) -> None:
        with self.assertRaises(ValueError):
            helm_down(
                namespace="cds",
                release="cds",
                kube_context=None,
                timeout=float("inf"),
                delete_pvcs=False,
                log_file=io.StringIO(),
            )
        mock_run_streamed.assert_not_called()


class HelmSettleTimeoutTest(unittest.TestCase):
    """Issue #1116: the helm settle default scales with local image builds."""

    def test_no_local_builds_keeps_base_timeout(self) -> None:
        self.assertEqual(helm_settle_timeout(), DEFAULT_TIMEOUT_SECONDS)
        self.assertEqual(
            helm_settle_timeout(local_build_count=0), DEFAULT_TIMEOUT_SECONDS
        )

    def test_scales_per_local_image_build(self) -> None:
        # 3 local images on a cold k3d cluster: 180s base + 3 x 120s = 540s
        self.assertEqual(
            helm_settle_timeout(DEFAULT_TIMEOUT_SECONDS, 3),
            DEFAULT_TIMEOUT_SECONDS + 3 * HELM_COLD_START_SECONDS_PER_IMAGE,
        )
        self.assertEqual(helm_settle_timeout(DEFAULT_TIMEOUT_SECONDS, 3), 540.0)

    def test_negative_count_clamps_to_zero(self) -> None:
        self.assertEqual(
            helm_settle_timeout(DEFAULT_TIMEOUT_SECONDS, -2), DEFAULT_TIMEOUT_SECONDS
        )

    def test_custom_base_timeout(self) -> None:
        self.assertEqual(
            helm_settle_timeout(60.0, 2), 60.0 + 2 * HELM_COLD_START_SECONDS_PER_IMAGE
        )


class SettleFailureMessageTest(unittest.TestCase):
    """Issue #1116: settle failures distinguish initializing from failing pods."""

    def test_initializing_only_hints_rerun_and_is_not_called_unhealthy(self) -> None:
        message = _settle_failure_message(
            "cds", 540, {"STARTING": ["cds-web", "cds-worker"]}
        )
        self.assertIn("helm release cds did not settle within 540s", message)
        self.assertIn("still initializing: cds-web, cds-worker", message)
        self.assertNotIn("unhealthy:", message)
        self.assertIn("re-run `cds up` or pass --timeout", message)

    def test_unhealthy_only_reports_unhealthy_without_hint(self) -> None:
        message = _settle_failure_message("cds", 180, {"UNHEALTHY": ["cds-db"]})
        self.assertIn("unhealthy: cds-db", message)
        self.assertNotIn("still initializing", message)
        self.assertNotIn("re-run", message)

    def test_mixed_reports_both_without_hint(self) -> None:
        message = _settle_failure_message(
            "cds", 180, {"UNHEALTHY": ["cds-db"], "STARTING": ["cds-web"]}
        )
        self.assertIn("unhealthy: cds-db", message)
        self.assertIn("still initializing: cds-web", message)
        self.assertNotIn("re-run", message)

    def test_unhealthy_exit_counts_as_failing(self) -> None:
        message = _settle_failure_message("cds", 180, {"UNHEALTHY EXIT": ["cds-init"]})
        self.assertIn("unhealthy: cds-init", message)
        self.assertNotIn("still initializing", message)


if __name__ == "__main__":
    unittest.main()
