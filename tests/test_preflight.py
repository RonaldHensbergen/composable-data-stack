import os
import socket
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from cli.preflight import _published_ports, preflight_passed, run_preflight


class PreflightTest(unittest.TestCase):
    def setUp(self) -> None:
        self.plan = {"runtime": {"type": "docker-compose"}}

    @patch("cli.preflight.subprocess.run")
    @patch("cli.preflight.shutil.which", return_value="/usr/bin/docker")
    def test_passes_with_runtime_environment_and_available_port(
        self,
        _mock_which,
        mock_run,
    ) -> None:
        mock_run.return_value = subprocess.CompletedProcess([], 0)
        compose_yaml = yaml.safe_dump(
            {
                "services": {
                    "web": {
                        "environment": {"TOKEN": "${CDS_REQUIRED}"},
                    }
                }
            }
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            env_file = Path(tmpdir) / ".env"
            env_file.write_text("CDS_REQUIRED=top-secret\n", encoding="utf-8")
            with patch.dict(os.environ, {}, clear=True):
                checks = run_preflight(self.plan, compose_yaml, env_file)

        self.assertTrue(preflight_passed(checks))
        self.assertNotIn("top-secret", " ".join(check.message for check in checks))
        self.assertEqual(mock_run.call_count, 2)

    @patch("cli.preflight.shutil.which", return_value=None)
    def test_fails_when_runtime_cli_is_missing(self, _mock_which) -> None:
        checks = run_preflight(self.plan, "services: {}\n", Path("missing.env"))

        self.assertFalse(preflight_passed(checks))
        self.assertTrue(
            any(check.name == "runtime.cli" and check.status == "FAIL" for check in checks)
        )

    @patch("cli.preflight.subprocess.run")
    @patch("cli.preflight.shutil.which", return_value="/usr/bin/docker")
    def test_fails_when_runtime_daemon_is_unreachable(
        self,
        _mock_which,
        mock_run,
    ) -> None:
        mock_run.side_effect = [
            subprocess.CompletedProcess([], 0),
            subprocess.CompletedProcess([], 1),
        ]

        checks = run_preflight(self.plan, "services: {}\n", Path("missing.env"))

        self.assertFalse(preflight_passed(checks))
        self.assertTrue(
            any(check.name == "runtime.daemon" and check.status == "FAIL" for check in checks)
        )

    @patch("cli.preflight.subprocess.run")
    @patch("cli.preflight.shutil.which", return_value="/usr/bin/docker")
    def test_fails_for_missing_required_environment_value(
        self,
        _mock_which,
        mock_run,
    ) -> None:
        mock_run.return_value = subprocess.CompletedProcess([], 0)
        compose_yaml = yaml.safe_dump(
            {
                "services": {
                    "worker": {
                        "environment": {"PASSWORD": "${CDS_REQUIRED_PASSWORD}"},
                    }
                }
            }
        )

        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(
            os.environ, {}, clear=True
        ):
            checks = run_preflight(
                self.plan,
                compose_yaml,
                Path(tmpdir) / ".env",
            )

        self.assertFalse(preflight_passed(checks))
        self.assertTrue(
            any(
                check.name == "environment.CDS_REQUIRED_PASSWORD"
                and check.status == "FAIL"
                for check in checks
            )
        )

    @patch("cli.preflight.subprocess.run")
    @patch("cli.preflight.shutil.which", return_value="/usr/bin/docker")
    def test_fails_for_empty_required_environment_value(
        self,
        _mock_which,
        mock_run,
    ) -> None:
        mock_run.return_value = subprocess.CompletedProcess([], 0)
        compose_yaml = "services:\n  app:\n    environment:\n      TOKEN: ${CDS_TOKEN}\n"

        with tempfile.TemporaryDirectory() as tmpdir:
            env_file = Path(tmpdir) / ".env"
            env_file.write_text("CDS_TOKEN=\n", encoding="utf-8")
            with patch.dict(os.environ, {}, clear=True):
                checks = run_preflight(self.plan, compose_yaml, env_file)

        self.assertFalse(preflight_passed(checks))

    @patch("cli.preflight.subprocess.run")
    @patch("cli.preflight.shutil.which", return_value="/usr/bin/docker")
    def test_warns_for_init_placeholder_without_exposing_value(
        self,
        _mock_which,
        mock_run,
    ) -> None:
        mock_run.return_value = subprocess.CompletedProcess([], 0)
        compose_yaml = "services:\n  app:\n    environment:\n      TOKEN: ${CDS_TOKEN}\n"

        with tempfile.TemporaryDirectory() as tmpdir:
            env_file = Path(tmpdir) / ".env"
            env_file.write_text("CDS_TOKEN=change-me\n", encoding="utf-8")
            with patch.dict(os.environ, {}, clear=True):
                checks = run_preflight(self.plan, compose_yaml, env_file)

        self.assertTrue(preflight_passed(checks))
        self.assertTrue(
            any(check.name == "environment.placeholders" for check in checks)
        )

    @patch("cli.preflight.subprocess.run")
    @patch("cli.preflight.shutil.which", return_value="/usr/bin/docker")
    def test_warns_for_insecure_default_on_secret_like_variable(
        self,
        _mock_which,
        mock_run,
    ) -> None:
        mock_run.return_value = subprocess.CompletedProcess([], 0)
        compose_yaml = "services:\n  app:\n    environment:\n      PASSWORD: ${CDS_DB_PASSWORD:-postgres}\n"

        with patch.dict(os.environ, {}, clear=True):
            checks = run_preflight(self.plan, compose_yaml, Path("missing.env"))

        self.assertTrue(preflight_passed(checks))
        self.assertTrue(
            any(
                check.name == "environment.insecure-defaults"
                and "CDS_DB_PASSWORD" in check.message
                for check in checks
            )
        )
        self.assertTrue(
            any(check.name == "environment" and check.status == "PASS" for check in checks)
        )

    @patch("cli.preflight.subprocess.run")
    @patch("cli.preflight.shutil.which", return_value="/usr/bin/docker")
    def test_does_not_warn_for_insecure_default_on_non_secret_variable(
        self,
        _mock_which,
        mock_run,
    ) -> None:
        mock_run.return_value = subprocess.CompletedProcess([], 0)
        compose_yaml = "services:\n  app:\n    environment:\n      LOG_LEVEL: ${CDS_LOG_LEVEL:-info}\n"

        with patch.dict(os.environ, {}, clear=True):
            checks = run_preflight(self.plan, compose_yaml, Path("missing.env"))

        self.assertTrue(preflight_passed(checks))
        self.assertNotIn(
            "environment.insecure-defaults", [check.name for check in checks]
        )

    @patch("cli.preflight.subprocess.run")
    @patch("cli.preflight.shutil.which", return_value="/usr/bin/docker")
    def test_does_not_warn_for_empty_default_on_secret_like_variable(
        self,
        _mock_which,
        mock_run,
    ) -> None:
        mock_run.return_value = subprocess.CompletedProcess([], 0)
        compose_yaml = "services:\n  app:\n    environment:\n      PASSWORD: ${CDS_TOKEN:-}\n"

        with patch.dict(os.environ, {}, clear=True):
            checks = run_preflight(self.plan, compose_yaml, Path("missing.env"))

        self.assertTrue(preflight_passed(checks))
        self.assertNotIn(
            "environment.insecure-defaults", [check.name for check in checks]
        )

    @patch("cli.preflight.subprocess.run")
    @patch("cli.preflight.shutil.which", return_value="/usr/bin/docker")
    def test_reports_insecure_default_warn_alongside_missing_required_value(
        self,
        _mock_which,
        mock_run,
    ) -> None:
        mock_run.return_value = subprocess.CompletedProcess([], 0)
        compose_yaml = (
            "services:\n  app:\n    environment:\n"
            "      REQUIRED: ${CDS_REQUIRED_VAR}\n"
            "      PASSWORD: ${CDS_DB_PASSWORD:-postgres}\n"
        )

        with patch.dict(os.environ, {}, clear=True):
            checks = run_preflight(self.plan, compose_yaml, Path("missing.env"))

        self.assertFalse(preflight_passed(checks))
        self.assertTrue(
            any(check.name == "environment.CDS_REQUIRED_VAR" for check in checks)
        )
        self.assertTrue(
            any(
                check.name == "environment.insecure-defaults"
                and "CDS_DB_PASSWORD" in check.message
                for check in checks
            )
        )

    @patch("cli.preflight._load_env_values", side_effect=OSError("denied"))
    @patch("cli.preflight.subprocess.run")
    @patch("cli.preflight.shutil.which", return_value="/usr/bin/docker")
    def test_reports_insecure_default_warn_when_env_file_unreadable(
        self,
        _mock_which,
        mock_run,
        _mock_load_env,
    ) -> None:
        mock_run.return_value = subprocess.CompletedProcess([], 0)
        compose_yaml = (
            "services:\n  app:\n    environment:\n"
            "      REQUIRED: ${CDS_REQUIRED_VAR}\n"
            "      PASSWORD: ${CDS_DB_PASSWORD:-postgres}\n"
        )

        with patch.dict(os.environ, {}, clear=True):
            checks = run_preflight(self.plan, compose_yaml, Path(".env"))

        self.assertFalse(preflight_passed(checks))
        self.assertTrue(
            any(
                check.name == "environment" and check.status == "FAIL"
                for check in checks
            )
        )
        self.assertTrue(
            any(
                check.name == "environment.insecure-defaults"
                and "CDS_DB_PASSWORD" in check.message
                for check in checks
            )
        )

    @patch("cli.preflight.subprocess.run")
    @patch("cli.preflight.shutil.which", return_value="/usr/bin/docker")
    def test_does_not_warn_for_nested_default_reference(
        self,
        _mock_which,
        mock_run,
    ) -> None:
        mock_run.return_value = subprocess.CompletedProcess([], 0)
        compose_yaml = (
            "services:\n  app:\n    environment:\n"
            "      PASSWORD: ${CDS_DB_PASSWORD:-${CDS_FALLBACK}}\n"
        )

        with patch.dict(os.environ, {}, clear=True):
            checks = run_preflight(self.plan, compose_yaml, Path("missing.env"))

        self.assertTrue(preflight_passed(checks))
        self.assertNotIn(
            "environment.insecure-defaults", [check.name for check in checks]
        )

    @patch("cli.preflight.subprocess.run")
    @patch("cli.preflight.shutil.which", return_value="/usr/bin/docker")
    def test_warns_for_insecure_default_on_pass_shorthand_variable(
        self,
        _mock_which,
        mock_run,
    ) -> None:
        mock_run.return_value = subprocess.CompletedProcess([], 0)
        compose_yaml = (
            "services:\n  app:\n    environment:\n"
            "      PASSWORD: ${CDS_DB_PASS:-postgres}\n"
        )

        with patch.dict(os.environ, {}, clear=True):
            checks = run_preflight(self.plan, compose_yaml, Path("missing.env"))

        self.assertTrue(preflight_passed(checks))
        self.assertTrue(
            any(
                check.name == "environment.insecure-defaults"
                and "CDS_DB_PASS" in check.message
                for check in checks
            )
        )

    @patch("cli.preflight.subprocess.run")
    @patch("cli.preflight.shutil.which", return_value="/usr/bin/docker")
    def test_does_not_warn_for_variable_name_containing_key_substring(
        self,
        _mock_which,
        mock_run,
    ) -> None:
        mock_run.return_value = subprocess.CompletedProcess([], 0)
        compose_yaml = (
            "services:\n  app:\n    environment:\n"
            "      SECRET: ${CDS_TURKEY:-food}\n"
        )

        with patch.dict(os.environ, {}, clear=True):
            checks = run_preflight(self.plan, compose_yaml, Path("missing.env"))

        self.assertTrue(preflight_passed(checks))
        self.assertNotIn(
            "environment.insecure-defaults", [check.name for check in checks]
        )

    @patch("cli.preflight.subprocess.run")
    @patch("cli.preflight.shutil.which", return_value="/usr/bin/docker")
    def test_fails_for_occupied_host_port(self, _mock_which, mock_run) -> None:
        mock_run.return_value = subprocess.CompletedProcess([], 0)
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            port = listener.getsockname()[1]
            compose_yaml = yaml.safe_dump(
                {
                    "services": {
                        "api": {
                            "ports": [f"127.0.0.1:{port}:8080"],
                        }
                    }
                }
            )

            checks = run_preflight(self.plan, compose_yaml, Path("missing.env"))

        self.assertFalse(preflight_passed(checks))
        self.assertTrue(
            any(check.name == "ports.api" and check.status == "FAIL" for check in checks)
        )

    def test_fails_for_unsupported_runtime(self) -> None:
        checks = run_preflight(
            {"runtime": {"type": "unsupported"}},
            "services: {}\n",
            Path("missing.env"),
        )

        self.assertFalse(preflight_passed(checks))
        self.assertEqual(checks[0].name, "runtime")

    def test_container_only_port_does_not_claim_a_host_port(self) -> None:
        self.assertEqual(_published_ports(8080), [])
        self.assertEqual(_published_ports("8080"), [])

    def test_published_port_range_expands_every_host_port(self) -> None:
        self.assertEqual(
            _published_ports("127.0.0.1:8000-8002:80-82"),
            [
                ("127.0.0.1", 8000, "tcp"),
                ("127.0.0.1", 8001, "tcp"),
                ("127.0.0.1", 8002, "tcp"),
            ],
        )

    @patch("cli.preflight.subprocess.run")
    @patch("cli.preflight.shutil.which", return_value="/usr/bin/docker")
    def test_fails_for_occupied_udp_port(self, _mock_which, mock_run) -> None:
        mock_run.return_value = subprocess.CompletedProcess([], 0)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
            compose_yaml = yaml.safe_dump(
                {
                    "services": {
                        "dns": {
                            "ports": [f"127.0.0.1:{port}:53/udp"],
                        }
                    }
                }
            )

            checks = run_preflight(self.plan, compose_yaml, Path("missing.env"))

        self.assertFalse(preflight_passed(checks))
        self.assertTrue(
            any(check.name == "ports.dns" and check.status == "FAIL" for check in checks)
        )


if __name__ == "__main__":
    unittest.main()
