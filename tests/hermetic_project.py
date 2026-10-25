"""Helpers for subprocess tests that must not depend on developer-local state.

`cds` loads `.env` from the working directory and resolves the project root
from it, so tests that run the CLI against the sample profile use a temporary
project copy plus a sanitized environment instead of the repository checkout.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

_PROJECT_DIRS = ("modules", "shared", "images", "workdirs")


def find_cds() -> list[str]:
    venv_cds = REPO_ROOT / ".venv" / (
        "Scripts/cds.exe" if sys.platform == "win32" else "bin/cds"
    )
    if venv_cds.exists():
        return [str(venv_cds)]
    return [sys.executable, "-m", "cli.main"]


def hermetic_env(extra: dict | None = None) -> dict:
    """Host environment without CDS_* variables, able to import `cli`."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("CDS_")}
    env["PYTHONPATH"] = os.pathsep.join(
        filter(None, [str(REPO_ROOT), env.get("PYTHONPATH")])
    )
    if extra:
        env.update(extra)
    return env


def make_project(root: Path, profile_name: str) -> Path:
    """Copy one profile and the module tree into `root`; return profile.yaml."""
    (root / "pyproject.toml").write_text("[project]\nname = 'hermetic'\n")
    for name in _PROJECT_DIRS:
        source = REPO_ROOT / name
        if source.exists():
            shutil.copytree(
                source, root / name, ignore=shutil.ignore_patterns("__pycache__")
            )
    shutil.copytree(
        REPO_ROOT / "profiles" / profile_name, root / "profiles" / profile_name
    )
    return root / "profiles" / profile_name / "profile.yaml"


def init_env(cds: list[str], root: Path, profile_name: str, environment: str | None = None) -> None:
    """Generate `<root>/.env` for the profile with a sanitized environment.

    Pass `environment` to generate against an environment overlay (e.g.
    "prod"), which may declare additional required secrets a base-only .env
    lacks.
    """
    command = ["init", profile_name]
    if environment:
        command += ["--environment", environment]
    result = subprocess.run(
        cds + command,
        cwd=str(root),
        capture_output=True,
        text=True,
        env=hermetic_env(),
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"cds init failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )
