import io
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from cli.k8s_runner import _secret_values, _write_secret_values, helm_down, helm_up


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
            with patch(
                "cli.k8s_runner.tempfile.mkstemp",
                return_value=(descriptor, str(path)),
            ), patch(
                "cli.k8s_runner.yaml.safe_dump", side_effect=ValueError("bad yaml")
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

    @patch("cli.k8s_runner.get_k8s_state", return_value=[])
    @patch("cli.k8s_runner.get_k8s_workloads", return_value=[])
    @patch("cli.k8s_runner.run_streamed", return_value=0)
    def test_helm_up_reports_failure_when_workload_never_settles(
        self, mock_run, mock_workloads, _mock_state
    ) -> None:
        plan: dict = {"secrets": {}}
        mock_workloads.return_value = [
            {"kind": "Deployment", "metadata": {"name": "cds-web"}, "spec": {"replicas": 1}}
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
        mock_state.return_value = [{"Service": "cds-web", "Health": "HEALTHY", "State": "running"}]

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


if __name__ == "__main__":
    unittest.main()
