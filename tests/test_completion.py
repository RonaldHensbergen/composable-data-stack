"""
Cross-platform tests for `cds` tab-completion.

These exercise the actual argcomplete completion protocol (not just the text
printed by `cds completion <shell>`), using the tempfile-based IPC mechanism
that argcomplete supports uniformly on Linux, macOS, and Windows (it is the
same mechanism used by the PowerShell integration, see
`register-python-argcomplete --shell powershell`). Driving the protocol
directly via subprocess env vars means these tests don't depend on bash,
zsh, or pwsh being installed on the host, so they run unconditionally on all
three OSes in CI.
"""

import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_PROFILES_ROOT = _REPO_ROOT / "profiles"

_ARGCOMPLETE_AVAILABLE = importlib.util.find_spec("argcomplete") is not None


def _find_cds() -> list[str]:
    """Return the command list to invoke the cds CLI (mirrors test_cds_workflow.py)."""
    venv_dir = _REPO_ROOT / ".venv"
    if sys.platform == "win32":
        venv_cds = venv_dir / "Scripts" / "cds.exe"
    else:
        venv_cds = venv_dir / "bin" / "cds"

    if venv_cds.exists():
        return [str(venv_cds)]
    return [sys.executable, "-m", "cli.main"]


def _complete(comp_line: str) -> list[str]:
    """Drive the argcomplete tempfile protocol against a real `cds` subprocess.

    Returns the list of completion candidates cds would offer for `comp_line`.
    """
    env = os.environ.copy()
    env["CDS_PROFILE_PATH"] = str(_PROFILES_ROOT)
    env.pop("CDS_CONFIG_PATH", None)

    with tempfile.NamedTemporaryFile(delete=False) as tmp:
        stdout_filename = tmp.name

    env.update(
        {
            "_ARGCOMPLETE": "1",
            "ARGCOMPLETE_USE_TEMPFILES": "1",
            "_ARGCOMPLETE_STDOUT_FILENAME": stdout_filename,
            "COMP_LINE": comp_line,
            "COMP_POINT": str(len(comp_line)),
            "COMP_TYPE": "9",
            "_ARGCOMPLETE_COMP_WORDBREAKS": " ",
            "_ARGCOMPLETE_SUPPRESS_SPACE": "0",
            "_ARGCOMPLETE_IFS": "\n",
        }
    )

    try:
        subprocess.run(
            _find_cds(),
            env=env,
            cwd=str(_REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=30,
        )
        content = Path(stdout_filename).read_text(encoding="utf-8")
    finally:
        os.unlink(stdout_filename)

    return [candidate.strip() for candidate in content.split("\n") if candidate.strip()]


@unittest.skipUnless(_ARGCOMPLETE_AVAILABLE, "argcomplete is not installed")
class ShellCompletionProtocolTest(unittest.TestCase):
    """Verifies the completion protocol itself, independent of any specific shell."""

    def test_completes_profile_names_for_validate(self):
        candidates = _complete("cds validate ")
        self.assertIn("local-dagster-postgres-superset", candidates)
        self.assertIn("local-dagster-postgres-superset-vault", candidates)

    def test_completes_profile_names_filtered_by_prefix(self):
        candidates = _complete("cds validate local-dagster-postgres-superset-v")
        self.assertEqual(candidates, ["local-dagster-postgres-superset-vault"])

    def test_completes_profile_names_for_use_command(self):
        candidates = _complete("cds use ")
        self.assertIn("local-dagster-postgres-superset", candidates)
        self.assertIn("local-dagster-postgres-superset-vault", candidates)

    def test_completes_subcommand_names(self):
        candidates = _complete("cds val")
        self.assertIn("validate", candidates)

    def test_completes_list_subcommands(self):
        candidates = _complete("cds list ")
        self.assertIn("profiles", candidates)
        self.assertIn("modules", candidates)
        self.assertIn("images", candidates)


if __name__ == "__main__":
    unittest.main()
