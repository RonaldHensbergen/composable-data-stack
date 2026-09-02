import re
import unittest
from pathlib import Path

import yaml


class DbtHardeningTest(unittest.TestCase):
    def setUp(self) -> None:
        self.repo_root = Path(__file__).resolve().parent.parent
        self.dockerfile = (self.repo_root / "images" / "dbt" / "Dockerfile").read_text(encoding="utf-8")
        self.entrypoint = (self.repo_root / "images" / "dbt" / "entrypoint.sh").read_text(encoding="utf-8")
        module = yaml.safe_load(
            (self.repo_root / "modules" / "transformation" / "dbt" / "module.yaml").read_text(encoding="utf-8")
        )
        self.module = module
        self.config_schema = module["spec"]["configSchema"]
        self.services = module["spec"]["implementation"]["compose"]["services"]

    def test_module_is_production_suitable(self) -> None:
        # Unlike modules/secrets/vault (which is genuinely dev-mode-only and
        # permanently productionSuitable: false), dbt-core has no such
        # inherent limitation, so the metadata block must not declare the
        # module unsuitable for production and must not carry the
        # "(experimental)" naming convention used by modules-experimental/.
        self.assertNotIn("productionSuitable", self.module["metadata"])
        self.assertNotIn("experimental", self.module["metadata"]["displayName"].lower())

    def test_base_image_is_digest_pinned(self) -> None:
        from_lines = re.findall(r"^FROM\s+(\S+)", self.dockerfile, flags=re.MULTILINE)

        self.assertTrue(from_lines, "expected at least one FROM line")
        for image in from_lines:
            with self.subTest(image=image):
                self.assertRegex(image, r"^[^@]+@sha256:[0-9a-f]{64}$")

    def test_image_has_minimal_immutable_runtime(self) -> None:
        users = re.findall(r"^USER\s+(\S+)$", self.dockerfile, flags=re.MULTILINE)

        self.assertEqual(users[-1], "dbt")
        self.assertNotIn("COPY . /app", self.dockerfile)
        # pip is uninstalled in both the builder venv and the final stage's
        # base-image site-packages, matching images/dagster's CVE/SBOM-noise
        # avoidance pattern.
        uninstall_lines = [
            line.strip() for line in self.dockerfile.splitlines()
            if "pip" in line and "uninstall" in line
        ]
        self.assertGreaterEqual(len(uninstall_lines), 2)

    def test_dbt_run_service_is_a_hardened_one_shot_job(self) -> None:
        service = self.services["dbt-run"]

        self.assertEqual(service["restart"], "no")
        self.assertTrue(service["read_only"])
        self.assertEqual(service["cap_drop"], ["ALL"])
        self.assertEqual(service["security_opt"], ["no-new-privileges:true"])
        self.assertTrue(service["healthcheck"]["disable"])
        self.assertIn("/tmp:rw,noexec,nosuid,nodev,uid=999,gid=999,mode=1777", service["tmpfs"])
        self.assertIn(
            "/home/dbt/.dbt:rw,noexec,nosuid,nodev,uid=999,gid=999,mode=0700",
            service["tmpfs"],
        )

    def test_project_mount_is_read_only(self) -> None:
        volumes = self.services["dbt-run"]["volumes"]
        project_mount = next(
            volume
            for volume in volumes
            if isinstance(volume, dict) and volume.get("target") == "${config.project.containerPath}"
        )
        self.assertTrue(project_mount["read_only"])

    def test_docs_sidecar_only_starts_after_dbt_run_succeeds(self) -> None:
        docs_service = self.services["dbt-docs"]

        self.assertEqual(
            docs_service["depends_on"]["dbt-run"]["condition"],
            "service_completed_successfully",
        )
        self.assertEqual(docs_service["enabledFrom"], "config.docs.enabled")

    def test_target_database_contract_ref_is_required(self) -> None:
        self.assertIn("targetDatabase", self.config_schema["required"])
        target_database = self.config_schema["properties"]["targetDatabase"]
        self.assertIn("contractRef", target_database["required"])

    def test_entrypoint_requires_connection_env_vars(self) -> None:
        for var in ("DBT_HOST", "DBT_PORT", "DBT_DBNAME", "DBT_USER", "DBT_PASSWORD", "DBT_SCHEMA"):
            with self.subTest(var=var):
                self.assertIn(f'"${{{var}:?', self.entrypoint)

    def test_entrypoint_runs_commands_in_order_and_stops_on_first_failure(self) -> None:
        self.assertIn("set -eu", self.entrypoint)
        self.assertIn("DBT_COMMANDS", self.entrypoint)


if __name__ == "__main__":
    unittest.main()
