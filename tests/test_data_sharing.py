"""Tests for the optional profile-level data-sharing declaration (#844).

A profile can declare under `spec.dataSharing` which datasets it discloses
as a data provider in an external, provider-neutral data-sharing system,
optionally linked to an external registration. Shape is enforced by
profile.schema.json (E010); cross-reference rules by
cli.validator.validate_data_sharing_declaration (E157/E158). The declaration
is carried through the plan and surfaced in `cds report`; CDS never performs,
verifies, or claims any registration.
"""

import contextlib
import copy
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from cli import validator
from cli.main import main
from cli.planner import build_plan_from_profile
from cli.report import (
    DATA_SHARING_NOTE,
    _build_data_sharing_summary,
    build_compliance_report,
)
from cli.validator import (
    validate_data_sharing_declaration,
    validate_loaded_profile,
    validate_profile,
    validate_profile_shape,
)
from tests.hermetic_project import find_cds, hermetic_env, init_env, make_project

_REPO_ROOT = Path(__file__).resolve().parent.parent
_PROFILE_NAME = "local-dagster-postgres-superset"
_PROFILE_FILE = str(_REPO_ROOT / "profiles" / _PROFILE_NAME / "profile.yaml")

_DECLARATION = {
    "registration": {
        "system": "fds-afsprakenstelsel",
        "reference": "https://realisatieibds.nl/registrations/example-123",
    },
    "datasets": [
        {"datasetId": "urn:example:datasets:address-register", "module": "postgres"},
        {"datasetId": "urn:example:datasets:permits"},
    ],
}


def _base_profile(data_sharing=None) -> dict:
    profile = {
        "apiVersion": "cds/v1alpha1",
        "kind": "Profile",
        "metadata": {"name": "example", "environment": "local"},
        "spec": {
            "runtime": {"type": "docker-compose"},
            "modules": [
                {
                    "id": "postgres",
                    "source": "modules/warehouse/postgres",
                    "version": "0.1.0",
                    "enabled": True,
                    "config": {},
                }
            ],
        },
    }
    if data_sharing is not None:
        profile["spec"]["dataSharing"] = data_sharing
    return profile


def _inst(inst_id: str = "postgres") -> dict:
    return {
        "index": 0,
        "id": inst_id,
        "source": "modules/warehouse/postgres",
        "config": {},
        "module": {
            "kind": "Module",
            "metadata": {"name": inst_id, "category": "warehouse"},
            "spec": {"provides": [], "consumes": [], "configSchema": {"type": "object"}},
        },
    }


class SchemaShapeTest(unittest.TestCase):
    def test_valid_declaration_passes_shape(self) -> None:
        diagnostics = validate_profile_shape(_base_profile(copy.deepcopy(_DECLARATION)))
        self.assertEqual([d for d in diagnostics if d.code == "E010"], [])

    def test_absent_block_passes_shape(self) -> None:
        # Existing profiles without spec.dataSharing are unaffected.
        self.assertEqual(validate_profile_shape(_base_profile()), [])

    def test_missing_datasets_is_e010(self) -> None:
        profile = _base_profile({"registration": _DECLARATION["registration"]})
        diagnostics = validate_profile_shape(profile)
        self.assertTrue(any(d.code == "E010" and d.path == "spec.dataSharing" for d in diagnostics))

    def test_empty_datasets_array_is_e010(self) -> None:
        profile = _base_profile({"datasets": []})
        diagnostics = validate_profile_shape(profile)
        self.assertTrue(any(d.code == "E010" and d.path == "spec.dataSharing.datasets" for d in diagnostics))

    def test_registration_requires_system_and_reference(self) -> None:
        profile = _base_profile(
            {"datasets": [{"datasetId": "urn:x:1"}], "registration": {"system": "fds"}}
        )
        diagnostics = validate_profile_shape(profile)
        self.assertTrue(
            any(d.code == "E010" and d.path == "spec.dataSharing.registration" for d in diagnostics)
        )

    def test_unknown_key_inside_block_is_e010(self) -> None:
        declaration = copy.deepcopy(_DECLARATION)
        declaration["bogus"] = True
        profile = _base_profile(declaration)
        diagnostics = validate_profile_shape(profile)
        self.assertTrue(any(d.code == "E010" and d.path == "spec.dataSharing" for d in diagnostics))

    def test_bad_module_pattern_is_e010(self) -> None:
        declaration = copy.deepcopy(_DECLARATION)
        declaration["datasets"][0]["module"] = "Bad_Module"
        profile = _base_profile(declaration)
        diagnostics = validate_profile_shape(profile)
        self.assertTrue(
            any(d.code == "E010" and d.path == "spec.dataSharing.datasets.0.module" for d in diagnostics)
        )

    def test_dataset_entry_requires_dataset_id(self) -> None:
        profile = _base_profile({"datasets": [{"module": "postgres"}]})
        diagnostics = validate_profile_shape(profile)
        self.assertTrue(
            any(d.code == "E010" and d.path == "spec.dataSharing.datasets.0" for d in diagnostics)
        )


