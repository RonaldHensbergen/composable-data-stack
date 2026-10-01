import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path
from unittest.mock import patch

import yaml

from cli.main import main
from cli.report import (
    REPORT_DISCLAIMER,
    _build_image_evidence,
    _build_module_summary,
    _build_topology_summary,
    _check_no_leaked_secrets,
    build_compliance_report,
)

_REPO_ROOT = Path(__file__).resolve().parent.parent
_PROFILE_NAME = "local-dagster-postgres-superset"
_PROFILE_FILE = str(_REPO_ROOT / "profiles" / _PROFILE_NAME / "profile.yaml")
_PROFILE_ENV_FILE = _REPO_ROOT / ".env"
_FIXTURE_PATH = _REPO_ROOT / "tests" / "fixtures" / "signed-images.json"


def _sample_plan() -> dict:
    return {
        "sourceProfile": "profiles/example/profile.yaml",
        "metadata": {"name": "example"},
        "environment": None,
        "secrets": {
            # Alias -> env var name (a declared secret): must never leak.
            "db_password": "CDS_DB_PASSWORD",
            # Raw pass-through entries self-map (name -> itself) and are
            # NOT profile-declared secrets, so they must be excluded from
            # the leak scan even though they share the CDS_ prefix.
            "CDS_DB_NAME": "CDS_DB_NAME",
        },
        "modules": [
            {
                "id": "postgres",
                "source": "modules/warehouse/postgres",
                "version": "0.1.0",
                "dependsOn": [],
                "provides": {
                    "sql-database": {"kind": "sql-database"},
                },
                "consumes": {},
            },
            {
                "id": "dagster",
                "source": "modules/orchestration/dagster",
                "version": "0.1.0",
                "dependsOn": ["postgres"],
                "provides": {
                    "http-service": {"kind": "http-service"},
                },
                "consumes": {
                    "analytics-database": {
                        "contract": {"kind": "sql-database"},
                        "contractRef": "postgres.sql-database",
                    },
                },
            },
        ],
    }


class ModuleAndTopologySummaryTest(unittest.TestCase):
    def test_module_summary_includes_id_source_version_dependson(self) -> None:
        plan = _sample_plan()
        summary = _build_module_summary(plan)
        self.assertEqual(
            summary,
            [
                {
                    "id": "postgres",
                    "source": "modules/warehouse/postgres",
                    "version": "0.1.0",
                    "dependsOn": [],
                },
                {
                    "id": "dagster",
                    "source": "modules/orchestration/dagster",
                    "version": "0.1.0",
                    "dependsOn": ["postgres"],
                },
            ],
        )

    def test_topology_summary_captures_provides_consumes_dependson(self) -> None:
        plan = _sample_plan()
        topology = _build_topology_summary(plan)
        dagster_entry = next(entry for entry in topology if entry["id"] == "dagster")
        self.assertEqual(dagster_entry["dependsOn"], ["postgres"])
        self.assertEqual(
            dagster_entry["consumes"],
            [{
                "name": "analytics-database",
                "kind": "sql-database",
                "contractRef": "postgres.sql-database",
                "provider": "postgres",
            }],
        )
        postgres_entry = next(entry for entry in topology if entry["id"] == "postgres")
        self.assertEqual(
            postgres_entry["provides"],
            [{"name": "sql-database", "kind": "sql-database"}],
        )


