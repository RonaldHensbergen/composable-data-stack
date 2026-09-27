import json
import os
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

import yaml

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
