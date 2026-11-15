"""Helpers for tests that must not depend on developer-local state.

`cds` loads `.env` from the working directory and resolves the project root
from it, so tests that run the CLI against the sample profile use a temporary
project copy plus a sanitized environment instead of the repository checkout.
In-process `cli.main.main()` calls use `HermeticMainTestCase` instead.
Both also disable the audit log (`CDS_AUDIT_LOG_DISABLE`), so no test run
appends entries to the checkout's own `.cds/audit-log.jsonl` (#1312).
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
    """Host environment without CDS_* variables, able to import `cli`.

    The audit log is disabled too: a `cds` subprocess that resolves a
    repo-anchored profile (e.g. a fixture under tests/) would otherwise append
    entries to the checkout's own .cds/audit-log.jsonl (#1312). Pass
    ``extra={"CDS_AUDIT_LOG_DISABLE": "false"}`` to override.
    """
    env = {k: v for k, v in os.environ.items() if not k.startswith("CDS_")}
    env["PYTHONPATH"] = os.pathsep.join(
        filter(None, [str(REPO_ROOT), env.get("PYTHONPATH")])
    )
    env["CDS_AUDIT_LOG_DISABLE"] = "true"
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

    The write side is neutralized too: ``main()`` appends audit-trail entries
    to ``<profile project root>/.cds/audit-log.jsonl``, which for tests that
    resolve repo profiles is the checkout itself, so ``CDS_AUDIT_LOG_DISABLE``
    is set by default (issue #1312). Classes that test audit behavior opt out
    by setting ``audit_log_enabled = True``.
    """

    # Opt out of the default audit-log suppression (see class docstring).
    audit_log_enabled = False

    def setUp(self) -> None:
        super().setUp()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        env = {
            "CDS_CONFIG_PATH": str(root / "config.json"),
            "CDS_SHARED_CONFIG_PATH": str(root / "cds.shared.json"),
        }
        if not self.audit_log_enabled:
            env["CDS_AUDIT_LOG_DISABLE"] = "true"
        env_patch = patch.dict(os.environ, env)
        env_patch.start()
        self.addCleanup(env_patch.stop)
        if self.audit_log_enabled:
            # Drop an inherited disable flag so the outer environment cannot
            # leak into audit tests; patch.dict restores os.environ on stop.
            os.environ.pop("CDS_AUDIT_LOG_DISABLE", None)
        # main() mutates os.environ with every CDS_* key from ./.env; stop
        # that so a project-local .env cannot leak into the test process.
        env_file_patch = patch.object(cli_main, "load_env_file")
        env_file_patch.start()
        self.addCleanup(env_file_patch.stop)