class ImageEvidenceTest(unittest.TestCase):
    def _compose(self, images: dict) -> str:
        return yaml.safe_dump({"services": images}, sort_keys=False)

    def test_available_evidence_for_fixture_match(self) -> None:
        digest = "sha256:eec0d481c1ac07099c2957b07598532746e261cbdeac29cdf651432dae9a96c8"
        compose = self._compose({
            "dagster": {"image": f"ghcr.io/ronaldhensbergen/cds-dagster@{digest}"},
        })
        entries, diagnostics = _build_image_evidence(compose, _FIXTURE_PATH)
        self.assertEqual(diagnostics, [])
        self.assertEqual(len(entries), 1)
        evidence = entries[0]["evidence"]
        self.assertEqual(evidence["status"], "available")
        self.assertTrue(evidence["signed"])
        self.assertTrue(evidence["provenanceAttested"])
        self.assertTrue(evidence["sbomAttested"])

    def test_local_build_reports_not_available_without_diagnostic(self) -> None:
        compose = self._compose({
            "custom": {"image": "local/dagster:custom", "build": {"context": "."}},
        })
        entries, diagnostics = _build_image_evidence(compose, _FIXTURE_PATH)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["evidence"]["status"], "not-available")
        self.assertIn("Locally built", entries[0]["evidence"]["reason"])
        # Locally-built images are an expected gap, not a warning-worthy one.
        self.assertEqual(diagnostics, [])

    def test_no_fixture_match_emits_w101_warning(self) -> None:
        compose = self._compose({
            "unknown": {"image": "quay.io/example/tool:1.0"},
        })
        entries, diagnostics = _build_image_evidence(compose, _FIXTURE_PATH)
        self.assertEqual(entries[0]["evidence"]["status"], "not-available")
        self.assertEqual(len(diagnostics), 1)
        self.assertEqual(diagnostics[0].code, "W101")
        self.assertEqual(diagnostics[0].level, "warning")

    def test_no_fixture_configured_gives_distinct_reason(self) -> None:
        # None only means "use the default fixture path" (which resolves to
        # the real, present fixture in this checkout); simulate no fixture
        # being configured at all by patching default_fixture_path().
        compose = self._compose({
            "unknown": {"image": "quay.io/example/tool:1.0"},
        })
        with unittest.mock.patch("cli.report.default_fixture_path", return_value=None):
            entries, diagnostics = _build_image_evidence(compose, None)
        self.assertEqual(entries[0]["evidence"]["status"], "not-available")
        self.assertIn("No signed-images fixture is configured", entries[0]["evidence"]["reason"])
        self.assertEqual(len(diagnostics), 1)


