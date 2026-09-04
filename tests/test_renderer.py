import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import yaml

from cli.renderer import render_compose
from cli.validator import has_errors


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

    def test_render_compose_flags_unresolved_binding_expression_as_error(self):
        """A module template that unconditionally references an optional,
        unbound contract (e.g. ${bindings.cache-service.connectionUri} with
        no cache-service module bound) must surface as an E071 error
        instead of silently shipping the literal placeholder text in the
        rendered Compose file."""
        plan = {
            "metadata": {"name": "cds-test"},
            "modules": [
                {
                    "id": "web",
                    "implementation": {
                        "kind": "docker-compose",
                        "compose": {
                            "services": {
                                "app": {
                                    "image": "web:latest",
                                    "environment": {
                                        "REDIS_URL": "${bindings.cache-service.connectionUri}",
                                    },
                                }
                            }
                        },
                    },
                }
            ],
        }

        output, diagnostics = render_compose(plan)

        errors = [d for d in diagnostics if d.level == "error"]
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0].code, "E071")
        self.assertIn("bindings.cache-service.connectionUri", errors[0].message)
        # The broken placeholder is still present in the output (render_compose
        # does not fail closed), but the caller can now detect it via has_errors().
        self.assertIn("${bindings.cache-service.connectionUri}", output)

    def test_render_compose_rejects_typed_pure_substitution_in_command_field(self):
        """A module template that uses a pure ${config.*} substitution in the
        `command` field position must not splice a profile-supplied list
        verbatim into the rendered Compose output: this would let an
        untrusted profile inject arbitrary command-line arguments through
        module config (GHSA-gmc4-jw3j-mqcf)."""
        plan = {
            "metadata": {"name": "cds-test"},
            "modules": [
                {
                    "id": "evil2",
                    "config": {
                        "cmd": ["/bin/sh", "-c", "cat /etc/shadow > /host/tmp/shadow.txt"],
                    },
                    "implementation": {
                        "kind": "docker-compose",
                        "compose": {
                            "services": {
                                "app": {
                                    "image": "alpine:3.19",
                                    "command": "${config.cmd}",
                                }
                            }
                        },
                    },
                }
            ],
        }

        output, diagnostics = render_compose(plan)

        errors = [d for d in diagnostics if d.level == "error"]
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0].code, "E072")
        self.assertIn("command", errors[0].message)
        # render_compose does not fail closed (same convention as E071); the
        # caller is expected to check has_errors() and abort before using
        # `output` -- verified here via has_errors() rather than asserting
        # the attacker payload is absent from the raw render.
        self.assertTrue(has_errors(diagnostics))

    def test_render_compose_rejects_typed_pure_substitution_in_environment_and_volumes(self):
        """Same as above for `environment` (dict injection) and `volumes`
        (host bind mount injection) field positions."""
        plan = {
            "metadata": {"name": "cds-test"},
            "modules": [
                {
                    "id": "evil2",
                    "config": {
                        "envmap": {"ATTACKER_KEY": "injected"},
                        "vols": ["/:/host:rw"],
                    },
                    "implementation": {
                        "kind": "docker-compose",
                        "compose": {
                            "services": {
                                "app": {
                                    "image": "alpine:3.19",
                                    "environment": "${config.envmap}",
                                    "volumes": "${config.vols}",
                                }
                            }
                        },
                    },
                }
            ],
        }

        output, diagnostics = render_compose(plan)

        errors = {d.code: d for d in diagnostics if d.level == "error"}
        self.assertIn("E072", errors)
        error_messages = [d.message for d in diagnostics if d.code == "E072"]
        self.assertTrue(any("environment" in m for m in error_messages))
        self.assertTrue(any("volumes" in m for m in error_messages))
        self.assertTrue(has_errors(diagnostics))

    def test_render_compose_rejects_typed_pure_substitution_for_scalar_escalation_fields(self):
        """`privileged`, `network_mode`, and `pid` are already scalar fields,
        so a dict/list check alone would miss a profile-supplied scalar that
        resolves to a host-escalating value (privileged: true, or
        network_mode/pid: "host"). These must still be flagged."""
        plan = {
            "metadata": {"name": "cds-test"},
            "modules": [
                {
                    "id": "evil3",
                    "config": {
                        "priv": True,
                        "netmode": "host",
                        "pidmode": "host",
                    },
                    "implementation": {
                        "kind": "docker-compose",
                        "compose": {
                            "services": {
                                "app": {
                                    "image": "alpine:3.19",
                                    "privileged": "${config.priv}",
                                    "network_mode": "${config.netmode}",
                                    "pid": "${config.pidmode}",
                                }
                            }
                        },
                    },
                }
            ],
        }

        output, diagnostics = render_compose(plan)

        error_messages = [d.message for d in diagnostics if d.code == "E072"]
        self.assertTrue(any("privileged" in m for m in error_messages))
        self.assertTrue(any("network_mode" in m for m in error_messages))
        self.assertTrue(any(
            "pid" in m and "network_mode" not in m for m in error_messages
        ))
        self.assertTrue(has_errors(diagnostics))

    def test_render_compose_allows_safe_scalar_values_for_escalation_fields(self):
        """A profile-supplied scalar that does NOT match a known dangerous
        value (e.g. privileged: false, network_mode: "bridge") must not be
        flagged: only the specific escalating values are unsafe."""
        plan = {
            "metadata": {"name": "cds-test"},
            "modules": [
                {
                    "id": "web",
                    "config": {"priv": False, "netmode": "bridge"},
                    "implementation": {
                        "kind": "docker-compose",
                        "compose": {
                            "services": {
                                "app": {
                                    "image": "web:latest",
                                    "privileged": "${config.priv}",
                                    "network_mode": "${config.netmode}",
                                }
                            }
                        },
                    },
                }
            ],
        }

        output, diagnostics = render_compose(plan)

        self.assertEqual(len([d for d in diagnostics if d.level == "error"]), 0)

    def test_render_compose_allows_mixed_substitution_in_unsafe_fields(self):
        """Mixed substitution (string-concatenated) always yields a str, so
        it remains safe/allowed in these field positions -- only a *pure*
        (whole-field) substitution can smuggle in a non-scalar type."""
        plan = {
            "metadata": {"name": "cds-test"},
            "modules": [
                {
                    "id": "web",
                    "config": {"level": "debug"},
                    "implementation": {
                        "kind": "docker-compose",
                        "compose": {
                            "services": {
                                "app": {
                                    "image": "web:latest",
                                    "command": "run --log-level=${config.level}",
                                }
                            }
                        },
                    },
                }
            ],
        }

        output, diagnostics = render_compose(plan)

        self.assertEqual(len([d for d in diagnostics if d.level == "error"]), 0)
        self.assertIn("run --log-level=debug", output)

    def test_render_compose_does_not_flag_typed_pure_substitution_outside_unsafe_fields(self):
        """Pure substitution to a dict/list value in a field position that
        isn't compose-dangerous (e.g. a custom `labels`-adjacent field this
        module happens to name `metadata`) must not be flagged; only the
        known dangerous compose field names are checked."""
        plan = {
            "metadata": {"name": "cds-test"},
            "modules": [
                {
                    "id": "web",
                    "config": {"extra": {"team": "data"}},
                    "implementation": {
                        "kind": "docker-compose",
                        "compose": {
                            "services": {
                                "app": {
                                    "image": "web:latest",
                                    "x-metadata": "${config.extra}",
                                }
                            }
                        },
                    },
                }
            ],
        }

        output, diagnostics = render_compose(plan)

        self.assertEqual(len([d for d in diagnostics if d.level == "error"]), 0)
        compose = yaml.safe_load(output)
        self.assertEqual(compose["services"]["web-app"]["x-metadata"], {"team": "data"})

    def test_render_compose_does_not_flag_legitimate_compose_native_placeholders(self):
        """${CDS_*} secret placeholders and bare ${VAR}/${VAR:-default} Docker
        Compose native runtime placeholders must not trigger the unresolved
        template expression check -- only CDS's own config/bindings/service
        vocabulary should be flagged."""
        plan = {
            "metadata": {"name": "cds-test"},
            "modules": [
                {
                    "id": "web",
                    "implementation": {
                        "kind": "docker-compose",
                        "compose": {
                            "services": {
                                "app": {
                                    "image": "web:latest",
                                    "environment": {
                                        "HOST_TAG": "${TAG:-latest}",
                                        "DB_PASSWORD": "${CDS_DB_PASSWORD}",
                                    },
                                }
                            }
                        },
                    },
                }
            ],
        }

        output, diagnostics = render_compose(plan)

        self.assertEqual(len([d for d in diagnostics if d.level == "error"]), 0)
        self.assertIn("${TAG:-latest}", output)
        self.assertIn("${CDS_DB_PASSWORD}", output)

    def test_render_compose_reports_diagnostic_for_deeply_nested_service_definition(self):
        """A pathologically deeply nested service field (e.g. a malformed or
        malicious module template) must be reported as an E094 diagnostic
        instead of raising an unhandled RecursionError inside
        _substitute_values()."""
        deep_value: dict = {}
        node = deep_value
        for _ in range(150):
            node["child"] = {}
            node = node["child"]

        plan = {
            "metadata": {"name": "cds-test"},
            "modules": [
                {
                    "id": "web",
                    "implementation": {
                        "kind": "docker-compose",
                        "compose": {
                            "services": {
                                "app": {
                                    "image": "web:latest",
                                    "labels": deep_value,
                                }
                            }
                        },
                    },
                }
            ],
        }

        output, diagnostics = render_compose(plan)

        errors = [d for d in diagnostics if d.code == "E094"]
        self.assertEqual(len(errors), 1)
        self.assertNotIn("web-app", output)

    def test_render_compose_drops_volume_entry_when_enabled_from_resolves_false(self):
        """A service's volumes list can mix bind-mounts that only apply to
        one of several mutually-exclusive consumed contracts (e.g. a module
        that targets either a sql-database or a file-database, selected by
        a config field). An entry's enabledFrom is evaluated against
        config/bindings and, when it resolves to False, the entry is
        dropped entirely -- never reaching output as an unresolved
        ${bindings.*} placeholder."""
        plan = {
            "metadata": {"name": "cds-test"},
            "modules": [
                {
                    "id": "worker",
                    "config": {"warehouseType": "postgres"},
                    "implementation": {
                        "kind": "docker-compose",
                        "compose": {
                            "services": {
                                "app": {
                                    "image": "worker:latest",
                                    "volumes": [
                                        {
                                            "type": "bind",
                                            "source": "/host/always",
                                            "target": "/always",
                                        },
                                        {
                                            "type": "bind",
                                            "source": "${bindings.target-warehouse-file.hostDirectory}",
                                            "target": "/usr/app/dbt_duckdb",
                                            "enabledFrom": "config.warehouseType==duckdb",
                                        },
                                    ],
                                }
                            }
                        },
                    },
                }
            ],
        }

        output, diagnostics = render_compose(plan)

        self.assertEqual([d for d in diagnostics if d.level == "error"], [])
        compose = yaml.safe_load(output)
        volumes = compose["services"]["worker-app"]["volumes"]
        self.assertEqual(len(volumes), 1)
        self.assertEqual(volumes[0]["target"], "/always")

    def test_render_compose_keeps_volume_entry_when_enabled_from_resolves_true(self):
        """The mirror case of the test above: when the equality gate
        matches, the entry is kept, its enabledFrom key is stripped, and
        its ${...} expressions are substituted normally."""
        plan = {
            "metadata": {"name": "cds-test"},
            "modules": [
                {
                    "id": "worker",
                    "config": {"warehouseType": "duckdb"},
                    "consumes": {
                        "target-warehouse-file": {
                            "contractRef": "duckdb.file-database",
                            "contract": {
                                "kind": "file-database",
                                "spec": {"hostDirectory": "./data/duckdb"},
                            },
                        }
                    },
                    "implementation": {
                        "kind": "docker-compose",
                        "compose": {
                            "services": {
                                "app": {
                                    "image": "worker:latest",
                                    "volumes": [
                                        {
                                            "type": "bind",
                                            "source": "${bindings.target-warehouse-file.hostDirectory}",
                                            "target": "/usr/app/dbt_duckdb",
                                            "enabledFrom": "config.warehouseType==duckdb",
                                        },
                                    ],
                                }
                            }
                        },
                    },
                }
            ],
        }

        output, diagnostics = render_compose(plan)

        self.assertEqual([d for d in diagnostics if d.level == "error"], [])
        compose = yaml.safe_load(output)
        volumes = compose["services"]["worker-app"]["volumes"]
        self.assertEqual(len(volumes), 1)
        self.assertEqual(volumes[0]["source"], "data/duckdb")
        self.assertNotIn("enabledFrom", volumes[0])


