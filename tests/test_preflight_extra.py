import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import yaml

from cli import preflight
from cli.preflight import (
    PreflightCheck,
    _check_images,
    _check_ports,
    _expand_port_range,
    _hosts_overlap,
    _load_env_values,
    _port_is_available,
    _published_ports,
    _reference_default_value,
    _run_runtime_command,
)


class RuntimeCommandErrorTest(unittest.TestCase):
    def test_runtime_command_fails_on_oserror(self) -> None:
        with patch("cli.preflight.subprocess.run", side_effect=OSError("boom")):
            check = _run_runtime_command(["docker", "info"], "n", "ok", "bad")
        self.assertEqual(check, PreflightCheck("FAIL", "n", "bad"))

    def test_runtime_command_fails_on_timeout(self) -> None:
        error = subprocess.TimeoutExpired(["docker"], 15)
        with patch("cli.preflight.subprocess.run", side_effect=error):
            check = _run_runtime_command(["docker", "info"], "n", "ok", "bad")
        self.assertEqual(check.status, "FAIL")


class ReferenceDefaultTest(unittest.TestCase):
    def test_default_value_forms(self) -> None:
        self.assertEqual(_reference_default_value("-abc"), "abc")
        self.assertEqual(_reference_default_value(":-abc"), "abc")
        self.assertIsNone(_reference_default_value("-"))
        self.assertIsNone(_reference_default_value(":?err"))
        self.assertIsNone(_reference_default_value(""))


class CheckImagesTest(unittest.TestCase):
    def _policy(self, mode: str) -> MagicMock:
        policy = MagicMock()
        policy.mode = mode
        return policy

    @patch("cli.preflight.default_fixture_path", return_value=None)
    @patch("cli.preflight.verify_images", return_value=[])
    @patch("cli.preflight.load_policy_from_env")
    def test_no_findings_passes(self, mock_load, mock_verify, _fixture) -> None:
        mock_load.return_value = self._policy("policy")
        checks = _check_images({}, "services: {}")
        self.assertEqual([c.status for c in checks], ["PASS"])
        self.assertEqual(checks[0].name, "images")
        mock_verify.assert_called_once()

    @patch("cli.preflight.default_fixture_path", return_value=None)
    @patch("cli.preflight.verify_images")
    @patch("cli.preflight.load_policy_from_env")
    def test_findings_fail(self, mock_load, mock_verify, _fixture) -> None:
        mock_load.return_value = self._policy("policy")
        mock_verify.return_value = [
            {"path": "services.web.image", "rule_id": "IMG-1", "message": "bad"}
        ]
        checks = _check_images({}, "services: {}")
        self.assertEqual(len(checks), 1)
        self.assertEqual(checks[0].status, "FAIL")
        self.assertEqual(checks[0].name, "images.services.web.image")
        self.assertEqual(checks[0].message, "IMG-1: bad")

    @patch("cli.preflight.load_policy_from_env")
    def test_strict_forces_prod_class_and_off_disables(self, mock_load) -> None:
        mock_load.return_value = self._policy("off")
        self.assertEqual(_check_images({}, "services: {}", strict=True), [])
        mock_load.assert_called_once_with("prod")


