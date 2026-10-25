"""End-to-end coverage for the team-dagster-postgres-superset profile.

The profile composes the stable example profile via `extends`, so unlike
the other profile tests these also exercise extends resolution, the
generated .env (including the required cache secret), and the prod
environment overlay through the real CLI.
"""

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml

from tests.hermetic_project import find_cds, hermetic_env, init_env, make_project

_REPO_ROOT = Path(__file__).resolve().parent.parent
_PROFILE_NAME = "team-dagster-postgres-superset"
_PARENT_PROFILE_NAME = "local-dagster-postgres-superset"


class TeamProfileWorkflowTest(unittest.TestCase):
    """Hermetic CLI round trip: init, validate, render (base and prod)."""

    @classmethod
    def setUpClass(cls):
        cls.cds = find_cds()
        cls._project_tmp = tempfile.TemporaryDirectory()
        cls.project = Path(cls._project_tmp.name)
        cls.profile_file = str(make_project(cls.project, _PROFILE_NAME))
        # The child profile extends the stable example profile, so the
        # parent must be present in the project like any other dependency.
        shutil.copytree(
            _REPO_ROOT / "profiles" / _PARENT_PROFILE_NAME,
            cls.project / "profiles" / _PARENT_PROFILE_NAME,
            ignore=shutil.ignore_patterns("__pycache__"),
        )
        # Traefik bind-mounts these from the project root; a real
        # deployment creates them the same way (see the profile README).
        (cls.project / "traefik" / "dynamic").mkdir(parents=True)
        (cls.project / "certs" / "traefik").mkdir(parents=True)
        shutil.copy(
            _REPO_ROOT / "traefik" / "dynamic" / "team-dagster-postgres-superset.yml",
            cls.project / "traefik" / "dynamic" / "team-dagster-postgres-superset.yml",
        )
        # The team host runs the prod overlay, so the .env must be generated
        # with --environment prod: the overlay declares an extra required
        # secret (the backup encryption key) that a base-only .env lacks.
        init_env(cls.cds, cls.project, _PROFILE_NAME, environment="prod")
        cls._render_tmp = tempfile.TemporaryDirectory()

    @classmethod
    def tearDownClass(cls):
        cls._render_tmp.cleanup()
        cls._project_tmp.cleanup()

    def _run(self, *args, environment=None):
        cmd = list(args)
        if environment:
            cmd += ["--environment", environment]
        return subprocess.run(
            self.cds + cmd,
            cwd=str(self.project),
            capture_output=True,
            text=True,
            env=hermetic_env(),
        )

    def _compose_for(self, environment):
        render_output = str(Path(self._render_tmp.name) / f"compose-{environment or 'base'}.yml")
        render = self._run("render", _PROFILE_NAME, "--output", render_output, environment=environment)
        self.assertEqual(render.returncode, 0, f"environment={environment}: {render.stdout}{render.stderr}")
        rendered = Path(render_output).read_text(encoding="utf-8")
        return yaml.safe_load(rendered), rendered

    def test_init_writes_required_cache_and_backup_secrets(self):
        env_file = self.project / ".env"
        text = env_file.read_text(encoding="utf-8")
        self.assertIn("CDS_CACHE_PASSWORD=", text)
        self.assertIn("CDS_BACKUP_ENCRYPTION_KEY=", text)
        if os.name != "nt":
            self.assertEqual(env_file.stat().st_mode & 0o777, 0o600)

    def test_validate_and_render_base_and_prod(self):
        for environment in (None, "prod"):
            subTest = f"environment={environment or 'base'}"

            validate = self._run("validate", _PROFILE_NAME, environment=environment)
            self.assertEqual(validate.returncode, 0, f"{subTest}: {validate.stdout}{validate.stderr}")

            compose, rendered = self._compose_for(environment)
            services = compose["services"]

            # The team additions are present in both environments.
            self.assertIn("traefik", services)
            traefik_ports = services["traefik"]["ports"]
            self.assertIn(":80:80", traefik_ports)
            self.assertIn(":443:443", traefik_ports)

            keydb = services["keydb"]
            self.assertEqual(keydb["environment"]["KEYDB_PASSWORD"], "${CDS_CACHE_PASSWORD}")
            self.assertIn(
                "--requirepass",
                " ".join(str(part) for part in keydb["command"]),
            )

            superset_url = services["superset"]["environment"]["SUPERSET__REDIS_URL"]
            self.assertEqual(superset_url, "redis://:${CDS_CACHE_PASSWORD}@keydb:6379")

            postgres_args = services["postgres"]["environment"]["POSTGRES_INITDB_ARGS"]
            self.assertIn("--auth-host=scram-sha-256", postgres_args)

            # No generated default ("change-me") or real secret value is
            # embedded in the rendered output; only placeholders are.
            self.assertNotIn("change-me", rendered)
            self.assertIn("${CDS_CACHE_PASSWORD}", rendered)


if __name__ == "__main__":
    unittest.main()
