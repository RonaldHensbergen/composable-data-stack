import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cli.audit_log import audit_log_path, is_enabled, record_entry

_SAMPLE_PLAN = {
    "apiVersion": "cds/v1alpha1",
    "kind": "Plan",
    "secrets": {
        "db_password": "CDS_DB_PASSWORD",
        "CDS_OTHER_TOKEN": "CDS_OTHER_TOKEN",
    },
    "modules": [
        {
            "id": "postgres",
            "source": "../../modules/warehouse/postgres",
            "version": "0.1.0",
            "config": {"image": {"tag": "16", "digest": "sha256:deadbeef"}},
        },
        {
            "id": "keydb",
            "source": "../../modules/cache/keydb",
            "version": "0.1.0",
            "config": {},
        },
    ],
}


class AuditLogTest(unittest.TestCase):
    def test_record_entry_writes_jsonl_without_secret_values(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            project_root = Path(tmp_dir)
            log_path = record_entry(
                command="render",
                profile="demo",
                outcome="success",
                environment="prod",
                plan=_SAMPLE_PLAN,
                details={"target": "compose", "output": "docker-compose.yml"},
                project_root=project_root,
            )

            self.assertEqual(log_path, audit_log_path(project_root))
            self.assertTrue(log_path.is_file())

            entry = json.loads(log_path.read_text(encoding="utf-8").strip())
            self.assertEqual(entry["command"], "render")
            self.assertEqual(entry["profile"], "demo")
            self.assertEqual(entry["outcome"], "success")
            self.assertEqual(entry["environment"], "prod")
            self.assertEqual(entry["details"], {"target": "compose", "output": "docker-compose.yml"})

            # Secret entries must only ever surface as alias/env-var *names*,
            # never values -- the plan's "secrets" dict already only maps
            # names to names (see cli/secrets.py), so this also guards
            # against a future regression leaking an actual secret value.
            self.assertEqual(sorted(entry["secretAliases"]), ["CDS_OTHER_TOKEN", "db_password"])

            module_ids = {m["id"] for m in entry["modules"]}
            self.assertEqual(module_ids, {"postgres", "keydb"})
            postgres_entry = next(m for m in entry["modules"] if m["id"] == "postgres")
            self.assertEqual(postgres_entry["image"], {"tag": "16", "digest": "sha256:deadbeef"})
            keydb_entry = next(m for m in entry["modules"] if m["id"] == "keydb")
            self.assertNotIn("image", keydb_entry)

    def test_record_entry_appends_multiple_lines(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            project_root = Path(tmp_dir)
            record_entry(command="validate", profile="demo", outcome="success", project_root=project_root)
            log_path = record_entry(command="validate", profile="demo", outcome="failure", project_root=project_root)

            lines = log_path.read_text(encoding="utf-8").strip().splitlines()
            self.assertEqual(len(lines), 2)
            self.assertEqual(json.loads(lines[0])["outcome"], "success")
            self.assertEqual(json.loads(lines[1])["outcome"], "failure")

    def test_record_entry_returns_none_when_disabled_by_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            project_root = Path(tmp_dir)
            result = record_entry(
                command="validate",
                profile="demo",
                outcome="success",
                project_root=project_root,
                config_disabled=True,
            )

            self.assertIsNone(result)
            self.assertFalse(audit_log_path(project_root).exists())

    def test_record_entry_returns_none_when_disabled_by_env_var(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            project_root = Path(tmp_dir)
            with patch.dict("os.environ", {"CDS_AUDIT_LOG_DISABLE": "true"}):
                result = record_entry(
                    command="validate",
                    profile="demo",
                    outcome="success",
                    project_root=project_root,
                )

            self.assertIsNone(result)
            self.assertFalse(audit_log_path(project_root).exists())

    def test_is_enabled_defaults_true(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            self.assertTrue(is_enabled())
            self.assertFalse(is_enabled(config_disabled=True))

    def test_record_entry_handles_no_plan(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            project_root = Path(tmp_dir)
            log_path = record_entry(
                command="validate",
                profile="demo",
                outcome="failure",
                project_root=project_root,
            )
            entry = json.loads(log_path.read_text(encoding="utf-8").strip())
            self.assertEqual(entry["modules"], [])
            self.assertEqual(entry["secretAliases"], [])

    def test_record_entry_never_writes_through_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir, tempfile.TemporaryDirectory() as outside_dir:
            project_root = Path(tmp_dir)
            outside_target = Path(outside_dir) / "escaped.jsonl"
            log_path = audit_log_path(project_root)
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_path.symlink_to(outside_target)

            record_entry(command="validate", profile="demo", outcome="success", project_root=project_root)

            self.assertFalse(outside_target.exists())
            self.assertTrue(log_path.is_file())
            self.assertFalse(log_path.is_symlink())


if __name__ == "__main__":
    unittest.main()
