"""Run the test suite, promoting repo-owned DeprecationWarnings to errors.

`PYTHONWARNINGS`/`-W` filter strings always run their module component
through `re.escape()` before anchoring it with `\\z` (see
`warnings._setoption` in the standard library). That means a value such as
``error::DeprecationWarning:cli`` can only ever match a module whose
``__name__`` is the literal string ``"cli"`` -- never ``cli.main``,
``cli.security``, or any other real submodule -- and a value such as
``error::DeprecationWarning:test_.*`` can only match a module literally
named ``test_.*`` (with a literal dot and asterisk), which no real module is
ever named. In short, the env-var syntax cannot express "starts with"
module scoping at all.

To scope deprecation-error promotion to repo-owned code (``cli`` and its
submodules, plus test modules named ``test_*``) while leaving third-party
dependency warnings alone, we must register the filters directly through
`warnings.filterwarnings`, which takes the module argument as a real regex
without escaping it.
"""

from __future__ import annotations

import os
import signal
import sys
import unittest
import warnings

# Running this file directly (rather than via `python -m`) does not add the
# current working directory to sys.path, so `import cli`/`import test_*`
# would otherwise resolve to a pip-installed copy of the package (if any)
# instead of this repository's own source tree -- silently breaking
# coverage measurement (source = ["cli"] in pyproject.toml would then match
# nothing). Insert the repo root (this script's parent directory) explicitly.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Match "cli" itself and any submodule ("cli.main", "cli.security", ...).
warnings.filterwarnings("error", category=DeprecationWarning, module=r"cli(\..*)?$")
# Match any module name starting with "test_" (tests/ has no __init__.py, so
# `unittest discover` imports test modules by their bare filename, e.g.
# "test_security", not "tests.test_security").
warnings.filterwarnings("error", category=DeprecationWarning, module=r"test_.*$")
# Everything else (third-party dependencies) keeps the default behavior.


class TestTimeoutError(TimeoutError):
    """Raised inside a test that exceeds its configured wall-clock limit."""


def apply_test_timeouts(
    suite: unittest.TestSuite, timeout_seconds: float
) -> unittest.TestSuite:
    if timeout_seconds <= 0:
        raise ValueError("Per-test timeout must be greater than zero")
    if not hasattr(signal, "SIGALRM") or not hasattr(signal, "setitimer"):
        raise RuntimeError("Per-test timeouts require SIGALRM and setitimer support")

    for item in suite:
        if isinstance(item, unittest.TestSuite):
            apply_test_timeouts(item, timeout_seconds)
            continue

        original_run = item.run

        def run_with_timeout(result=None, *, _test=item, _run=original_run):
            previous_handler = signal.getsignal(signal.SIGALRM)
            previous_timer = signal.getitimer(signal.ITIMER_REAL)

            def timeout_handler(_signum, _frame):
                raise TestTimeoutError(
                    f"{_test.id()} exceeded {timeout_seconds:g} seconds"
                )

            signal.signal(signal.SIGALRM, timeout_handler)
            signal.setitimer(signal.ITIMER_REAL, timeout_seconds)
            try:
                return _run(result)
            finally:
                signal.setitimer(signal.ITIMER_REAL, *previous_timer)
                signal.signal(signal.SIGALRM, previous_handler)

        item.run = run_with_timeout
    return suite


def main() -> int:
    loader = unittest.TestLoader()
    suite = loader.discover(start_dir="tests", pattern="test_*.py")
    timeout_seconds = float(os.environ.get("CDS_TEST_TIMEOUT_SECONDS", "60"))
    apply_test_timeouts(suite, timeout_seconds)
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
