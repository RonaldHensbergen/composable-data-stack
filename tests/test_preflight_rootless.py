"""Rootless Docker preflight check (#207)."""
import subprocess
import unittest
from unittest.mock import patch

from cli.preflight import _check_runtime


def _run(stdout="[]", rootless_rc=0):
    def fake(command, **_kwargs):
        if "--format" in command:
            return subprocess.CompletedProcess(command, rootless_rc, stdout, "")
        return subprocess.CompletedProcess(command, 0, "", "")
    return fake


@patch("cli.preflight.shutil.which", return_value="/usr/bin/docker")
class RootlessPreflightTest(unittest.TestCase):
    def _check(self, environment, **kwargs):
        plan = {"runtime": {"type": "docker-compose"}, "metadata": {"environment": environment}}
        with patch("cli.preflight.subprocess.run", side_effect=_run(**kwargs)):
            checks = _check_runtime(plan["runtime"], plan)
        return next(c for c in checks if c.name == "runtime.rootless")

    def test_rootless_daemon_passes(self, _which):
        check = self._check("production", stdout='["name=seccomp,profile=builtin","name=rootless"]')
        self.assertEqual(check.status, "PASS")

    def test_rootful_daemon_warns_for_production(self, _which):
        check = self._check("production", stdout='["name=seccomp,profile=builtin"]')
        self.assertEqual(check.status, "WARN")
        self.assertIn("docs/rootless-docker.md", check.message)

    def test_rootful_daemon_does_not_warn_for_local(self, _which):
        self.assertEqual(self._check("local", stdout="[]").status, "PASS")

    def test_undeterminable_daemon_warns(self, _which):
        self.assertEqual(self._check("local", rootless_rc=1).status, "WARN")

    def test_skipped_when_daemon_unreachable(self, _which):
        def fail(command, **_kwargs):
            return subprocess.CompletedProcess(command, 1, "", "")
        with patch("cli.preflight.subprocess.run", side_effect=fail):
            names = [c.name for c in _check_runtime({"type": "docker-compose"}, {})]
        self.assertNotIn("runtime.rootless", names)


if __name__ == "__main__":
    unittest.main()
