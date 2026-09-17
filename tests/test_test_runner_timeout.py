import importlib.util
import io
import signal
import sys
import time
import types
import unittest
from pathlib import Path
from unittest import mock


def _load_runner_module():
    path = (
        Path(__file__).resolve().parent.parent
        / "scripts"
        / "run_tests_with_deprecation_gate.py"
    )
    spec = importlib.util.spec_from_file_location("cds_bounded_test_runner", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(spec.name, None)
    return module


_RUNNER = _load_runner_module()


class TestRunnerTimeoutTest(unittest.TestCase):
    @unittest.skipUnless(
        hasattr(signal, "SIGALRM") and hasattr(signal, "setitimer"),
        "per-test timeouts require SIGALRM/setitimer, unavailable on this platform (e.g. Windows)",
    )
    def test_slow_test_is_interrupted_and_reported(self) -> None:
        class SlowTest(unittest.TestCase):
            def runTest(self) -> None:
                time.sleep(1)

        suite = unittest.TestSuite([SlowTest()])
        _RUNNER.apply_test_timeouts(suite, 0.05)

        started = time.monotonic()
        result = unittest.TextTestRunner(stream=io.StringIO()).run(suite)
        elapsed = time.monotonic() - started

        self.assertEqual(len(result.errors), 1)
        self.assertLess(elapsed, 0.5)
        self.assertIn("exceeded 0.05 seconds", result.errors[0][1])

    def test_non_positive_timeout_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "greater than zero"):
            _RUNNER.apply_test_timeouts(unittest.TestSuite(), 0)

    def test_unsupported_platform_warns_and_runs_without_signal_timeout(self) -> None:
        suite = unittest.TestSuite()
        unsupported_signal = types.SimpleNamespace()

        with mock.patch.object(_RUNNER, "signal", unsupported_signal), self.assertWarnsRegex(
            RuntimeWarning, "Per-test timeouts are disabled"
        ):
            returned = _RUNNER.apply_test_timeouts(suite, 1)

        self.assertIs(returned, suite)


if __name__ == "__main__":
    unittest.main()
