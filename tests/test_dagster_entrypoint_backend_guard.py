import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

# Environment variables whose values are Windows paths when the guard tests
# run under the WSL tier; WSLENV translates these to WSL form via the /p flag.
_WSL_PATH_ENV_VARS = frozenset({"DAGSTER_HOME", "DAGSTER_SQLITE_DIR"})


def _is_wsl_shell(shell: list[str]) -> bool:
    """True when the argv prefix targets WSL's sh (Windows-only third tier)."""
    if len(shell) != 3 or shell[1] != "-e" or shell[2] != "sh":
        return False
    # Handle both POSIX and Windows path separators so the check also works
    # for Windows paths on non-Windows hosts (mocked unit tests).
    basename = shell[0].replace("\\", "/").rsplit("/", 1)[-1]
    return basename.lower() == "wsl.exe"


def _wslenv_for(env: dict[str, str]) -> str:
    """WSLENV value that forwards `env` into WSL, translating path-like vars."""
    return ":".join(
        f"{key}/p" if key in _WSL_PATH_ENV_VARS else key for key in sorted(env)
    )


def find_posix_shell() -> list[str]:
    """Return an argv prefix that runs a script under a POSIX shell.

    Prefers `sh` on PATH (Linux/macOS, Git for Windows on PATH). On Windows,
    falls back to a Git for Windows install outside PATH, and then to WSL's
    `sh` (a distro must be installed; a one-shot probe verifies it actually
    executes). The WSL prefix is driven via stdin (`sh -s`) rather than
    `-c`, because wsl.exe argument quoting is not reliable for scripts that
    contain quotes and newlines. Returns an empty list when no POSIX shell
    is discoverable.
    """
    shell = shutil.which("sh")
    if shell:
        return [shell]
    if sys.platform.startswith("win"):
        program_dirs = [
            os.environ.get("ProgramFiles", ""),
            os.environ.get("ProgramFiles(x86)", ""),
            os.path.join(os.environ.get("LocalAppData", ""), "Programs"),
        ]
        for base in program_dirs:
            if not base:
                continue
            for relative in (
                Path("Git") / "bin" / "sh.exe",
                Path("Git") / "usr" / "bin" / "sh.exe",
            ):
                candidate = Path(base) / relative
                if candidate.is_file():
                    return [str(candidate)]
        wsl = shutil.which("wsl.exe")
        if wsl:
            try:
                probe = subprocess.run(
                    [wsl, "-e", "sh", "-c", "exit 0"],
                    capture_output=True,
                    timeout=15,
                )
            except (OSError, subprocess.TimeoutExpired):
                return []
            if probe.returncode == 0:
                return [wsl, "-e", "sh"]
    return []


def posix_shell_or_skip() -> list[str]:
    """Return the POSIX shell argv prefix, skipping the test when none exists."""
    shell = find_posix_shell()
    if not shell:
        raise unittest.SkipTest(
            "no POSIX shell (sh) found on PATH, in a Git for Windows "
            "install, or through WSL; cannot exercise the entrypoint guard "
            "without one"
        )
    return shell


