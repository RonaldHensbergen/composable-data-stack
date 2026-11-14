"""Helpers for tests that must not depend on developer-local state.

`cds` loads `.env` from the working directory and resolves the project root
from it, so tests that run the CLI against the sample profile use a temporary
project copy plus a sanitized environment instead of the repository checkout.
In-process `cli.main.main()` calls use `HermeticMainTestCase` instead.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import cli.main as cli_main

REPO_ROOT = Path(__file__).resolve().parent.parent

_PROJECT_DIRS = ("modules", "shared", "images", "workdirs")


def find_cds() -> list[str]:
    venv_cds = (
        REPO_ROOT
        / ".venv"
        / ("Scripts/cds.exe" if sys.platform == "win32" else "bin/cds")
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


def init_env(
    cds: list[str], root: Path, profile_name: str, environment: str | None = None
) -> None:
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


class HermeticMainTestCase(unittest.TestCase):
    """Base for test cases that invoke ``cli.main.main()`` in-process.

    ``main()`` reads project-local state anchored at the current working
    directory: every ``CDS_*`` key from ``./.env`` (``load_env_file``) and the
    saved per-project config at ``<project root>/.cds/config.json``
    (``get_config_path`` -- e.g. a saved ``"target": "helm"``, which turns
    ``cds state``/``cds up`` into ``kubectl`` calls). On a developer checkout
    that has those files, that state leaks into the tests and the suite fails
    locally while CI (clean checkout) stays green (issue #1277).

    This base points ``CDS_CONFIG_PATH``/``CDS_SHARED_CONFIG_PATH`` at
    nonexistent files in a per-test temp dir and disables ``.env`` loading, so
    ``main()`` sees no saved project state. Tests that need a specific saved
    config set ``CDS_CONFIG_PATH`` themselves; their ``patch.dict`` is nested
    inside this one and wins while it is active.
    """

    def setUp(self) -> None:
        super().setUp()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        env_patch = patch.dict(
            os.environ,
            {
                "CDS_CONFIG_PATH": str(root / "config.json"),
                "CDS_SHARED_CONFIG_PATH": str(root / "cds.shared.json"),
            },
        )
        env_patch.start()
        self.addCleanup(env_patch.stop)
        # main() mutates os.environ with every CDS_* key from ./.env; stop
        # that so a project-local .env cannot leak into the test process.
        env_file_patch = patch.object(cli_main, "load_env_file")
        env_file_patch.start()
        self.addCleanup(env_file_patch.stop)