class LoadEnvValuesTest(unittest.TestCase):
    def test_parses_comments_quotes_and_skips_invalid_lines(self) -> None:
        content = (
            "# comment\n"
            "\n"
            "NOEQUALS\n"
            "A=plain\n"
            'B="double"\n'
            "C='single'\n"
            "D=\"mismatch'\n"
            "E=\"\n"
            "=novalue\n"
            "  F = spaced \n"
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            env_file = Path(tmpdir) / ".env"
            env_file.write_text(content, encoding="utf-8")
            with patch.dict("os.environ", {"A": "from-env"}, clear=True):
                values = _load_env_values(env_file)
        self.assertEqual(values["A"], "from-env")
        self.assertEqual(values["B"], "double")
        self.assertEqual(values["C"], "single")
        self.assertEqual(values["D"], "\"mismatch'")
        self.assertEqual(values["E"], '"')
        self.assertEqual(values["F"], "spaced")
        self.assertNotIn("", values)
        self.assertNotIn("NOEQUALS", values)

    def test_missing_file_returns_only_environment(self) -> None:
        with patch.dict("os.environ", {"X": "1"}, clear=True):
            values = _load_env_values(Path("/nonexistent/.env"))
        self.assertEqual(values, {"X": "1"})


class CheckPortsTest(unittest.TestCase):
    def _compose(self, services: object) -> str:
        return yaml.safe_dump({"services": services})

    def test_no_services_or_non_dict_services(self) -> None:
        for compose in ("", "services: []", "services: {}"):
            with self.subTest(compose=compose):
                checks = _check_ports(compose)
                self.assertEqual(checks, [PreflightCheck("PASS", "ports", "No host ports were declared.")])

    def test_skips_non_dict_service_and_non_list_ports(self) -> None:
        compose = self._compose({"a": "str", "b": {"ports": "8080:80"}})
        checks = _check_ports(compose)
        self.assertEqual([c.name for c in checks], ["ports"])

    @patch("cli.preflight._port_is_available", return_value=True)
    def test_conflict_between_services(self, _mock) -> None:
        compose = self._compose(
            {
                "a": {"ports": ["8080:80"]},
                "b": {"ports": ["127.0.0.1:8080:81"]},
                "c": {"ports": ["127.0.0.1:8080:82/udp"]},
            }
        )
        checks = {c.name: c for c in _check_ports(compose)}
        self.assertEqual(checks["ports.a"].status, "PASS")
        self.assertEqual(checks["ports.b"].status, "FAIL")
        self.assertIn("conflicts with the port declared by a", checks["ports.b"].message)
        self.assertEqual(checks["ports.c"].status, "PASS")

    @patch("cli.preflight._port_is_available", return_value=None)
    def test_unsupported_protocol_warns(self, _mock) -> None:
        checks = _check_ports(self._compose({"a": {"ports": ["9000:90/sctp"]}}))
        self.assertEqual(checks[0].status, "WARN")
        self.assertIn("unsupported protocol", checks[0].message)

    @patch("cli.preflight._port_is_available", return_value=False)
    def test_unavailable_port_fails(self, _mock) -> None:
        checks = _check_ports(self._compose({"a": {"ports": ["9000:90"]}}))
        self.assertEqual(checks[0].status, "FAIL")
        self.assertIn("is unavailable", checks[0].message)

    @patch("cli.preflight._port_is_available", return_value=True)
    def test_available_port_passes(self, _mock) -> None:
        checks = _check_ports(self._compose({"a": {"ports": ["9000:90"]}}))
        self.assertEqual(checks[0].status, "PASS")


class HostsOverlapTest(unittest.TestCase):
    def test_overlap_rules(self) -> None:
        self.assertTrue(_hosts_overlap("127.0.0.1", "127.0.0.1"))
        self.assertTrue(_hosts_overlap("0.0.0.0", "127.0.0.1"))  # noqa: S104
        self.assertTrue(_hosts_overlap("127.0.0.1", "::"))
        self.assertTrue(_hosts_overlap("", "10.0.0.1"))
        self.assertFalse(_hosts_overlap("127.0.0.1", "10.0.0.1"))


class PublishedPortsTest(unittest.TestCase):
    def test_non_string_entries(self) -> None:
        self.assertEqual(_published_ports(8080), [])
        self.assertEqual(_published_ports(None), [])
        self.assertEqual(_published_ports(["8080:80"]), [])

    def test_long_syntax(self) -> None:
        entry = {"published": "8080-8081", "host_ip": "127.0.0.1", "protocol": "UDP"}
        self.assertEqual(
            _published_ports(entry),
            [("127.0.0.1", 8080, "udp"), ("127.0.0.1", 8081, "udp")],
        )
        self.assertEqual(
            _published_ports({"published": 80, "target": 80}),
            [("0.0.0.0", 80, "tcp")],  # noqa: S104
        )
        self.assertEqual(_published_ports({"target": 80}), [])

    def test_short_syntax_forms(self) -> None:
        self.assertEqual(_published_ports("80"), [])
        self.assertEqual(
            _published_ports("8080:80"), [("0.0.0.0", 8080, "tcp")]  # noqa: S104
        )
        self.assertEqual(
            _published_ports("[::1]:8080:80/UDP"), [("::1", 8080, "udp")]
        )
        self.assertEqual(
            _published_ports(":8080:80"), [("0.0.0.0", 8080, "tcp")]  # noqa: S104
        )


class ExpandPortRangeTest(unittest.TestCase):
    def test_ranges(self) -> None:
        self.assertEqual(_expand_port_range("5"), [5])
        self.assertEqual(_expand_port_range("5-7"), [5, 6, 7])
        self.assertEqual(_expand_port_range("abc"), [])
        self.assertEqual(_expand_port_range("1-x"), [])
        self.assertEqual(_expand_port_range("0"), [])
        self.assertEqual(_expand_port_range("7-5"), [])
        self.assertEqual(_expand_port_range("65535-65536"), [])


class PortIsAvailableTest(unittest.TestCase):
    def test_unsupported_protocol_returns_none(self) -> None:
        self.assertIsNone(_port_is_available("127.0.0.1", 80, "sctp"))

    def test_bind_outcomes_and_families(self) -> None:
        with patch("cli.preflight.socket.socket") as mock_socket:
            self.assertTrue(_port_is_available("127.0.0.1", 80, "tcp"))
            mock_socket.assert_called_with(
                preflight.socket.AF_INET, preflight.socket.SOCK_STREAM
            )
            self.assertTrue(_port_is_available("::1", 80, "udp"))
            mock_socket.assert_called_with(
                preflight.socket.AF_INET6, preflight.socket.SOCK_DGRAM
            )
            sock = mock_socket.return_value.__enter__.return_value
            for error in (OSError("in use"), OverflowError("range")):
                sock.bind.side_effect = error
                self.assertFalse(_port_is_available("127.0.0.1", 80, "tcp"))


if __name__ == "__main__":
    unittest.main()