def run_shell_script(
    shell: list[str],
    script: str,
    env: dict[str, str],
    flags: tuple[str, ...] = (),
    timeout: int = 5,
) -> subprocess.CompletedProcess:
    """Run script text under the discovered POSIX shell argv prefix.

    Under the WSL tier the script is fed via stdin and the environment is
    forwarded through WSLENV: path-like variables are translated to WSL
    form, and local CDS_* variables are stripped so the subprocess stays
    hermetic against the caller's environment. WSL runs get a larger
    timeout to absorb cold-start latency.
    """
    if _is_wsl_shell(shell):
        forwarded = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith("CDS_")
        }
        forwarded.update(env)
        forwarded["WSLENV"] = _wslenv_for(env)
        return subprocess.run(
            [*shell, *flags, "-s"],
            env=forwarded,
            input=script,
            capture_output=True,
            text=True,
            timeout=max(timeout, 20),
        )
    return subprocess.run(
        [*shell, *flags, "-c", script],
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


class DagsterEntrypointBackendGuardTest(unittest.TestCase):
    """Exercises images/dagster/entrypoint.sh's backend-selection guard directly.

    The guard runs before entrypoint.sh touches any container-only paths (it
    exits or falls through before `cp /app/images/dagster/workspace.yaml ...`),
    so we can execute the real script text under `sh` with a marker appended
    to see whether the guard let execution continue.
    """

    MARKER = "GUARD_PASSED"

    @classmethod
    def setUpClass(cls) -> None:
        cls.shell = posix_shell_or_skip()
        repo_root = Path(__file__).resolve().parent.parent
        entrypoint = (repo_root / "images" / "dagster" / "entrypoint.sh").read_text(encoding="utf-8")
        guard_snippet, _, _ = entrypoint.partition("cp /app/images/dagster/workspace.yaml")
        cls.guard_snippet = guard_snippet

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.dagster_home = self._tmpdir.name

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def run_guard(self, env: dict) -> subprocess.CompletedProcess:
        full_env = {"DAGSTER_HOME": self.dagster_home, **env}
        script = f"{self.guard_snippet}\necho {self.MARKER}\n"
        return run_shell_script(self.shell, script, full_env)

    def test_sqlite_backend_is_allowed_on_postgres_built_image(self) -> None:
        result = self.run_guard({"DB_BACKEND": "sqlite", "DAGSTER_IMAGE_DB_BACKEND": "postgres"})

        self.assertEqual(result.returncode, 0)
        self.assertIn(self.MARKER, result.stdout)

    def test_sqlite_backend_is_allowed_on_sqlite_built_image(self) -> None:
        result = self.run_guard({"DB_BACKEND": "sqlite", "DAGSTER_IMAGE_DB_BACKEND": "sqlite"})

        self.assertEqual(result.returncode, 0)
        self.assertIn(self.MARKER, result.stdout)

    def test_postgres_backend_is_allowed_on_postgres_built_image(self) -> None:
        result = self.run_guard({"DB_BACKEND": "postgres", "DAGSTER_IMAGE_DB_BACKEND": "postgres"})

        self.assertEqual(result.returncode, 0)
        self.assertIn(self.MARKER, result.stdout)

    def test_postgres_backend_is_rejected_on_sqlite_built_image(self) -> None:
        result = self.run_guard({"DB_BACKEND": "postgres", "DAGSTER_IMAGE_DB_BACKEND": "sqlite"})

        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn(self.MARKER, result.stdout)
        self.assertIn("requires an image built with DB_BACKEND=postgres", result.stderr)

    def test_mysql_backend_is_rejected(self) -> None:
        result = self.run_guard({"DB_BACKEND": "mysql", "DAGSTER_IMAGE_DB_BACKEND": "postgres"})

        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn(self.MARKER, result.stdout)
        self.assertIn("MySQL storage is not supported by this Dagster image", result.stderr)

    def test_unsupported_backend_is_rejected(self) -> None:
        result = self.run_guard({"DB_BACKEND": "bogus", "DAGSTER_IMAGE_DB_BACKEND": "postgres"})

        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn(self.MARKER, result.stdout)
        self.assertIn("Unsupported Dagster database backend: bogus", result.stderr)

    def test_backend_is_inferred_from_connection_uri_when_unset(self) -> None:
        result = self.run_guard(
            {
                "DAGSTER_DB_CONNECTION_URI": "sqlite:////tmp/db.sqlite",
                "DAGSTER_IMAGE_DB_BACKEND": "postgres",
            }
        )

        self.assertEqual(result.returncode, 0)
        self.assertIn(self.MARKER, result.stdout)

    def test_sqlite_storage_dir_defaults_under_dagster_home(self) -> None:
        result = self.run_guard({"DB_BACKEND": "sqlite", "DAGSTER_IMAGE_DB_BACKEND": "postgres"})

        self.assertEqual(result.returncode, 0)
        expected_dir = Path(self.dagster_home) / "storage"
        self.assertTrue(expected_dir.is_dir())

    def test_sqlite_storage_dir_respects_explicit_override(self) -> None:
        with tempfile.TemporaryDirectory() as custom_dir:
            override_path = Path(custom_dir) / "custom-sqlite"
            result = self.run_guard(
                {
                    "DB_BACKEND": "sqlite",
                    "DAGSTER_IMAGE_DB_BACKEND": "postgres",
                    "DAGSTER_SQLITE_DIR": str(override_path),
                }
            )

            self.assertEqual(result.returncode, 0)
            self.assertTrue(override_path.is_dir())


class DagsterWorkspaceInstallTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.shell = posix_shell_or_skip()
        if _is_wsl_shell(cls.shell):
            raise unittest.SkipTest(
                "workspace install test needs a native POSIX shell: the WSL "
                "tier translates environment-variable paths but not paths "
                "embedded in the script text, and drvfs does not honor chmod"
            )
        repo_root = Path(__file__).resolve().parent.parent
        entrypoint = (repo_root / "images" / "dagster" / "entrypoint.sh").read_text(encoding="utf-8")
        _, marker, remainder = entrypoint.partition('workspace_tmp="$DAGSTER_HOME/.workspace.yaml.tmp"')
        if not marker:
            raise AssertionError("entrypoint workspace installation block is missing")
        install_body, separator, _ = remainder.partition("python /app/images/dagster/generate_config.py")
        if not separator:
            raise AssertionError("entrypoint workspace installation block has no end marker")
        cls.install_snippet = marker + install_body

    def test_workspace_install_is_restart_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            dagster_home = Path(tmpdir) / "dagster-home"
            dagster_home.mkdir()
            source = Path(tmpdir) / "workspace.yaml"
            source.write_text("load_from: []\n", encoding="utf-8")
            source.chmod(0o444)
            script = self.install_snippet.replace(
                "/app/images/dagster/workspace.yaml",
                shlex.quote(str(source)),
            )

            first = run_shell_script(
                self.shell, script, {"DAGSTER_HOME": str(dagster_home)}, flags=("-eu",)
            )
            second = run_shell_script(
                self.shell, script, {"DAGSTER_HOME": str(dagster_home)}, flags=("-eu",)
            )

            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertEqual(second.returncode, 0, second.stderr)
            destination = dagster_home / "workspace.yaml"
            self.assertEqual(destination.read_text(encoding="utf-8"), "load_from: []\n")
            self.assertTrue(destination.stat().st_mode & 0o200)


class WslShellTierTest(unittest.TestCase):
    """Unit-level coverage for the WSL fallback tier (issue #947).

    These tests mock the platform so they exercise the tier-selection and
    WSLENV plumbing on any OS; the real end-to-end behavior only runs on a
    Windows machine with WSL and no Git for Windows.
    """

    WINDOWS_ENV = {
        "ProgramFiles": "",
        "ProgramFiles(x86)": "",
        "LocalAppData": "",
    }

    def _find_with_mocks(self, probe_result=None, probe_error=None):
        with (
            mock.patch(
                "shutil.which",
                side_effect=lambda name: (
                    r"C:\Windows\System32\wsl.exe" if name == "wsl.exe" else None
                ),
            ),
            mock.patch("sys.platform", "win32"),
            mock.patch("os.environ", dict(self.WINDOWS_ENV)),
            mock.patch("subprocess.run") as probe,
        ):
            if probe_error is not None:
                probe.side_effect = probe_error
            else:
                probe.return_value = probe_result
            return find_posix_shell(), probe

    def test_wsl_tier_used_when_no_sh_and_no_git_for_windows(self) -> None:
        shell, probe = self._find_with_mocks(
            probe_result=subprocess.CompletedProcess([], 0)
        )

        self.assertEqual(shell, [r"C:\Windows\System32\wsl.exe", "-e", "sh"])
        probe.assert_called_once()
        self.assertEqual(probe.call_args.args[0][-2:], ["-c", "exit 0"])

    def test_wsl_tier_rejected_when_probe_fails(self) -> None:
        shell, _ = self._find_with_mocks(
            probe_result=subprocess.CompletedProcess([], 1)
        )

        self.assertEqual(shell, [])

    def test_wsl_tier_rejected_when_probe_times_out(self) -> None:
        shell, _ = self._find_with_mocks(
            probe_error=subprocess.TimeoutExpired(cmd="wsl.exe", timeout=15)
        )

        self.assertEqual(shell, [])

    def test_empty_when_no_shell_of_any_kind_is_available(self) -> None:
        with (
            mock.patch("shutil.which", return_value=None),
            mock.patch("sys.platform", "win32"),
            mock.patch("os.environ", dict(self.WINDOWS_ENV)),
            mock.patch("subprocess.run") as probe,
        ):
            self.assertEqual(find_posix_shell(), [])
        probe.assert_not_called()

    def test_non_windows_platform_never_probes_wsl(self) -> None:
        with (
            mock.patch("shutil.which", return_value=None),
            mock.patch("sys.platform", "linux"),
            mock.patch("subprocess.run") as probe,
        ):
            self.assertEqual(find_posix_shell(), [])
        probe.assert_not_called()

    def test_is_wsl_shell_matches_wsl_prefix_case_insensitively(self) -> None:
        self.assertTrue(
            _is_wsl_shell([r"C:\Windows\System32\WSL.exe", "-e", "sh"])
        )
        self.assertFalse(_is_wsl_shell(["/usr/bin/sh"]))
        self.assertFalse(_is_wsl_shell([r"C:\Program Files\Git\bin\sh.exe"]))
        self.assertFalse(_is_wsl_shell([]))

    def test_wslenv_forwards_and_translates_path_vars_sorted(self) -> None:
        env = {
            "DB_BACKEND": "sqlite",
            "DAGSTER_HOME": r"C:\t",
            "DAGSTER_SQLITE_DIR": r"C:\s",
            "DB": "x",
        }

        self.assertEqual(
            _wslenv_for(env),
            "DAGSTER_HOME/p:DAGSTER_SQLITE_DIR/p:DB:DB_BACKEND",
        )

    def test_run_shell_script_wsl_mode_feeds_stdin_and_sets_wslenv(self) -> None:
        shell = [r"C:\Windows\System32\wsl.exe", "-e", "sh"]
        with (
            mock.patch(
                "os.environ",
                {"SystemRoot": r"C:\Windows", "CDS_PROFILE_PATH": "leak"},
            ),
            mock.patch("subprocess.run") as run,
        ):
            run_shell_script(
                shell,
                "echo hi\n",
                {"DAGSTER_HOME": r"C:\tmp", "DB_BACKEND": "sqlite"},
            )

        run.assert_called_once()
        self.assertEqual(
            run.call_args.args[0], [r"C:\Windows\System32\wsl.exe", "-e", "sh", "-s"]
        )
        kwargs = run.call_args.kwargs
        self.assertEqual(kwargs["input"], "echo hi\n")
        self.assertEqual(kwargs["env"]["DAGSTER_HOME"], r"C:\tmp")
        self.assertEqual(kwargs["env"]["WSLENV"], "DAGSTER_HOME/p:DB_BACKEND")
        self.assertNotIn("CDS_PROFILE_PATH", kwargs["env"])
        self.assertEqual(kwargs["env"]["SystemRoot"], r"C:\Windows")
        self.assertGreaterEqual(kwargs["timeout"], 20)

    def test_run_shell_script_native_mode_is_unchanged(self) -> None:
        with mock.patch("subprocess.run") as run:
            run_shell_script(
                ["/usr/bin/sh"], "echo hi", {"A": "b"}, flags=("-eu",), timeout=5
            )

        run.assert_called_once()
        self.assertEqual(
            run.call_args.args[0], ["/usr/bin/sh", "-eu", "-c", "echo hi"]
        )
        kwargs = run.call_args.kwargs
        self.assertEqual(kwargs["env"], {"A": "b"})
        self.assertEqual(kwargs["timeout"], 5)
        self.assertNotIn("input", kwargs)


if __name__ == "__main__":
    unittest.main()