class SecretLeakCheckTest(unittest.TestCase):
    def _write_env(self, tmp_path: Path, contents: str) -> Path:
        env_path = tmp_path / ".env"
        env_path.write_text(contents, encoding="utf-8")
        return env_path

    def test_pass_when_only_placeholder_appears(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env_path = self._write_env(Path(tmp), "CDS_DB_PASSWORD=super-secret\n")
            compose = "services:\n  db:\n    environment:\n      POSTGRES_PASSWORD: ${CDS_DB_PASSWORD}\n"
            result, diagnostics = _check_no_leaked_secrets(_sample_plan(), compose, str(env_path))
            self.assertEqual(result["status"], "pass")
            self.assertEqual(result["checkedSecretCount"], 1)
            self.assertEqual(diagnostics, [])

    def test_fail_when_literal_secret_value_leaks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env_path = self._write_env(Path(tmp), "CDS_DB_PASSWORD=super-secret\n")
            compose = "services:\n  db:\n    environment:\n      POSTGRES_PASSWORD: super-secret\n"
            result, diagnostics = _check_no_leaked_secrets(_sample_plan(), compose, str(env_path))
            self.assertEqual(result["status"], "fail")
            self.assertEqual(result["leakedSecretNames"], ["CDS_DB_PASSWORD"])
            self.assertEqual(len(diagnostics), 1)
            self.assertEqual(diagnostics[0].code, "E119")
            self.assertEqual(diagnostics[0].level, "error")

    def test_non_declared_cds_var_is_not_scanned(self) -> None:
        """
        Regression test: plan["secrets"] self-maps every CDS_* variable found
        in .env (name -> itself) so raw secrets.CDS_FOO references keep
        working, but only variables the profile actually declared under
        spec.secrets.values (alias != env name) are treated as secrets here.
        Otherwise ordinary config incidentally named CDS_* (e.g. a database
        *name*) would false-positive whenever it happens to also appear as
        a literal, legitimate value elsewhere in the rendered output.
        """
        with tempfile.TemporaryDirectory() as tmp:
            env_path = self._write_env(
                Path(tmp), "CDS_DB_PASSWORD=super-secret\nCDS_DB_NAME=analytics\n"
            )
            compose = (
                "services:\n"
                "  db:\n"
                "    environment:\n"
                "      POSTGRES_DB: analytics\n"
                "      POSTGRES_PASSWORD: ${CDS_DB_PASSWORD}\n"
            )
            result, diagnostics = _check_no_leaked_secrets(_sample_plan(), compose, str(env_path))
            self.assertEqual(result["status"], "pass")
            self.assertEqual(result["checkedSecretCount"], 1)
            self.assertEqual(diagnostics, [])


class MainReportCommandTest(unittest.TestCase):
    """In-process tests for cli.main's `report` handler (mocks
    build_compliance_report so these don't depend on plan/render/.env
    state -- that end-to-end path is covered by BuildComplianceReportTest
    and ReportCLITest above)."""

    def _canned_report(self, leak_status: str = "pass") -> dict:
        return {
            "apiVersion": "cds/v1alpha1",
            "kind": "ComplianceReport",
            "generatedAt": "2026-09-27T09:49:56+00:00",
            "profile": {
                "sourceProfile": _PROFILE_FILE,
                "name": _PROFILE_NAME,
                "environment": "prod",
            },
            "modules": [
                {"id": "postgres", "source": "modules/warehouse/postgres", "version": "0.1.0", "dependsOn": []},
                {"id": "dagster", "source": "modules/orchestration/dagster", "version": "0.1.0", "dependsOn": ["postgres"]},
            ],
            "images": [
                {
                    "service": "postgres",
                    "image": "postgres:18@sha256:" + "a" * 64,
                    "digestPinned": True,
                    "evidence": {
                        "status": "available",
                        "digest": "sha256:" + "a" * 64,
                        "signed": True,
                        "provenanceAttested": True,
                        "sbomAttested": True,
                        "source": str(_FIXTURE_PATH),
                    },
                },
                {
                    "service": "dagster",
                    "image": "local/dagster:custom",
                    "digestPinned": False,
                    "evidence": {"status": "not-available", "reason": "Locally built image."},
                },
            ],
            "topology": [
                {
                    "id": "postgres",
                    "dependsOn": [],
                    "provides": [{"name": "sql-database", "kind": "sql-database"}],
                    "consumes": [],
                },
                {
                    "id": "dagster",
                    "dependsOn": ["postgres"],
                    "provides": [],
                    "consumes": [{
                        "name": "analytics-database",
                        "kind": "sql-database",
                        "contractRef": "postgres.sql-database",
                        "provider": "postgres",
                    }],
                },
            ],
            "secretLeakCheck": {
                "status": leak_status,
                "checkedSecretCount": 1,
                "leakedSecretNames": ["CDS_DB_PASSWORD"] if leak_status == "fail" else [],
            },
            "disclaimer": REPORT_DISCLAIMER,
        }

    def _run_main(self, argv: list) -> tuple[int, str, str]:
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.object(sys, "argv", ["cds", *argv]), \
             contextlib.redirect_stdout(stdout), \
             contextlib.redirect_stderr(stderr):
            code = main()
        return code, stdout.getvalue(), stderr.getvalue()

    def test_json_output_exit_zero_on_pass(self) -> None:
        with patch("cli.main.build_compliance_report", return_value=(self._canned_report(), [])):
            code, stdout, stderr = self._run_main(["report", _PROFILE_FILE, "--json"])
        self.assertEqual(code, 0)
        self.assertEqual(stderr, "")
        parsed = json.loads(stdout)
        self.assertEqual(parsed["kind"], "ComplianceReport")

    def test_text_output_default_format(self) -> None:
        with patch("cli.main.build_compliance_report", return_value=(self._canned_report(), [])):
            code, stdout, _stderr = self._run_main(["report", _PROFILE_FILE])
        self.assertEqual(code, 0)
        self.assertIn("Compliance/Evidence Report", stdout)
        self.assertIn("Environment: prod", stdout)
        self.assertIn("dependsOn: postgres", stdout)
        self.assertIn("evidence: signed=True", stdout)
        self.assertIn("evidence: NOT AVAILABLE", stdout)
        self.assertIn("consumes: analytics-database", stdout)
        self.assertIn(REPORT_DISCLAIMER, stdout)

    def test_text_output_lists_leaked_secret_names(self) -> None:
        with patch("cli.main.build_compliance_report", return_value=(self._canned_report("fail"), [])):
            code, stdout, _stderr = self._run_main(["report", _PROFILE_FILE])
        self.assertEqual(code, 1)
        self.assertIn("status=fail", stdout)
        self.assertIn("leaked: CDS_DB_PASSWORD", stdout)

    def test_diagnostics_go_to_stderr_not_stdout(self) -> None:
        from cli.diagnostics import Diagnostic
        diag = Diagnostic(level="warning", code="W101", message="no evidence", path="services.x.image")
        with patch("cli.main.build_compliance_report", return_value=(self._canned_report(), [diag])):
            code, stdout, stderr = self._run_main(["report", _PROFILE_FILE, "--json"])
        self.assertEqual(code, 0)
        self.assertIn("W101", stderr)
        self.assertNotIn("W101", stdout)
        json.loads(stdout)  # still valid JSON despite the warning

    def test_exit_code_one_when_secret_leak_fails(self) -> None:
        with patch("cli.main.build_compliance_report", return_value=(self._canned_report("fail"), [])):
            code, stdout, _stderr = self._run_main(["report", _PROFILE_FILE, "--json"])
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(stdout)["secretLeakCheck"]["status"], "fail")

    def test_exit_code_one_when_report_is_none(self) -> None:
        from cli.diagnostics import Diagnostic
        diag = Diagnostic(level="error", code="E010", message="bad profile", path="spec.modules")
        with patch("cli.main.build_compliance_report", return_value=(None, [diag])):
            code, stdout, stderr = self._run_main(["report", _PROFILE_FILE])
        self.assertEqual(code, 1)
        self.assertIn("validate/plan/render failed", stderr)
        self.assertIn("E010", stderr)
        self.assertEqual(stdout, "")

    def test_output_flag_writes_file_and_prints_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out_path = Path(tmp) / "report.json"
            with patch("cli.main.build_compliance_report", return_value=(self._canned_report(), [])):
                code, stdout, _stderr = self._run_main(
                    ["report", _PROFILE_FILE, "--json", "--output", str(out_path)]
                )
            self.assertEqual(code, 0)
            self.assertIn(f"Compliance report saved to {out_path}", stdout)
            saved = json.loads(out_path.read_text(encoding="utf-8"))
            self.assertEqual(saved["kind"], "ComplianceReport")


