import importlib.util
import io
import sys
import time
import unittest
from pathlib import Path


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


if __name__ == "__main__":
    unittest.main()