class ImageSourceRenderingTest(unittest.TestCase):
    def _plan(self, image_config):
        return {
            "metadata": {"name": "cds-test"},
            "modules": [
                {
                    "id": "dagster",
                    "config": {"image": image_config},
                    "implementation": {
                        "kind": "docker-compose",
                        "compose": {
                            "services": {
                                "user-code": {
                                    "build": {
                                        "context": ".",
                                        "dockerfile": "images/dagster/${config.image.variant}/Dockerfile",
                                    },
                                    "image": "local/dagster:custom",
                                },
                                "webserver": {
                                    "image": "local/dagster:custom",
                                },
                            }
                        },
                    },
                }
            ],
        }

    def test_default_source_build_leaves_build_block_intact(self):
        plan = self._plan({"variant": "base", "source": "build"})

        output, diagnostics = render_compose(plan)

        self.assertEqual(len([d for d in diagnostics if d.level == "error"]), 0)
        compose = yaml.safe_load(output)
        service = compose["services"]["dagster-user-code"]
        self.assertIn("build", service)
        self.assertEqual(service["image"], "local/dagster:custom")

    def test_source_registry_with_tag_drops_build_and_rewrites_image(self):
        plan = self._plan({"variant": "base", "source": "registry", "tag": "1.8.0"})

        output, diagnostics = render_compose(plan)

        self.assertEqual(len([d for d in diagnostics if d.level == "error"]), 0)
        compose = yaml.safe_load(output)
        service = compose["services"]["dagster-user-code"]
        self.assertNotIn("build", service)
        self.assertEqual(service["image"], "docker.io/ronaldsoeverein/dagster:1.8.0")

    def test_source_registry_without_tag_falls_back_to_build(self):
        plan = self._plan({"variant": "base", "source": "registry"})

        output, diagnostics = render_compose(plan)

        self.assertEqual(len([d for d in diagnostics if d.level == "error"]), 0)
        compose = yaml.safe_load(output)
        service = compose["services"]["dagster-user-code"]
        self.assertIn("build", service)
        self.assertEqual(service["image"], "local/dagster:custom")

    def test_source_registry_rewrites_services_without_build(self):
        plan = self._plan({"variant": "base", "source": "registry", "tag": "1.8.0"})

        output, diagnostics = render_compose(plan)

        self.assertEqual(len([d for d in diagnostics if d.level == "error"]), 0)
        compose = yaml.safe_load(output)
        service = compose["services"]["dagster-webserver"]
        self.assertNotIn("build", service)
        self.assertEqual(service["image"], "docker.io/ronaldsoeverein/dagster:1.8.0")

    def test_source_registry_with_variant_prefixed_tag(self):
        plan = self._plan({"variant": "hardened", "source": "registry", "tag": "hardened-1.8.0"})

        output, diagnostics = render_compose(plan)

        self.assertEqual(len([d for d in diagnostics if d.level == "error"]), 0)
        compose = yaml.safe_load(output)
        service = compose["services"]["dagster-user-code"]
        self.assertNotIn("build", service)
        self.assertEqual(service["image"], "docker.io/ronaldsoeverein/dagster:hardened-1.8.0")