class BuildComplianceReportFailurePathsTest(unittest.TestCase):
    """Covers build_compliance_report's early-return branches (validate,
    plan, and render failures each return (None, diagnostics))."""

    def test_returns_none_when_validate_has_errors(self) -> None:
        from cli.diagnostics import Diagnostic
        err = Diagnostic(level="error", code="E010", message="bad", path="spec.modules")
        with patch("cli.report.validate_profile", return_value=[err]):
            report, diagnostics = build_compliance_report(_PROFILE_FILE)
        self.assertIsNone(report)
        self.assertIn(err, diagnostics)

    def test_returns_none_when_plan_fails(self) -> None:
        from cli.diagnostics import Diagnostic
        err = Diagnostic(level="error", code="E020", message="bad plan", path="spec")
        with patch("cli.report.validate_profile", return_value=[]), \
             patch("cli.report.build_plan", return_value=(None, [err])):
            report, diagnostics = build_compliance_report(_PROFILE_FILE)
        self.assertIsNone(report)
        self.assertIn(err, diagnostics)

    def test_returns_none_when_render_fails(self) -> None:
        from cli.diagnostics import Diagnostic
        err = Diagnostic(level="error", code="E030", message="bad render", path="spec")
        with patch("cli.report.validate_profile", return_value=[]), \
             patch("cli.report.build_plan", return_value=({"modules": [], "secrets": {}}, [])), \
             patch("cli.report.render_compose", return_value=(None, [err])):
            report, diagnostics = build_compliance_report(_PROFILE_FILE)
        self.assertIsNone(report)
        self.assertIn(err, diagnostics)


class SecretLeakCheckEmptyValueTest(unittest.TestCase):
    def test_declared_secret_with_empty_value_is_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            env_path.write_text("CDS_DB_PASSWORD=\n", encoding="utf-8")
            plan = {"secrets": {"db_password": "CDS_DB_PASSWORD"}}
            result, diagnostics = _check_no_leaked_secrets(plan, "services: {}\n", str(env_path))
            self.assertEqual(result["status"], "pass")
            self.assertEqual(result["checkedSecretCount"], 0)
            self.assertEqual(diagnostics, [])