class DeclarationValidationTest(unittest.TestCase):
    def test_absent_block_returns_no_diagnostics(self) -> None:
        self.assertEqual(validate_data_sharing_declaration(_base_profile(), [_inst()]), [])

    def test_valid_declaration_returns_no_diagnostics(self) -> None:
        profile = _base_profile(copy.deepcopy(_DECLARATION))
        self.assertEqual(validate_data_sharing_declaration(profile, [_inst()]), [])

    def test_duplicate_dataset_id_is_e157(self) -> None:
        declaration = copy.deepcopy(_DECLARATION)
        declaration["datasets"].append({"datasetId": "urn:example:datasets:address-register"})
        profile = _base_profile(declaration)
        diagnostics = validate_data_sharing_declaration(profile, [_inst()])
        e157 = [d for d in diagnostics if d.code == "E157"]
        self.assertEqual(len(e157), 1)
        self.assertEqual(e157[0].path, "spec.dataSharing.datasets[2].datasetId")
        self.assertEqual(e157[0].level, "error")

    def test_unknown_module_is_e158(self) -> None:
        declaration = copy.deepcopy(_DECLARATION)
        declaration["datasets"][0]["module"] = "ghost"
        profile = _base_profile(declaration)
        diagnostics = validate_data_sharing_declaration(profile, [_inst()])
        e158 = [d for d in diagnostics if d.code == "E158"]
        self.assertEqual(len(e158), 1)
        self.assertEqual(e158[0].path, "spec.dataSharing.datasets[0].module")
        self.assertIn('"ghost"', e158[0].message)

    def test_wired_into_validate_loaded_profile(self) -> None:
        declaration = copy.deepcopy(_DECLARATION)
        declaration["datasets"].append({"datasetId": "urn:example:datasets:address-register", "module": "ghost"})
        profile = _base_profile(declaration)
        with (
            patch.object(validator, "validate_profile_shape", return_value=[]),
            patch.object(validator, "load_module_instances", return_value=([_inst()], [])),
        ):
            diagnostics = validate_loaded_profile(profile, Path("profile.yaml"))
        codes = {d.code for d in diagnostics}
        self.assertIn("E157", codes)
        self.assertIn("E158", codes)


