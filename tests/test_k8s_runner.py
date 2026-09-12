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
    _secret_values,
    _validate_k8s_name,
    _validate_kube_context,
    _write_secret_values,
    get_k8s_workloads,
    helm_down,
    helm_up,
)


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
        self.assertIn("--wait", command)

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


if __name__ == "__main__":
    unittest.main()
