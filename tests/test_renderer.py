import tempfile
import unittest
from pathlib import Path

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

if __name__ == "__main__":
    unittest.main()