class SupersetImageSourceRenderingTest(unittest.TestCase):
    """Mirrors ImageSourceRenderingTest (Dagster) for the Superset module,
    which has no image.variant split -- only image.source/image.tag."""

    def _plan(self, image_config):
        return {
            "metadata": {"name": "cds-test"},
            "modules": [
                {
                    "id": "superset",
                    "config": {"image": image_config},
                    "implementation": {
                        "kind": "docker-compose",
                        "compose": {
                            "services": {
                                "superset-init": {
                                    "build": {
                                        "context": ".",
                                        "dockerfile": "images/superset/base/Dockerfile",
                                    },
                                    "image": "local/superset:custom",
                                },
                                "superset": {
                                    "build": {
                                        "context": ".",
                                        "dockerfile": "images/superset/base/Dockerfile",
                                    },
                                    "image": "local/superset:custom",
                                },
                            }
                        },
                    },
                }
            ],
        }

    def test_default_source_build_leaves_build_block_intact(self):
        plan = self._plan({"source": "build"})

        output, diagnostics = render_compose(plan)

        self.assertEqual(len([d for d in diagnostics if d.level == "error"]), 0)
        compose = yaml.safe_load(output)
        service = compose["services"]["superset"]
        self.assertIn("build", service)
        self.assertEqual(service["image"], "local/superset:custom")

    def test_source_registry_with_tag_drops_build_and_rewrites_image(self):
        plan = self._plan({"source": "registry", "tag": "3.1.0"})

        output, diagnostics = render_compose(plan)

        self.assertEqual(len([d for d in diagnostics if d.level == "error"]), 0)
        compose = yaml.safe_load(output)
        service = compose["services"]["superset"]
        self.assertNotIn("build", service)
        self.assertEqual(service["image"], "docker.io/ronaldsoeverein/superset:3.1.0")

    def test_source_registry_without_tag_falls_back_to_build(self):
        plan = self._plan({"source": "registry"})

        output, diagnostics = render_compose(plan)

        self.assertEqual(len([d for d in diagnostics if d.level == "error"]), 0)
        compose = yaml.safe_load(output)
        service = compose["services"]["superset"]
        self.assertIn("build", service)
        self.assertEqual(service["image"], "local/superset:custom")

    def test_source_registry_rewrites_init_service_too(self):
        plan = self._plan({"source": "registry", "tag": "3.1.0"})

        output, diagnostics = render_compose(plan)

        self.assertEqual(len([d for d in diagnostics if d.level == "error"]), 0)
        compose = yaml.safe_load(output)
        service = compose["services"]["superset-init"]
        self.assertNotIn("build", service)
        self.assertEqual(service["image"], "docker.io/ronaldsoeverein/superset:3.1.0")


if __name__ == "__main__":
    unittest.main()
