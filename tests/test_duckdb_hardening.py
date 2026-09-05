import unittest
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator


class DuckdbHardeningTest(unittest.TestCase):
    def setUp(self) -> None:
        self.repo_root = Path(__file__).resolve().parent.parent
        module = yaml.safe_load(
            (
                self.repo_root
                / "modules-experimental"
                / "warehouse"
                / "duckdb"
                / "module.yaml"
            ).read_text(encoding="utf-8")
        )
        self.module = module
        self.config_schema = module["spec"]["configSchema"]
        self.services = module["spec"]["implementation"]["compose"]["services"]
        self.contract = yaml.safe_load(
            (self.repo_root / "shared" / "contracts" / "file-database.yaml").read_text(encoding="utf-8")
        )

    def test_module_is_experimental(self) -> None:
        # DuckDB's contract shape (file-database, not sql-database) is a new
        # design this repo hasn't validated in production yet, so the
        # module must declare itself unproven, matching the
        # modules-experimental/ convention used by dlt and (previously) dbt.
        self.assertFalse(self.module["metadata"]["productionSuitable"])
        self.assertIn("experimental", self.module["metadata"]["displayName"].lower())

    def test_module_schema_is_valid_json_schema(self) -> None:
        Draft202012Validator.check_schema(self.config_schema)

    def test_host_directory_is_required(self) -> None:
        self.assertIn("hostDirectory", self.config_schema["required"])
        self.assertEqual(self.config_schema["additionalProperties"], False)

    def test_filename_defaults_and_is_constrained_to_safe_characters(self) -> None:
        filename_schema = self.config_schema["properties"]["filename"]
        self.assertEqual(filename_schema["default"], "warehouse.duckdb")
        self.assertRegex(filename_schema["default"], filename_schema["pattern"])
        # A path traversal attempt must not satisfy the filename pattern --
        # this field is spliced directly into a shell command and a bind
        # mount target in the compose template.
        self.assertNotRegex("../../etc/passwd", filename_schema["pattern"])

    def test_provides_file_database_contract_with_all_documented_fields(self) -> None:
        provided = self.module["spec"]["provides"]
        file_database = next(entry for entry in provided if entry["name"] == "file-database")
        self.assertEqual(file_database["contract"]["kind"], "file-database")

        contract_fields = set(self.contract["spec"]["fields"])
        provided_fields = set(file_database["contract"]["spec"])
        self.assertEqual(contract_fields, provided_fields)

    def test_contract_fields_are_all_required(self) -> None:
        # Unlike sql-database, file-database has no optional fields --
        # every consumer needs the full hostDirectory/filename/path/readOnly
        # set to correctly bind-mount and open the shared file.
        for field_name, field_def in self.contract["spec"]["fields"].items():
            with self.subTest(field=field_name):
                self.assertTrue(field_def["required"])

    def test_init_service_is_a_hardened_one_shot_job(self) -> None:
        service = self.services["duckdb-init"]

        self.assertEqual(service["restart"], "no")
        self.assertTrue(service["read_only"])
        self.assertEqual(service["cap_drop"], ["ALL"])
        self.assertEqual(service["security_opt"], ["no-new-privileges:true"])
        self.assertTrue(service["healthcheck"]["disable"])

    def test_base_image_is_digest_pinned(self) -> None:
        image = self.services["duckdb-init"]["image"]
        self.assertRegex(image, r"^[^@]+@sha256:[0-9a-f]{64}$")

    def test_shared_directory_bind_mount_matches_configured_host_directory(self) -> None:
        volumes = self.services["duckdb-init"]["volumes"]
        bind_mount = next(
            volume
            for volume in volumes
            if isinstance(volume, dict) and volume.get("target") == "/data"
        )
        self.assertEqual(bind_mount["type"], "bind")
        self.assertEqual(bind_mount["source"], "${config.hostDirectory}")

    def test_init_command_prepares_the_shared_file_permissively(self) -> None:
        command = "\n".join(self.services["duckdb-init"]["command"])
        self.assertIn("mkdir -p /data", command)
        self.assertIn('touch "/data/${config.filename}"', command)
        self.assertIn("chmod 0777 /data", command)
        self.assertIn('chmod 0666 "/data/${config.filename}"', command)

    def test_runtime_declares_no_network_ports(self) -> None:
        # DuckDB is embedded -- there is nothing listening on the network,
        # unlike every sql-database-providing module (e.g. postgres).
        self.assertEqual(self.module["spec"]["runtime"]["service"]["ports"], [])


class FileDatabaseContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.repo_root = Path(__file__).resolve().parent.parent
        self.contract = yaml.safe_load(
            (self.repo_root / "shared" / "contracts" / "file-database.yaml").read_text(encoding="utf-8")
        )

    def test_contract_has_no_network_fields(self) -> None:
        # Regression guard distinguishing file-database from sql-database:
        # a file-database contract must never grow host/port/username
        # fields, since that would misrepresent DuckDB as network-reachable.
        fields = set(self.contract["spec"]["fields"])
        self.assertFalse(fields & {"host", "port", "username", "password"})

    def test_contract_kind_matches_metadata_name(self) -> None:
        self.assertEqual(self.contract["metadata"]["name"], "file-database")


if __name__ == "__main__":
    unittest.main()
