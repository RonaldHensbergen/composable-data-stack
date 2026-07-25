import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import yaml

from cli.renderer import render_compose

class RendererRegressionTest(unittest.TestCase):
    def test_render_compose_namespaces_long_form_named_volume(self):
        plan = {
            "metadata": {"name": "cds-test"},
            "modules": [
                {
                    "id": "worker",
                    "implementation": {
                        "kind": "docker-compose",
                        "compose": {
                            "services": {
                                "api": {
                                    "image": "worker:latest",
                                    "volumes": [
                                        {
                                            "type": "volume",
                                            "source": "runtime-socket",
                                            "target": "/var/run/worker",
                                            "read_only": True,
                                        }
                                    ],
                                }
                            },
                            "volumes": {"runtime-socket": {}},
                        },
                    },
                }
            ],
        }

        output, diagnostics = render_compose(plan)

        self.assertEqual(len([d for d in diagnostics if d.level == "error"]), 0)
        compose = yaml.safe_load(output)
        self.assertIn("worker-runtime-socket", compose["volumes"])
        mount = compose["services"]["worker-api"]["volumes"][0]
        self.assertEqual(mount["source"], "worker-runtime-socket")
        self.assertTrue(mount["read_only"])

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

    def test_render_compose_preserves_repo_relative_paths_for_external_output_path(self):
        with tempfile.TemporaryDirectory() as tmpdir, tempfile.TemporaryDirectory() as output_tmpdir:
            root = Path(tmpdir)
            external_output = Path(output_tmpdir) / "docker-compose.yml"
            (root / "pyproject.toml").write_text("[project]\nname='tmp'\nversion='0.0.0'\n", encoding="utf-8")
            (root / "profiles" / "local").mkdir(parents=True)
            (root / "modules" / "orchestration" / "dagster").mkdir(parents=True)
            (root / "images" / "dagster").mkdir(parents=True)
            (root / "images" / "dagster" / "Dockerfile").write_text("FROM python:3.11\n", encoding="utf-8")
            (root / "profiles" / "local" / "data.txt").write_text("data\n", encoding="utf-8")

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
                                        },
                                        "volumes": [
                                            {
                                                "type": "bind",
                                                "source": "../../profiles/local/data.txt",
                                                "target": "/app/data.txt",
                                            }
                                        ],
                                    }
                                }
                            },
                        },
                    }
                ],
            }

            output, diagnostics = render_compose(plan, output_path=str(external_output))

            self.assertEqual(len([d for d in diagnostics if d.level == "error"]), 0)
            compose = yaml.safe_load(output)
            self.assertEqual(compose["services"]["dagster-web"]["build"]["context"], "images/dagster")
            self.assertEqual(compose["services"]["dagster-web"]["volumes"][0]["source"], "profiles/local/data.txt")

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

    def test_render_compose_rejects_module_source_traversal_for_volume_local_path(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            profile_dir = root / "profiles" / "local"
            outside_zone = root / "outside_zone"
            profile_dir.mkdir(parents=True)
            outside_zone.mkdir()
            (outside_zone / "payload.txt").write_text("exfiltrated", encoding="utf-8")
            profile_file = profile_dir / "profile.yaml"
            profile_file.write_text("placeholder", encoding="utf-8")

            plan = {
                "sourceProfile": str(profile_file),
                "metadata": {"name": "cds-test"},
                "secrets": {},
                "modules": [
                    {
                        "id": "evil",
                        "source": "modules/../../../outside_zone",
                        "implementation": {
                            "kind": "docker-compose",
                            "compose": {
                                "services": {
                                    "evil-svc": {
                                        "image": "alpine",
                                        "volumes": ["./payload.txt:/payload.txt"],
                                    }
                                }
                            },
                        },
                    }
                ],
            }

            output_path = str(root / "out.yml")
            compose_yaml, diagnostics = render_compose(plan, output_path=output_path)
            compose = yaml.safe_load(compose_yaml)
            rewritten_source = compose["services"]["evil-svc"]["volumes"][0].split(":")[0]

            resolved = (root / rewritten_source).resolve()
            self.assertFalse(str(resolved).startswith(str(outside_zone.resolve())))

    def test_render_compose_rejects_module_source_traversal_for_build_context(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            profile_dir = root / "profiles" / "local"
            outside_zone = root / "outside_zone"
            profile_dir.mkdir(parents=True)
            (outside_zone / "buildctx").mkdir(parents=True)
            (outside_zone / "buildctx" / "Dockerfile").write_text("FROM scratch", encoding="utf-8")
            profile_file = profile_dir / "profile.yaml"
            profile_file.write_text("placeholder", encoding="utf-8")

            plan = {
                "sourceProfile": str(profile_file),
                "metadata": {"name": "cds-test"},
                "secrets": {},
                "modules": [
                    {
                        "id": "evil",
                        "source": "modules/../../../outside_zone",
                        "implementation": {
                            "kind": "docker-compose",
                            "compose": {
                                "services": {
                                    "evil-svc": {
                                        "build": {"context": "./buildctx"},
                                    }
                                }
                            },
                        },
                    }
                ],
            }

            output_path = str(root / "out.yml")
            compose_yaml, diagnostics = render_compose(plan, output_path=output_path)
            compose = yaml.safe_load(compose_yaml)
            rewritten_context = compose["services"]["evil-svc"]["build"]["context"]

            resolved = (root / rewritten_context).resolve()
            self.assertFalse(str(resolved).startswith(str(outside_zone.resolve())))

    def test_render_compose_uses_cds_module_path_for_module_local_path_base(self):
        # Positive case: CDS_MODULE_PATH-relative resolution (previously
        # ignored entirely by _resolve_module_dir) must still work so a
        # module's own local files remain usable as a volume base.
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            profile_dir = root / "profiles" / "local"
            modules_root = root / "modules"
            module_dir = modules_root / "warehouse" / "postgres"
            profile_dir.mkdir(parents=True)
            module_dir.mkdir(parents=True)
            (module_dir / "init-db.sql").write_text("-- init", encoding="utf-8")
            profile_file = profile_dir / "profile.yaml"
            profile_file.write_text("placeholder", encoding="utf-8")

            plan = {
                "sourceProfile": str(profile_file),
                "metadata": {"name": "cds-test"},
                "secrets": {},
                "modules": [
                    {
                        "id": "postgres",
                        "source": "warehouse/postgres",
                        "implementation": {
                            "kind": "docker-compose",
                            "compose": {
                                "services": {
                                    "postgres-db": {
                                        "image": "postgres",
                                        "volumes": ["./init-db.sql:/docker-entrypoint-initdb.d/init.sql"],
                                    }
                                }
                            },
                        },
                    }
                ],
            }

            output_path = str(root / "out.yml")
            with mock.patch.dict("os.environ", {"CDS_MODULE_PATH": str(modules_root)}, clear=False):
                compose_yaml, diagnostics = render_compose(plan, output_path=output_path)

            compose = yaml.safe_load(compose_yaml)
            rewritten_source = compose["services"]["postgres-db"]["volumes"][0].split(":")[0]
            resolved = (root / rewritten_source).resolve()
            self.assertEqual(resolved, (module_dir / "init-db.sql").resolve())

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
