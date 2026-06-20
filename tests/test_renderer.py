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

if __name__ == "__main__":
    unittest.main()
