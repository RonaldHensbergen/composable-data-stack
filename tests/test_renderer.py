import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import yaml

from cli.renderer import render_compose

class RendererRegressionTest(unittest.TestCase):
    def test_render_compose_emits_env_placeholders_for_secret_refs(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            env_file = root / ".env"
            env_file.write_text("CDS_DB_PASSWORD=supersecret\n", encoding="utf-8")

            plan = {
                "metadata": {"name": "cds-test"},
                "secrets": {
                    "CDS_DB_PASSWORD": "CDS_DB_PASSWORD",
                },
                "modules": [
                    {
                        "id": "db",
                        "implementation": {
                            "kind": "docker-compose",
                            "compose": {
                                "services": {
                                    "postgres": {
                                        "image": "postgres:latest",
                                        "environment": {
                                            "POSTGRES_PASSWORD": "${secrets.CDS_DB_PASSWORD}",
                                        },
                                    }
                                }
                            },
                        },
                    }
                ],
            }

            output, diagnostics = render_compose(plan, env_file=str(env_file))

            self.assertEqual(len([d for d in diagnostics if d.level == "error"]), 0)
            compose = yaml.safe_load(output)
            self.assertIn("db-postgres", compose["services"])
            self.assertEqual(
                compose["services"]["db-postgres"]["environment"]["POSTGRES_PASSWORD"],
                "${CDS_DB_PASSWORD}",
            )
            self.assertNotIn("supersecret", output)

    def test_render_compose_alias_secret_leak_regression(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            env_file = root / ".env"
            env_file.write_text("CDS_REAL_DB_PASSWORD=my_actual_secret\n", encoding="utf-8")

            plan = {
                "metadata": {"name": "cds-alias-test"},
                "secrets": {
                    "DB_PASS_ALIAS": "CDS_REAL_DB_PASSWORD",
                },
                "modules": [
                    {
                        "id": "db",
                        "implementation": {
                            "kind": "docker-compose",
                            "compose": {
                                "services": {
                                    "postgres": {
                                        "image": "postgres:latest",
                                        "environment": {
                                            "POSTGRES_PASSWORD": "${secrets.DB_PASS_ALIAS}",
                                        },
                                    }
                                }
                            },
                        },
                    }
                ],
            }

            output, diagnostics = render_compose(plan, env_file=str(env_file))

            self.assertEqual(len([d for d in diagnostics if d.level == "error"]), 0)
            compose = yaml.safe_load(output)
            self.assertEqual(
                compose["services"]["db-postgres"]["environment"]["POSTGRES_PASSWORD"],
                "${CDS_REAL_DB_PASSWORD}",
            )
            self.assertNotIn("my_actual_secret", output)

    def test_render_compose_rewrites_build_contexts_for_output_location(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "pyproject.toml").write_text("[project]\nname='tmp'\nversion='0.0.0'\n", encoding="utf-8")
            (root / "profiles" / "local").mkdir(parents=True)
            (root / "modules" / "orchestration" / "dagster").mkdir(parents=True)
            (root / "images" / "dagster").mkdir(parents=True)
            (root / "images" / "dagster" / "Dockerfile").write_text("FROM python:3.11\n", encoding="utf-8")

            plan = {
                "metadata": {"name": "cds-test"},
                "sourceProfile": str(root / "profiles" / "local" / "profile.yaml"),
                "modules": [
                    {
                        "id": "dagster",
                        "source": "../../modules/orchestration/dagster",
                        "implementation": {
                            "kind": "docker-compose",
                            "compose": {
                                "services": {
                                    "web": {
                                        "build": {
                                            "context": "../../../images/dagster",
                                            "dockerfile": "Dockerfile",
                                        }
                                    },
                                    "daemon": {
                                        "build": {
                                            "context": "../images/dagster",
                                            "dockerfile": "Dockerfile",
                                        }
                                    },
                                }
                            },
                        },
                    }
                ],
            }

            output, diagnostics = render_compose(plan, output_path=str(root / "docker-compose.yml"))

            self.assertEqual(len([d for d in diagnostics if d.level == "error"]), 0)
            compose = yaml.safe_load(output)
            self.assertEqual(compose["services"]["dagster-web"]["build"]["context"], "images/dagster")
            self.assertEqual(compose["services"]["dagster-daemon"]["build"]["context"], "images/dagster")

    def test_render_compose_rewrites_build_contexts_for_nested_output_path(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "pyproject.toml").write_text("[project]\nname='tmp'\nversion='0.0.0'\n", encoding="utf-8")
            (root / "profiles" / "local").mkdir(parents=True)
            (root / "modules" / "orchestration" / "dagster").mkdir(parents=True)
            (root / "images" / "dagster").mkdir(parents=True)
            (root / "images" / "dagster" / "Dockerfile").write_text("FROM python:3.11\n", encoding="utf-8")

            nested_output = root / "build" / "output" / "docker-compose.yml"

            plan = {
                "metadata": {"name": "cds-test"},
                "sourceProfile": str(root / "profiles" / "local" / "profile.yaml"),
                "modules": [
                    {
                        "id": "dagster",
                        "source": "../../modules/orchestration/dagster",
                        "implementation": {
                            "kind": "docker-compose",
                            "compose": {
                                "services": {
                                    "web": {
                                        "build": {
                                            "context": "../../../images/dagster",
                                            "dockerfile": "Dockerfile",
                                        }
                                    },
                                    "daemon": {
                                        "build": {
                                            "context": "../images/dagster",
                                            "dockerfile": "Dockerfile",
                                        }
                                    },
                                }
                            },
                        },
                    }
                ],
            }

            output, diagnostics = render_compose(plan, output_path=str(nested_output))

            self.assertEqual(len([d for d in diagnostics if d.level == "error"]), 0)
            compose = yaml.safe_load(output)
            self.assertEqual(compose["services"]["dagster-web"]["build"]["context"], "../../images/dagster")
            self.assertEqual(compose["services"]["dagster-daemon"]["build"]["context"], "../../images/dagster")

    def test_render_compose_falls_back_to_absolute_context_on_cross_drive_relpath(self):
        """Regression test for a Windows-only bug: os.path.relpath raises
        ValueError when the build context and the compose output directory
        are on different drives (e.g. C:\\ vs D:\\), which happens on
        GitHub Actions Windows runners (repo checked out to D:\\, temp dirs
        on C:\\). No relative path can express a cross-drive location, so
        _resolve_context_path must fall back to an absolute path instead of
        crashing. This can't be reproduced with real paths on Linux/macOS
        (no drive letters), so os.path.relpath is mocked to simulate it.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "pyproject.toml").write_text("[project]\nname='tmp'\nversion='0.0.0'\n", encoding="utf-8")
            (root / "profiles" / "local").mkdir(parents=True)
            (root / "modules" / "orchestration" / "dagster").mkdir(parents=True)
            (root / "images" / "dagster").mkdir(parents=True)
            (root / "images" / "dagster" / "Dockerfile").write_text("FROM python:3.11\n", encoding="utf-8")

            nested_output = root / "build" / "output" / "docker-compose.yml"

            plan = {
                "metadata": {"name": "cds-test"},
                "sourceProfile": str(root / "profiles" / "local" / "profile.yaml"),
                "modules": [
                    {
                        "id": "dagster",
                        "source": "../../modules/orchestration/dagster",
                        "implementation": {
                            "kind": "docker-compose",
                            "compose": {
                                "services": {
                                    "web": {
                                        "build": {
                                            "context": "../../../images/dagster",
                                            "dockerfile": "Dockerfile",
                                        }
                                    },
                                }
                            },
                        },
                    }
                ],
            }

            real_relpath = os.path.relpath

            def _relpath_simulating_cross_drive(path, start=None):
                if "images" in str(path):
                    raise ValueError("path is on mount 'D:', start on mount 'C:'")
                return real_relpath(path, start)

            with mock.patch("os.path.relpath", side_effect=_relpath_simulating_cross_drive):
                output, diagnostics = render_compose(plan, output_path=str(nested_output))

            self.assertEqual(len([d for d in diagnostics if d.level == "error"]), 0)
            compose = yaml.safe_load(output)
            context = compose["services"]["dagster-web"]["build"]["context"]
            # Must fall back to an absolute POSIX-style path instead of
            # crashing with the cross-drive ValueError.
            self.assertTrue(Path(context).is_absolute() or context.startswith("/"))
            self.assertTrue(context.endswith("images/dagster"))

def test_render_compose_falls_back_to_absolute_volume_source_on_cross_drive_relpath(self):
        """Regression test for the same Windows-only cross-drive bug as
        above, but in _rewrite_local_path (used for bind-mount volume
        sources like init-db.sql), a separate function from
        _resolve_context_path. Both independently call os.path.relpath and
        both needed the fix.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "pyproject.toml").write_text("[project]\nname='tmp'\nversion='0.0.0'\n", encoding="utf-8")
            (root / "profiles" / "local").mkdir(parents=True)
            (root / "modules" / "warehouse" / "postgres").mkdir(parents=True)
            (root / "modules" / "warehouse" / "postgres" / "init-db.sql").write_text(
                "CREATE DATABASE app;\n", encoding="utf-8"
            )

            nested_output = root / "build" / "output" / "docker-compose.yml"

            plan = {
                "metadata": {"name": "cds-test"},
                "sourceProfile": str(root / "profiles" / "local" / "profile.yaml"),
                "modules": [
                    {
                        "id": "postgres",
                        "source": "../../modules/warehouse/postgres",
                        "implementation": {
                            "kind": "docker-compose",
                            "compose": {
                                "services": {
                                    "db": {
                                        "image": "postgres:16",
                                        "volumes": [
                                            {
                                                "type": "bind",
                                                "source": "init-db.sql",
                                                "target": "/docker-entrypoint-initdb.d/init-db.sql",
                                            }
                                        ],
                                    },
                                }
                            },
                        },
                    }
                ],
            }

            real_relpath = os.path.relpath

            def _relpath_simulating_cross_drive(path, start=None):
                if "init-db.sql" in str(path) or "postgres" in str(path):
                    raise ValueError("path is on mount 'D:', start on mount 'C:'")
                return real_relpath(path, start)

            with mock.patch("os.path.relpath", side_effect=_relpath_simulating_cross_drive):
                output, diagnostics = render_compose(plan, output_path=str(nested_output))

            self.assertEqual(len([d for d in diagnostics if d.level == "error"]), 0)
            compose = yaml.safe_load(output)
            source = compose["services"]["postgres-db"]["volumes"][0]["source"]
            # Must fall back to an absolute POSIX-style path instead of
            # crashing with the cross-drive ValueError.
            self.assertTrue(Path(source).is_absolute() or source.startswith("/"))
            self.assertTrue(source.endswith("init-db.sql"))

if __name__ == "__main__":
    unittest.main()