class PlanCarriesDeclarationTest(unittest.TestCase):
    """build_plan_from_profile carries spec.dataSharing into the plan only
    when the profile declares it."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        cls.project = Path(cls._tmp.name)
        cls.profile_file = make_project(cls.project, _PROFILE_NAME)

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def _profile_with(self, data_sharing) -> tuple[dict, Path]:
        profile = yaml.safe_load(self.profile_file.read_text(encoding="utf-8"))
        if data_sharing is not None:
            profile["spec"]["dataSharing"] = copy.deepcopy(data_sharing)
        return profile, self.profile_file.parent

    def test_plan_carries_declaration_when_present(self) -> None:
        profile, profile_dir = self._profile_with(_DECLARATION)
        plan, diagnostics = build_plan_from_profile(profile, profile_dir, allow_missing_secrets=True)
        errors = [d for d in diagnostics if d.level == "error"]
        self.assertEqual(errors, [], errors)
        self.assertIsNotNone(plan)
        self.assertEqual(plan["dataSharing"], _DECLARATION)

    def test_plan_omits_key_when_absent(self) -> None:
        profile, profile_dir = self._profile_with(None)
        plan, diagnostics = build_plan_from_profile(profile, profile_dir, allow_missing_secrets=True)
        errors = [d for d in diagnostics if d.level == "error"]
        self.assertEqual(errors, [], errors)
        self.assertIsNotNone(plan)
        self.assertNotIn("dataSharing", plan)


class DataSharingSummaryTest(unittest.TestCase):
    def _plan(self, data_sharing, provides=None) -> dict:
        return {
            "dataSharing": data_sharing,
            "modules": [
                {
                    "id": "postgres",
                    "provides": provides
                    if provides is not None
                    else {"data-provider-metadata": {"kind": "open-data-provider"}},
                }
            ],
        }

    def test_summary_echoes_registration_and_enriches_datasets(self) -> None:
        summary = _build_data_sharing_summary(self._plan(copy.deepcopy(_DECLARATION)))
        self.assertEqual(
            summary["registration"],
            {
                "system": "fds-afsprakenstelsel",
                "reference": "https://realisatieibds.nl/registrations/example-123",
            },
        )
        self.assertEqual(summary["note"], DATA_SHARING_NOTE)
        by_id = {d["datasetId"]: d for d in summary["datasets"]}
        linked = by_id["urn:example:datasets:address-register"]
        self.assertEqual(linked["module"], "postgres")
        self.assertIs(linked["moduleProvidesOpenDataProvider"], True)
        unlinked = by_id["urn:example:datasets:permits"]
        self.assertNotIn("module", unlinked)
        self.assertNotIn("moduleProvidesOpenDataProvider", unlinked)

    def test_module_without_open_data_provider_is_flagged_false(self) -> None:
        plan = self._plan(copy.deepcopy(_DECLARATION), provides={"sql-database": {"kind": "sql-database"}})
        summary = _build_data_sharing_summary(plan)
        linked = summary["datasets"][0]
        self.assertIs(linked["moduleProvidesOpenDataProvider"], False)

    def test_summary_without_registration_omits_it(self) -> None:
        declaration = {"datasets": [{"datasetId": "urn:x:1"}]}
        summary = _build_data_sharing_summary(self._plan(declaration))
        self.assertNotIn("registration", summary)
        self.assertEqual(summary["datasets"], [{"datasetId": "urn:x:1"}])


class BuildReportDataSharingTest(unittest.TestCase):
    """build_compliance_report includes the dataSharing section only when the
    plan carries a declaration (pipeline mocked past validate)."""

    def _plan(self, with_declaration: bool) -> dict:
        plan = {
            "sourceProfile": _PROFILE_FILE,
            "metadata": {"name": _PROFILE_NAME},
            "environment": None,
            "secrets": {},
            "modules": [
                {
                    "id": "postgres",
                    "provides": {"data-provider-metadata": {"kind": "open-data-provider"}},
                    "consumes": {},
                }
            ],
        }
        if with_declaration:
            plan["dataSharing"] = copy.deepcopy(_DECLARATION)
        return plan

    def _build(self, plan: dict) -> dict:
        with (
            patch("cli.report.validate_profile", return_value=[]),
            patch("cli.report.build_plan", return_value=(plan, [])),
            patch("cli.report.render_compose", return_value=("services: {}\n", [])),
        ):
            report, diagnostics = build_compliance_report(_PROFILE_FILE)
        self.assertIsNotNone(report, diagnostics)
        return report

    def test_report_includes_declaration_when_present(self) -> None:
        report = self._build(self._plan(True))
        self.assertIn("dataSharing", report)
        section = report["dataSharing"]
        self.assertEqual(section["registration"]["system"], "fds-afsprakenstelsel")
        self.assertEqual(section["note"], DATA_SHARING_NOTE)
        linked = section["datasets"][0]
        self.assertEqual(linked["module"], "postgres")
        self.assertIs(linked["moduleProvidesOpenDataProvider"], True)

    def test_report_omits_declaration_when_absent(self) -> None:
        report = self._build(self._plan(False))
        self.assertNotIn("dataSharing", report)


class MainReportTextFormatTest(unittest.TestCase):
    def _canned_report(self) -> dict:
        return {
            "apiVersion": "cds/v1alpha1",
            "kind": "ComplianceReport",
            "generatedAt": "2026-11-07T10:00:00+00:00",
            "profile": {"sourceProfile": _PROFILE_FILE, "name": _PROFILE_NAME, "environment": None},
            "modules": [],
            "images": [],
            "topology": [],
            "dataSharing": {
                "registration": {
                    "system": "fds-afsprakenstelsel",
                    "reference": "https://realisatieibds.nl/registrations/example-123",
                },
                "datasets": [
                    {
                        "datasetId": "urn:example:datasets:address-register",
                        "module": "postgres",
                        "moduleProvidesOpenDataProvider": True,
                    }
                ],
                "note": DATA_SHARING_NOTE,
            },
            "secretLeakCheck": {"status": "pass", "checkedSecretCount": 0, "leakedSecretNames": []},
            "disclaimer": "disclaimer text",
        }

    def _run_main(self, argv: list) -> tuple[int, str, str]:
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.object(sys, "argv", ["cds", *argv]), \
             contextlib.redirect_stdout(stdout), \
             contextlib.redirect_stderr(stderr):
            code = main()
        return code, stdout.getvalue(), stderr.getvalue()

    def test_text_output_includes_data_sharing_section(self) -> None:
        with patch("cli.main.build_compliance_report", return_value=(self._canned_report(), [])):
            code, stdout, _stderr = self._run_main(["report", _PROFILE_FILE])
        self.assertEqual(code, 0)
        self.assertIn("-- Data Sharing Declaration --", stdout)
        self.assertIn("system=fds-afsprakenstelsel", stdout)
        self.assertIn("reference=https://realisatieibds.nl/registrations/example-123", stdout)
        self.assertIn("dataset: urn:example:datasets:address-register module=postgres", stdout)
        self.assertIn("open-data-provider contract: yes", stdout)
        self.assertIn(DATA_SHARING_NOTE, stdout)

    def test_json_output_includes_data_sharing_section(self) -> None:
        with patch("cli.main.build_compliance_report", return_value=(self._canned_report(), [])):
            code, stdout, _stderr = self._run_main(["report", _PROFILE_FILE, "--json"])
        self.assertEqual(code, 0)
        parsed = json.loads(stdout)
        self.assertEqual(parsed["dataSharing"]["datasets"][0]["datasetId"], "urn:example:datasets:address-register")


class EndToEndTest(unittest.TestCase):
    """End-to-end against a hermetic copy of a shipped profile with a
    dataSharing declaration injected: validate accepts it, plan carries it,
    and `cds report` surfaces it."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.cds = find_cds()
        cls._tmp = tempfile.TemporaryDirectory()
        cls.project = Path(cls._tmp.name)
        cls.profile_file = str(make_project(cls.project, _PROFILE_NAME))
        init_env(cls.cds, cls.project, _PROFILE_NAME)
        profile = yaml.safe_load(Path(cls.profile_file).read_text(encoding="utf-8"))
        profile["spec"]["dataSharing"] = copy.deepcopy(_DECLARATION)
        Path(cls.profile_file).write_text(yaml.safe_dump(profile, sort_keys=False), encoding="utf-8")

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def test_validate_accepts_declaration(self) -> None:
        diagnostics = validate_profile(self.profile_file)
        errors = [d for d in diagnostics if d.level == "error"]
        self.assertEqual(errors, [], errors)

    def test_validate_flags_duplicate_dataset_id(self) -> None:
        profile = yaml.safe_load(Path(self.profile_file).read_text(encoding="utf-8"))
        profile["spec"]["dataSharing"]["datasets"].append(
            {"datasetId": "urn:example:datasets:address-register"}
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "profile.yaml"
            path.write_text(yaml.safe_dump(profile, sort_keys=False), encoding="utf-8")
            # Module sources are relative to the profile dir; anchor a copy
            # next to the original so resolution still works.
            path = Path(self.profile_file).parent / "duplicate-ds-profile.yaml"
            path.write_text(yaml.safe_dump(profile, sort_keys=False), encoding="utf-8")
            try:
                diagnostics = validate_profile(str(path))
            finally:
                path.unlink(missing_ok=True)
        self.assertTrue(any(d.code == "E157" for d in diagnostics), diagnostics)

    def test_validate_flags_unknown_module(self) -> None:
        profile = yaml.safe_load(Path(self.profile_file).read_text(encoding="utf-8"))
        profile["spec"]["dataSharing"]["datasets"][0]["module"] = "ghost"
        path = Path(self.profile_file).parent / "ghost-module-profile.yaml"
        path.write_text(yaml.safe_dump(profile, sort_keys=False), encoding="utf-8")
        try:
            diagnostics = validate_profile(str(path))
        finally:
            path.unlink(missing_ok=True)
        self.assertTrue(any(d.code == "E158" for d in diagnostics), diagnostics)

    def test_report_includes_declaration_end_to_end(self) -> None:
        clean_environ = {k: v for k, v in os.environ.items() if not k.startswith("CDS_")}
        with patch.dict(os.environ, clean_environ, clear=True):
            report, diagnostics = build_compliance_report(
                self.profile_file, env_file=str(self.project / ".env")
            )
        self.assertIsNotNone(report, f"report build failed: {diagnostics}")
        self.assertIn("dataSharing", report)
        section = report["dataSharing"]
        self.assertEqual(section["registration"]["system"], "fds-afsprakenstelsel")
        self.assertEqual(section["note"], DATA_SHARING_NOTE)
        by_id = {d["datasetId"]: d for d in section["datasets"]}
        linked = by_id["urn:example:datasets:address-register"]
        self.assertEqual(linked["module"], "postgres")
        self.assertIn("moduleProvidesOpenDataProvider", linked)

    def test_cds_report_cli_json_includes_declaration(self) -> None:
        result = subprocess.run(
            self.cds + ["report", _PROFILE_NAME, "--json"],
            cwd=str(self.project),
            capture_output=True,
            text=True,
            env=hermetic_env(),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertIn("dataSharing", report)
        self.assertEqual(
            report["dataSharing"]["registration"]["reference"],
            "https://realisatieibds.nl/registrations/example-123",
        )

    def test_cds_report_cli_text_shows_section(self) -> None:
        result = subprocess.run(
            self.cds + ["report", _PROFILE_NAME],
            cwd=str(self.project),
            capture_output=True,
            text=True,
            env=hermetic_env(),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("-- Data Sharing Declaration --", result.stdout)
        self.assertIn("dataset: urn:example:datasets:address-register", result.stdout)


if __name__ == "__main__":
    unittest.main()