class BuildComplianceReportTest(unittest.TestCase):
    """End-to-end (validate -> plan -> render -> report) coverage using the
    repository's own sample profile, matching the fixture setup used by
    tests.test_cds_workflow."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._created_env = False
        if not _PROFILE_ENV_FILE.exists():
            init = subprocess.run(
                [sys.executable, "-m", "cli.main", "init", _PROFILE_NAME],
                cwd=str(_REPO_ROOT),
                capture_output=True,
                text=True,
            )
            if init.returncode != 0:
                raise RuntimeError(f"cds init failed:\n{init.stdout}\n{init.stderr}")
            cls._created_env = True

    @classmethod
    def tearDownClass(cls) -> None:
        if cls._created_env and _PROFILE_ENV_FILE.exists():
            _PROFILE_ENV_FILE.unlink()

    def test_report_assembles_modules_images_topology_and_passes_secret_check(self) -> None:
        report, diagnostics = build_compliance_report(
            _PROFILE_FILE, env_file=str(_PROFILE_ENV_FILE)
        )
        self.assertIsNotNone(report, f"report build failed: {diagnostics}")
        self.assertEqual(report["kind"], "ComplianceReport")
        self.assertEqual(report["disclaimer"], REPORT_DISCLAIMER)

        module_ids = {module["id"] for module in report["modules"]}
        self.assertEqual(module_ids, {"postgres", "dagster", "keydb", "superset"})

        self.assertTrue(report["images"])
        for entry in report["images"]:
            self.assertIn(entry["evidence"]["status"], {"available", "not-available"})

        topology_ids = {entry["id"] for entry in report["topology"]}
        self.assertEqual(topology_ids, module_ids)

        # None of the real, declared secrets (all *_PASSWORD/secret_key
        # aliases in this profile) should ever leak into rendered Compose.
        self.assertEqual(report["secretLeakCheck"]["status"], "pass")
        self.assertGreater(report["secretLeakCheck"]["checkedSecretCount"], 0)


class ReportCLITest(unittest.TestCase):
    """CLI-level smoke tests for `cds report`, following the venv-cds
    discovery pattern used by tests.test_cds_workflow."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.cds = cls._find_cds()
        cls._created_env = False
        if not _PROFILE_ENV_FILE.exists():
            init = subprocess.run(
                cls.cds + ["init", _PROFILE_NAME],
                cwd=str(_REPO_ROOT),
                capture_output=True,
                text=True,
            )
            if init.returncode != 0:
                raise RuntimeError(f"cds init failed:\n{init.stdout}\n{init.stderr}")
            cls._created_env = True

    @classmethod
    def tearDownClass(cls) -> None:
        if cls._created_env and _PROFILE_ENV_FILE.exists():
            _PROFILE_ENV_FILE.unlink()

    @staticmethod
    def _find_cds() -> list:
        venv_dir = _REPO_ROOT / ".venv"
        venv_cds = venv_dir / ("Scripts/cds.exe" if sys.platform == "win32" else "bin/cds")
        if venv_cds.exists():
            return [str(venv_cds)]
        return [sys.executable, "-m", "cli.main"]

    def _run(self, *args) -> subprocess.CompletedProcess:
        env = os.environ.copy()
        env.pop("CDS_PROFILE_PATH", None)
        return subprocess.run(
            self.cds + ["report", _PROFILE_NAME, *args],
            cwd=str(_REPO_ROOT),
            capture_output=True,
            text=True,
            env=env,
        )

    def test_text_output_includes_disclaimer(self) -> None:
        result = self._run()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Compliance/Evidence Report", result.stdout)
        self.assertIn(REPORT_DISCLAIMER, result.stdout)

    def test_json_output_is_valid_and_diagnostics_go_to_stderr(self) -> None:
        result = self._run("--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["kind"], "ComplianceReport")
        self.assertEqual(report["secretLeakCheck"]["status"], "pass")

    def test_output_flag_writes_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out_path = Path(tmp) / "report.json"
            result = self._run("--json", "--output", str(out_path))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(out_path.is_file())
            report = json.loads(out_path.read_text(encoding="utf-8"))
            self.assertEqual(report["kind"], "ComplianceReport")


if __name__ == "__main__":
    unittest.main()
